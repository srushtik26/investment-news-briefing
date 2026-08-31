"""
Email Sender Module.
Uses Python standard library smtplib and email.message.EmailMessage to send
daily executive news briefings securely via Gmail SMTP (SSL Port 465).

Generates a responsive, executive HTML email with inline styling alongside
the canonical plain-text fallback.
"""

import html
import os
import re
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
from app.logging_config import get_logger

logger = get_logger("email.sender")

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465

SECTION_CONFIG: Dict[str, Dict[str, str]] = {
    "INDIA": {
        "title": "TOP 5 INDIA BUSINESS HEADLINES",
        "accent": "#2563eb",
        "bg": "#eff6ff",
    },
    "DOMESTIC": {
        "title": "TOP 5 DOMESTIC HEADLINES",
        "accent": "#0f766e",
        "bg": "#f0fdfa",
    },
    "INTERNATIONAL": {
        "title": "TOP 5 INTERNATIONAL BUSINESS HEADLINES",
        "accent": "#7c3aed",
        "bg": "#f5f3ff",
    },
}


def parse_briefing_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Deterministically parse the canonical briefing text into structured components
    (title, date, sections, and story cards with headline, summary, source, and url).

    Returns None if structure cannot be determined so the caller can fall back cleanly.
    """
    if not text or not text.strip():
        return None

    lines = [line.strip() for line in text.strip().splitlines()]
    if not lines:
        return None

    title = "INVESTMENT COMMITTEE BRIEFING"
    date_str = ""
    content_start_idx = 0

    # Scan top lines for title and date
    for idx, line in enumerate(lines[:6]):
        if not line or set(line) <= {"━", "-", "="}:
            continue
        cleaned_line = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", line).strip()
        if "INVESTMENT COMMITTEE" in cleaned_line.upper():
            title = cleaned_line
            content_start_idx = idx + 1
        elif "DATE:" in line.upper():
            date_str = line.split(":", 1)[1].strip()
            content_start_idx = idx + 1
        elif any(
            month in line
            for month in [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
                "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
            ]
        ):
            date_str = cleaned_line
            content_start_idx = idx + 1
            break

    def get_section_key(raw_line: str) -> Optional[str]:
        cleaned = re.sub(r"^[\*\s_━\-#]+|[\*\s_━\-#]+$", "", raw_line).strip()
        u = cleaned.upper()
        if "INDIA BUSINESS" in u:
            return "INDIA"
        if "DOMESTIC" in u:
            return "DOMESTIC"
        if "INTERNATIONAL" in u:
            return "INTERNATIONAL"
        return None

    sections: List[Dict[str, Any]] = []
    current_sec_key: Optional[str] = None
    current_stories: List[Dict[str, str]] = []

    current_headline: Optional[str] = None
    current_summary_parts: List[str] = []
    current_source: Optional[str] = None
    current_url: Optional[str] = None

    def flush_story() -> None:
        nonlocal current_headline, current_summary_parts, current_source, current_url
        if current_headline:
            summary = " ".join(current_summary_parts).strip()
            if summary.lower().startswith("summary:"):
                summary = summary[8:].strip()
            current_stories.append({
                "headline": current_headline,
                "summary": summary,
                "source": current_source or "Verified Source",
                "url": current_url or "",
            })
        current_headline = None
        current_summary_parts = []
        current_source = None
        current_url = None

    def flush_section() -> None:
        nonlocal current_sec_key, current_stories
        flush_story()
        if current_sec_key and current_stories:
            cfg = SECTION_CONFIG.get(current_sec_key, {
                "title": current_sec_key,
                "accent": "#334155",
                "bg": "#f8fafc",
            })
            sections.append({
                "key": current_sec_key,
                "title": cfg["title"],
                "accent": cfg["accent"],
                "bg": cfg["bg"],
                "stories": current_stories,
            })
        current_stories = []

    for line in lines[content_start_idx:]:
        if not line or set(line) <= {"━", "-", "="}:
            continue

        sec_key = get_section_key(line)
        if sec_key:
            flush_section()
            current_sec_key = sec_key
            continue

        if not current_sec_key:
            continue

        # Check for Source line
        if line.lower().startswith("source:"):
            current_source = line.split(":", 1)[1].strip()
            continue

        # Check for URL line
        url_match = re.search(r"https?://\S+", line)
        if url_match and (line.startswith("http") or line.lower().startswith("url:")):
            current_url = url_match.group(0).rstrip(")>].,;")
            flush_story()
            continue

        # Check for Headline line:
        # Either surrounded by asterisks (*Headline*) or numbered (1. Headline)
        is_headline = False
        cand_headline = line
        if line.startswith("*") and line.endswith("*") and len(line) > 2:
            cand_headline = line.strip("*").strip()
            is_headline = True
        elif re.match(r"^\d+[\.\)]\s+", line):
            cand_headline = re.sub(r"^\d+[\.\)]\s+", "", line).strip().strip("*").strip()
            is_headline = True

        if is_headline:
            flush_story()
            current_headline = cand_headline
        else:
            if current_headline is not None:
                current_summary_parts.append(line)

    flush_section()

    if not sections:
        return None

    return {
        "title": title,
        "date": date_str,
        "sections": sections,
    }


def generate_briefing_html(parsed: Dict[str, Any]) -> str:
    """
    Generate an executive, responsive HTML email body with inline CSS from parsed components.
    Uses clean corporate styling (#0f172a navy header, card containers, section accents).
    """
    title_raw = parsed.get("title", "INVESTMENT COMMITTEE BRIEFING")
    # Strip any emojis from header title
    title_clean = re.sub(r"[^\w\s\-\–—]", "", title_raw).strip()
    title = html.escape(title_clean)

    date_raw = parsed.get("date", "")
    date_clean = re.sub(r"[^\w\s\-\–—,\.]", "", date_raw).strip()
    date_str = html.escape(date_clean)

    sections_html: List[str] = []
    for sec in parsed.get("sections", []):
        sec_title = html.escape(sec["title"])
        accent = sec["accent"]
        bg = sec["bg"]

        stories_html: List[str] = []
        for s in sec.get("stories", []):
            # Clean surrounding markdown asterisks from headline
            clean_headline = html.escape(s["headline"].strip("*").strip())
            clean_summary = html.escape(s["summary"].strip())
            clean_source = html.escape(s["source"].strip())
            raw_url = s.get("url", "").strip()
            escaped_url = html.escape(raw_url, quote=True)

            summary_block = ""
            if clean_summary:
                summary_block = (
                    f'<div style="font-size: 14px; color: #374151; line-height: 1.5; margin-bottom: 12px;">'
                    f'{clean_summary}</div>'
                )

            url_button = ""
            if raw_url:
                url_button = (
                    f'<a href="{escaped_url}" target="_blank" '
                    f'style="color: #2563eb; text-decoration: none; font-weight: 600; font-size: 13px; display: inline-block;">'
                    f'Read full article &rarr;</a>'
                )

            stories_html.append(f"""
              <div style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 12px;">
                <div style="font-size: 16px; font-weight: 700; color: #0f172a; line-height: 1.4; margin-bottom: 8px;">
                  {clean_headline}
                </div>
                {summary_block}
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top: 10px; border-top: 1px solid #f3f4f6; padding-top: 10px;">
                  <tr>
                    <td align="left" style="font-size: 13px; color: #6b7280;">
                      Source: <strong style="color: #1f2937; font-weight: 600;">{clean_source}</strong>
                    </td>
                    <td align="right" style="font-size: 13px;">
                      {url_button}
                    </td>
                  </tr>
                </table>
              </div>
            """)

        sections_html.append(f"""
          <div style="margin-top: 24px; margin-bottom: 14px;">
            <div style="background-color: {bg}; border-left: 4px solid {accent}; padding: 10px 14px; border-radius: 6px; font-size: 14px; font-weight: 700; color: {accent}; letter-spacing: 0.05em; text-transform: uppercase;">
              {sec_title}
            </div>
          </div>
          {"".join(stories_html)}
        """)

    full_sections_html = "".join(sections_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin: 0; padding: 24px 12px; background-color: #f4f6f8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
  <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 760px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e5e7eb;">
    <!-- Header -->
    <tr>
      <td style="background-color: #0f172a; padding: 28px 32px; border-top-left-radius: 12px; border-top-right-radius: 12px;">
        <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; color: #94a3b8; text-transform: uppercase; margin-bottom: 6px;">
          DAILY MARKET INTELLIGENCE
        </div>
        <h1 style="color: #ffffff; font-size: 24px; font-weight: 700; line-height: 1.25; margin: 0; letter-spacing: -0.01em;">
          {title}
        </h1>
        <div style="color: #cbd5e1; font-size: 14px; font-weight: 500; margin-top: 8px;">
          {date_str}
        </div>
      </td>
    </tr>
    <!-- Content Body -->
    <tr>
      <td style="padding: 12px 28px 12px;">
        {full_sections_html}
      </td>
    </tr>
    <!-- Footer -->
    <tr>
      <td style="padding: 16px 28px 28px; text-align: center; border-top: 1px solid #f1f5f9;">
        <div style="font-size: 12px; color: #9ca3af; line-height: 1.5;">
          Automated Investment Committee News Briefing<br>
          Generated from verified news sources.
        </div>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_briefing_email(
    recipient: Optional[str] = None,
    subject: Optional[str] = None,
    briefing_text: str = "",
    sender: Optional[str] = None,
    password: Optional[str] = None,
) -> bool:
    """
    Send the executive news briefing via Gmail SMTP SSL.

    Includes both the canonical plain-text fallback and a professional
    HTML alternative with inline styling.

    Args:
        recipient: Target email address (defaults to GMAIL_RECIPIENT env var).
        subject: Email subject line.
        briefing_text: Plain text content of the final executive briefing.
        sender: Sender email address (defaults to GMAIL_SENDER env var).
        password: Gmail App Password (defaults to GMAIL_APP_PASSWORD env var).

    Returns:
        bool: True if email was delivered successfully to SMTP server, False otherwise.
    """
    sender_email = sender or os.environ.get("GMAIL_SENDER", "").strip()
    recipient_email = recipient or os.environ.get("GMAIL_RECIPIENT", "").strip()
    app_password = password or os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not sender_email:
        logger.error("EMAIL_DELIVERY_FAILED: GMAIL_SENDER environment variable is not configured.")
        return False

    if not recipient_email:
        logger.error("EMAIL_DELIVERY_FAILED: GMAIL_RECIPIENT environment variable is not configured.")
        return False

    if not app_password:
        logger.error("EMAIL_DELIVERY_FAILED: GMAIL_APP_PASSWORD environment variable is not configured.")
        return False

    if not briefing_text or not briefing_text.strip():
        logger.error("EMAIL_DELIVERY_FAILED: briefing_text is empty.")
        return False

    email_subject = subject or "Investment Committee Daily Briefing"

    msg = EmailMessage()
    msg["Subject"] = email_subject
    msg["From"] = sender_email
    msg["To"] = recipient_email

    # 1. Always set canonical plain-text content first as primary/fallback
    msg.set_content(briefing_text, charset="utf-8")

    # 2. Parse and generate responsive HTML alternative
    try:
        parsed_briefing = parse_briefing_text(briefing_text)
        if parsed_briefing:
            html_body = generate_briefing_html(parsed_briefing)
            if html_body:
                msg.add_alternative(html_body, subtype="html")
        else:
            logger.warning("Briefing text structure could not be parsed into HTML sections; delivering plain text only.")
    except Exception as parse_err:
        logger.warning(
            "Failed to generate HTML briefing representation (%s); delivering plain text only.",
            parse_err,
        )

    try:
        logger.info(
            "Connecting to %s:%d (SSL) to send briefing to %s...",
            GMAIL_SMTP_HOST,
            GMAIL_SMTP_PORT,
            recipient_email,
        )
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30.0) as server:
            server.login(sender_email, app_password)
            server.send_message(msg)

        logger.info("EMAIL_DELIVERY_SUCCESS: Briefing email successfully delivered to %s", recipient_email)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "EMAIL_DELIVERY_FAILED: SMTP Authentication failed for sender %s. Check GMAIL_APP_PASSWORD.",
            sender_email,
        )
        return False
    except Exception as e:
        logger.error("EMAIL_DELIVERY_FAILED: Failed to send email via SMTP: %s", type(e).__name__)
        return False
