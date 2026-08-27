"""
Briefing Formatter — Section 11.

Produces the *exact* WhatsApp-ready Investment Committee briefing text.

Output contract (character-perfect):

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

Rules enforced:
- Single asterisks only (never double).
- No bullets, no numbering, no markdown headings.
- No commentary, no introduction, no conclusion, no additional text.
- Generated 100 % by deterministic Python — Gemini is NOT used here.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import List, Optional

from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.formatting.url_shortener import get_display_url
from app.logging_config import get_logger

logger = get_logger(__name__)

# Known mojibake mappings for text display cleaning (CP437, Windows-1252, and UTF-8 double-encoding)
MOJIBAKE_MAP = {
    # CP437 decodings of UTF-8 byte sequences
    "ΓÇÖ": "’",
    "ΓÇÿ": "‘",
    "ΓÇ£": "“",
    "ΓÇ¥": "”",
    "ΓÇö": "—",
    "ΓÇô": "–",
    "ΓÇª": "…",
    "Γé╣": "₹",
    "ΓÇ¢": "'",
    "ΓÇó": "•",
    "ΓÇ": "",
    # Windows-1252 / ISO-8859-1 decodings of UTF-8 byte sequences
    "â€™": "’",
    "â€˜": "‘",
    "â€œ": "“",
    "â€ ": "”",
    "â€\x9d": "”",
    "â€”": "—",
    "â€“": "–",
    "â‚¹": "₹",
    "â€¦": "…",
    "â€¢": "•",
    # Double-encoded UTF-8 sequences
    "Ã¢â‚¬â„¢": "’",
    "Ã¢â‚¬Ëœ": "‘",
    "Ã¢â‚¬Å“": "“",
    "Ã¢â‚¬Â ": "”",
    "Ã¢â‚¬â€ ": "—",
    "Ã¢â‚¬â€œ": "–",
    "Ã¢â€šÂ¹": "₹",
    "Ã¢â‚¬Â¦": "…",
    "Â": "",
}

# Presentation-only source handle / label mapping
SOURCE_DISPLAY_MAP = {
    "@bsindia": "Business Standard",
    "bsindia": "Business Standard",
    "@the_hindu": "The Hindu",
    "the_hindu": "The Hindu",
    "@moneycontrolcom": "Moneycontrol",
    "moneycontrolcom": "Moneycontrol",
    "@ndtv": "NDTV",
    "@economictimes": "The Economic Times",
    "@livemint": "Livemint",
    "@financialxpress": "Financial Express",
    "@reuters": "Reuters",
    "@bloomberg": "Bloomberg",
    "@cnbc": "CNBC",
    "@wsj": "The Wall Street Journal",
}

# Trailing site-title / publication suffixes to safely strip for display
TITLE_SUFFIX_PATTERN = re.compile(
    r"\s*(?:[-–—|]\s*(?:Moneycontrol(?:\.com)?|India News|Business News|Reuters|Bloomberg|Mint|Livemint|The Economic Times|Economic Times|Business Standard|The Hindu|NDTV Profit|NDTV|Financial Express|Times of India|The Times of India|CNBC|BBC News|BBC|AP News|Associated Press|MarketWatch|Financial Times|WSJ|Wall Street Journal))\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MalformedHeadlineError(ValueError):
    """
    Raised when a headline received from an upstream AI layer contains
    structural markdown artefacts that the formatter must NOT silently repair.

    Examples of forbidden patterns:
        ``**Reliance profit rises 15%**``   — double-asterisk bold
        ``## HDFC Bank results``             — markdown heading
        ``- Tata Motors EV sales up 48%``   — leading bullet
        ``1. Apple revenue beats``           — numbered-list prefix
        ``1) Microsoft acquires Nuance``     — numbered-list (parenthesis)
        ``• SEBI bans Quant Fund``           — Unicode bullet
        ``__Fed holds rates__``              — double-underscore bold/italic

    Minor whitespace (leading/trailing spaces) is still normalised silently.
    """

    def __init__(self, headline: str, reason: str) -> None:
        self.headline = headline
        self.reason = reason
        super().__init__(
            f"Malformed headline rejected — {reason}. "
            f"Headline received: {headline!r}"
        )


# ---------------------------------------------------------------------------
# Public data-transfer object
# ---------------------------------------------------------------------------


class FormattedBriefing:
    """Holds the final briefing text and lightweight validation metadata."""

    def __init__(
        self,
        text: str,
        briefing_date: date,
        india_count: int,
        international_count: int,
        domestic_count: int = 0,
    ) -> None:
        self.text: str = text
        self.briefing_date: date = briefing_date
        self.domestic_count: int = domestic_count
        self.india_count: int = india_count
        self.international_count: int = international_count
        self.story_count: int = domestic_count + india_count + international_count

    @property
    def total_count(self) -> int:
        return self.domestic_count + self.india_count + self.international_count

    def __str__(self) -> str:  # pragma: no cover
        return self.text


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class BriefingFormatter:
    """
    Converts a validated ``BriefingEditorialPayload`` into the exact
    Investment Committee WhatsApp text format.

    This class is intentionally stateless and side-effect-free so that it
    can be unit-tested without any I/O.
    """

    # Ordinal suffixes for day-of-month formatting
    _ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd"}

    @classmethod
    def clean_text(cls, text: str) -> str:
        """
        Deterministic text cleaner for display layer:
        1. HTML unescape (&amp; -> &, &#039; -> ', &quot; -> ", etc.)
        2. Mojibake normalization pass 1 (ΓÇÖ -> ’, Γé╣ -> ₹, etc.)
        3. Unicode normalization (NFKC)
        4. Mojibake normalization pass 2
        5. Whitespace cleanup (collapse tabs/newlines and redundant spaces)
        """
        if not text:
            return ""

        # 1. HTML unescape
        cleaned = html.unescape(text)

        # 2. Mojibake normalization pass 1
        for bad_seq, good_char in MOJIBAKE_MAP.items():
            if bad_seq in cleaned:
                cleaned = cleaned.replace(bad_seq, good_char)

        # 3. Unicode normalization
        cleaned = unicodedata.normalize("NFKC", cleaned)

        # 4. Mojibake normalization pass 2 (catches any post-NFKC artifacts)
        for bad_seq, good_char in MOJIBAKE_MAP.items():
            if bad_seq in cleaned:
                cleaned = cleaned.replace(bad_seq, good_char)

        # 5. Collapse tabs/newlines into space
        cleaned = re.sub(r"[\r\n\t]+", " ", cleaned)

        # 6. Collapse repeated spaces and strip
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return cleaned

    def format(
        self,
        payload: BriefingEditorialPayload,
        briefing_date: Optional[date] = None,
        shorten_urls: bool = True,
    ) -> FormattedBriefing:
        """
        Render the final briefing text.

        Parameters
        ----------
        payload:
            Validated editorial payload from Section 9/10.
        briefing_date:
            The date that appears in the header.  Defaults to today (UTC).
        shorten_urls:
            Whether to shorten display URLs via TinyURL (default: True).

        Returns
        -------
        FormattedBriefing
            Wrapper containing the finished text and basic metadata.

        Raises
        ------
        ValueError
            If story counts are outside the required 5/5/5 range.
        MalformedHeadlineError
            If any headline contains structural markdown artefacts.
        """
        if briefing_date is None:
            briefing_date = datetime.now(tz=timezone.utc).date()

        domestic_stories = list(getattr(payload, "domestic_stories", []) or [])
        india_stories = list(payload.india_stories)
        intl_stories = list(payload.international_stories)

        if len(domestic_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 Domestic stories; received {len(domestic_stories)}."
            )
        if len(india_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 India stories; received {len(india_stories)}."
            )
        if len(intl_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 International stories; received {len(intl_stories)}."
            )
        if len(domestic_stories) + len(india_stories) + len(intl_stories) != 15:
            raise ValueError(
                f"Formatter requires exactly 15 stories total; received {len(domestic_stories) + len(india_stories) + len(intl_stories)}."
            )

        lines: List[str] = []

        # ── Header ──────────────────────────────────────────────────────────
        lines.append("*INVESTMENT COMMITTEE BRIEFING*")
        lines.append(f"*{self._format_date(briefing_date)}*")
        lines.append("")

        # ── 1. India Business Section ────────────────────────────────────────
        lines.append("*TOP 5 INDIA BUSINESS HEADLINES*")
        lines.append("")
        for story in india_stories:
            lines.extend(self._render_story(story, shorten_urls=shorten_urls))

        # ── 2. Domestic Section ──────────────────────────────────────────────
        if domestic_stories:
            lines.append("*TOP 5 DOMESTIC HEADLINES*")
            lines.append("")
            for story in domestic_stories:
                lines.extend(self._render_story(story, shorten_urls=shorten_urls))

        # ── 3. International Business Section ────────────────────────────────
        lines.append("*TOP 5 INTERNATIONAL BUSINESS HEADLINES*")
        lines.append("")
        for story in intl_stories:
            lines.extend(self._render_story(story, shorten_urls=shorten_urls))

        # Join with newlines; strip trailing whitespace from every line.
        text = "\n".join(line.rstrip() for line in lines).rstrip()

        logger.info(
            "Briefing formatted: %d India + %d Domestic + %d International stories for %s.",
            len(india_stories),
            len(domestic_stories),
            len(intl_stories),
            briefing_date.isoformat(),
        )

        return FormattedBriefing(
            text=text,
            briefing_date=briefing_date,
            domestic_count=len(domestic_stories),
            india_count=len(india_stories),
            international_count=len(intl_stories),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _clean_source_name(self, text: str) -> str:
        """Sanitize and normalize source name for display presentation."""
        clean_src = self.clean_text(text)
        clean_src = self._sanitize_inline(clean_src)
        lower_key = clean_src.lower().strip()
        if lower_key in SOURCE_DISPLAY_MAP:
            return SOURCE_DISPLAY_MAP[lower_key]
        return clean_src

    def _render_story(self, story: EditorialStorySelection, shorten_urls: bool = True) -> List[str]:
        """Validate and render the three output lines for a single story."""
        headline = self._validate_and_normalize_headline(story.headline)
        source = self._clean_source_name(story.source)
        url = story.url.strip()
        display_url = get_display_url(url) if shorten_urls else url

        return [
            f"*{headline}*",
            f"Source: {source}",
            display_url,
            "",
        ]

    # Structural markdown patterns that must be REJECTED, not repaired.
    # Each tuple is (pattern, human-readable reason).
    _MALFORMED_PATTERNS: List[tuple[re.Pattern[str], str]] = [
        (re.compile(r"\*\*"),              "double-asterisk bold markup (**)"),
        (re.compile(r"__"),               "double-underscore markup (__)"),
        (re.compile(r"^#+\s", re.M),      "markdown heading marker (#)"),
        (re.compile(r"^[-•]\s"),           "leading bullet marker (- or •)"),
        (re.compile(r"^\d+[.)]\ "),        "numbered-list prefix (N. or N))"),
    ]

    def _validate_and_normalize_headline(self, text: str) -> str:
        """
        Clean text encoding, collapse embedded newlines/tabs to single space,
        safely strip trailing publication suffixes for display, and validate
        no forbidden structural markdown artefacts exist.
        """
        # 1. Deterministic text and whitespace cleaning
        cleaned = self.clean_text(text)

        # 2. Safely strip publication/site-title suffixes
        cleaned = TITLE_SUFFIX_PATTERN.sub("", cleaned).strip()

        # 3. Check for structural markdown artefacts
        for pattern, reason in self._MALFORMED_PATTERNS:
            if pattern.search(cleaned):
                logger.error(
                    "Malformed headline rejected (%s): %r",
                    reason,
                    cleaned,
                )
                raise MalformedHeadlineError(headline=cleaned, reason=reason)

        return cleaned

    @staticmethod
    def _sanitize_inline(text: str) -> str:
        """Strip stray markdown from short inline fields (source name etc.)."""
        return text.strip().replace("**", "").replace("__", "").replace("*", "")

    def _format_date(self, d: date) -> str:
        """
        Return the date string used in the briefing header.

        Example: ``Monday, 18th August, 2025``
        """
        day_name = d.strftime("%A")
        month_name = d.strftime("%B")
        day_num = d.day
        suffix = self._ordinal_suffix(day_num)
        year = d.year
        return f"{day_name}, {day_num}{suffix} {month_name}, {year}"

    def _ordinal_suffix(self, day: int) -> str:
        """Return 'st', 'nd', 'rd', or 'th' for a day-of-month integer."""
        # Teens always use 'th'
        if 11 <= (day % 100) <= 13:
            return "th"
        return self._ORDINAL_SUFFIXES.get(day % 10, "th")
