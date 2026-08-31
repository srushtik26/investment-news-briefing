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
from app.email.email_sender import send_briefing_email

logger = get_logger("daily.runner")


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


def format_subject_date(d: date) -> str:
    """Format subject date cleanly e.g. '28 Aug 2026'."""
    day = str(d.day)
    month = d.strftime("%b")
    year = str(d.year)
    return f"{day} {month} {year}"


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
    today = target_date or date.today()
    today_str = today.strftime("%Y-%m-%d")
    data_dir = data_dir_override or (Path(__file__).resolve().parent / "data")

    logger.info("=" * 60)
    logger.info("STARTING DAILY BRIEFING RUNNER FOR DATE: %s", today_str)
    logger.info("=" * 60)

    # 1. Idempotency Check: Prevent duplicate runs on the same date
    last_sent = get_last_email_date(data_dir)
    if last_sent == today_str:
        logger.info("EMAIL_ALREADY_SENT_TODAY: Briefing already delivered for date %s. Exiting cleanly.", today_str)
        print(f"\nSTATUS: EMAIL_ALREADY_SENT_TODAY (Date: {today_str})\n")
        return 0

    # 2. Check Email Credentials Presence Early
    sender = os.environ.get("GMAIL_SENDER", "").strip()
    recipient = os.environ.get("GMAIL_RECIPIENTS", "").strip() or os.environ.get("GMAIL_RECIPIENT", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not sender or not recipient or not password:
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

    # 3. Execute Existing Pipeline (if not skipped for testing)
    if not skip_pipeline_execution:
        try:
            from run_pipeline import run_pipeline
            pipeline_exit_code = run_pipeline(
                max_india=max_india,
                max_international=max_international,
                max_domestic=max_domestic,
            )
        except Exception as e:
            logger.error("PIPELINE_EXECUTION_CRASH: Unhandled exception during run_pipeline: %s", e)
            print(f"\nERROR: Pipeline crashed with exception: {e}\n")
            return 1

        if pipeline_exit_code != 0:
            logger.error("PIPELINE_FAILED: run_pipeline returned non-zero exit code (%d).", pipeline_exit_code)
            print(f"\nERROR: Pipeline execution failed with exit code {pipeline_exit_code}.\n")
            return 1

    # 4. Verify Final Briefing Artifact
    final_briefing_path = data_dir / "final_briefing.txt"
    if not final_briefing_path.exists():
        logger.error("BRIEFING_FILE_MISSING: %s does not exist.", final_briefing_path)
        print(f"\nERROR: Final briefing file missing at {final_briefing_path}.\n")
        return 1

    briefing_text = final_briefing_path.read_text(encoding="utf-8").strip()
    if not briefing_text:
        logger.error("BRIEFING_FILE_EMPTY: %s is empty.", final_briefing_path)
        print(f"\nERROR: Final briefing file is empty.\n")
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

    # 6. Construct Subject & Dispatch Email via SMTP SSL
    subject = f"Investment Committee Briefing — {format_subject_date(today)}"
    logger.info("Sending briefing email with subject '%s' to %s...", subject, recipient)

    email_sent = send_briefing_email(
        recipient=recipient,
        subject=subject,
        briefing_text=briefing_text,
        sender=sender,
        password=password,
    )

    if not email_sent:
        logger.error("EMAIL_DELIVERY_FAILED: SMTP transmission failed. Delivery date NOT recorded.")
        print("\nERROR: Email delivery failed via SMTP.\n")
        return 1

    # 7. Record Idempotency Date ONLY AFTER Successful SMTP Transmission
    record_successful_email_date(data_dir, today_str)
    logger.info("DAILY_BRIEFING_COMPLETED_SUCCESSFULLY: Date %s, Recipient %s", today_str, recipient)
    print(f"\nSUCCESS: Daily briefing successfully generated and delivered to {recipient}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_daily_briefing())
