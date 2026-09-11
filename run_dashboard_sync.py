"""
Sync script for the standalone Plutus Wealth Management Dashboard.

Reads the authoritative final sent-email briefing artifact,
parses the exact 5/5/5 stories, and persists them into data/dashboard.db.

CRITICAL ARCHITECTURE:
- Completely separate from the pipeline execution.
- NEVER imports run_pipeline or run_daily.
- NEVER calls external APIs (Gemini, SerpAPI, Gmail).
- Purely read-only on pipeline files, writing only to data/dashboard.db.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from app.dashboard.models import DashboardBriefing, DashboardStory
from app.dashboard.repository import DashboardRepository, DashboardSyncConflictError, compute_briefing_content_hash


def parse_date_string(date_text: str) -> Optional[date]:
    """Parse various human-readable date formats found in briefing headers."""
    clean = date_text.strip().strip("*")
    # Clean ordinals e.g. "5th", "8th", "1st", "2nd", "3rd"
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", clean)

    # Clean day-of-week prefix e.g. "Tuesday, 8 September, 2026"
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    candidates = [clean]
    if len(parts) >= 2:
        candidates.append(", ".join(parts[1:]))
        candidates.append(" ".join(parts[1:]))

    formats = [
        "%A, %d %B, %Y",
        "%A, %d %B %Y",
        "%d %B, %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%B %d %Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]

    for cand in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(cand.strip(), fmt).date()
            except ValueError:
                continue
    return None


def extract_date_from_briefing_text(text: str) -> Optional[date]:
    """Extract briefing date strictly from header lines."""
    lines = [line.strip().strip("*") for line in text.splitlines() if line.strip()]
    for line in lines[:8]:
        # Search for months or date patterns
        if any(m in line for m in ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December", "2024", "2025", "2026")):
            extracted = parse_date_string(line)
            if extracted:
                return extracted
    return None


def parse_briefing_text(text: str, default_date: Optional[date] = None) -> Optional[DashboardBriefing]:
    """
    Parse authoritative copy_paste_briefing.txt or final_briefing.txt into DashboardBriefing.
    Extracts date from header and parses exact 5 India, 5 Domestic, 5 International stories.
    """
    extracted_date = extract_date_from_briefing_text(text)
    briefing_date = extracted_date or default_date
    if not briefing_date:
        return None

    lines = [line.strip() for line in text.splitlines()]

    section_headers = {
        "india": r"TOP\s+5\s+INDIA\s+BUSINESS\s+HEADLINES",
        "domestic": r"TOP\s+5\s+DOMESTIC\s+HEADLINES",
        "international": r"TOP\s+5\s+INTERNATIONAL\s+BUSINESS\s+HEADLINES",
    }

    sec_positions: List[Tuple[str, int]] = []
    for idx, line in enumerate(lines):
        for sec, pattern in section_headers.items():
            if re.search(pattern, line, re.IGNORECASE):
                sec_positions.append((sec, idx))

    if len(sec_positions) != 3:
        return _parse_sections_regex(text, briefing_date)

    sec_positions.sort(key=lambda x: x[1])

    stories: List[DashboardStory] = []
    for i, (sec_name, start_idx) in enumerate(sec_positions):
        end_idx = sec_positions[i + 1][1] if i + 1 < len(sec_positions) else len(lines)
        section_lines = lines[start_idx + 1:end_idx]
        sec_stories = _parse_section_stories(section_lines, sec_name)
        stories.extend(sec_stories)

    if len(stories) != 15:
        alt_briefing = _parse_sections_regex(text, briefing_date)
        if alt_briefing and len(alt_briefing.stories) == 15:
            return alt_briefing
        return None

    briefing = DashboardBriefing(
        briefing_date=briefing_date,
        generated_at=datetime.utcnow(),
        full_text=text,
        story_count=len(stories),
        stories=stories,
    )
    briefing.content_hash = compute_briefing_content_hash(briefing)
    return briefing


def _parse_section_stories(lines: List[str], section: str) -> List[DashboardStory]:
    """Parse 5 individual stories from lines belonging to a section."""
    full_sec_text = "\n".join(lines)
    story_blocks = re.split(r"\n(?=\*?\s*[1-5][\.\)])", "\n" + full_sec_text)
    stories: List[DashboardStory] = []

    for block in story_blocks:
        block_clean = block.strip()
        if not block_clean:
            continue

        match = re.match(r"^\*?\s*([1-5])[\.\)]\s*(.+)", block_clean, re.DOTALL)
        if not match:
            continue

        pos = int(match.group(1))
        content = match.group(2).strip()

        b_lines = [l.strip() for l in content.splitlines() if l.strip()]
        if not b_lines:
            continue

        headline = b_lines[0].strip().strip("*")

        summary_parts = []
        source = "Unknown Source"
        url = ""

        for l in b_lines[1:]:
            if re.match(r"^https?://", l, re.IGNORECASE):
                if not url:
                    url = l
            elif re.match(r"^Source:\s*(.+)", l, re.IGNORECASE):
                s_match = re.match(r"^Source:\s*(.+)", l, re.IGNORECASE)
                if s_match:
                    source = s_match.group(1).strip()
            elif re.match(r"^Also verified by:", l, re.IGNORECASE):
                continue
            else:
                summary_parts.append(l)

        summary = " ".join(summary_parts).strip() if summary_parts else None

        if headline and url:
            stories.append(
                DashboardStory(
                    section=section,
                    position=pos,
                    headline=headline,
                    summary=summary,
                    source=source,
                    url=url,
                )
            )

    return sorted(stories, key=lambda s: s.position)


def _parse_sections_regex(text: str, briefing_date: date) -> Optional[DashboardBriefing]:
    """Regex-based fallback for parsing 3 sections."""
    india_pat = r"TOP\s+5\s+INDIA\s+BUSINESS\s+HEADLINES(.*?)(?=TOP\s+5\s+DOMESTIC\s+HEADLINES|$)"
    dom_pat = r"TOP\s+5\s+DOMESTIC\s+HEADLINES(.*?)(?=TOP\s+5\s+INTERNATIONAL\s+BUSINESS\s+HEADLINES|$)"
    intl_pat = r"TOP\s+5\s+INTERNATIONAL\s+BUSINESS\s+HEADLINES(.*?)$"

    m_in = re.search(india_pat, text, re.DOTALL | re.IGNORECASE)
    m_dom = re.search(dom_pat, text, re.DOTALL | re.IGNORECASE)
    m_int = re.search(intl_pat, text, re.DOTALL | re.IGNORECASE)

    if not (m_in and m_dom and m_int):
        return None

    stories: List[DashboardStory] = []
    stories.extend(_parse_section_stories(m_in.group(1).splitlines(), "india"))
    stories.extend(_parse_section_stories(m_dom.group(1).splitlines(), "domestic"))
    stories.extend(_parse_section_stories(m_int.group(1).splitlines(), "international"))

    if len(stories) != 15:
        return None

    briefing = DashboardBriefing(
        briefing_date=briefing_date,
        generated_at=datetime.utcnow(),
        full_text=text,
        story_count=len(stories),
        stories=stories,
    )
    briefing.content_hash = compute_briefing_content_hash(briefing)
    return briefing


def parse_briefing_json(json_path: Path) -> Optional[DashboardBriefing]:
    """Parse JSON format only when explicitly provided."""
    if not json_path.exists():
        return None

    try:
        content = json_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        print(f"[ERROR] Failed to load JSON from {json_path}: {e}")
        return None

    b_date_str = data.get("briefing_date")
    b_date = date.fromisoformat(b_date_str) if b_date_str else None
    if not b_date and "full_text" in data:
        b_date = extract_date_from_briefing_text(data["full_text"])

    stories: List[DashboardStory] = []
    for sec_key in ("india", "domestic", "international"):
        sec_list = data.get(sec_key) or []
        for idx, item in enumerate(sec_list, 1):
            headline = item.get("headline", "").strip().strip("*")
            summary = item.get("summary", "") or None
            source = item.get("source", "Unknown Source")
            url = item.get("url", "").strip()

            if headline and url:
                stories.append(
                    DashboardStory(
                        section=sec_key,
                        position=idx,
                        headline=headline,
                        summary=summary,
                        source=source,
                        url=url,
                    )
                )

    if len(stories) != 15 or not b_date:
        return None

    briefing = DashboardBriefing(
        briefing_date=b_date,
        generated_at=datetime.utcnow(),
        full_text=data.get("full_text") or content,
        story_count=len(stories),
        stories=stories,
    )
    briefing.content_hash = compute_briefing_content_hash(briefing)
    return briefing


def sync_dashboard(
    input_file: Optional[Path] = None,
    db_path: Optional[Path] = None,
    target_date: Optional[date] = None,
    replace_date: Optional[date] = None,
) -> bool:
    """
    Main sync function:
    1. Selects the authoritative sent-email briefing artifact (default data/copy_paste_briefing.txt).
    2. Extracts and verifies the briefing date from content.
    3. Rejects date mismatches.
    4. Enforces conflict protection unless --replace-date is explicitly provided.
    5. Saves into dashboard.db.
    """
    candidate_file: Optional[Path] = None
    if input_file:
        candidate_file = Path(input_file)
        if not candidate_file.exists():
            print(f"[ERROR] Specified input file '{candidate_file}' does not exist.")
            return False
    else:
        data_dir = Path("data")
        # Authoritative sent-email output files in precedence order
        email_artifacts = [
            data_dir / "copy_paste_briefing.txt",
            data_dir / "final_briefing.txt",
        ]
        for f in email_artifacts:
            if f.exists():
                candidate_file = f
                break

    if not candidate_file:
        print("[ERROR] No authoritative email briefing file found in data/ (checked copy_paste_briefing.txt, final_briefing.txt).")
        return False

    # Parse candidate file
    briefing: Optional[DashboardBriefing] = None
    if candidate_file.suffix.lower() == ".json":
        briefing = parse_briefing_json(candidate_file)
    else:
        text_content = candidate_file.read_text(encoding="utf-8")
        briefing = parse_briefing_text(text_content, default_date=target_date or replace_date)

    if not briefing or briefing.story_count != 15:
        print(f"[ERROR] Could not extract complete 15-story briefing from {candidate_file}.")
        return False

    briefing.sync_source = candidate_file.name

    # DATE SAFETY CHECKS
    # 1. If explicit target_date supplied, content date MUST match
    if target_date and briefing.briefing_date != target_date:
        print(
            f"[DATE_MISMATCH_ERROR] Briefing content date ({briefing.briefing_date}) "
            f"does not match requested date ({target_date}). Aborting sync."
        )
        return False

    # 2. If replace_date supplied, content date MUST match
    allow_replace = False
    if replace_date:
        if briefing.briefing_date != replace_date:
            print(
                f"[DATE_MISMATCH_ERROR] Briefing content date ({briefing.briefing_date}) "
                f"does not match --replace-date ({replace_date}). Aborting replacement."
            )
            return False
        allow_replace = True

    repo = DashboardRepository(db_path=db_path)

    try:
        b_id = repo.save_briefing(briefing, allow_replace=allow_replace)
        print(
            f"[SYNC_SUCCESS] Briefing id={b_id} for date {briefing.briefing_date} "
            f"synced into {repo.db_path} (source: {briefing.sync_source}, hash: {briefing.content_hash[:10]}...)"
        )
        return True
    except DashboardSyncConflictError as conflict_err:
        print(f"[ERROR] {conflict_err}")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to save briefing: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync authoritative sent-email briefing artifact to Dashboard DB.")
    parser.add_argument("--file", type=Path, default=None, help="Path to authoritative briefing file (e.g. data/copy_paste_briefing.txt)")
    parser.add_argument("--db", type=Path, default=None, help="Path to dashboard.db")
    parser.add_argument("--date", type=str, default=None, help="Assert expected briefing date (YYYY-MM-DD)")
    parser.add_argument("--replace-date", type=str, default=None, help="Explicitly allow overwriting dashboard record for date (YYYY-MM-DD)")

    args = parser.parse_args()

    target_d = None
    if args.date:
        try:
            target_d = date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] Invalid --date format '{args.date}'. Expected YYYY-MM-DD.")
            sys.exit(1)

    replace_d = None
    if args.replace_date:
        try:
            replace_d = date.fromisoformat(args.replace_date)
        except ValueError:
            print(f"[ERROR] Invalid --replace-date format '{args.replace_date}'. Expected YYYY-MM-DD.")
            sys.exit(1)

    success = sync_dashboard(
        input_file=args.file,
        db_path=args.db,
        target_date=target_d,
        replace_date=replace_d,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
