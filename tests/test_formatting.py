"""
Tests for Section 11 — BriefingFormatter.

Verifies that the formatter produces output that exactly matches the
required Investment Committee briefing structure:

    *INVESTMENT COMMITTEE BRIEFING*
    *[Day], [Date], [Year]*

    *TOP 5 INDIA BUSINESS HEADLINES*

    *Headline*
    Source: Publication
    Direct URL

    ... (5 India stories)

    *TOP 5 INTERNATIONAL BUSINESS HEADLINES*

    *Headline*
    Source: Publication
    Direct URL

    ... (5 International stories)

Rules asserted:
- Exactly 5 India + 5 International stories (raises ValueError otherwise).
- Single asterisks only — no double-asterisk ``**`` anywhere in output.
- No markdown headings (``#``, ``##`` …).
- No bullets, no numbering.
- No commentary, no introduction, no conclusion.
- URLs are reproduced verbatim.
- Ordinal date suffix is correct for all day-of-month values.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.formatting.formatter import BriefingFormatter, FormattedBriefing, MalformedHeadlineError


# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_story(
    section: str,
    headline: str,
    source: str,
    url: str,
    event_id: str = "evt-001",
) -> EditorialStorySelection:
    return EditorialStorySelection(
        section=section,
        event_id=event_id,
        headline=headline,
        source=source,
        url=url,
    )


def _india_stories(n: int = 5) -> list[EditorialStorySelection]:
    stories = []
    companies = [
        ("Reliance Industries posts ₹19,878 cr Q1 net profit, up 12%", "Business Standard",
         "https://www.business-standard.com/article/reliance-q1-2025"),
        ("HDFC Bank net profit rises 35% YoY to ₹16,175 cr in Q1 FY26", "Economic Times",
         "https://economictimes.indiatimes.com/hdfc-bank-q1-2025"),
        ("Adani Green raises $1.2 bn via dollar bonds at 6.7% coupon", "Livemint",
         "https://www.livemint.com/adani-green-bonds-2025"),
        ("SEBI bans Quant Mutual Fund from new subscriptions for 30 days", "Financial Express",
         "https://www.financialexpress.com/sebi-quant-ban-2025"),
        ("Tata Motors EV sales jump 48% in June quarter; guides for record FY26", "Business Standard",
         "https://www.business-standard.com/tata-motors-ev-june-2025"),
    ]
    for i in range(n):
        h, s, u = companies[i % len(companies)]
        stories.append(_make_story("india", h, s, u, event_id=f"ind-{i+1:03d}"))
    return stories


def _intl_stories(n: int = 5) -> list[EditorialStorySelection]:
    stories = []
    companies = [
        ("Apple Q3 revenue $94.9 bn beats estimates; iPhone sales up 6%", "Reuters",
         "https://www.reuters.com/apple-q3-2025"),
        ("Microsoft acquires Nuance competitor Suki AI for $3.4 bn", "Bloomberg",
         "https://www.bloomberg.com/microsoft-suki-acquisition-2025"),
        ("Fed holds rates at 5.25–5.50%; Powell signals two cuts in 2025", "CNBC",
         "https://www.cnbc.com/fed-holds-rates-2025"),
        ("LVMH Q2 organic sales fall 2%, dragged by Asia slowdown", "Financial Times",
         "https://www.ft.com/lvmh-q2-asia-2025"),
        ("Chevron posts $6.3 bn Q2 profit as oil prices stabilise above $80", "Wall Street Journal",
         "https://www.wsj.com/chevron-q2-2025"),
    ]
    for i in range(n):
        h, s, u = companies[i % len(companies)]
        stories.append(_make_story("international", h, s, u, event_id=f"intl-{i+1:03d}"))
    return stories


def _make_payload(
    india_n: int = 5,
    intl_n: int = 5,
) -> BriefingEditorialPayload:
    return BriefingEditorialPayload(
        india_stories=_india_stories(india_n),
        international_stories=_intl_stories(intl_n),
    )


FIXED_DATE = date(2025, 8, 18)  # Monday


@pytest.fixture
def formatter() -> BriefingFormatter:
    return BriefingFormatter()


@pytest.fixture
def full_payload() -> BriefingEditorialPayload:
    return _make_payload()


@pytest.fixture
def briefing(formatter, full_payload) -> FormattedBriefing:
    return formatter.format(full_payload, briefing_date=FIXED_DATE)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Structural / count tests
# ──────────────────────────────────────────────────────────────────────────────

class TestStructure:
    """The output must contain the required header blocks and story blocks."""

    def test_returns_formatted_briefing_instance(self, briefing):
        assert isinstance(briefing, FormattedBriefing)

    def test_text_is_non_empty(self, briefing):
        assert len(briefing.text.strip()) > 0

    def test_india_count_stored(self, briefing):
        assert briefing.india_count == 5

    def test_international_count_stored(self, briefing):
        assert briefing.international_count == 5

    def test_briefing_date_stored(self, briefing):
        assert briefing.briefing_date == FIXED_DATE

    def test_header_line_present(self, briefing):
        assert "*INVESTMENT COMMITTEE BRIEFING*" in briefing.text

    def test_india_section_header_present(self, briefing):
        assert "*TOP 5 INDIA BUSINESS HEADLINES*" in briefing.text

    def test_international_section_header_present(self, briefing):
        assert "*TOP 5 INTERNATIONAL BUSINESS HEADLINES*" in briefing.text

    def test_date_header_present(self, briefing):
        # Date header line must be present and wrapped in single asterisks
        assert "*Monday, 18th August, 2025*" in briefing.text

    def test_india_section_before_international(self, briefing):
        india_pos = briefing.text.index("*TOP 5 INDIA BUSINESS HEADLINES*")
        intl_pos = briefing.text.index("*TOP 5 INTERNATIONAL BUSINESS HEADLINES*")
        assert india_pos < intl_pos

    def test_briefing_header_before_india_section(self, briefing):
        header_pos = briefing.text.index("*INVESTMENT COMMITTEE BRIEFING*")
        india_pos = briefing.text.index("*TOP 5 INDIA BUSINESS HEADLINES*")
        assert header_pos < india_pos


# ──────────────────────────────────────────────────────────────────────────────
# 2. Format rules — asterisks, bullets, numbering, markdown
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatRules:
    """Strict formatting contract checks."""

    def test_no_double_asterisks(self, briefing):
        assert "**" not in briefing.text, (
            "Output must NOT contain double-asterisk markdown bold."
        )

    def test_no_markdown_headings(self, briefing):
        for line in briefing.text.splitlines():
            stripped = line.lstrip()
            assert not stripped.startswith("#"), (
                f"Markdown heading found in line: {line!r}"
            )

    def test_no_bullet_characters(self, briefing):
        """Lines must not start with '- ', '• ', or '* ' (bullet-style)."""
        for line in briefing.text.splitlines():
            stripped = line.lstrip()
            # Asterisk-wrapped headline lines start and end with *, which is fine.
            # Reject lines that are ONLY a leading bullet.
            assert not re.match(r"^[-•]\s", stripped), (
                f"Bullet line found: {line!r}"
            )

    def test_no_numbered_list_items(self, briefing):
        for line in briefing.text.splitlines():
            stripped = line.lstrip()
            assert not re.match(r"^\d+[.)]\s", stripped), (
                f"Numbered list item found: {line!r}"
            )

    def test_source_prefix_present_for_every_story(self, briefing):
        source_lines = [
            ln for ln in briefing.text.splitlines()
            if ln.startswith("Source: ")
        ]
        assert len(source_lines) == 10, (
            f"Expected 10 'Source:' lines (5 India + 5 Intl), got {len(source_lines)}."
        )

    def test_no_extra_text_before_header(self, briefing):
        """The very first non-empty line must be the briefing header."""
        first_non_empty = next(
            ln for ln in briefing.text.splitlines() if ln.strip()
        )
        assert first_non_empty == "*INVESTMENT COMMITTEE BRIEFING*"

    def test_no_trailing_commentary(self, briefing):
        """
        The last non-empty line in the briefing must be a URL
        (the final story's direct URL line).
        """
        last_non_empty = next(
            ln for ln in reversed(briefing.text.splitlines()) if ln.strip()
        )
        assert last_non_empty.startswith("https://") or last_non_empty.startswith("http://"), (
            f"Last line is not a URL: {last_non_empty!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 3. URL fidelity
# ──────────────────────────────────────────────────────────────────────────────

class TestURLFidelity:
    """URLs must appear verbatim and unchanged in the output."""

    def test_india_urls_reproduced_verbatim(self, formatter, full_payload):
        result = formatter.format(full_payload, briefing_date=FIXED_DATE)
        for story in full_payload.india_stories:
            assert story.url in result.text, (
                f"India URL missing or mutated: {story.url}"
            )

    def test_international_urls_reproduced_verbatim(self, formatter, full_payload):
        result = formatter.format(full_payload, briefing_date=FIXED_DATE)
        for story in full_payload.international_stories:
            assert story.url in result.text, (
                f"International URL missing or mutated: {story.url}"
            )

    def test_url_appears_on_its_own_line(self, formatter, full_payload):
        """Each URL must occupy an entire line — not embedded mid-sentence."""
        result = formatter.format(full_payload, briefing_date=FIXED_DATE)
        url_lines = {
            ln.strip()
            for ln in result.text.splitlines()
            if ln.strip().startswith("http")
        }
        all_urls = {s.url for s in full_payload.india_stories} | {
            s.url for s in full_payload.international_stories
        }
        for url in all_urls:
            assert url in url_lines, f"URL not on its own line: {url}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Headline rejection — malformed upstream AI output
# ──────────────────────────────────────────────────────────────────────────────

class TestHeadlineRejection:
    """
    Malformed structural markdown from upstream AI must raise
    MalformedHeadlineError rather than being silently repaired.

    Permitted: leading/trailing whitespace (silently stripped).
    Forbidden: **, __, ##, leading '- ', leading '1. ', leading '1) ',
               leading '• '.
    """

    def _make_payload_with_headline(self, raw_headline: str) -> BriefingEditorialPayload:
        """Return a full 5+5 payload whose first India story has raw_headline."""
        stories = _india_stories(5)
        stories[0] = _make_story(
            "india", raw_headline, "Business Standard",
            "https://www.business-standard.com/test",
        )
        return BriefingEditorialPayload(
            india_stories=stories,
            international_stories=_intl_stories(5),
        )

    # ── Each forbidden pattern must raise MalformedHeadlineError ─────────────

    def test_double_asterisk_bold_rejected(self, formatter):
        """'**Reliance profit rises 15%**' must be rejected, not silently fixed."""
        payload = self._make_payload_with_headline("**Reliance profit rises 15%**")
        with pytest.raises(MalformedHeadlineError, match="double-asterisk"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_double_asterisk_mid_headline_rejected(self, formatter):
        """Bold applied to part of the headline is equally forbidden."""
        payload = self._make_payload_with_headline(
            "Reliance posts **record** Q1 profit of ₹19,878 cr"
        )
        with pytest.raises(MalformedHeadlineError, match="double-asterisk"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_double_underscore_rejected(self, formatter):
        """'__Fed holds rates__' must be rejected."""
        payload = self._make_payload_with_headline("__Fed holds rates at 5.25%__")
        with pytest.raises(MalformedHeadlineError, match="double-underscore"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_markdown_heading_hash_rejected(self, formatter):
        """'## HDFC Bank results' must be rejected."""
        payload = self._make_payload_with_headline("## HDFC Bank net profit up 35%")
        with pytest.raises(MalformedHeadlineError, match="heading marker"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_leading_dash_bullet_rejected(self, formatter):
        """'- Tata Motors EV sales up 48%' must be rejected."""
        payload = self._make_payload_with_headline("- Tata Motors EV sales up 48%")
        with pytest.raises(MalformedHeadlineError, match="bullet marker"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_leading_unicode_bullet_rejected(self, formatter):
        """'• SEBI bans Quant Fund' must be rejected."""
        payload = self._make_payload_with_headline("• SEBI bans Quant Mutual Fund")
        with pytest.raises(MalformedHeadlineError, match="bullet marker"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_numbered_list_dot_rejected(self, formatter):
        """'1. *Reliance profit rises 15%*' must be rejected — not silently become '*Reliance profit rises 15%*'."""
        payload = self._make_payload_with_headline("1. *Reliance profit rises 15%*")
        with pytest.raises(MalformedHeadlineError, match="numbered-list"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_numbered_list_paren_rejected(self, formatter):
        """'2) Microsoft acquires Suki AI' must be rejected."""
        payload = self._make_payload_with_headline("2) Microsoft acquires Suki AI for $3.4 bn")
        with pytest.raises(MalformedHeadlineError, match="numbered-list"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    # ── MalformedHeadlineError carries structured metadata ───────────────────

    def test_error_carries_headline_attribute(self, formatter):
        payload = self._make_payload_with_headline("**Adani Green raises $1.2 bn**")
        with pytest.raises(MalformedHeadlineError) as exc_info:
            formatter.format(payload, briefing_date=FIXED_DATE)
        assert exc_info.value.headline == "**Adani Green raises $1.2 bn**"

    def test_error_carries_reason_attribute(self, formatter):
        payload = self._make_payload_with_headline("**Adani Green raises $1.2 bn**")
        with pytest.raises(MalformedHeadlineError) as exc_info:
            formatter.format(payload, briefing_date=FIXED_DATE)
        assert exc_info.value.reason  # non-empty string

    def test_error_message_contains_offending_headline(self, formatter):
        payload = self._make_payload_with_headline("## Chevron Q2 profit $6.3 bn")
        with pytest.raises(MalformedHeadlineError) as exc_info:
            formatter.format(payload, briefing_date=FIXED_DATE)
        assert "## Chevron Q2 profit $6.3 bn" in str(exc_info.value)

    # ── Clean headlines must still be accepted ───────────────────────────────

    def test_clean_headline_accepted(self, formatter):
        """A headline with no markdown artefacts must produce output normally."""
        payload = self._make_payload_with_headline(
            "Reliance posts \u20b919,878 cr Q1 net profit, up 12%"
        )
        result = formatter.format(payload, briefing_date=FIXED_DATE)
        assert "*Reliance posts \u20b919,878 cr Q1 net profit, up 12%*" in result.text

    def test_headline_with_leading_whitespace_accepted(self, formatter):
        """Whitespace-only normalisation is still permitted silently."""
        payload = self._make_payload_with_headline(
            "  HDFC Bank net profit \u20b916,175 cr, up 35%  "
        )
        result = formatter.format(payload, briefing_date=FIXED_DATE)
        assert "*HDFC Bank net profit \u20b916,175 cr, up 35%*" in result.text

    def test_headline_with_single_asterisk_in_middle_accepted(self, formatter):
        """
        A bare single asterisk mid-headline (rare but possible) is not a
        structural artefact — it must not be rejected.
        """
        payload = self._make_payload_with_headline(
            "Tata Motors* EV sales up 48% in Q1"
        )
        # This should NOT raise — single asterisks are the format's own markup
        # and a lone * inside headline text is not a forbidden pattern.
        result = formatter.format(payload, briefing_date=FIXED_DATE)
        assert "Tata Motors* EV sales up 48% in Q1" in result.text


# ──────────────────────────────────────────────────────────────────────────────
# 5. Ordinal date formatting
# ──────────────────────────────────────────────────────────────────────────────

class TestOrdinalDate:
    """The ordinal suffix for the day-of-month must be correct."""

    @pytest.mark.parametrize("day, expected_suffix", [
        (1, "st"),
        (2, "nd"),
        (3, "rd"),
        (4, "th"),
        (10, "th"),
        (11, "th"),   # teen — always 'th'
        (12, "th"),   # teen
        (13, "th"),   # teen
        (21, "st"),
        (22, "nd"),
        (23, "rd"),
        (31, "st"),
    ])
    def test_ordinal_suffix(self, formatter, day, expected_suffix):
        assert formatter._ordinal_suffix(day) == expected_suffix

    def test_date_in_output_uses_ordinal(self, formatter):
        d = date(2025, 1, 1)  # 1st January
        result = formatter.format(_make_payload(), briefing_date=d)
        assert "1st January" in result.text

    def test_month_and_year_in_output(self, formatter):
        result = formatter.format(_make_payload(), briefing_date=FIXED_DATE)
        assert "August" in result.text
        assert "2025" in result.text

    def test_day_name_in_output(self, formatter):
        result = formatter.format(_make_payload(), briefing_date=FIXED_DATE)
        assert "Monday" in result.text

    def test_date_2026_08_18_is_tuesday(self, formatter):
        """
        2026-08-18 is a Tuesday.

        The weekday name MUST be derived from the date object itself
        (via strftime), never hardcoded independently of the date.
        This test verifies both the day name and the full header string.
        """
        d = date(2026, 8, 18)
        # Verify our assumption: Python's date arithmetic confirms the weekday.
        assert d.strftime("%A") == "Tuesday", (
            "Sanity check: 2026-08-18 must be a Tuesday according to Python's calendar."
        )
        result = formatter.format(_make_payload(), briefing_date=d)
        assert "*Tuesday, 18th August, 2026*" in result.text, (
            f"Expected '*Tuesday, 18th August, 2026*' in briefing header.\n"
            f"Actual text:\n{result.text[:200]}"
        )

# ──────────────────────────────────────────────────────────────────────────────
# 6. Error handling — wrong story counts
# ──────────────────────────────────────────────────────────────────────────────

class TestErrorHandling:
    """Formatter must reject payloads that don't have exactly 5 + 5 stories."""

    def test_raises_if_fewer_than_5_india(self, formatter):
        payload = _make_payload(india_n=4, intl_n=5)
        with pytest.raises(ValueError, match="5 India stories"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_raises_if_more_than_5_india(self, formatter):
        # Manually append a duplicate to bypass payload-level validation
        payload = _make_payload(india_n=5, intl_n=5)
        object.__setattr__(
            payload,
            "india_stories",
            list(payload.india_stories) + [_india_stories(1)[0]],
        )
        with pytest.raises(ValueError, match="5 India stories"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_raises_if_fewer_than_5_international(self, formatter):
        payload = _make_payload(india_n=5, intl_n=3)
        with pytest.raises(ValueError, match="5 International stories"):
            formatter.format(payload, briefing_date=FIXED_DATE)

    def test_raises_if_more_than_5_international(self, formatter):
        payload = _make_payload(india_n=5, intl_n=5)
        object.__setattr__(
            payload,
            "international_stories",
            list(payload.international_stories) + [_intl_stories(1)[0]],
        )
        with pytest.raises(ValueError, match="5 International stories"):
            formatter.format(payload, briefing_date=FIXED_DATE)


# ──────────────────────────────────────────────────────────────────────────────
# 7. Exact structure comparison (character-level contract test)
# ──────────────────────────────────────────────────────────────────────────────

class TestExactStructure:
    """
    Build a fully deterministic payload and compare the output to a
    hand-crafted expected string.  This is the most strict test —
    any deviation in spacing, asterisks, or line ordering fails here.
    """

    # ── Deterministic test data ──────────────────────────────────────────────
    _INDIA = [
        ("Reliance Q1 profit ₹19,878 cr, up 12% YoY", "Business Standard",
         "https://bs.com/rel-q1"),
        ("HDFC Bank net profit ₹16,175 cr, up 35% in Q1 FY26", "Economic Times",
         "https://et.com/hdfc-q1"),
        ("Adani Green raises $1.2 bn via 6.7% dollar bonds", "Livemint",
         "https://mint.com/adani-bonds"),
        ("SEBI bans Quant Mutual Fund from new subscriptions 30 days", "Financial Express",
         "https://fe.com/sebi-quant"),
        ("Tata Motors EV sales jump 48% in Q1; guides for record FY26", "Business Standard",
         "https://bs.com/tata-ev"),
    ]
    _INTL = [
        ("Apple Q3 revenue $94.9 bn beats estimates; iPhone sales +6%", "Reuters",
         "https://reuters.com/apple-q3"),
        ("Microsoft acquires Suki AI for $3.4 bn", "Bloomberg",
         "https://bloomberg.com/msft-suki"),
        ("Fed holds rates at 5.25–5.50%; Powell signals two 2025 cuts", "CNBC",
         "https://cnbc.com/fed-holds"),
        ("LVMH Q2 organic sales fall 2% on Asia slowdown", "Financial Times",
         "https://ft.com/lvmh-q2"),
        ("Chevron Q2 profit $6.3 bn as oil stabilises above $80", "Wall Street Journal",
         "https://wsj.com/chevron-q2"),
    ]

    @pytest.fixture
    def det_payload(self) -> BriefingEditorialPayload:
        india = [
            _make_story("india", h, s, u, event_id=f"i{i}")
            for i, (h, s, u) in enumerate(self._INDIA)
        ]
        intl = [
            _make_story("international", h, s, u, event_id=f"x{i}")
            for i, (h, s, u) in enumerate(self._INTL)
        ]
        return BriefingEditorialPayload(india_stories=india, international_stories=intl)

    def test_exact_output(self, formatter, det_payload):
        fixed_date = date(2025, 8, 18)  # Monday
        result = formatter.format(det_payload, briefing_date=fixed_date)

        india_block = "\n".join(
            f"*{h}*\nSource: {s}\n{u}\n"
            for h, s, u in self._INDIA
        )
        intl_block = "\n".join(
            f"*{h}*\nSource: {s}\n{u}\n"
            for h, s, u in self._INTL
        )

        expected = (
            "*INVESTMENT COMMITTEE BRIEFING*\n"
            "*Monday, 18th August, 2025*\n"
            "\n"
            "*TOP 5 INDIA BUSINESS HEADLINES*\n"
            "\n"
            + india_block
            + "\n"
            "*TOP 5 INTERNATIONAL BUSINESS HEADLINES*\n"
            "\n"
            + intl_block
        ).rstrip()

        assert result.text == expected, (
            "Formatter output does not match the required format exactly.\n"
            f"--- EXPECTED ---\n{expected}\n"
            f"--- ACTUAL ---\n{result.text}"
        )
