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

import re
from datetime import date, datetime, timezone
from typing import List, Optional

from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.logging_config import get_logger

logger = get_logger(__name__)


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
    ) -> None:
        self.text: str = text
        self.briefing_date: date = briefing_date
        self.india_count: int = india_count
        self.international_count: int = international_count

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

    def format(
        self,
        payload: BriefingEditorialPayload,
        briefing_date: Optional[date] = None,
    ) -> FormattedBriefing:
        """
        Render the final briefing text.

        Parameters
        ----------
        payload:
            Validated editorial payload from Section 9/10.
        briefing_date:
            The date that appears in the header.  Defaults to today (UTC).

        Returns
        -------
        FormattedBriefing
            Wrapper containing the finished text and basic metadata.

        Raises
        ------
        ValueError
            If story counts are outside the required 5/5 range.
        MalformedHeadlineError
            If any headline contains structural markdown artefacts.
        """
        if briefing_date is None:
            briefing_date = datetime.now(tz=timezone.utc).date()

        india_stories = list(payload.india_stories)
        intl_stories = list(payload.international_stories)

        if len(india_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 India stories; received {len(india_stories)}."
            )
        if len(intl_stories) != 5:
            raise ValueError(
                f"Formatter requires exactly 5 International stories; received {len(intl_stories)}."
            )

        lines: List[str] = []

        # ── Header ──────────────────────────────────────────────────────────
        lines.append("*INVESTMENT COMMITTEE BRIEFING*")
        lines.append(f"*{self._format_date(briefing_date)}*")
        lines.append("")

        # ── India section ───────────────────────────────────────────────────
        lines.append("*TOP 5 INDIA BUSINESS HEADLINES*")
        lines.append("")
        for story in india_stories:
            lines.extend(self._render_story(story))

        # ── International section ────────────────────────────────────────────
        lines.append("*TOP 5 INTERNATIONAL BUSINESS HEADLINES*")
        lines.append("")
        for story in intl_stories:
            lines.extend(self._render_story(story))

        # Join with newlines; strip trailing whitespace from every line.
        text = "\n".join(line.rstrip() for line in lines).rstrip()

        logger.info(
            "Briefing formatted: %d India + %d International stories for %s.",
            len(india_stories),
            len(intl_stories),
            briefing_date.isoformat(),
        )

        return FormattedBriefing(
            text=text,
            briefing_date=briefing_date,
            india_count=len(india_stories),
            international_count=len(intl_stories),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render_story(self, story: EditorialStorySelection) -> List[str]:
        """Validate and render the three output lines for a single story."""
        headline = self._validate_and_normalize_headline(story.headline)
        source = self._sanitize_inline(story.source)
        url = story.url.strip()

        return [
            f"*{headline}*",
            f"Source: {source}",
            url,
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
        Validate that the headline contains no structural markdown artefacts,
        then return it with only leading/trailing whitespace removed.

        Only minor normalisation (strip) is applied silently.
        Any structural formatting artefact raises ``MalformedHeadlineError``.

        Parameters
        ----------
        text:
            Raw headline string from the upstream editorial AI.

        Returns
        -------
        str
            Whitespace-stripped headline ready for output.

        Raises
        ------
        MalformedHeadlineError
            On any detected structural markdown artefact.
        """
        # Only normalise whitespace — everything else is a hard rejection.
        normalized = text.strip()

        for pattern, reason in self._MALFORMED_PATTERNS:
            if pattern.search(normalized):
                logger.error(
                    "Malformed headline rejected (%s): %r",
                    reason,
                    normalized,
                )
                raise MalformedHeadlineError(headline=normalized, reason=reason)

        return normalized

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
