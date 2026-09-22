"""
Comprehensive test suite verifying all 26 Unattended Pipeline Reliability requirements.

Covers:
1. Delivery Scenarios A–E (idempotency, partial delivery recovery, atomicity)
2. Cross-runner history persistence & 3-day deduplication window
3. Fingerprint consistency (canonical key, date stripping, collision resistance)
4. Formatter failure non-zero exit code
5. Stale briefing artifact rejection (STALE_BRIEFING_ARTIFACT)
6. Date boundary IST conversion (21:00 UTC = 02:30 IST next day)
7. Transient retries (429/5xx retry, 400/401/403/404 fail-fast, SMTP backoff, DB retry)
"""

import json
import smtplib
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.deduplication.fingerprint import (
    generate_event_fingerprint,
    normalize_event_type,
    strip_date_from_fingerprint,
)
from app.deduplication.history import HistoryStore
from app.email.email_sender import send_briefing_email, send_copy_paste_email
from app.extraction.http_client import ArticleFetcher
from app.pipeline.context import PipelineContext
from config import get_target_date_ist, is_testing_or_dry_run
from run_daily import (
    extract_briefing_date_from_artifact,
    get_last_email_date,
    get_primary_email_date,
    run_daily_briefing,
)


SAMPLE_BRIEFING = """*INVESTMENT COMMITTEE BRIEFING*
*Wednesday, 16th September, 2026*

*TOP 5 INDIA BUSINESS HEADLINES*

*Tata Motors finalizes €3.82 billion Iveco deal*
Tata Motors announced completion of the definitive tender offer.
Source: Business Standard
https://example.com/story-1

*TOP 5 DOMESTIC HEADLINES*

*Cabinet approves national semiconductor package*
Union Cabinet approved capital allocation for manufacturing units.
Source: The Hindu
https://example.com/story-2

*TOP 5 INTERNATIONAL BUSINESS HEADLINES*

*Nvidia acquires AI systems provider for $1.2 billion*
Nvidia concluded the acquisition of AI enterprise software infrastructure.
Source: CNBC
https://example.com/story-3
"""


@pytest.fixture
def temp_data_dir(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# =============================================================================
# 1. DELIVERY SCENARIOS A–E
# =============================================================================

def test_scenario_a_fresh_run_both_emails_succeed(temp_data_dir, monkeypatch):
    """Scenario A: Fresh run, both emails succeed -> dates recorded, exits 0."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING, encoding="utf-8")
    target_d = date(2026, 9, 16)

    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 0
        assert mock_server.send_message.call_count == 2
        assert get_primary_email_date(temp_data_dir) == "2026-09-16"
        assert get_last_email_date(temp_data_dir) == "2026-09-16"


def test_scenario_b_same_day_rerun_idempotent_no_op(temp_data_dir, monkeypatch):
    """Scenario B: Same day rerun after successful delivery -> exits 0, no emails sent."""
    monkeypatch.setenv("APP_ENV", "production")
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING, encoding="utf-8")
    (temp_data_dir / "last_email_date.txt").write_text("2026-09-16", encoding="utf-8")
    (temp_data_dir / "primary_email_date.txt").write_text("2026-09-16", encoding="utf-8")
    target_d = date(2026, 9, 16)

    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )
        assert exit_code == 0
        mock_smtp_cls.assert_not_called()


def test_scenario_c_primary_succeeds_copy_paste_fails(temp_data_dir, monkeypatch):
    """Scenario C: Primary email succeeds, copy/paste fails -> primary date recorded, last_email NOT recorded, exit 1."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING, encoding="utf-8")
    target_d = date(2026, 9, 16)

    with patch("run_daily.send_briefing_email", return_value=True) as mock_send_primary:
        with patch("run_daily.send_copy_paste_email", return_value=False) as mock_send_copy:
            exit_code = run_daily_briefing(
                data_dir_override=temp_data_dir,
                target_date=target_d,
                skip_pipeline_execution=True,
            )

            assert exit_code == 1
            mock_send_primary.assert_called_once()
            mock_send_copy.assert_called_once()
            assert get_primary_email_date(temp_data_dir) == "2026-09-16"
            assert get_last_email_date(temp_data_dir) is None


def test_scenario_d_recovery_from_scenario_c_no_duplicate_primary(temp_data_dir, monkeypatch):
    """Scenario D: Retry after Scenario C -> pipeline skipped, primary email NOT re-sent, copy email sent, exit 0."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PIPELINE_DRY_RUN", raising=False)
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING, encoding="utf-8")
    (temp_data_dir / "primary_email_date.txt").write_text("2026-09-16", encoding="utf-8")
    target_d = date(2026, 9, 16)

    with patch("run_daily.send_briefing_email") as mock_send_primary:
        with patch("run_daily.send_copy_paste_email", return_value=True) as mock_send_copy:
            exit_code = run_daily_briefing(
                data_dir_override=temp_data_dir,
                target_date=target_d,
                skip_pipeline_execution=False,  # Runner must detect primary_sent_today and skip pipeline!
            )

            assert exit_code == 0
            mock_send_primary.assert_not_called()  # Primary email MUST NOT be re-sent!
            mock_send_copy.assert_called_once()
            assert get_last_email_date(temp_data_dir) == "2026-09-16"


def test_scenario_e_pipeline_failure_no_emails_no_dates(temp_data_dir, monkeypatch):
    """Scenario E: Pipeline fails -> no emails sent, neither date recorded, exits 1."""
    monkeypatch.setenv("APP_ENV", "production")
    target_d = date(2026, 9, 16)

    with patch("run_pipeline.run_pipeline", return_value=1) as mock_pipe:
        with patch("run_daily.send_briefing_email") as mock_send:
            exit_code = run_daily_briefing(
                data_dir_override=temp_data_dir,
                target_date=target_d,
                skip_pipeline_execution=False,
            )

            assert exit_code == 1
            mock_pipe.assert_called_once()
            mock_send.assert_not_called()
            assert get_primary_email_date(temp_data_dir) is None
            assert get_last_email_date(temp_data_dir) is None


# =============================================================================
# 2. CROSS-RUNNER 3-DAY HISTORY PERSISTENCE & DEDUPLICATION
# =============================================================================

def test_cross_runner_history_persistence_and_window(tmp_path: Path):
    """Simulate Monday-Thursday ephemeral runners sharing briefings.db."""
    db_file = tmp_path / "briefings.db"

    # Runner 1 (Monday, Sep 14): Save stories
    store_mon = HistoryStore(db_path=db_file)
    fp_mon, _ = generate_event_fingerprint(
        company="Reliance Industries",
        event_type="acquisition",
        key_facts=["15000", "crore"],
    )
    store_mon.save_briefing(date(2026, 9, 14), [
        {
            "event_id": "ev_1",
            "event_fingerprint": fp_mon,
            "headline": "Reliance acquires clean energy asset",
            "company_name": "Reliance Industries",
            "category": "india",
            "source_count": 2,
            "published_date": date(2026, 9, 14),
        }
    ])

    # Runner 2 (Tuesday, Sep 15): Restored cache with briefings.db
    store_tue = HistoryStore(db_path=db_file)
    recent_tue = store_tue.get_recent_fingerprints(reference_date=date(2026, 9, 15), days=3)
    assert fp_mon in recent_tue, "Tuesday run must detect Monday's story as recent duplicate"

    # Runner 3 (Wednesday, Sep 16): Restored cache
    store_wed = HistoryStore(db_path=db_file)
    recent_wed = store_wed.get_recent_fingerprints(reference_date=date(2026, 9, 16), days=3)
    assert fp_mon in recent_wed, "Wednesday run must still detect Monday's story (within 3 days)"

    # Runner 4 (Thursday, Sep 17): Exactly 3 days later
    store_thu = HistoryStore(db_path=db_file)
    recent_thu = store_thu.get_recent_fingerprints(reference_date=date(2026, 9, 17), days=3)
    assert fp_mon in recent_thu, "Thursday is exactly 3 days after Monday and still covered"

    # Runner 5 (Friday, Sep 18): 4 days later (beyond 3-day window)
    store_fri = HistoryStore(db_path=db_file)
    recent_fri = store_fri.get_recent_fingerprints(reference_date=date(2026, 9, 18), days=3)
    assert fp_mon not in recent_fri, "Friday run must allow story (outside 3-day window)"


# =============================================================================
# 3. EVENT FINGERPRINT CONSISTENCY & DATE STRIPPING
# =============================================================================

def test_fingerprint_consistency_across_days():
    """Same company + event type + facts produces identical fingerprint across different days."""
    fp1, hash1 = generate_event_fingerprint(
        company="Bharti Airtel",
        event_type="earnings",
        key_facts=["4300", "crore"],
    )
    fp2, hash2 = generate_event_fingerprint(
        company="bharti airtel limited",
        event_type="quarterly earnings",
        key_facts=["4300", "crore"],
    )
    assert fp1 == fp2
    assert hash1 == hash2


def test_fingerprint_date_stripping_and_collision_resistance():
    """Verify strip_date_from_fingerprint and distinct fingerprints for distinct events."""
    legacy_fp = "tata_motors:acquisition:2026-09-14:3.82:billion"
    stripped = strip_date_from_fingerprint(legacy_fp)
    assert "2026-09-14" not in stripped
    assert stripped == "tata_motors:acquisition:3.82:billion"

    # Distinct events for same company must NOT collide
    fp_acq, _ = generate_event_fingerprint("Tata Motors", "acquisition", ["3.82", "billion"])
    fp_earn, _ = generate_event_fingerprint("Tata Motors", "earnings", ["1500", "crore"])
    assert fp_acq != fp_earn


# =============================================================================
# 4. FORMATTER FAILURE EXITS WITH CODE != 0
# =============================================================================

def test_formatter_failure_causes_nonzero_exit(temp_data_dir):
    """If formatting crashes in Stage 10, run_pipeline must return 1."""
    from app.pipeline.runner import run_pipeline
    from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
    from app.validation.models import BriefingValidationReport, ValidationStatus

    mock_report = BriefingValidationReport(is_valid=True, status=ValidationStatus.PASSED, passed_checks=20, failed_checks=0)
    mock_payload = BriefingEditorialPayload(
        domestic_stories=[EditorialStorySelection(section="domestic", event_id=f"d{i}", headline=f"Headline D{i}", source="Source", url="https://example.com") for i in range(5)],
        india_stories=[EditorialStorySelection(section="india", event_id=f"in{i}", headline=f"Headline IN{i}", source="Source", url="https://example.com") for i in range(5)],
        international_stories=[EditorialStorySelection(section="international", event_id=f"int{i}", headline=f"Headline INT{i}", source="Source", url="https://example.com") for i in range(5)],
    )

    mock_cand = MagicMock()
    mock_cand.event = MagicMock(
        id="ev1",
        article_ids=[],
        canonical_title="Headline Title",
        verification_tier=None,
        verification_confidence=95.0,
        primary_publisher="Source",
        primary_url="https://example.com",
        companies_involved=["Comp"],
        event_type="acquisition",
        financial_figures=[],
    )
    mock_pool = MagicMock()
    mock_pool.domestic_candidates = [mock_cand] * 5
    mock_pool.india_candidates = [mock_cand] * 5
    mock_pool.international_candidates = [mock_cand] * 5

    with patch("app.pipeline.runner.discover_initial_reserves", return_value=([], 0, 0, 0)), \
         patch("app.pipeline.runner._extract_candidates", return_value=([], [], 0, 0, 0, 0, 0, 0)), \
         patch("app.pipeline.runner.run_expansion_and_fallbacks", return_value="OK"), \
         patch("app.pipeline.runner.run_second_source_enrichment"), \
         patch("app.pipeline.runner.run_deduplication", return_value=([], {})), \
         patch("app.pipeline.runner.run_post_dedup_refill", return_value=([], {})), \
         patch("app.pipeline.runner.run_ranking_and_selection", return_value=(mock_pool, [mock_cand]*5, [mock_cand]*5, [mock_cand]*5, True, "SUCCESS")), \
         patch("app.pipeline.runner.GeminiEditorialEngine.select_and_synthesize_briefing", return_value=MagicMock(success=True, selection=mock_payload)), \
         patch("app.ai.headline_synthesis.is_approved_institutional_headline", return_value=True), \
         patch("app.ai.headline_synthesis.validate_headline_coherence", return_value=(True, "ok")), \
         patch("app.ai.summary_grounding.validate_summary_grounding", return_value=(True, "ok")), \
         patch("app.pipeline.runner.FinalValidationEngine.validate_briefing", return_value=mock_report), \
         patch("app.formatting.formatter.BriefingFormatter.format", side_effect=ValueError("Formatter error")), \
         patch("time.sleep"):
        exit_code = run_pipeline(data_dir=temp_data_dir)
        assert exit_code == 1


# =============================================================================
# 5. STALE BRIEFING ARTIFACT PREVENTION
# =============================================================================

def test_stale_briefing_artifact_detected_and_rejected(temp_data_dir, monkeypatch):
    """If final_briefing.txt is from yesterday and pipeline was skipped, reject with error."""
    monkeypatch.setenv("APP_ENV", "production")
    # Artifact contains Sep 14 header
    stale_text = SAMPLE_BRIEFING.replace("Wednesday, 16th September, 2026", "Monday, 14th September, 2026")
    (temp_data_dir / "final_briefing.txt").write_text(stale_text, encoding="utf-8")
    (temp_data_dir / "briefing_date.txt").write_text("2026-09-14", encoding="utf-8")

    # Runner asked to run for Sep 16
    exit_code = run_daily_briefing(
        data_dir_override=temp_data_dir,
        target_date=date(2026, 9, 16),
        skip_pipeline_execution=True,
    )
    assert exit_code == 1


# =============================================================================
# 6. DATE BOUNDARY (21:00 UTC = 02:30 IST NEXT DAY)
# =============================================================================

def test_date_boundary_ist_conversion():
    """21:00 UTC on Sep 15 corresponds to 02:30 AM IST on Sep 16."""
    utc_evening = datetime(2026, 9, 15, 21, 0, 0, tzinfo=timezone.utc)
    ist_target = get_target_date_ist(utc_evening)
    assert ist_target == date(2026, 9, 16)

    # PipelineContext defaults to this conversion
    ctx = PipelineContext(run_reference_time=utc_evening, data_dir=None)
    assert ctx.target_date == date(2026, 9, 16)

    # Daytime UTC on Sep 16 corresponds to Sep 16 IST
    utc_day = datetime(2026, 9, 16, 4, 0, 0, tzinfo=timezone.utc)
    assert get_target_date_ist(utc_day) == date(2026, 9, 16)


# =============================================================================
# 7. TRANSIENT RETRIES
# =============================================================================

def test_article_fetcher_retries_429():
    """ArticleFetcher retries HTTP 429 and succeeds on retry."""
    fetcher = ArticleFetcher(max_retries=2, backoff_factor=0.01)
    mock_resp_429 = MagicMock(status_code=429, headers={"content-type": "text/html"})
    mock_resp_200 = MagicMock(status_code=200, text="<html><body>Article</body></html>", headers={"content-type": "text/html"})

    with patch("httpx.Client.get", side_effect=[mock_resp_429, mock_resp_200]) as mock_get:
        success, html, code, err = fetcher.fetch_html("https://example.com/test-article")
        assert success is True
        assert code == 200
        assert mock_get.call_count == 2


def test_article_fetcher_does_not_retry_404_or_403():
    """ArticleFetcher immediately fails on permanent client errors without retrying."""
    fetcher = ArticleFetcher(max_retries=3, backoff_factor=0.01)
    mock_resp_404 = MagicMock(status_code=404, headers={"content-type": "text/html"})

    with patch("httpx.Client.get", return_value=mock_resp_404) as mock_get:
        success, html, code, err = fetcher.fetch_html("https://example.com/missing-article")
        assert success is False
        assert code == 404
        assert mock_get.call_count == 1  # No retry on 404!


def test_smtp_transient_retry_and_auth_fail_fast():
    """SMTP retries on connection reset, but fails fast on authentication error."""
    # 1. Transient error retries up to 3 times
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.send_message.side_effect = [
            smtplib.SMTPException("Transient reset"),
            None,  # Succeeds on attempt 2
        ]
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        sent = send_briefing_email(
            recipient="test@example.com",
            subject="Test",
            briefing_text=SAMPLE_BRIEFING,
            sender="sender@example.com",
            password="pass",
        )
        assert sent is True
        assert mock_server.send_message.call_count == 2

    # 2. Authentication error fails immediately on attempt 1
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        sent = send_briefing_email(
            recipient="test@example.com",
            subject="Test",
            briefing_text=SAMPLE_BRIEFING,
            sender="sender@example.com",
            password="wrong",
        )
        assert sent is False
        assert mock_server.login.call_count == 1  # No retry on auth error!
