"""
PDF export module for Plutus Wealth Management LLP - MarketPulse Briefing Dashboard.
Renders high-quality, professional Investment Committee / Market Briefing reports
visually harmonized with the MarketPulse dashboard design system.
"""

import html
import io
import logging
from pathlib import Path
import re
from typing import Optional
import unicodedata
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.dashboard.models import DashboardBriefing, DashboardStory

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_IMAGES_DIR = BASE_DIR / "static" / "images"

# MarketPulse Institutional Palette
COLOR_PRIMARY_GREEN = colors.HexColor("#0B4F35")
COLOR_DEEP_GREEN = colors.HexColor("#073D2A")
COLOR_ACCENT_GOLD = colors.HexColor("#B68A2C")
COLOR_SOFT_GOLD = colors.HexColor("#C9A84D")
COLOR_TEXT_MAIN = colors.HexColor("#17221C")
COLOR_TEXT_SUMMARY = colors.HexColor("#2D3732")
COLOR_TEXT_MUTED = colors.HexColor("#55635C")
COLOR_BORDER = colors.HexColor("#E3E9E5")
COLOR_BORDER_GOLD = colors.HexColor("#B68A2C")
COLOR_BG_CARD = colors.HexColor("#FFFFFF")
COLOR_DIVIDER = colors.HexColor("#E2E8E4")


def _safe_text(text: Optional[str]) -> str:
    """
    Sanitize and normalize text for ReportLab's built-in Helvetica font.
    Converts unsupported Unicode characters (e.g. Rupee symbol, smart quotes,
    dashes, bullets, ellipsis, non-breaking spaces) into safe ASCII equivalents,
    preventing black-square/box glyphs while preserving readable typography.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # 1. Currency replacements
    text = re.sub(r"₹\s*(?=[)\]}>,.;:!?])", "Rs.", text)
    text = re.sub(r"₹\s*", "Rs. ", text)
    text = text.replace("€", "EUR ").replace("£", "GBP ").replace("¥", "JPY ")

    # 2. Smart/curly quotes & apostrophes -> normal quotes
    for q in ("“", "”", "„", "‟", "«", "»", "″"):
        text = text.replace(q, '"')
    for q in ("‘", "’", "‚", "‛", "‹", "›", "′", "´", "`", "ʼ"):
        text = text.replace(q, "'")

    # 3. En dash, em dash, minus sign -> standard hyphen -
    for d in ("—", "–", "―", "−", "‒", "⁃", "﹣", "－"):
        text = text.replace(d, "-")

    # 4. Bullets & dot symbols -> hyphen -
    for b in ("•", "‣", "◦", "▪", "▫", "∙", "·"):
        text = text.replace(b, "-")

    # 5. Ellipsis -> three periods ...
    text = text.replace("…", "...")

    # 6. Non-breaking and special spaces -> standard space
    for sp in ("\u00a0", "\u202f", "\u2007", "\u2009", "\u200a", "\u3000"):
        text = text.replace(sp, " ")

    # 7. Remove zero-width / directional / invisible formatting characters
    for zw in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c"):
        text = text.replace(zw, "")

    # 8. Common trademark / copyright symbols
    text = text.replace("™", "(TM)").replace("©", "(c)").replace("®", "(R)")

    # 9. NFKC normalization
    text = unicodedata.normalize("NFKC", text)

    # 10. Filter unprintable/control characters (keep \t, \n, \r)
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cc" and ch not in "\t\n\r":
            continue
        if cat == "Cf":
            continue
        cleaned.append(ch)
    text = "".join(cleaned)

    # 11. Final pass: ensure characters can be encoded in latin-1 / standard ASCII
    safe_chars = []
    for ch in text:
        try:
            ch.encode("latin-1")
            safe_chars.append(ch)
        except UnicodeEncodeError:
            decomposed = unicodedata.normalize("NFKD", ch)
            ascii_equiv = "".join(c for c in decomposed if not unicodedata.combining(c))
            try:
                ascii_equiv.encode("latin-1")
                safe_chars.append(ascii_equiv)
            except UnicodeEncodeError:
                safe_chars.append(" ")
    return "".join(safe_chars)


def get_short_display_url(url: Optional[str]) -> str:

    """
    Extract a clean canonical publisher domain for compact display.
    Strips schemes (http, https), www prefix, query parameters, fragments, and paths.

    Examples:
        https://www.reuters.com/world/us/example-story-2026-09-23/ -> reuters.com
        https://economictimes.indiatimes.com/markets/example -> economictimes.indiatimes.com
        https://www.business-standard.com/companies/news/example -> business-standard.com
    """
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip()
    if not clean:
        return ""

    # Strip scheme if present
    if clean.startswith("http://"):
        clean = clean[7:]
    elif clean.startswith("https://"):
        clean = clean[8:]
    elif clean.startswith("//"):
        clean = clean[2:]

    # Strip leading www.
    if clean.lower().startswith("www."):
        clean = clean[4:]

    # Strip query parameters and fragments
    clean = clean.split("?")[0].split("#")[0]

    # Strip trailing path
    domain = clean.split("/")[0].strip().lower()

    # Strip port if present
    if ":" in domain:
        domain = domain.split(":")[0].strip()

    return domain or clean[:30]


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas for dynamic total page count in footers ('Page X of Y').
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Helvetica", 7.5)
        self.setFillColor(COLOR_TEXT_MUTED)

        # Subtle divider line above footer
        self.setStrokeColor(COLOR_BORDER)
        self.setLineWidth(0.75)
        page_width, _ = A4
        left_margin = 36
        right_margin = page_width - 36
        footer_y = 26

        self.line(left_margin, footer_y, right_margin, footer_y)

        # Footer labels
        left_text = "Investment Committee Daily Briefing  |  MarketPulse Dashboard"
        right_text = f"Page {self._pageNumber} of {total_pages}"
        self.drawString(left_margin, footer_y - 11, left_text)
        self.drawRightString(right_margin, footer_y - 11, right_text)

        self.restoreState()


def _get_logo_image() -> Optional[Image]:
    """Retrieve existing dashboard logo asset safely if available."""
    candidates = [
        STATIC_IMAGES_DIR / "marketpulse_logo.png",
        STATIC_IMAGES_DIR / "logo.png",
        STATIC_IMAGES_DIR / "logo_circle.png",
    ]
    for path in candidates:
        if path.exists():
            try:
                # Render logo at 42x42 pt
                return Image(str(path), width=42, height=42)
            except Exception as e:
                logger.warning(f"Failed to load logo image {path}: {e}")
    return None


def generate_briefing_pdf(briefing: DashboardBriefing) -> bytes:
    """
    Generate a professional A4 Investment Committee Briefing PDF from stored dashboard data.

    Contains:
    - MarketPulse visual identity and header
    - Formatted briefing title and date
    - All 15 stories across 3 required sections:
        * TOP 5 DOMESTIC HEADLINES
        * TOP 5 INDIA BUSINESS HEADLINES
        * TOP 5 INTERNATIONAL BUSINESS HEADLINES
    - Headline, summary, source, and short clickable hyperlink for every story
    - Two-pass pagination footer ("Page X of Y")
    """
    buffer = io.BytesIO()

    # A4 dimensions: 595.27 x 841.89 points
    # 36 pt (0.5 in) left/right margins -> usable width = 523.27 pt
    left_margin = 36
    right_margin = 36
    top_margin = 36
    bottom_margin = 42

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title="MarketPulse Investment Committee Briefing",
        author="MarketPulse Intelligence",
    )

    styles = getSampleStyleSheet()

    # Custom typography styles
    style_brand_tag = ParagraphStyle(
        "BrandTag",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=COLOR_ACCENT_GOLD,
        textTransform="uppercase",
        spaceAfter=1,
    )

    style_title = ParagraphStyle(
        "BriefingTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=COLOR_DEEP_GREEN,
        spaceAfter=2,
    )

    style_date = ParagraphStyle(
        "BriefingDate",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=COLOR_TEXT_MAIN,
        spaceAfter=1,
    )

    style_subtitle = ParagraphStyle(
        "BriefingSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=COLOR_TEXT_MUTED,
    )

    style_section_title = ParagraphStyle(
        "SectionTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.white,
    )

    style_section_badge = ParagraphStyle(
        "SectionBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=COLOR_SOFT_GOLD,
        alignment=2,  # Right-aligned
    )

    style_headline = ParagraphStyle(
        "StoryHeadline",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.2,
        leading=12.2,
        textColor=COLOR_TEXT_MAIN,
        spaceAfter=2.5,
    )

    style_summary = ParagraphStyle(
        "StorySummary",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.0,
        leading=11.0,
        textColor=COLOR_TEXT_SUMMARY,
        spaceAfter=2.5,
    )

    style_meta = ParagraphStyle(
        "StoryMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=9.5,
        textColor=COLOR_TEXT_MUTED,
    )

    story_elements = []

    # ---------------------------------------------------------
    # 1. HEADER SECTION (Brand & Briefing Title)
    # ---------------------------------------------------------
    logo_img = _get_logo_image()
    usable_width = 595.27 - (left_margin + right_margin)  # 523.27 pt

    formatted_date = (
        briefing.briefing_date.strftime("%A, %d %B %Y")
        if briefing.briefing_date
        else "Daily Briefing"
    )

    header_text_cells = [
        Paragraph("MARKETPULSE &nbsp;|&nbsp; INVESTMENT INTELLIGENCE", style_brand_tag),
        Paragraph("Investment Committee Daily Briefing", style_title),
        Paragraph(html.escape(_safe_text(formatted_date)), style_date),
        Paragraph("Domestic &nbsp;|&nbsp; India Business &nbsp;|&nbsp; International Business", style_subtitle),
    ]

    if logo_img:
        logo_col_w = 48
        text_col_w = usable_width - logo_col_w
        header_table = Table([[logo_img, header_text_cells]], colWidths=[logo_col_w, text_col_w])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
            ])
        )
    else:
        header_table = Table([[header_text_cells]], colWidths=[usable_width])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )

    story_elements.append(header_table)
    # Gold decorative accent rule
    story_elements.append(
        HRFlowable(
            width="100%",
            thickness=2,
            color=COLOR_ACCENT_GOLD,
            spaceBefore=6,
            spaceAfter=10,
        )
    )

    # ---------------------------------------------------------
    # 2. STORY SECTIONS (Domestic -> India -> International)
    # ---------------------------------------------------------
    section_configs = [
        ("TOP 5 DOMESTIC HEADLINES", briefing.domestic_stories),
        ("TOP 5 INDIA BUSINESS HEADLINES", briefing.india_stories),
        ("TOP 5 INTERNATIONAL BUSINESS HEADLINES", briefing.international_stories),
    ]

    for section_idx, (section_title, stories) in enumerate(section_configs):
        # Section Header Banner Table
        sec_left = Paragraph(section_title, style_section_title)
        count_label = f"{len(stories)} Stories" if stories else "0 Stories"
        sec_right = Paragraph(count_label, style_section_badge)

        banner_table = Table(
            [[sec_left, sec_right]],
            colWidths=[usable_width - 70, 70],
        )
        banner_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_DEEP_GREEN),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 1.5, COLOR_BORDER_GOLD),
            ])
        )

        section_flowables = [banner_table, Spacer(1, 5)]

        # Render stories
        story_count = len(stories)
        for idx, story in enumerate(stories, start=1):
            pos_num = story.position if story.position else idx
            escaped_headline = html.escape(_safe_text(story.headline or "Untitled Headline"))
            headline_html = f"<b>{pos_num}. {escaped_headline}</b>"

            headline_flowable = Paragraph(headline_html, style_headline)
            card_items = [headline_flowable]

            if story.summary and story.summary.strip():
                escaped_summary = html.escape(_safe_text(story.summary.strip()))
                card_items.append(Paragraph(escaped_summary, style_summary))

            source_name = html.escape(_safe_text((story.source or "Unknown").strip()))
            raw_display_url = get_short_display_url(story.url)
            escaped_display_url = html.escape(_safe_text(raw_display_url))

            if story.url and story.url.strip():
                escaped_raw_url = html.escape(story.url.strip(), quote=True)
                link_html = (
                    f'Source: <b>{source_name}</b> &nbsp;|&nbsp; '
                    f'<a href="{escaped_raw_url}" color="#0B4F35"><u>{escaped_display_url}</u></a>'
                )
            else:
                link_html = f"Source: <b>{source_name}</b>"

            card_items.append(Paragraph(link_html, style_meta))

            # Light divider between stories
            if idx < story_count:
                card_items.append(
                    HRFlowable(
                        width="100%",
                        thickness=0.5,
                        color=COLOR_DIVIDER,
                        spaceBefore=3,
                        spaceAfter=4,
                    )
                )
            else:
                card_items.append(Spacer(1, 6))

            # Keep each story together so it doesn't break awkwardly across pages
            section_flowables.append(KeepTogether(card_items))

        story_elements.extend(section_flowables)

        # Spacing between sections (if not last)
        if section_idx < len(section_configs) - 1:
            story_elements.append(Spacer(1, 6))

    # Build PDF using NumberedCanvas
    doc.build(story_elements, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
