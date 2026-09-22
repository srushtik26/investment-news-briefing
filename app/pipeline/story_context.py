"""
Canonical StoryContext data structure.

Introduces a lightweight immutable structure built once per candidate,
caching normalized text, extracted entities, numeric tokens, materiality,
region classification, India nexus validation, and portfolio matching.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Set
import re

from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event


# Module-level pre-compiled regexes for extraction and normalization
_CLEAN_WS_PATTERN = re.compile(r"\s+")
_NUMBER_PATTERN = re.compile(r"\b(?:\$|₹|Rs\.?|INR|EUR|GBP)?\s*\d+(?:,\d+)*(?:\.\d+)?\s*(?:crore|lakh|bn|billion|million|mn|k|%|percent)?\b", re.IGNORECASE)
_WORD_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9&.\-]+\b")


@dataclass(frozen=True)
class StoryContext:
    """
    Lightweight immutable story representation built once per candidate.
    Eliminates repeated computations across selection, editorial, and validation.
    """
    event: Event
    article: Optional[Article]
    source_text: str
    normalized_title: str
    normalized_body: str
    entities: Tuple[str, ...]
    numbers: Tuple[str, ...]
    region: NewsCategory
    materiality_score: float
    topic: str
    freshness: float
    verification_tier: VerificationTier
    portfolio_company: bool
    portfolio_canonical_name: Optional[str]
    india_nexus: bool
    event_family: str
    nexus_reason: str = ""
    portfolio_role: str = "none"
    portfolio_eligible: bool = False
    business_relevance_score: float = 0.0


def extract_normalized_text(title: Any, body: Any) -> Tuple[str, str, str]:
    """Clean and normalize title, body, and combined source text once."""
    t_str = title if isinstance(title, str) else ("" if title is None or hasattr(title, "_mock_return_value") else str(title))
    b_str = body if isinstance(body, str) else ("" if body is None or hasattr(body, "_mock_return_value") else str(body))
    norm_title = _CLEAN_WS_PATTERN.sub(" ", t_str).strip()
    norm_body = _CLEAN_WS_PATTERN.sub(" ", b_str[:3000]).strip()
    source_text = f"{norm_title} {norm_body}".strip()
    return norm_title, norm_body, source_text


def extract_numeric_tokens(text: str) -> Tuple[str, ...]:
    """Extract distinct normalized numeric/financial figure tokens."""
    if not text:
        return ()
    matches = _NUMBER_PATTERN.findall(text)
    seen = set()
    res = []
    for m in matches:
        cleaned = m.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            res.append(cleaned)
    return tuple(res)


def extract_candidate_entities(event: Event, article: Optional[Article]) -> Tuple[str, ...]:
    """Extract canonical entities from event and article metadata."""
    entities: List[str] = []
    seen: Set[str] = set()

    for comp in (event.companies_involved or []):
        c = comp.strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            entities.append(c)

    if article and article.metadata:
        for ent in article.metadata.get("entities", []):
            if isinstance(ent, str):
                e = ent.strip()
                if e and e.lower() not in seen:
                    seen.add(e.lower())
                    entities.append(e)

    return tuple(entities)


def build_story_context(
    event: Event,
    article: Optional[Article] = None,
    ctx: Optional[Any] = None,
    eval_materiality: bool = True,
    eval_nexus: bool = True,
) -> StoryContext:
    """
    Build or retrieve cached StoryContext for an event.
    Caches on event.metadata["__story_context__"] to guarantee computation happens once.
    """
    if hasattr(event, "metadata") and isinstance(event.metadata, dict):
        cached = event.metadata.get("__story_context__")
        if isinstance(cached, StoryContext):
            return cached

    art = article
    if not art and ctx and hasattr(ctx, "articles_lookup") and event.article_ids:
        art = ctx.articles_lookup.get(event.article_ids[0])

    # Text normalization
    raw_title = event.canonical_title if isinstance(getattr(event, "canonical_title", None), str) else (art.title if (art and isinstance(art.title, str)) else "")
    raw_body = (art.content_text if (art and isinstance(art.content_text, str)) else "") or (event.description if isinstance(getattr(event, "description", None), str) else "")
    norm_title, norm_body, source_text = extract_normalized_text(raw_title, raw_body)

    # Entities and numbers
    entities = extract_candidate_entities(event, art)
    numbers = extract_numeric_tokens(source_text)

    # Freshness
    freshness = 0.8
    if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "freshness_score" in event.metadata:
        freshness = float(event.metadata["freshness_score"])
    elif art and art.metadata and "freshness_score" in art.metadata:
        freshness = float(art.metadata["freshness_score"])

    # Region
    region = event.event_category or (art.category if art else NewsCategory.INDIA)

    # Portfolio Matching (centralized)
    from app.ranking.watchlist import match_portfolio_company
    pf_match = match_portfolio_company(event, art)
    portfolio_company = pf_match is not None
    portfolio_canonical_name = pf_match.canonical_name if pf_match else None
    portfolio_role = pf_match.role if pf_match else "none"
    portfolio_eligible = pf_match.eligible_for_priority if pf_match else False

    # Topic Bucket
    topic = ""
    if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "topic_bucket" in event.metadata:
        topic = str(event.metadata["topic_bucket"])
    else:
        from app.ranking.topic_classifier import classify_topic_bucket
        topic = classify_topic_bucket(
            headline=norm_title,
            body=norm_body[:500],
            entity=" ".join(entities),
        )

    # Business Relevance
    b_score = 0.0
    if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "business_relevance_score" in event.metadata:
        b_score = float(event.metadata["business_relevance_score"])
    else:
        from app.filtering.business_relevance import calculate_business_relevance_score
        b_score, _ = calculate_business_relevance_score(event, art)

    # Materiality Score
    mat_score = 0.0
    if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "investment_materiality_score" in event.metadata:
        mat_score = float(event.metadata["investment_materiality_score"])
    elif eval_materiality:
        from app.verification.materiality import evaluate_investment_materiality
        _, mat_score, _ = evaluate_investment_materiality(event, art, ctx=ctx)

    # India Nexus
    india_nexus = True
    nexus_reason = ""
    if region == NewsCategory.INDIA and eval_nexus:
        if hasattr(event, "metadata") and isinstance(event.metadata, dict) and "india_nexus_verified" in event.metadata:
            india_nexus = bool(event.metadata["india_nexus_verified"])
            nexus_reason = str(event.metadata.get("india_nexus_reason", ""))
        else:
            from app.classification.region_classifier import verify_india_business_nexus
            india_nexus, nexus_reason = verify_india_business_nexus(event, art)

    # Event family classification
    event_family = "general_business"
    tf_lower = norm_title.lower()
    if any(k in tf_lower for k in ["acquir", "merger", "takeover", "buyout", "bought"]):
        event_family = "m_and_a"
    elif any(k in tf_lower for k in ["results", "profit", "revenue", "earnings", "pat", "ebitda", "loss"]):
        event_family = "earnings"
    elif any(k in tf_lower for k in ["capex", "plant", "expansion", "facility", "invests", "outlay", "gigafactory"]):
        event_family = "capex"
    elif any(k in tf_lower for k in ["order", "bags", "wins", "contract", "tender", "pact"]):
        event_family = "contract_win"
    elif any(k in tf_lower for k in ["ipo", "qip", "raises", "bonds", "ncd", "fundrais"]):
        event_family = "fundraising"
    elif any(k in tf_lower for k in ["sebi", "rbi", "penalty", "penaliz", "nclt", "court", "probe"]):
        event_family = "regulatory"

    # Assemble StoryContext
    sc = StoryContext(
        event=event,
        article=art,
        source_text=source_text,
        normalized_title=norm_title,
        normalized_body=norm_body,
        entities=entities,
        numbers=numbers,
        region=region,
        materiality_score=mat_score,
        topic=topic,
        freshness=freshness,
        verification_tier=event.verification_tier,
        portfolio_company=portfolio_company,
        portfolio_canonical_name=portfolio_canonical_name,
        india_nexus=india_nexus,
        event_family=event_family,
        nexus_reason=nexus_reason,
        portfolio_role=portfolio_role,
        portfolio_eligible=portfolio_eligible,
        business_relevance_score=b_score,
    )

    # Cache back to event metadata
    if not hasattr(event, "metadata") or not isinstance(event.metadata, dict):
        event.metadata = {}
    event.metadata["__story_context__"] = sc
    event.metadata["investment_materiality_score"] = mat_score
    event.metadata["business_relevance_score"] = b_score
    event.metadata["topic_bucket"] = topic
    if region == NewsCategory.INDIA:
        event.metadata["india_nexus_verified"] = india_nexus
        event.metadata["india_nexus_reason"] = nexus_reason

    return sc
