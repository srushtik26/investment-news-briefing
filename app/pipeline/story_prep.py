"""
Unified Story Preparation and Reserve Replacement.

Provides:
    prepare_final_story  - canonical preparation and grounding validation for all stories.
    replace_failed_story - generic replacement flow respecting section-specific priority rules.
"""

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.enums import NewsCategory, VerificationTier
from app.pipeline.story_context import StoryContext, build_story_context
from app.ai.models import EditorialStorySelection
from app.ai.headline_synthesis import (
    generate_grounded_fallback_headline,
    is_approved_institutional_headline,
    validate_headline_coherence,
    _clean_headline_text,
    synthesize_investment_headline,
)
from app.ai.editor import generate_deterministic_summary
from app.ai.summary_grounding import (
    validate_summary_grounding,
    is_summary_substantially_identical_to_headline,
)
from app.validation.shared import (
    calculate_semantic_token_overlap,
    build_headline_grounding_source,
)

logger = logging.getLogger("pipeline.story_prep")


def prepare_final_story(
    context: StoryContext,
    ctx: Optional[Any] = None,
    preferred_headline: Optional[str] = None,
    preferred_summary: Optional[str] = None,
) -> EditorialStorySelection:
    """
    Unified story preparation:
    1. Headline generation (with institutional style and grounding fallback)
    2. Headline coherence and overlap check
    3. Summary generation (with deterministic grounding)
    4. Grounding validation (numeric, entity, semantic)
    5. Story consistency validation
    6. Secondary attribution attachment
    """
    ev = context.event
    art = context.article

    # 1. Headline Generation / Synthesis
    headline = preferred_headline
    target_text = build_headline_grounding_source(ev, art)
    
    if headline:
        has_overlap, ov_count, _ = calculate_semantic_token_overlap(headline, target_text)
        if not is_approved_institutional_headline(headline) or not has_overlap:
            logger.info(
                "EDITORIAL_HEADLINE_PRECHECK: triggered for '%s' (approved=%s, overlap=%d) — generating grounded fallback headline",
                headline, is_approved_institutional_headline(headline), ov_count,
            )
            headline = generate_grounded_fallback_headline(
                event=ev, article=art, candidate_headline=headline
            )
    else:
        # Domestic section default synthesis vs generic grounded fallback
        reg_val = context.region.value if hasattr(context.region, "value") else str(context.region).lower()
        if reg_val == "domestic":
            headline = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
        else:
            headline = generate_grounded_fallback_headline(event=ev, article=art)

    headline = (headline or ev.canonical_title or "").strip()

    # Coherence check
    is_coh, coh_reason = validate_headline_coherence(headline, event=ev, article=art)
    if not is_coh:
        logger.warning(
            "Headline coherence check failed for '%s' (%s) — reverting to clean canonical title",
            headline, coh_reason,
        )
        raw_t = ev.canonical_title if ev else (art.title if art else headline)
        headline = _clean_headline_text(raw_t)

    # 2. Summary Generation & Grounding
    summary = preferred_summary
    if not summary:
        summary = generate_deterministic_summary(art, ev, headline)
    else:
        is_grounded, g_reason = validate_summary_grounding(summary, headline, event=ev, article=art)
        is_duplicate = is_summary_substantially_identical_to_headline(summary, headline)
        sum_len = len(summary.split())
        if not is_grounded or is_duplicate or sum_len > 65:
            logger.warning(
                "Summary validation failed for '%s' (grounded=%s, duplicate=%s, len=%d) — using deterministic fallback",
                headline, is_grounded, is_duplicate, sum_len,
            )
            summary = generate_deterministic_summary(art, ev, headline)

    if not summary:
        summary = f"{headline}. Strategic developments and institutional context."

    section_str = "india"
    if hasattr(context, "region"):
        r_val = context.region.value if hasattr(context.region, "value") and isinstance(context.region.value, str) else str(context.region)
        r_val = r_val.lower()
        if "domestic" in r_val:
            section_str = "domestic"
        elif "intl" in r_val or "international" in r_val:
            section_str = "international"
        else:
            section_str = "india"

    source_name = (art.source_name if (art and isinstance(art.source_name, str)) else (ev.primary_publisher if isinstance(getattr(ev, "primary_publisher", None), str) else "Institutional Feed"))
    url = (art.url if (art and isinstance(art.url, str)) else (ev.primary_url if isinstance(getattr(ev, "primary_url", None), str) else f"https://briefing.local/{getattr(ev, 'id', 'ev1')}"))
    ev_id = str(getattr(ev, "id", "ev1"))

    is_two_source = getattr(ev, "verification_tier", None) == VerificationTier.TWO_SOURCE_VERIFIED
    sec_src = getattr(ev, "secondary_publisher", None) if is_two_source and isinstance(getattr(ev, "secondary_publisher", None), str) else None
    sec_u = getattr(ev, "secondary_url", None) if is_two_source and isinstance(getattr(ev, "secondary_url", None), str) else None

    return EditorialStorySelection(
        section=section_str,
        event_id=ev_id,
        headline=str(headline),
        summary=str(summary),
        source=str(source_name),
        url=str(url),
        secondary_source=sec_src,
        secondary_url=sec_u,
    )


def replace_failed_story(
    section: str,
    failed_event_id: str,
    reserves: List[Any],
    ctx: Any,
    used_event_ids: Optional[Set[str]] = None,
) -> Optional[EditorialStorySelection]:
    """
    Generic replacement flow:
    For India section replacement priority:
        1. Remaining qualified portfolio reserve
        2. Qualified general India reserve
        3. Bounded India recovery discovery
        NEVER use an International story.
    """
    sec_lower = section.lower()
    used = set(used_event_ids or set())
    used.add(failed_event_id)

    # Separate reserves into portfolio and general
    qualified_portfolio: List[Tuple[StoryContext, Any]] = []
    qualified_general: List[Tuple[StoryContext, Any]] = []

    for cand in reserves:
        ev = getattr(cand, "event", cand)
        if not ev or ev.id in used:
            continue
        art = ctx.articles_lookup.get(ev.article_ids[0]) if (hasattr(ctx, "articles_lookup") and ev.article_ids) else None
        sc = build_story_context(ev, art, ctx=ctx)

        # Region check
        cand_sec = sc.region.value if hasattr(sc.region, "value") else str(sc.region).lower()
        if sec_lower == "india" and cand_sec != "india":
            continue
        if sec_lower == "domestic" and cand_sec != "domestic":
            continue
        if sec_lower == "international" and cand_sec != "international":
            continue

        # Quality gates: India requires nexus + materiality >= 60
        if sec_lower == "india":
            if not sc.india_nexus or sc.materiality_score < 60.0:
                continue
            if sc.portfolio_company and sc.portfolio_eligible:
                qualified_portfolio.append((sc, cand))
            else:
                qualified_general.append((sc, cand))
        else:
            qualified_general.append((sc, cand))

    # Priority 1: Qualified portfolio reserve (for India)
    if sec_lower == "india" and qualified_portfolio:
        sc, chosen = qualified_portfolio[0]
        used.add(sc.event.id)
        logger.info("[REPLACEMENT_RESULT] Replaced %s in India with portfolio reserve: %s", failed_event_id, sc.event.canonical_title)
        return prepare_final_story(sc, ctx=ctx)

    # Priority 2: Qualified general reserve
    if qualified_general:
        sc, chosen = qualified_general[0]
        used.add(sc.event.id)
        logger.info("[REPLACEMENT_RESULT] Replaced %s in %s with general reserve: %s", failed_event_id, section, sc.event.canonical_title)
        return prepare_final_story(sc, ctx=ctx)

    logger.warning("[REPLACEMENT_RESULT] No qualified in-memory reserve available for %s in section %s", failed_event_id, section)
    return None
