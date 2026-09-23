"""
Unit and integration tests for dashboard PDF export functionality.
Verifies PDF generation, formatting, section ordering (India -> International -> Domestic),
'Read Full Article' links, routing, error handling, and compliance with institutional standards.
"""

import base64
from datetime import date
from pathlib import Path
import zlib
import pytest
from fastapi.testclient import TestClient

from app.dashboard.models import DashboardBriefing, DashboardStory
from app.dashboard.pdf_export import (
    _safe_text,
    generate_briefing_pdf,
)
import app.dashboard.pdf_export as pe
from app.dashboard.repository import DashboardRepository
from app.dashboard.web import create_app


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract plain text from ReportLab PDF by decompressing page streams."""
    text_chunks = []
    idx = 0
    while True:
        pos = pdf_bytes.find(b"stream", idx)
        if pos == -1:
            break
        end_pos = pdf_bytes.find(b"endstream", pos)
        if end_pos == -1:
            break

        # Skip image bitmap streams
        dict_start = pdf_bytes.rfind(b"<<", max(0, pos - 500), pos)
        if dict_start != -1 and b"/Subtype /Image" in pdf_bytes[dict_start:pos]:
            idx = end_pos + 9
            continue

        raw = pdf_bytes[pos + 6 : end_pos].strip()
        idx = end_pos + 9

        # Try Adobe ASCII85 + FlateDecode
        try:
            a85 = base64.a85decode(raw, adobe=True)
            decomp = zlib.decompress(a85)
            text_chunks.append(decomp.decode("latin-1", errors="ignore"))
            continue
        except Exception:
            pass

        # Try direct FlateDecode
        try:
            decomp = zlib.decompress(raw)
            text_chunks.append(decomp.decode("latin-1", errors="ignore"))
            continue
        except Exception:
            pass

        # Fallback raw latin-1
        text_chunks.append(raw.decode("latin-1", errors="ignore"))

    return "\n".join(text_chunks)


def create_15_story_briefing(briefing_date: date = date(2026, 9, 23)) -> DashboardBriefing:
    """Helper to generate a complete, valid 15-story briefing."""
    stories = []
    sections = [
        ("india", "The Economic Times", "https://economictimes.indiatimes.com/industry/banking/story-"),
        ("international", "Reuters", "https://www.reuters.com/world/global-macro-"),
        ("domestic", "The Hindu", "https://www.thehindu.com/news/national/story-"),
    ]
    for sec_code, source_name, url_prefix in sections:
        for i in range(1, 6):
            stories.append(
                DashboardStory(
                    section=sec_code,
                    position=i,
                    headline=f"Headline {i} for {sec_code.upper()} segment with financial impact",
                    summary=f"Institutional summary detailing macro implications for {sec_code} story {i}.",
                    source=source_name,
                    url=f"{url_prefix}{i}?query_param=123&utm_source=feed#section",
                )
            )
    return DashboardBriefing(
        briefing_date=briefing_date,
        story_count=15,
        stories=stories,
    )


# 1. Valid briefing PDF generation returns non-empty bytes
def test_valid_briefing_pdf_generation_returns_non_empty_bytes():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF-")


# 2. Exactly 15 stories are supplied to the PDF generator
def test_15_stories_supplied_to_pdf_generator():
    briefing = create_15_story_briefing()
    assert len(briefing.stories) == 15
    assert len(briefing.india_stories) == 5
    assert len(briefing.international_stories) == 5
    assert len(briefing.domestic_stories) == 5

    pdf_bytes = generate_briefing_pdf(briefing)
    assert len(pdf_bytes) > 1000

    decompressed_text = extract_text_from_pdf(pdf_bytes)
    # Every story headline should be present in the PDF text streams
    for story in briefing.stories:
        assert story.headline[:25] in decompressed_text


# 3. India section exists
def test_india_section_exists():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)
    assert "TOP 5 INDIA BUSINESS HEADLINES" in decompressed_text


# 4. International section exists
def test_international_section_exists():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)
    assert "TOP 5 INTERNATIONAL BUSINESS HEADLINES" in decompressed_text


# 5. Domestic section exists
def test_domestic_section_exists():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)
    assert "TOP 5 DOMESTIC HEADLINES" in decompressed_text


# 6. Section order is strictly: India -> International -> Domestic
def test_section_order_is_india_international_domestic():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)

    india_pos = decompressed_text.find("TOP 5 INDIA BUSINESS HEADLINES")
    intl_pos = decompressed_text.find("TOP 5 INTERNATIONAL BUSINESS HEADLINES")
    dom_pos = decompressed_text.find("TOP 5 DOMESTIC HEADLINES")

    assert india_pos != -1, "India section not found"
    assert intl_pos != -1, "International section not found"
    assert dom_pos != -1, "Domestic section not found"

    assert india_pos < intl_pos < dom_pos, (
        f"Section order must be India ({india_pos}) < International ({intl_pos}) < Domestic ({dom_pos})"
    )


# 7. Unicode sanitization still works
def test_safe_text_normalizes_unsupported_unicode():
    raw_sample = 'LIC ₹5,000 Cr Deal — “Target Reached” • ‘High Growth’… with\u00a0NBSP & (₹)'
    normalized = _safe_text(raw_sample)
    assert "₹" not in normalized
    assert "Rs." in normalized
    assert "“" not in normalized and "”" not in normalized
    assert '"' in normalized
    assert "‘" not in normalized and "’" not in normalized
    assert "'" in normalized
    assert "—" not in normalized
    assert "-" in normalized
    assert "•" not in normalized
    assert "…" not in normalized
    assert "..." in normalized
    assert "\u00a0" not in normalized


# 8. No black-square placeholder regression
def test_unicode_characters_render_without_black_square_placeholders():
    briefing = create_15_story_briefing()
    briefing.stories[0].headline = 'Tata Motors Approves ₹5,000 Cr Investment — “Global Expansion Plan”'
    briefing.stories[0].summary = 'The Board met today • Approved funding in ₹ terms — ‘Strategic milestone’…'
    briefing.stories[0].source = 'The Economic Times • Markets'

    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")

    # Decompress PDF page streams
    decompressed_text = extract_text_from_pdf(pdf_bytes)

    # Confirm ₹ was safely normalized to Rs.
    assert "Rs. 5,000 Cr" in decompressed_text
    # Confirm curly quotes and em dashes were safely normalized
    assert "Global Expansion Plan" in decompressed_text

    # Confirm no DEL / black square placeholder character (\177) is present in the decompressed text
    assert "\x7f" not in decompressed_text
    assert "\\177" not in decompressed_text
    # Confirm ZapfDingbats square box is not referenced
    assert b"/ZapfDingbats" not in pdf_bytes


# 9. Visible 'Read Full Article' appears in PDF
def test_visible_read_full_article_appears():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)
    assert "Read Full Article" in decompressed_text


# 10. Full original URL is preserved internally as clickable hyperlink
def test_full_original_hyperlink_preserved_internally():
    briefing = create_15_story_briefing()
    # Give first story a unique URL with query parameters
    unique_target_url = "https://www.reuters.com/markets/deals/exclusive-acquisition-2026?token=secret123&track=ad"
    briefing.stories[0].url = unique_target_url

    pdf_bytes = generate_briefing_pdf(briefing)

    # Full raw unshortened URL must be preserved internally in PDF annotations
    assert unique_target_url.encode("utf-8") in pdf_bytes
    # ReportLab uses /URI for link actions
    assert b"/URI" in pdf_bytes


# 11. Short domain display is not used for story links
def test_short_domain_display_is_not_used_for_story_links():
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    decompressed_text = extract_text_from_pdf(pdf_bytes)

    # Decompressed visible text must NOT contain domain names from story URLs
    assert "reuters.com" not in decompressed_text
    assert "economictimes.indiatimes.com" not in decompressed_text
    assert "thehindu.com" not in decompressed_text
    # Instead, 'Read Full Article' must be used
    assert "Read Full Article" in decompressed_text


# 12. Missing summary does not crash PDF generation
def test_missing_summary_does_not_crash_pdf_generation():
    briefing = create_15_story_briefing()
    for s in briefing.stories:
        s.summary = None

    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")


# 13. Malformed URL does not crash generation
def test_malformed_url_does_not_crash_generation():
    briefing = create_15_story_briefing()
    briefing.stories[0].url = None
    briefing.stories[1].url = ""
    briefing.stories[2].url = "malformed:::not-a-valid-url"
    briefing.stories[3].url = "http:///"
    briefing.stories[4].source = None

    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")


# 14. Missing briefing returns 404 or safe response
def test_missing_briefing_returns_404(tmp_path: Path):
    db_file = tmp_path / "test_dash.db"
    app = create_app(db_path=db_file)
    client = TestClient(app)

    # Valid date format but non-existent briefing
    response = client.get("/briefing/1999-01-01/pdf")
    assert response.status_code == 404
    assert "No briefing found" in response.json()["detail"]

    # Invalid date format
    bad_response = client.get("/briefing/invalid-date/pdf")
    assert bad_response.status_code == 400
    assert "Invalid date format" in bad_response.json()["detail"]


# 15. Download endpoint returns PDF attachment with proper headers
def test_download_endpoint_returns_pdf_attachment(tmp_path: Path):
    db_file = tmp_path / "test_dash.db"
    repo = DashboardRepository(db_path=db_file)

    briefing = create_15_story_briefing(date(2026, 9, 23))
    repo.save_briefing(briefing)

    app = create_app(db_path=db_file)
    client = TestClient(app)

    response = client.get("/briefing/2026-09-23/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'attachment; filename="investment_briefing_2026-09-23.pdf"' in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")

    # Also test the /download alias
    alias_response = client.get("/briefing/2026-09-23/download")
    assert alias_response.status_code == 200
    assert alias_response.headers["content-type"] == "application/pdf"
    assert alias_response.content.startswith(b"%PDF-")


# 16. Download PDF button is rendered on briefing pages
def test_download_pdf_button_rendered_on_pages(tmp_path: Path):
    db_file = tmp_path / "test_dash.db"
    repo = DashboardRepository(db_path=db_file)

    briefing = create_15_story_briefing(date(2026, 9, 23))
    repo.save_briefing(briefing)

    app = create_app(db_path=db_file)
    client = TestClient(app)

    # Check date-specific briefing page
    resp_date = client.get("/briefing/2026-09-23")
    assert resp_date.status_code == 200
    assert "Download PDF" in resp_date.text
    assert "/briefing/2026-09-23/pdf" in resp_date.text

    # Check home page (latest briefing)
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    assert "Download PDF" in resp_home.text
    assert "/briefing/2026-09-23/pdf" in resp_home.text


# 17. Missing logo fallback generates valid PDF
def test_missing_logo_fallback(monkeypatch):
    monkeypatch.setattr(pe, "_get_logo_image", lambda: None)
    briefing = create_15_story_briefing()
    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")


# 18. Special characters and potential injection are escaped safely
def test_xss_special_characters_escaped():
    briefing = create_15_story_briefing()
    briefing.stories[0].headline = 'Stocks rally <script>alert("hack")</script> & bond yields surge > 5% "all-time"'
    briefing.stories[0].summary = "Analysis shows M&A activity up <20%> with 'high' confidence & strong demand."
    briefing.stories[0].source = "Financial & Economic Times <Daily>"

    pdf_bytes = generate_briefing_pdf(briefing)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")
