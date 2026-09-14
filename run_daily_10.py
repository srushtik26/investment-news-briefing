"""Daily GitHub Actions runner for the canonical 5 India + 5 International briefing.

The core pipeline may still discover/rank Domestic candidates internally, but this
runtime adapter keeps the public output contract at exactly 10 stories without
weakening India/International story-level validation.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.ai.models import BriefingEditorialPayload
from app.email.email_sender import send_briefing_email, send_copy_paste_email
from app.formatting.formatter import BriefingFormatter as BaseBriefingFormatter, FormattedBriefing
from app.logging_config import get_logger, setup_logging
from app.validation.engine import FinalValidationEngine as BaseFinalValidationEngine
from app.validation.models import BriefingValidationReport, ValidationStatus
from run_daily import (
    format_subject_date,
    get_last_email_date,
    get_primary_email_date,
    record_primary_email_date,
    record_successful_email_date,
)

logger = get_logger("daily.runner10")


class TenStoryBriefingFormatter(BaseBriefingFormatter):
    """Render only the canonical India and International sections."""

    def format(
        self,
        payload: BriefingEditorialPayload,
        briefing_date: Optional[date] = None,
        shorten_urls: Optional[bool] = None,
    ) -> FormattedBriefing:
        india_stories = list(payload.india_stories)
        intl_stories = list(payload.international_stories)

        if len(india_stories) != 5:
            raise ValueError(f"Formatter requires exactly 5 India stories; received {len(india_stories)}.")
        if len(intl_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 International stories; received {len(intl_stories)}."
            )

        if briefing_date is None:
            briefing_date = datetime.now(tz=timezone.utc).date()
        if shorten_urls is None:
            shorten_urls = False

        lines = [
            "*INVESTMENT COMMITTEE BRIEFING*",
            f"*{self._format_date(briefing_date)}*",
            "",
            "*TOP 5 INDIA BUSINESS HEADLINES*",
            "",
        ]
        for story in india_stories:
            lines.extend(self._render_story(story, shorten_urls=shorten_urls))

        lines.extend(["*TOP 5 INTERNATIONAL BUSINESS HEADLINES*", ""])
        for story in intl_stories:
            lines.extend(self._render_story(story, shorten_urls=shorten_urls))

        text = "\n".join(line.rstrip() for line in lines).rstrip()
        return FormattedBriefing(
            text=text,
            briefing_date=briefing_date,
            india_count=5,
            international_count=5,
            domestic_count=0,
        )


class TenStoryValidationEngine(BaseFinalValidationEngine):
    """Reuse existing story-level checks while removing the obsolete Domestic gate."""

    def validate_briefing(self, payload, *args, **kwargs) -> BriefingValidationReport:
        if len(payload.india_stories) != 5 or len(payload.international_stories) != 5:
            reason = (
                "Expected exactly 5 India and 5 International stories; "
                f"found India={len(payload.india_stories)}, "
                f"International={len(payload.international_stories)}"
            )
            return BriefingValidationReport(
                status=ValidationStatus.FAILED,
                is_valid=False,
                passed_checks=0,
                failed_checks=1,
                failure_reason=reason,
                failed_check_id=1,
                check_results=[],
            )

        ten_payload = payload.model_copy(deep=True)
        ten_payload.domestic_stories = []
        kwargs["strict_5_per_section"] = False
        report = super().validate_briefing(ten_payload, *args, **kwargs)

        # In non-strict mode the legacy validator's only unavoidable failure for
        # a 10-story payload is its obsolete Domestic-section presence check.
        meaningful_failures = [
            r for r in report.check_results
            if (not r.passed)
            and not (
                r.check_id == 1
                and "domestic" in ((r.check_name or "") + " " + (r.failure_reason or "")).lower()
            )
        ]

        if meaningful_failures:
            first = meaningful_failures[0]
            return BriefingValidationReport(
                status=ValidationStatus.FAILED,
                is_valid=False,
                passed_checks=max(0, 20 - len(meaningful_failures)),
                failed_checks=len(meaningful_failures),
                failure_reason=first.failure_reason,
                failed_check_id=first.check_id,
                check_results=report.check_results,
            )

        return BriefingValidationReport(
            status=ValidationStatus.PASSED,
            is_valid=True,
            passed_checks=20,
            failed_checks=0,
            check_results=[r for r in report.check_results if r.passed],
        )


def patch_pipeline_for_ten_story_contract() -> None:
    """Patch only final sufficiency/validation/formatting behavior."""
    import app.pipeline.runner as pipeline_runner

    original_ranking = pipeline_runner.run_ranking_and_selection

    def run_ranking_and_selection_10(ctx, accepted_stories, event_by_id):
        candidate_pool, domestic_pool, india_pool, intl_pool, _legacy_sufficient, status = original_ranking(
            ctx, accepted_stories, event_by_id
        )
        sufficient = len(india_pool) >= 5 and len(intl_pool) >= 5
        ctx.log_exec(
            f"[10_STORY_SUFFICIENCY] India={len(india_pool)}/5, "
            f"International={len(intl_pool)}/5, sufficient={sufficient}"
        )
        return candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, status

    pipeline_runner.run_ranking_and_selection = run_ranking_and_selection_10
    pipeline_runner.FinalValidationEngine = TenStoryValidationEngine
    pipeline_runner.BriefingFormatter = TenStoryBriefingFormatter


def run_daily_ten_story() -> int:
    setup_logging()
    patch_pipeline_for_ten_story_contract()

    # Import after patching so the compatibility entry point resolves to the
    # patched runner module.
    from run_pipeline import run_pipeline

    today = date.today()
    today_str = today.isoformat()
    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if get_last_email_date(data_dir) == today_str:
        print(f"STATUS: EMAIL_ALREADY_SENT_TODAY (Date: {today_str})")
        return 0

    sender = os.environ.get("GMAIL_SENDER", "").strip()
    recipient = os.environ.get("GMAIL_RECIPIENTS", "").strip() or os.environ.get("GMAIL_RECIPIENT", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    missing = [
        name for name, value in (
            ("GMAIL_SENDER", sender),
            ("GMAIL_RECIPIENT", recipient),
            ("GMAIL_APP_PASSWORD", password),
        ) if not value
    ]
    if missing:
        print(f"ERROR: MISSING_EMAIL_CREDENTIALS: {', '.join(missing)}")
        return 1

    try:
        pipeline_exit_code = run_pipeline()
    except Exception as exc:
        logger.exception("Pipeline crashed")
        print(f"ERROR: Pipeline crashed with exception: {exc}")
        return 1

    if pipeline_exit_code != 0:
        print(f"ERROR: Pipeline execution failed with exit code {pipeline_exit_code}.")
        return 1

    final_path = data_dir / "final_briefing.txt"
    if not final_path.exists():
        print(f"ERROR: Final briefing file missing at {final_path}.")
        return 1

    briefing_text = final_path.read_text(encoding="utf-8").strip()
    if not briefing_text:
        print("ERROR: Final briefing file is empty.")
        return 1

    required_headers = (
        "TOP 5 INDIA BUSINESS HEADLINES",
        "TOP 5 INTERNATIONAL BUSINESS HEADLINES",
    )
    if not all(header in briefing_text for header in required_headers):
        print("ERROR: Final briefing is missing a required India/International section.")
        return 1
    if "TOP 5 DOMESTIC HEADLINES" in briefing_text:
        print("ERROR: Final briefing unexpectedly contains the obsolete Domestic section.")
        return 1

    # The copy/paste artifact must be character-for-character the same public
    # briefing contract, not a separately reformatted 15-story payload.
    copy_paste_text = briefing_text
    (data_dir / "copy_paste_briefing.txt").write_text(copy_paste_text, encoding="utf-8")

    primary_subject = f"Investment Committee Briefing — {format_subject_date(today)}"
    copy_subject = f"Investment Committee Briefing — Copy/Paste Text — {format_subject_date(today)}"

    if get_primary_email_date(data_dir) != today_str:
        if not send_briefing_email(
            recipient=recipient,
            subject=primary_subject,
            briefing_text=briefing_text,
            sender=sender,
            password=password,
        ):
            print("ERROR: Primary email delivery failed via SMTP.")
            return 1
        record_primary_email_date(data_dir, today_str)

    if not send_copy_paste_email(
        recipient=recipient,
        subject=copy_subject,
        text_content=copy_paste_text,
        sender=sender,
        password=password,
    ):
        print("ERROR: Copy/paste email delivery failed via SMTP.")
        return 1

    record_successful_email_date(data_dir, today_str)
    print(f"SUCCESS: 10-story daily briefing generated and delivered to {recipient}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_daily_ten_story())
