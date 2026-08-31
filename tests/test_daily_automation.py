"""
Unit and regression tests for daily automation runner (run_daily.py).

Verifies:
1. Exact keyword compatibility between run_daily_briefing() and run_pipeline().
2. Proper error handling when credentials are missing or pipeline fails.
3. Idempotency checks preventing duplicate daily deliveries.
4. Correct dispatch and argument passing without unsupported keywords like max_domestic.
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock
import inspect
import pytest

from run_daily import run_daily_briefing, format_subject_date, get_last_email_date, record_successful_email_date
import run_pipeline


def test_run_pipeline_signature_does_not_contain_max_domestic():
    """
    Verify run_pipeline signature across entry-point and runner does not accept max_domestic.
    """
    sig = inspect.signature(run_pipeline.run_pipeline)
    assert "max_domestic" not in sig.parameters
    assert "max_india" in sig.parameters
    assert "max_international" in sig.parameters


def test_run_daily_briefing_calls_run_pipeline_with_exact_signature(tmp_path, monkeypatch):
    """
    Test run_daily_briefing invokes run_pipeline using only supported parameters.
    Passing max_domestic to run_daily_briefing must NOT forward max_domestic to run_pipeline.
    """
    monkeypatch.setenv("GMAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("GMAIL_RECIPIENT", "recipient@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app_password_123")

    # Create dummy artifact so execution succeeds
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    briefing_file = data_dir / "final_briefing.txt"
    briefing_file.write_text(
        "INVESTMENT COMMITTEE BRIEFING\n\n"
        "DOMESTIC\n1. Story 1\n2. Story 2\n3. Story 3\n4. Story 4\n5. Story 5\n\n"
        "INDIA BUSINESS\n1. Story 1\n2. Story 2\n3. Story 3\n4. Story 4\n5. Story 5\n\n"
        "INTERNATIONAL BUSINESS\n1. Story 1\n2. Story 2\n3. Story 3\n4. Story 4\n5. Story 5\n",
        encoding="utf-8",
    )

    # Use a mock that enforces the real inspect signature of run_pipeline
    mock_pipeline = MagicMock(spec=run_pipeline.run_pipeline)
    mock_pipeline.return_value = 0

    with patch("run_pipeline.run_pipeline", mock_pipeline), \
         patch("run_daily.send_briefing_email", return_value=True):

        exit_code = run_daily_briefing(
            data_dir_override=data_dir,
            target_date=date(2026, 8, 31),
            skip_pipeline_execution=False,
            max_india=20,
            max_international=20,
            max_domestic=40,  # caller passes max_domestic for compatibility
        )

        assert exit_code == 0
        assert mock_pipeline.called
        call_kwargs = mock_pipeline.call_args.kwargs
        assert "max_domestic" not in call_kwargs
        assert call_kwargs.get("max_india") == 20
        assert call_kwargs.get("max_international") == 20


def test_run_daily_briefing_missing_credentials_fails(tmp_path, monkeypatch):
    """
    Verify early exit if required email credentials are missing.
    """
    monkeypatch.delenv("GMAIL_SENDER", raising=False)
    monkeypatch.delenv("GMAIL_RECIPIENT", raising=False)
    monkeypatch.delenv("GMAIL_RECIPIENTS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    data_dir = tmp_path / "data"
    exit_code = run_daily_briefing(
        data_dir_override=data_dir,
        target_date=date(2026, 8, 31),
        skip_pipeline_execution=True,
    )
    assert exit_code == 1


def test_run_daily_briefing_idempotency_prevents_duplicate(tmp_path, monkeypatch):
    """
    Verify idempotency guard exits cleanly with code 0 if briefing already delivered today.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    record_successful_email_date(data_dir, "2026-08-31")

    exit_code = run_daily_briefing(
        data_dir_override=data_dir,
        target_date=date(2026, 8, 31),
        skip_pipeline_execution=True,
    )
    assert exit_code == 0
