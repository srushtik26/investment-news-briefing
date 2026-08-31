"""
Deterministic Business Relevance Scoring Engine.

Calculates a 0-100 institutional business relevance score for candidate events:
- Concrete financial amount/result (+25)
- Major named company/entity (+20)
- Hard business event (+20)
- Direct investment/market impact (+15)
- Verified freshness (+10)
- Trusted publisher (+10)

Penalties:
- Opinion/advice (-40)
- Generic headline (-30)
- Commentary (-30)
- Lifestyle/personal finance (-25)
- Speculative preview (-20)

India Business and International candidates must achieve score >= 70.
"""

import re
from typing import Any, Dict, Optional, Tuple
from app.models.article import Article
from app.models.event import Event
from app.logging_config import get_logger

logger = get_logger("filtering.business_relevance")

BUSINESS_RELEVANCE_THRESHOLD = 70.0


def calculate_business_relevance_score(
    event: Event,
    article: Optional[Article] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Calculate deterministic business relevance score (0-100).
    Returns (score, breakdown_dict).
    """
    headline = (getattr(event, "canonical_title", "") or (article.title if article else "")).strip()
    body = (getattr(event, "description", "") or (article.content_text if article else "")).strip()
    full_text = f"{headline} {body[:500]}".lower()
    headline_low = headline.lower()

    breakdown: Dict[str, float] = {}

    # 1. Concrete financial amount/result (+25)
    has_amount = bool(
        re.search(
            r"(?:₹|rs\.?|\$|€|£)\s*[\d,]+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:crore|cr|billion|million|trillion|percent|%)\b",
            full_text,
        )
        or (event.financial_figures if hasattr(event, "financial_figures") and event.financial_figures else False)
    )
    if has_amount:
        breakdown["financial_amount"] = 25.0

    # 2. Major named company/entity (+20)
    has_entity = bool(
        (hasattr(event, "companies_involved") and event.companies_involved and any(c.strip() for c in event.companies_involved))
        or re.search(r"\b[A-Z][a-zA-Z0-9&]+(?:\s+[A-Z][a-zA-Z0-9&]+)*\b", headline)
    )
    if has_entity:
        breakdown["named_company"] = 20.0

    # 3. Hard business event (+20)
    hard_event_match = bool(
        re.search(
            r"\b(?:net profit|revenue|q[1-4] profit|q[1-4] results|earnings|ebitda|margin|acquires?|acquisition|buyout|merger|bags order|secures contract|wins order|order worth|epc contract|raises funds|funding round|qip|rights issue|dividend|share buyback|penalty order|antitrust fine|sebi order|rbi penalty|pli scheme|tariff|customs duty|joint venture|capex|order win)\b",
            full_text,
        )
    )
    if hard_event_match:
        breakdown["hard_event"] = 20.0

    # 4. Direct investment / market impact (+15)
    market_impact_match = bool(
        re.search(
            r"\b(?:deal|stake|shares|valuation|investor|investment|equity|bonds|capex|acquisition|order|contract|profit|revenue|margin|market share|capacity expansion)\b",
            full_text,
        )
    )
    if market_impact_match:
        breakdown["market_impact"] = 15.0

    # 5. Verified freshness (+10)
    # Check if article published within 24h
    freshness_pts = 10.0
    if article and article.published_at:
        # If older than 24h, reduce to 5 pts
        age_h = getattr(article, "age_hours", None)
        if age_h is not None and age_h > 24.0:
            freshness_pts = 5.0
    breakdown["verified_freshness"] = freshness_pts

    # 6. Trusted publisher (+10)
    trusted_pubs = {
        "business standard", "economic times", "livemint", "financial express",
        "moneycontrol", "reuters", "bloomberg", "financial times", "cnbc",
        "the hindu", "the indian express", "ap news", "bbc", "wall street journal",
    }
    src_name = (getattr(article, "source_name", "") or getattr(event, "primary_publisher", "") or "").lower()
    if any(p in src_name for p in trusted_pubs):
        breakdown["trusted_publisher"] = 10.0
    else:
        breakdown["trusted_publisher"] = 5.0

    # --- PENALTIES ---

    # Opinion / advice (-40)
    is_opinion = bool(
        re.search(
            r"\b(?:opinion|editorial|column|view|analysis|what next\?|deciding what to do|financial freedom|advice|expert view|our take|why we think)\b",
            headline_low,
        )
        or (article and any(p in (article.url or "").lower() for p in ("/opinion/", "/columns/", "/editorial/", "/comment/", "/analysis/")))
    )
    if is_opinion and not (hard_event_match and has_amount):
        breakdown["penalty_opinion_advice"] = -40.0

    # Generic headline (-30)
    is_generic = bool(
        re.match(
            r"^(?:company announcements?|corporate announcements?|latest news|market updates?|business news|stock market live|today's news|news updates?|top news|headlines today|morning bell|closing bell)$",
            headline_low.strip(),
        )
    )
    if is_generic:
        breakdown["penalty_generic_headline"] = -30.0

    # Commentary (-30)
    is_commentary = bool(
        re.search(
            r"\b(?:what should investors do|how should you invest|do you own\??|stocks to watch|market wrap|sensex ends|nifty today|closing bell)\b",
            headline_low,
        )
    )
    if is_commentary:
        breakdown["penalty_commentary"] = -30.0

    # Lifestyle / personal finance (-25)
    is_lifestyle_pf = bool(
        re.search(
            r"\b(?:financial freedom|wealth building|personal finance|retirement planning|saving tips|lifestyle|career advice|tax saving tips)\b",
            headline_low,
        )
        or (article and any(p in (article.url or "").lower() for p in ("/personal-finance/", "/wealth/", "/advice/")))
    )
    if is_lifestyle_pf and not has_amount:
        breakdown["penalty_lifestyle_pf"] = -25.0

    # Speculative preview (-20)
    is_speculative = bool(
        re.search(
            r"\b(?:what to expect|earnings preview|results preview|faces big test|analysts expect|could see|may see|likely to report)\b",
            headline_low,
        )
    )
    if is_speculative and not hard_event_match:
        breakdown["penalty_speculative_preview"] = -20.0

    total_score = max(0.0, min(100.0, sum(breakdown.values())))
    return total_score, breakdown
