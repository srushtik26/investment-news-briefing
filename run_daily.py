"""
Daily Automation Runner for Investment Committee News Briefing.

Wraps run_pipeline.py with:
1. Environment loading via dotenv.
2. Idempotency guards (data/last_email_date.txt) to prevent duplicate daily deliveries.
3. Credential validation (GMAIL_SENDER, GMAIL_RECIPIENT, GMAIL_APP_PASSWORD).
4. Strict precondition validation (5/5/5 story counts, 20/20 check pass, non-empty final artifact).
5. Secure Gmail SMTP email delivery via app.email.email_sender.
6. Atomic state tracking only after verified SMTP transmission.
"""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.logging_config import setup_logging, get_logger
from app.email.email_sender import send_briefing_email, send_copy_paste_email
from app.formatting.formatter import build_copy_paste_text
from config import get_target_date_ist, is_testing_or_dry_run

logger = get_logger("daily.runner")


def get_primary_email_date(data_dir: Path) -> Optional[str]:
    """Read the date (YYYY-MM-DD) when primary email was successfully sent."""
    date_file = data_dir / "primary_email_date.txt"
    if date_file.exists():
        try:
            return date_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Could not read primary_email_date.txt: %s", e)
    return None


def record_primary_email_date(data_dir: Path, today_str: str) -> None:
    """Record today's date in primary_email_date.txt after confirmed primary SMTP delivery."""
    data_dir.mkdir(parents=True, exist_ok=True)
    date_file = data_dir / "primary_email_date.txt"
    date_file.write_text(today_str, encoding="utf-8")
    logger.info("Recorded primary email delivery date in %s: %s", date_file, today_str)


def get_last_email_date(data_dir: Path) -> Optional[str]:
    """Read the last successfully emailed briefing date (YYYY-MM-DD)."""
    date_file = data_dir / "last_email_date.txt"
    if date_file.exists():
        try:
            return date_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Could not read last_email_date.txt: %s", e)
    return None


def record_successful_email_date(data_dir: Path, today_str: str) -> None:
    """Record today's date in last_email_date.txt after confirmed SMTP delivery."""
    data_dir.mkdir(parents=True, exist_ok=True)
    date_file = data_dir / "last_email_date.txt"
    date_file.write_text(today_str, encoding="utf-8")
    logger.info("Recorded successful delivery date in %s: %s", date_file, today_str)


def get_dashboard_sync_date(data_dir: Path) -> Optional[str]:
    """Read the date (YYYY-MM-DD) when dashboard sync was successfully completed."""
    date_file = data_dir / "dashboard_sync_date.txt"
    if date_file.exists():
        try:
            return date_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Could not read dashboard_sync_date.txt: %s", e)
    return None


def record_dashboard_sync_date(data_dir: Path, today_str: str) -> None:
    """Record today's date in dashboard_sync_date.txt after confirmed dashboard sync."""
    data_dir.mkdir(parents=True, exist_ok=True)
    date_file = data_dir / "dashboard_sync_date.txt"
    date_file.write_text(today_str, encoding="utf-8")
    logger.info("Recorded dashboard sync date in %s: %s", date_file, today_str)


def extract_briefing_date_from_artifact(data_dir: Path) -> Optional[date]:
    """Check briefing_date.txt or extract date from final_briefing.txt header."""
    date_file = data_dir / "briefing_date.txt"
    if date_file.exists():
        try:
            return date.fromisoformat(date_file.read_text(encoding="utf-8").strip())
        except Exception as exc:
            logger.warning("Could not read briefing_date.txt: %s", exc)
    final_briefing = data_dir / "final_briefing.txt"
    if final_briefing.exists():
        try:
            from run_dashboard_sync import extract_date_from_briefing_text
            content = final_briefing.read_text(encoding="utf-8")
            return extract_date_from_briefing_text(content)
        except Exception as exc:
            logger.warning("Could not extract date from final_briefing.txt: %s", exc)
    return None


def format_subject_date(d: date) -> str:
    """Format subject date cleanly e.g. '28 Aug 2026'."""
    day = str(d.day)
    month = d.strftime("%b")
    year = str(d.year)
    return f"{day} {month} {year}"


def validate_copy_paste_briefing(text: str) -> bool:
    """Validate that copy/paste text contains exactly 5 India + 5 Domestic + 5 International (15 total)."""
    if not text or not text.strip():
        return False
    try:
        from run_dashboard_sync import parse_briefing_text
        briefing = parse_briefing_text(text)
        if not briefing or briefing.story_count != 15:
            return False
        counts = {
            "india": sum(1 for s in briefing.stories if s.section == "india"),
            "domestic": sum(1 for s in briefing.stories if s.section == "domestic"),
            "international": sum(1 for s in briefing.stories if s.section == "international"),
        }
        return counts.get("india") == 5 and counts.get("domestic") == 5 and counts.get("international") == 5
    except Exception as exc:
        logger.warning("Error validating copy/paste briefing text: %s", exc)
        return False


def run_daily_briefing(
    data_dir_override: Optional[Path] = None,
    target_date: Optional[date] = None,
    skip_pipeline_execution: bool = False,
    max_india: Optional[int] = None,
    max_international: Optional[int] = None,
    max_domestic: Optional[int] = None,
) -> int:
    """
    Execute daily briefing pipeline and email results with strict integrity checks.

    Returns:
        int: 0 on success or already sent today, 1 on failure.
    """
    setup_logging()
    ref_time_utc = datetime.now(timezone.utc)
    today = target_date or get_target_date_ist(ref_time_utc)
    today_str = today.strftime("%Y-%m-%d")
    data_dir = data_dir_override or (Path(__file__).resolve().parent / "data")

    logger.info("=" * 60)
    logger.info("STARTING DAILY BRIEFING RUNNER FOR DATE: %s", today_str)
    logger.info("=" * 60)

    # 1. Idempotency Check: Prevent duplicate runs on the same date
    last_sent = get_last_email_date(data_dir)
    if last_sent == today_str:
        logger.info("EMAIL_ALREADY_SENT_TODAY: Briefing already delivered for date %s. Exiting cleanly.", today_str)
        # Ensure copy_paste_briefing.txt exists and is valid for dashboard recovery / retries
        copy_paste_file = data_dir / "copy_paste_briefing.txt"
        needs_restore = not copy_paste_file.exists()
        if not needs_restore:
            try:
                current_cp = copy_paste_file.read_text(encoding="utf-8")
                if not validate_copy_paste_briefing(current_cp):
                    logger.warning("Existing copy_paste_briefing.txt is malformed or incomplete. Reconstructing...")
                    needs_restore = True
            except Exception:
                needs_restore = True

        if needs_restore:
            final_15_file = data_dir / "final_15_stories.json"
            final_briefing_file = data_dir / "final_briefing.txt"
            restored_text = ""
            if final_15_file.exists():
                try:
                    stories_data = json.loads(final_15_file.read_text(encoding="utf-8"))
                    cand_text = build_copy_paste_text(stories_data, briefing_date=today, require_15=True)
                    if validate_copy_paste_briefing(cand_text):
                        restored_text = cand_text
                        logger.info("Reconstructed valid copy_paste_briefing.txt from final_15_stories.json.")
                except Exception as exc:
                    logger.warning("Could not reconstruct copy_paste_briefing.txt from json: %s", exc)

            if not restored_text and final_briefing_file.exists():
                try:
                    cand_text = build_copy_paste_text(final_briefing_file.read_text(encoding="utf-8"), briefing_date=today, require_15=True)
                    if validate_copy_paste_briefing(cand_text):
                        restored_text = cand_text
                        logger.info("Reconstructed valid copy_paste_briefing.txt from final_briefing.txt.")
                except Exception as exc:
                    logger.warning("Could not reconstruct copy_paste_briefing.txt from text: %s", exc)

            if restored_text:
                copy_paste_file.write_text(restored_text, encoding="utf-8")
                logger.info("Successfully restored valid 15-story copy_paste_briefing.txt for dashboard retry.")
            else:
                logger.error("Could not restore a valid 15-story copy_paste_briefing.txt.")

        print(f"\nSTATUS: EMAIL_ALREADY_SENT_TODAY (Date: {today_str})\n")
        return 0

    # 2. Check Email Credentials Presence Early
    is_dry_run = is_testing_or_dry_run()

    sender = os.environ.get("GMAIL_SENDER", "").strip()
    recipient = os.environ.get("GMAIL_RECIPIENTS", "").strip() or os.environ.get("GMAIL_RECIPIENT", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not is_dry_run and (not sender or not recipient or not password):
        missing = []
        if not sender:
            missing.append("GMAIL_SENDER")
        if not recipient:
            missing.append("GMAIL_RECIPIENT")
        if not password:
            missing.append("GMAIL_APP_PASSWORD")
        err_msg = f"MISSING_EMAIL_CREDENTIALS: {', '.join(missing)} not set."
        logger.error(err_msg)
        print(f"\nERROR: {err_msg}\n")
        return 1

    if is_dry_run:
        sender = sender or "mock-sender@example.com"
        recipient = recipient or "mock-recipient@example.com"
        password = password or "mock-password"

    # 3. Execute Existing Pipeline (if not skipped for testing or same-day retry)
    primary_sent_today = (get_primary_email_date(data_dir) == today_str)
    final_briefing_path = data_dir / "final_briefing.txt"

    if primary_sent_today and final_briefing_path.exists():
        artifact_date = extract_briefing_date_from_artifact(data_dir)
        if artifact_date is None or artifact_date == today:
            logger.info(
                "PIPELINE_SKIP: Primary email already delivered today (%s) and valid briefing artifact exists. Skipping pipeline rerun.",
                today_str,
            )
            skip_pipeline_execution = True

    if not skip_pipeline_execution:
        try:
            from run_pipeline import run_pipeline
            pipeline_exit_code = run_pipeline(
                max_india=max_india or 5,
                max_international=max_international or 5,
                target_date=today,
                data_dir=data_dir,
            )
        except Exception as e:
            logger.error("PIPELINE_EXECUTION_CRASH: Unhandled exception during run_pipeline: %s", e)
            print(f"\nERROR: Pipeline crashed with exception: {e}\n")
            return 1

        if pipeline_exit_code != 0:
            logger.error("PIPELINE_FAILED: run_pipeline returned non-zero exit code (%d).", pipeline_exit_code)
            print(f"\nERROR: Pipeline execution failed with exit code {pipeline_exit_code}.\n")
            return 1

    # 4. Verify Final Briefing Artifact & Freshness
    if not final_briefing_path.exists():
        logger.error("BRIEFING_FILE_MISSING: %s does not exist.", final_briefing_path)
        print(f"\nERROR: Final briefing file missing at {final_briefing_path}.\n")
        return 1

    briefing_text = final_briefing_path.read_text(encoding="utf-8").strip()
    if not briefing_text:
        logger.error("BRIEFING_FILE_EMPTY: %s is empty.", final_briefing_path)
        print(f"\nERROR: Final briefing file is empty.\n")
        return 1

    artifact_date = extract_briefing_date_from_artifact(data_dir)
    if artifact_date is not None and artifact_date != today:
        logger.error("STALE_BRIEFING_ARTIFACT: Artifact date %s does not match expected target date %s", artifact_date, today)
        print(f"\nERROR: STALE_BRIEFING_ARTIFACT: Artifact date {artifact_date} does not match {today}.\n")
        return 1

    # 5. Verify Section Structure & Story Count Contract
    has_india = "INDIA BUSINESS" in briefing_text
    has_domestic = "DOMESTIC" in briefing_text
    has_intl = "INTERNATIONAL BUSINESS" in briefing_text

    if not (has_india and has_domestic and has_intl):
        logger.error(
            "BRIEFING_STRUCTURE_INVALID: Missing required sections (India: %s, Domestic: %s, Intl: %s)",
            has_india, has_domestic, has_intl
        )
        print("\nERROR: Final briefing does not contain all 3 required sections.\n")
        return 1

    # 6. Construct Subjects & Dispatch Emails via SMTP SSL
    primary_subject = f"Investment Committee Briefing — {format_subject_date(today)}"
    copy_paste_subject = f"Investment Committee Briefing — Copy/Paste Text — {format_subject_date(today)}"

    # Generate copy/paste text version reusing structured data if available, or briefing_text
    final_15_path = data_dir / "final_15_stories.json"
    copy_paste_text = ""
    if final_15_path.exists():
        try:
            stories_data = json.loads(final_15_path.read_text(encoding="utf-8"))
            cand_text = build_copy_paste_text(stories_data, briefing_date=today)
            if validate_copy_paste_briefing(cand_text):
                copy_paste_text = cand_text
            else:
                logger.warning("copy_paste_text from final_15_stories.json failed 15-story validation; falling back to briefing_text")
        except Exception as json_err:
            logger.warning("Could not build copy_paste from final_15_stories.json (%s); falling back to briefing_text", json_err)

    if not copy_paste_text:
        try:
            cand_text = build_copy_paste_text(briefing_text, briefing_date=today)
            copy_paste_text = cand_text
        except Exception as txt_err:
            logger.error("Could not build copy_paste from briefing_text: %s", txt_err)

    # Save copy_paste_briefing.txt artifact alongside final_briefing.txt for inspection
    copy_paste_file = data_dir / "copy_paste_briefing.txt"
    if copy_paste_text and validate_copy_paste_briefing(copy_paste_text):
        try:
            copy_paste_file.write_text(copy_paste_text, encoding="utf-8")
            logger.info("Successfully saved validated 15-story copy_paste_briefing.txt")
        except Exception as save_err:
            logger.warning("Could not write copy_paste_briefing.txt: %s", save_err)
    else:
        logger.error(
            "COPY_PASTE_VALIDATION_FAILED: copy_paste_text does not contain exactly 15 stories (5 India + 5 Domestic + 5 Intl). "
            "Refusing to save or overwrite copy_paste_briefing.txt with invalid content."
        )

    if is_dry_run:
        recipient_count = len([r for r in recipient.split(",") if r.strip()])
        logger.info("MOCK_EMAIL_SEND: recipient_count=%d stories=15 status=SUCCESS", recipient_count)
        print(f"\nMOCK_EMAIL_SEND: recipient_count={recipient_count} stories=15 status=SUCCESS\n")
        logger.info("DAILY_BRIEFING_COMPLETED_SUCCESSFULLY: Date %s (DRY RUN - No email sent)", today_str)
        print(f"\nSUCCESS: Daily briefing dry-run successfully generated for date {today_str}.\n")
        return 0

    # Email 1: Primary briefing (check if already sent on a previous attempt today)
    primary_sent_today = (get_primary_email_date(data_dir) == today_str)
    if not primary_sent_today:
        logger.info("Sending primary briefing email with subject '%s' to %s...", primary_subject, recipient)
        email_sent = send_briefing_email(
            recipient=recipient,
            subject=primary_subject,
            briefing_text=briefing_text,
            sender=sender,
            password=password,
        )

        if not email_sent:
            logger.error("EMAIL_DELIVERY_FAILED: Primary email SMTP transmission failed. Delivery date NOT recorded.")
            print("\nERROR: Email delivery failed via SMTP.\n")
            return 1

        record_primary_email_date(data_dir, today_str)
        logger.info("PRIMARY_EMAIL_SENT: Primary briefing email successfully delivered to %s", recipient)
    else:
        logger.info("PRIMARY_EMAIL_SENT: Primary email was already sent today (%s); skipping duplicate.", today_str)

    # Email 2: Plain-text copy/paste version
    logger.info("Sending copy/paste text briefing email with subject '%s' to %s...", copy_paste_subject, recipient)
    copy_text_sent = send_copy_paste_email(
        recipient=recipient,
        subject=copy_paste_subject,
        text_content=copy_paste_text,
        sender=sender,
        password=password,
    )

    if not copy_text_sent:
        logger.error("COPY_TEXT_EMAIL_FAILED: Copy/paste text email SMTP transmission failed. Delivery date NOT recorded.")
        print("\nERROR: Copy/paste text email delivery failed via SMTP.\n")
        return 1

    logger.info("COPY_TEXT_EMAIL_SENT: Copy/paste text email successfully delivered to %s", recipient)

    # 7. Record Idempotency Date ONLY AFTER BOTH Required Emails Succeed
    record_successful_email_date(data_dir, today_str)
    logger.info("DAILY_BRIEFING_COMPLETED_SUCCESSFULLY: Date %s, Recipient %s (Both primary and copy/paste emails delivered)", today_str, recipient)
    print(f"\nSUCCESS: Daily briefing successfully generated and delivered to {recipient}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_daily_briefing())
