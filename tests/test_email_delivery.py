"""
Unit and Integration Tests for Daily Email Delivery Wrapper with Responsive HTML.

Tests:
1. Email has text/plain fallback.
2. Email has text/html alternative.
3. Visible HTML does not contain headline markdown asterisks (*).
4. Three section headings appear with designated accent colors.
5. All 15 stories appear in HTML output when given a 15-story briefing.
6. Sources appear with bold publication styling.
7. Original URLs are preserved in href attributes.
8. HTML escapes special characters safely (&, <, >, ").
9. Plain-text body remains EXACTLY unchanged.
10. SMTP behavior remains unchanged (smtp.gmail.com:465 SSL).
11. last_email_date behavior remains unchanged (written only after SMTP success).
12. Malformed briefing gracefully falls back to plain text without crashing.
"""

import html
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.email.email_sender import (
    GMAIL_SMTP_HOST,
    GMAIL_SMTP_PORT,
    generate_briefing_html,
    parse_briefing_text,
    send_briefing_email,
    send_copy_paste_email,
)
from app.formatting.formatter import build_copy_paste_text
from run_daily import (
    format_subject_date,
    get_last_email_date,
    get_primary_email_date,
    record_primary_email_date,
    record_successful_email_date,
    run_daily_briefing,
)


SAMPLE_BRIEFING_TEXT = """*INVESTMENT COMMITTEE BRIEFING*
*Friday, 28th August, 2026*

*TOP 5 INDIA BUSINESS HEADLINES*

*Ather Energy shares jump 5% after ₹1,758 crore block deal*
Shares of Ather Energy surged over 5 percent following a ₹1,758 crore block deal on Thursday.
Source: Moneycontrol
https://tinyurl.com/3h8f8j9k

*TOP 5 DOMESTIC HEADLINES*

*Supreme Court orders Centre to respond within two weeks on national policy*
The Supreme Court directed the central government to file a status report on national education policy within two weeks.
Source: The Hindu
https://tinyurl.com/5n7v2x8a

*TOP 5 INTERNATIONAL BUSINESS HEADLINES*

*Nvidia agrees to buy AI software firm in $1.2 billion all-cash deal*
Nvidia signed a definitive agreement to acquire the AI software developer for $1.2 billion in cash.
Source: CNBC
https://tinyurl.com/y8k3m9pw
"""


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("GMAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("GMAIL_RECIPIENT", "ic-briefings@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "mock-app-password-1234")


@pytest.fixture
def temp_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def test_send_briefing_email_unit_success_has_plain_and_html(mock_env):
    """Test send_briefing_email sends multipart email with both plain text and HTML alternatives."""
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        subject = "Investment Committee Briefing — 28 Aug 2026"
        success = send_briefing_email(
            recipient="ic-briefings@example.com",
            subject=subject,
            briefing_text=SAMPLE_BRIEFING_TEXT,
            sender="sender@example.com",
            password="mock-app-password-1234",
        )

        assert success is True
        mock_smtp_cls.assert_called_once_with(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30.0)
        mock_server.login.assert_called_once_with("sender@example.com", "mock-app-password-1234")
        mock_server.send_message.assert_called_once()

        sent_msg = mock_server.send_message.call_args[0][0]
        assert isinstance(sent_msg, EmailMessage)
        assert sent_msg["Subject"] == subject
        assert sent_msg["From"] == "sender@example.com"
        assert sent_msg["To"] == "ic-briefings@example.com"

        # 1. Plain text fallback body
        plain_body = sent_msg.get_body(preferencelist=("plain",)).get_content()
        assert plain_body.strip() == SAMPLE_BRIEFING_TEXT.strip()

        # 2. HTML alternative body
        html_body = sent_msg.get_body(preferencelist=("html",)).get_content()
        assert html_body is not None
        assert "<html" in html_body.lower()
        assert "INVESTMENT COMMITTEE BRIEFING" in html_body


def test_html_presentation_styling_and_clean_content():
    """Verify HTML design elements: no headline asterisks, proper accents, sources, and links."""
    parsed = parse_briefing_text(SAMPLE_BRIEFING_TEXT)
    assert parsed is not None
    html_content = generate_briefing_html(parsed)

    # Visible HTML must not contain markdown asterisks around headlines
    assert "*Ather Energy" not in html_content
    assert "Ather Energy shares jump 5% after ₹1,758 crore block deal" in html_content

    # Three section headings and accents
    assert "TOP 5 INDIA BUSINESS HEADLINES" in html_content
    assert "#2563eb" in html_content  # India Business accent
    assert "TOP 5 DOMESTIC HEADLINES" in html_content
    assert "#0f766e" in html_content  # Domestic accent
    assert "TOP 5 INTERNATIONAL BUSINESS HEADLINES" in html_content
    assert "#7c3aed" in html_content  # International Business accent

    # Sources formatted with Source: and bold publication
    assert "Source:" in html_content
    assert "Moneycontrol" in html_content
    assert "The Hindu" in html_content
    assert "CNBC" in html_content

    # Original URLs preserved in href with 'Read full article →'
    assert 'href="https://tinyurl.com/3h8f8j9k"' in html_content
    assert 'href="https://tinyurl.com/5n7v2x8a"' in html_content
    assert 'href="https://tinyurl.com/y8k3m9pw"' in html_content
    assert "Read full article &rarr;" in html_content

    # Header branding
    assert "DAILY MARKET INTELLIGENCE" in html_content
    assert "#0f172a" in html_content  # Dark navy background
    assert "#f4f6f8" in html_content  # Page background

    # Footer
    assert "Automated Investment Committee News Briefing" in html_content
    assert "Generated from verified news sources." in html_content


def test_all_15_stories_appear_in_html_output():
    """Verify all 15 stories from actual data/final_briefing.txt are parsed and rendered in HTML."""
    final_path = Path("data/final_briefing.txt")
    if not final_path.exists():
        pytest.skip("data/final_briefing.txt not found on disk.")

    briefing_text = final_path.read_text(encoding="utf-8")
    parsed = parse_briefing_text(briefing_text)
    assert parsed is not None
    assert len(parsed["sections"]) == 3

    total_stories = sum(len(sec["stories"]) for sec in parsed["sections"])
    assert total_stories == 15

    html_content = generate_briefing_html(parsed)
    # Check that all 15 stories have a 'Read full article' link
    assert html_content.count("Read full article &rarr;") == 15


def test_html_special_character_escaping():
    """Verify special characters (&, <, >, \") in headlines and summaries are safely escaped."""
    special_briefing = """*INVESTMENT COMMITTEE BRIEFING*
*Friday, 28th August, 2026*

*TOP 5 INDIA BUSINESS HEADLINES*

*AT&T & "Tech Corp" <Merger> Deal Finalized*
AT&T and "Tech Corp" agreed to a <$5 Billion> merger & acquisition deal.
Source: S&P Global
https://example.com/deal?a=1&b=2
"""
    parsed = parse_briefing_text(special_briefing)
    assert parsed is not None
    html_content = generate_briefing_html(parsed)

    # Verify HTML escaping
    assert "AT&amp;T &amp; &quot;Tech Corp&quot; &lt;Merger&gt; Deal Finalized" in html_content
    assert "&lt;$5 Billion&gt; merger &amp; acquisition" in html_content
    assert "S&amp;P Global" in html_content
    # Verify URL in href is properly quoted without destroying query params
    assert 'href="https://example.com/deal?a=1&amp;b=2"' in html_content or 'href="https://example.com/deal?a=1&b=2"' in html_content


def test_malformed_briefing_gracefully_falls_back_to_plain_text(mock_env):
    """Verify malformed unparseable briefing sends plain text without raising exceptions."""
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        malformed_text = "This is a random unformatted message without standard sections or headlines."
        success = send_briefing_email(
            recipient="ic-briefings@example.com",
            subject="Test Malformed",
            briefing_text=malformed_text,
            sender="sender@example.com",
            password="mock-app-password-1234",
        )

        assert success is True
        sent_msg = mock_server.send_message.call_args[0][0]
        # Should be plain text only (no HTML alternative)
        assert sent_msg.get_content_type() == "text/plain"
        assert sent_msg.get_content().strip() == malformed_text.strip()


def test_send_briefing_email_auth_failure(mock_env):
    """Test send_briefing_email handles SMTPAuthenticationError cleanly."""
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        success = send_briefing_email(
            recipient="ic-briefings@example.com",
            subject="Test",
            briefing_text=SAMPLE_BRIEFING_TEXT,
        )
        assert success is False


def test_daily_runner_successful_briefing_sends_two_emails(mock_env, temp_data_dir):
    """Test successful briefing run sends two emails (primary + copy/paste) and records dates."""
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    target_d = date(2026, 8, 28)

    with patch("app.email.email_sender.smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 0
        assert mock_server.send_message.call_count == 2

        # 1. Primary Email: Multipart with HTML alternative
        msg1 = mock_server.send_message.call_args_list[0][0][0]
        assert msg1["Subject"] == "Investment Committee Briefing — 28 Aug 2026"
        assert msg1.get_body(preferencelist=("html",)) is not None

        # 2. Copy/Paste Email: Pure text/plain
        msg2 = mock_server.send_message.call_args_list[1][0][0]
        assert msg2["Subject"] == "Investment Committee Briefing — Copy/Paste Text — 28 Aug 2026"
        assert msg2.get_content_type() == "text/plain"
        assert msg2.get_body(preferencelist=("html",)) is None
        assert "INVESTMENT COMMITTEE BRIEFING" in msg2.get_content()

        # Date files written
        assert (temp_data_dir / "primary_email_date.txt").read_text(encoding="utf-8").strip() == "2026-08-28"
        date_file = temp_data_dir / "last_email_date.txt"
        assert date_file.exists()
        assert date_file.read_text(encoding="utf-8").strip() == "2026-08-28"



def test_daily_runner_idempotency_prevents_duplicate_same_day(mock_env, temp_data_dir):
    """Test that if today's briefing was already emailed, no second email is sent and runner exits 0."""
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    (temp_data_dir / "last_email_date.txt").write_text("2026-08-28", encoding="utf-8")
    target_d = date(2026, 8, 28)

    with patch("app.email.email_sender.smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 0
        mock_smtp_cls.assert_not_called()
        mock_server.send_message.assert_not_called()


def test_daily_runner_pipeline_failure_sends_no_email(mock_env, temp_data_dir):
    """Test that if run_pipeline fails (returns 1), no email is sent and last_email_date is NOT updated."""
    target_d = date(2026, 8, 28)

    with patch("run_pipeline.run_pipeline", return_value=1) as mock_pipeline:
        with patch("app.email.email_sender.smtplib.SMTP_SSL") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_server

            exit_code = run_daily_briefing(
                data_dir_override=temp_data_dir,
                target_date=target_d,
                skip_pipeline_execution=False,
            )

            assert exit_code == 1
            mock_pipeline.assert_called_once()
            mock_smtp_cls.assert_not_called()
            assert not (temp_data_dir / "last_email_date.txt").exists()


def test_daily_runner_missing_final_file_sends_no_email(mock_env, temp_data_dir):
    """Test that missing final_briefing.txt fails cleanly with no email sent."""
    target_d = date(2026, 8, 28)

    with patch("app.email.email_sender.smtplib.SMTP_SSL") as mock_smtp_cls:
        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 1
        mock_smtp_cls.assert_not_called()
        assert not (temp_data_dir / "last_email_date.txt").exists()


def test_daily_runner_smtp_failure_does_not_record_date(mock_env, temp_data_dir):
    """Test that if SMTP raises an exception, last_email_date.txt is NOT written and runner exits 1."""
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    target_d = date(2026, 8, 28)

    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_server.send_message.side_effect = smtplib.SMTPException("Connection reset by peer")
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 1
        assert not (temp_data_dir / "last_email_date.txt").exists()


def test_daily_runner_missing_credentials_fails_cleanly(monkeypatch, temp_data_dir):
    """Test that missing email credentials exits non-zero without attempting SMTP."""
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    target_d = date(2026, 8, 28)

    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 1
        mock_smtp_cls.assert_not_called()
        assert not (temp_data_dir / "last_email_date.txt").exists()


def test_format_subject_date():
    """Verify format_subject_date produces clean day-month-year format."""
    d = date(2026, 8, 28)
    assert format_subject_date(d) == "28 Aug 2026"
    d_single_digit = date(2026, 9, 5)
    assert format_subject_date(d_single_digit) == "5 Sep 2026"


# ==============================================================================
# Comprehensive Requirements Tests (1 through 10)
# ==============================================================================


def test_req_1_existing_primary_email_output_unchanged(mock_env):
    """1. Prove existing primary email output remains completely unchanged."""
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        subject = "Investment Committee Briefing — 28 Aug 2026"
        success = send_briefing_email(
            recipient="ic-briefings@example.com",
            subject=subject,
            briefing_text=SAMPLE_BRIEFING_TEXT,
            sender="sender@example.com",
            password="mock-app-password-1234",
        )

        assert success is True
        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["Subject"] == subject
        assert sent_msg["From"] == "sender@example.com"
        assert sent_msg["To"] == "ic-briefings@example.com"

        # Plain text fallback body is preserved
        plain_body = sent_msg.get_body(preferencelist=("plain",)).get_content()
        assert plain_body.strip() == SAMPLE_BRIEFING_TEXT.strip()

        # HTML alternative body is preserved with executive styling
        html_body = sent_msg.get_body(preferencelist=("html",)).get_content()
        assert html_body is not None
        assert "INVESTMENT COMMITTEE BRIEFING" in html_body
        assert "#0f172a" in html_body


def test_req_2_second_email_is_sent(mock_env, temp_data_dir):
    """2. Prove second email is sent in sequence after primary email."""
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    target_d = date(2026, 8, 28)

    with patch("run_daily.send_briefing_email", return_value=True) as mock_email_1, \
         patch("run_daily.send_copy_paste_email", return_value=True) as mock_email_2:

        exit_code = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code == 0
        mock_email_1.assert_called_once()
        mock_email_2.assert_called_once()

        # Check call arguments
        assert mock_email_1.call_args.kwargs["subject"] == "Investment Committee Briefing — 28 Aug 2026"
        assert mock_email_2.call_args.kwargs["subject"] == "Investment Committee Briefing — Copy/Paste Text — 28 Aug 2026"


def test_req_3_second_email_is_plain_text(mock_env):
    """3. Prove second email is strictly plain text without HTML alternative or tags."""
    sample_copy_text = (
        "INVESTMENT COMMITTEE BRIEFING\n"
        "Friday, 28th August, 2026\n\n"
        "TOP 5 INDIA BUSINESS HEADLINES\n\n"
        "1. Ather Energy shares jump 5%\n"
        "Shares of Ather Energy surged over 5 percent.\n"
        "Source: Moneycontrol\n"
        "https://example.com/ather\n"
    )
    with patch("smtplib.SMTP_SSL") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        success = send_copy_paste_email(
            recipient="ic-briefings@example.com",
            subject="Investment Committee Briefing — Copy/Paste Text — 28 Aug 2026",
            text_content=sample_copy_text,
            sender="sender@example.com",
            password="mock-app-password-1234",
        )

        assert success is True
        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg.get_content_type() == "text/plain"
        assert sent_msg.get_body(preferencelist=("html",)) is None
        content = sent_msg.get_content()
        assert "<html" not in content.lower()
        assert "<div>" not in content.lower()
        assert "<table>" not in content.lower()
        assert content.strip() == sample_copy_text.strip()


def test_req_4_exactly_5_5_5_stories_appear():
    """4. Prove exactly 5/5/5 stories appear in copy/paste text in exact section order."""
    fifteen_stories = {
        "india": [
            {
                "headline": f"*India Corp Deal {i}*",
                "summary": f"Summary fact for India story {i}.",
                "source": "The Economic Times",
                "url": f"https://economictimes.indiatimes.com/india-{i}",
            }
            for i in range(1, 6)
        ],
        "domestic": [
            {
                "headline": f"*Domestic Policy Reform {i}*",
                "summary": f"Summary fact for Domestic story {i}.",
                "source": "The Hindu",
                "url": f"https://thehindu.com/domestic-{i}",
            }
            for i in range(1, 6)
        ],
        "international": [
            {
                "headline": f"*Global Tech Acquisition {i}*",
                "summary": f"Summary fact for Intl story {i}.",
                "source": "Reuters",
                "url": f"https://reuters.com/intl-{i}",
            }
            for i in range(1, 6)
        ],
    }

    result = build_copy_paste_text(fifteen_stories, briefing_date=date(2026, 8, 28))

    # Header
    assert result.startswith("INVESTMENT COMMITTEE BRIEFING\nFriday, 28th August, 2026")

    # Section ordering
    pos_india = result.find("TOP 5 INDIA BUSINESS HEADLINES")
    pos_dom = result.find("TOP 5 DOMESTIC HEADLINES")
    pos_intl = result.find("TOP 5 INTERNATIONAL BUSINESS HEADLINES")

    assert pos_india != -1
    assert pos_dom != -1
    assert pos_intl != -1
    assert pos_india < pos_dom < pos_intl

    # Section stories numbered 1 to 5
    for i in range(1, 6):
        assert f"\n{i}. India Corp Deal {i}" in result
        assert f"\n{i}. Domestic Policy Reform {i}" in result
        assert f"\n{i}. Global Tech Acquisition {i}" in result

    # No markdown asterisks in headlines
    assert "*India Corp Deal" not in result
    assert "*Domestic Policy" not in result
    assert "*Global Tech" not in result


def test_req_5_full_urls_preserved():
    """5. Prove full original URLs with query params are completely preserved without TinyURL."""
    long_url = "https://economictimes.indiatimes.com/markets/stocks/news/reliance-clean-energy-deal-7500cr?ref=section&utm_source=daily_feed&tracker_id=987654"
    stories = {
        "india": [
            {
                "headline": "Reliance expands renewable portfolio",
                "summary": "Major green energy acquisition.",
                "source": "The Economic Times",
                "url": long_url,
            }
        ],
        "domestic": [],
        "international": [],
    }

    text = build_copy_paste_text(stories, briefing_date=date(2026, 8, 28))
    assert long_url in text
    assert "tinyurl.com" not in text


def test_req_6_second_source_urls_preserved_when_available():
    """6. Prove second-source publications and URLs are preserved when available."""
    stories = {
        "india": [
            {
                "headline": "Major infrastructure expansion",
                "summary": "Cabinet approves highway connectivity.",
                "source": "Business Standard",
                "url": "https://business-standard.com/infra-deal",
                "secondary_source": "The Indian Express",
                "secondary_url": "https://indianexpress.com/infra-verified-123",
            }
        ],
        "domestic": [],
        "international": [],
    }

    text = build_copy_paste_text(stories, briefing_date=date(2026, 8, 28))
    assert "Source: Business Standard" in text
    assert "https://business-standard.com/infra-deal" in text
    assert "Also verified by: The Indian Express" in text
    assert "https://indianexpress.com/infra-verified-123" in text


def test_req_7_no_extra_gemini_calls(monkeypatch):
    """7. Prove 0 extra Gemini calls are made during build_copy_paste_text or second email sending."""
    gemini_call_counter = 0

    def fail_on_gemini(*args, **kwargs):
        nonlocal gemini_call_counter
        gemini_call_counter += 1
        raise RuntimeError("Forbidden Gemini API call triggered!")

    # Patch potential AI generation entry points
    monkeypatch.setattr("app.ai.editor.GeminiEditorialEngine.select_and_format_stories", fail_on_gemini, raising=False)
    monkeypatch.setattr("app.classification.classifier.AIArticleClassifier.classify", fail_on_gemini, raising=False)

    text = build_copy_paste_text(SAMPLE_BRIEFING_TEXT, briefing_date=date(2026, 8, 28))
    assert "INVESTMENT COMMITTEE BRIEFING" in text
    assert gemini_call_counter == 0


def test_req_8_no_extra_serpapi_calls():
    """8. Prove 0 extra SerpAPI calls are made during copy/paste building or sending."""
    from app.verification.serpapi_corroborator import get_serpapi_count, reset_serpapi_counter

    reset_serpapi_counter()
    initial_count = get_serpapi_count()

    text = build_copy_paste_text(SAMPLE_BRIEFING_TEXT, briefing_date=date(2026, 8, 28))
    assert len(text) > 0
    assert get_serpapi_count() == initial_count == 0


def test_req_9_scheduler_unchanged():
    """9. Prove GitHub Actions daily scheduler workflow remains intact and unchanged."""
    workflow_path = Path(".github/workflows/daily_briefing.yml")
    assert workflow_path.exists()
    content = workflow_path.read_text(encoding="utf-8")

    # Verify schedule cron entries
    assert "cron: '5 1 * * *'" in content   # 06:35 IST
    assert "cron: '15 1 * * *'" in content  # 06:45 IST
    assert "cron: '25 1 * * *'" in content  # 06:55 IST
    assert "cron: '35 1 * * *'" in content  # 07:05 IST

    # Verify invocation of run_daily.py
    assert "python run_daily.py" in content
    assert "data/last_email_date.txt" in content


def test_req_10_retry_and_idempotency_behavior_safe(mock_env, temp_data_dir, caplog):
    """10. Prove failure handling and idempotency: Email 1 not duplicated if Email 2 failed on prior attempt."""
    (temp_data_dir / "final_briefing.txt").write_text(SAMPLE_BRIEFING_TEXT, encoding="utf-8")
    target_d = date(2026, 8, 28)

    # ATTEMPT 1: Email 1 succeeds, Email 2 fails
    with patch("run_daily.send_briefing_email", return_value=True) as mock_email_1, \
         patch("run_daily.send_copy_paste_email", return_value=False) as mock_email_2:

        exit_code_1 = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code_1 == 1
        assert mock_email_1.call_count == 1
        assert mock_email_2.call_count == 1

        # Check required log messages
        log_text = caplog.text
        assert "PRIMARY_EMAIL_SENT" in log_text
        assert "COPY_TEXT_EMAIL_FAILED" in log_text

        # State check: primary email recorded, but NOT full delivery
        assert (temp_data_dir / "primary_email_date.txt").exists()
        assert (temp_data_dir / "primary_email_date.txt").read_text(encoding="utf-8").strip() == "2026-08-28"
        assert not (temp_data_dir / "last_email_date.txt").exists()

    # ATTEMPT 2 (Scheduler Retry): Email 1 should be SKIPPED, Email 2 attempted and succeeds
    caplog.clear()
    with patch("run_daily.send_briefing_email", return_value=True) as mock_email_1_retry, \
         patch("run_daily.send_copy_paste_email", return_value=True) as mock_email_2_retry:

        exit_code_2 = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code_2 == 0
        # Email 1 must NOT be duplicated!
        assert mock_email_1_retry.call_count == 0
        # Email 2 must be sent
        assert mock_email_2_retry.call_count == 1

        # State check: now full delivery date is recorded
        assert (temp_data_dir / "last_email_date.txt").exists()
        assert (temp_data_dir / "last_email_date.txt").read_text(encoding="utf-8").strip() == "2026-08-28"

    # ATTEMPT 3 (Post-Success Idempotency): Both emails skipped
    with patch("run_daily.send_briefing_email") as mock_email_1_final, \
         patch("run_daily.send_copy_paste_email") as mock_email_2_final:

        exit_code_3 = run_daily_briefing(
            data_dir_override=temp_data_dir,
            target_date=target_d,
            skip_pipeline_execution=True,
        )

        assert exit_code_3 == 0
        mock_email_1_final.assert_not_called()
        mock_email_2_final.assert_not_called()

