"""Sync the authoritative sent-email briefing into the dashboard database.

Canonical public contract: exactly 5 India Business + 5 International Business
stories, using the same unnumbered text format sent by email. The parser keeps
backward compatibility with older numbered / 15-story artifacts.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

from app.dashboard.models import DashboardBriefing, DashboardStory
from app.dashboard.repository import (
    DashboardRepository,
    DashboardSyncConflictError,
    compute_briefing_content_hash,
)


def parse_date_string(date_text: str) -> Optional[date]:
    """Parse human-readable date formats used by briefing headers."""
    clean = date_text.strip().strip("*")
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", clean)

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
    """Extract the briefing date from the first few header lines."""
    lines = [line.strip().strip("*") for line in text.splitlines() if line.strip()]
    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    for line in lines[:8]:
        if any(month in line for month in months) or re.search(r"\b20\d{2}\b", line):
            parsed = parse_date_string(line)
            if parsed:
                return parsed
    return None


def _story_from_lines(block: List[str], section: str, position: int) -> Optional[DashboardStory]:
    """Convert one canonical/legacy story block into a DashboardStory."""
    clean = [line.strip() for line in block if line.strip()]
    if not clean:
        return None

    first = clean[0]
    numbered = re.match(r"^\*?\s*([1-5])[.)]\s*(.+?)\*?$", first)
    if numbered:
        headline = numbered.group(2).strip().strip("*")
        position = int(numbered.group(1))
    else:
        headline = first.strip().strip("*")

    source = "Unknown Source"
    primary_url = ""
    summary_parts: List[str] = []
    after_secondary_label = False

    for line in clean[1:]:
        source_match = re.match(r"^Source:\s*(.+)$", line, re.IGNORECASE)
        if source_match:
            source = source_match.group(1).strip()
            continue
        if re.match(r"^Also verified by:", line, re.IGNORECASE):
            after_secondary_label = True
            continue
        if re.match(r"^https?://", line, re.IGNORECASE):
            if not primary_url:
                primary_url = line
            # Later URLs are secondary verification URLs and are intentionally
            # not persisted as the dashboard's primary story URL.
            continue
        if not after_secondary_label:
            summary_parts.append(line)

    if not headline or not primary_url:
        return None

    return DashboardStory(
        section=section,
        position=position,
        headline=headline,
        summary=" ".join(summary_parts).strip() or None,
        source=source,
        url=primary_url,
    )


def _parse_section_stories(lines: List[str], section: str) -> List[DashboardStory]:
    """Parse up to five numbered or unnumbered stories from one section."""
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty:
        return []

    # Canonical format: each headline is a single-asterisk line. Legacy format:
    # each story starts with 1. / 1) ... 5. / 5).
    starts: List[int] = []
    for idx, line in enumerate(nonempty):
        is_numbered = bool(re.match(r"^\*?\s*[1-5][.)]\s+", line))
        is_canonical_headline = (
            line.startswith("*")
            and line.endswith("*")
            and not line.startswith("**")
            and not re.search(r"TOP\s+5\s+", line, re.IGNORECASE)
        )
        if is_numbered or is_canonical_headline:
            starts.append(idx)

    stories: List[DashboardStory] = []
    for ordinal, start in enumerate(starts[:5], 1):
        end = starts[ordinal] if ordinal < len(starts) else len(nonempty)
        story = _story_from_lines(nonempty[start:end], section, ordinal)
        if story:
            stories.append(story)

    return sorted(stories, key=lambda story: story.position)


def _build_briefing(text: str, briefing_date: date, stories: List[DashboardStory]) -> Optional[DashboardBriefing]:
    """Build a dashboard briefing only for a complete canonical or legacy set."""
    counts = {
        "india": sum(1 for s in stories if s.section == "india"),
        "domestic": sum(1 for s in stories if s.section == "domestic"),
        "international": sum(1 for s in stories if s.section == "international"),
    }

    canonical_10 = counts["india"] == 5 and counts["international"] == 5 and counts["domestic"] == 0
    legacy_15 = counts["india"] == 5 and counts["international"] == 5 and counts["domestic"] == 5
    if not (canonical_10 or legacy_15):
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


def parse_briefing_text(text: str, default_date: Optional[date] = None) -> Optional[DashboardBriefing]:
    """Parse the canonical 10-story briefing, with legacy 15-story support."""
    briefing_date = extract_date_from_briefing_text(text) or default_date
    if not briefing_date:
        return None

    lines = [line.strip() for line in text.splitlines()]
    section_headers = {
        "india": r"TOP\s+5\s+INDIA\s+BUSINESS\s+HEADLINES",
        "domestic": r"TOP\s+5\s+DOMESTIC\s+HEADLINES",
        "international": r"TOP\s+5\s+INTERNATIONAL\s+BUSINESS\s+HEADLINES",
    }

    positions: List[Tuple[str, int]] = []
    for idx, line in enumerate(lines):
        for section, pattern in section_headers.items():
            if re.search(pattern, line, re.IGNORECASE):
                positions.append((section, idx))

    present_sections = {section for section, _ in positions}
    if not {"india", "international"}.issubset(present_sections):
        return None

    positions.sort(key=lambda item: item[1])
    stories: List[DashboardStory] = []
    for idx, (section, start) in enumerate(positions):
        end = positions[idx + 1][1] if idx + 1 < len(positions) else len(lines)
        stories.extend(_parse_section_stories(lines[start + 1:end], section))

    return _build_briefing(text, briefing_date, stories)


def _parse_sections_regex(text: str, briefing_date: date) -> Optional[DashboardBriefing]:
    """Compatibility wrapper retained for older callers/tests."""
    return parse_briefing_text(text, default_date=briefing_date)


def parse_briefing_json(json_path: Path) -> Optional[DashboardBriefing]:
    """Parse canonical 10-story or legacy 15-story JSON artifacts."""
    if not json_path.exists():
        return None
    try:
        content = json_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        print(f"[ERROR] Failed to load JSON from {json_path}: {exc}")
        return None

    date_value = data.get("briefing_date")
    briefing_date = date.fromisoformat(date_value) if date_value else None
    if not briefing_date and data.get("full_text"):
        briefing_date = extract_date_from_briefing_text(data["full_text"])
    if not briefing_date:
        return None

    stories: List[DashboardStory] = []
    for section in ("india", "domestic", "international"):
        for position, item in enumerate(data.get(section) or [], 1):
            headline = (item.get("headline") or "").strip().strip("*")
            source = item.get("source") or "Unknown Source"
            url = (item.get("url") or "").strip()
            if headline and url:
                stories.append(DashboardStory(
                    section=section,
                    position=position,
                    headline=headline,
                    summary=item.get("summary") or None,
                    source=source,
                    url=url,
                ))

    return _build_briefing(data.get("full_text") or content, briefing_date, stories)


def get_dashboard_sync_date(data_dir: Path) -> Optional[str]:
    """Read the date (YYYY-MM-DD) when dashboard sync was successfully completed."""
    date_file = data_dir / "dashboard_sync_date.txt"
    if date_file.exists():
        try:
            return date_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def record_dashboard_sync_date(data_dir: Path, today_str: str) -> None:
    """Record today's date in dashboard_sync_date.txt after confirmed dashboard sync."""
    data_dir.mkdir(parents=True, exist_ok=True)
    date_file = data_dir / "dashboard_sync_date.txt"
    date_file.write_text(today_str, encoding="utf-8")


def sync_dashboard(

    input_file: Optional[Path] = None,
    db_path: Optional[Path] = None,
    target_date: Optional[date] = None,
    replace_date: Optional[date] = None,
    dry_run: bool = False,
) -> bool:
    """Parse the authoritative emailed artifact and persist it safely."""
    candidate_file: Optional[Path] = None
    if input_file:
        candidate_file = Path(input_file)
        if not candidate_file.exists():
            print(f"[ERROR] Specified input file '{candidate_file}' does not exist.")
            return False
    else:
        for path in (Path("data/copy_paste_briefing.txt"), Path("data/final_briefing.txt")):
            if path.exists():
                candidate_file = path
                break

    if not candidate_file:
        print("[ERROR] No authoritative email briefing file found in data/.")
        return False

    if candidate_file.suffix.lower() == ".json":
        briefing = parse_briefing_json(candidate_file)
    else:
        text = candidate_file.read_text(encoding="utf-8")
        briefing = parse_briefing_text(text, default_date=target_date or replace_date)

    if not briefing or briefing.story_count not in (10, 15):
        print(f"[ERROR] Could not extract a complete 10-story briefing from {candidate_file}.")
        return False

    briefing.sync_source = candidate_file.name

    if target_date and briefing.briefing_date != target_date:
        print(
            f"[DATE_MISMATCH_ERROR] Briefing content date ({briefing.briefing_date}) "
            f"does not match requested date ({target_date}). Aborting sync."
        )
        return False

    allow_replace = False
    if replace_date:
        if briefing.briefing_date != replace_date:
            print(
                f"[DATE_MISMATCH_ERROR] Briefing content date ({briefing.briefing_date}) "
                f"does not match --replace-date ({replace_date}). Aborting replacement."
            )
            return False
        allow_replace = True

    from config import is_testing_or_dry_run

    data_dir = candidate_file.parent if candidate_file else Path("data")

    is_dry_run = dry_run or is_testing_or_dry_run()
    if is_dry_run:
        test_db = db_path or (data_dir / "test_briefings.db")
        repo = DashboardRepository(db_path=test_db)
        try:
            briefing_id = repo.save_briefing(briefing, allow_replace=True)
            record_dashboard_sync_date(data_dir, briefing.briefing_date.isoformat())
            print(f"[MOCK_DASHBOARD_SYNC] stories={briefing.story_count} status=SUCCESS (DRY RUN)")
            if not db_path and test_db.exists():
                try:
                    test_db.unlink()
                except Exception:
                    pass
            return True
        except Exception as exc:
            print(f"[ERROR] Mock dashboard sync failed: {exc}")
            return False

    repo = DashboardRepository(db_path=db_path)
    try:
        briefing_id = repo.save_briefing(briefing, allow_replace=allow_replace)
        record_dashboard_sync_date(data_dir, briefing.briefing_date.isoformat())
        target_info = f"backend={repo.backend}" if repo.backend == "postgresql" else str(repo.db_path)
        print(
            f"[SYNC_SUCCESS] Briefing id={briefing_id} for date {briefing.briefing_date} "
            f"synced into {target_info} (stories={briefing.story_count}, "
            f"source={briefing.sync_source}, hash={briefing.content_hash[:10]}...)"
        )
        return True
    except DashboardSyncConflictError as exc:
        print(f"[ERROR] {exc}")
        return False
    except Exception as exc:
        print(f"[ERROR] Failed to save briefing: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync authoritative sent-email briefing artifact to Dashboard DB.")
    parser.add_argument("--file", type=Path, default=None, help="Path to authoritative briefing file")
    parser.add_argument("--db", type=Path, default=None, help="Path to dashboard.db")
    parser.add_argument("--date", type=str, default=None, help="Assert expected briefing date (YYYY-MM-DD)")
    parser.add_argument("--replace-date", type=str, default=None, help="Allow replacing this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Safe dry-run sync into temporary SQLite database")
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date) if args.date else None
        replace_date = date.fromisoformat(args.replace_date) if args.replace_date else None
    except ValueError as exc:
        print(f"[ERROR] Invalid date format. Expected YYYY-MM-DD: {exc}")
        sys.exit(1)

    success = sync_dashboard(
        input_file=args.file,
        db_path=args.db,
        target_date=target_date,
        replace_date=replace_date,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
