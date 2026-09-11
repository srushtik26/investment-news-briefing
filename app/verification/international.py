"""
International News Verification and Final Eligibility Gate.

Provides canonical validation for International candidate events:
- Geopolitical stories (war, sanctions, geopolitical, tariffs, ceasefire)
  must have quantified market impact figures (digits) in their title/headline.
- Used identically in Stage 7 (selection.py) and Stage 9 Check #19 (engine.py).
"""

import re
from typing import Any, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.article import Article
    from app.models.event import Event

GEOPOLITICAL_KEYWORDS: Tuple[str, ...] = (
    "war",
    "sanctions",
    "geopolitical",
    "tariffs",
    "ceasefire",
)


def is_geopolitical_story(text: str) -> bool:
    """Return True if text contains any geopolitical keywords."""
    if not text:
        return False
    t_lower = text.lower()
    return any(geo in t_lower for geo in GEOPOLITICAL_KEYWORDS)


def is_geopolitical_market_impact_eligible(
    event_or_headline: Any,
    article: Optional["Article"] = None,
) -> Tuple[bool, str]:
    """
    Canonical check for geopolitical market-impact eligibility (Stage 9 Check #19).

    If a story is geopolitical (mentions war, sanctions, geopolitical, tariffs, ceasefire),
    it MUST have quantified numbers (digits) in the headline/title representing market impact.

    Used by BOTH Stage 7 (_intl_select in selection.py) AND Stage 9
    (FinalValidationEngine Check 19 in engine.py) so that the same event
    can never be accepted by one stage and rejected by the other.

    Returns:
        (is_eligible: bool, failure_reason: str)
    """
    if isinstance(event_or_headline, str):
        headline = event_or_headline
    elif hasattr(event_or_headline, "headline") and getattr(event_or_headline, "headline"):
        headline = getattr(event_or_headline, "headline")
    elif hasattr(event_or_headline, "canonical_title") and getattr(event_or_headline, "canonical_title"):
        headline = getattr(event_or_headline, "canonical_title")
    elif article and getattr(article, "title", None):
        headline = article.title
    else:
        headline = str(event_or_headline or "")

    if not is_geopolitical_story(headline):
        return True, ""

    has_numbers = any(c.isdigit() for c in headline)
    if not has_numbers:
        return False, f"Geopolitical story '{headline}' lacks quantified market impact figures"

    return True, ""


def is_international_final_eligible(
    event: Any,
    article: Optional["Article"] = None,
    **kwargs: Any,
) -> Tuple[bool, float, str]:
    """
    Canonical gate for International final-selection eligibility.

    Returns:
        (is_eligible: bool, score: float, failure_reason: str)
    """
    is_geo_elig, reason = is_geopolitical_market_impact_eligible(event, article)
    if not is_geo_elig:
        return False, 0.0, reason

    score = float(getattr(event, "verification_confidence", 0.0) or getattr(event, "relevance_score", 0.0) or 80.0)
    return True, score, ""
