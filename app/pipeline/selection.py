"""
Selection, deduplication, ranking, and topic diversity coordination.
"""

from datetime import date
from typing import List, Dict, Any, Tuple, Set, Optional

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.models.entity_sanitizer import sanitize_company_entities
from app.deduplication.fingerprint import normalize_entity_name
from app.ranking.models import ScoredEvent
from app.ranking.topic_classifier import classify_topic_bucket, audit_topic_distribution
from app.ranking.sorter import (
    select_diverse_domestic_candidates,
    select_diverse_topic_candidates,
    select_diverse_publisher_candidates,
)
from app.filtering.business_relevance import calculate_business_relevance_score
from app.verification.query_builder import EventQueryBuilder
from app.pipeline.context import PipelineContext
from app.pipeline.candidate_processing import get_article_age_hours
from app.pipeline.fallback_manager import get_quality_level


def ladder_quality_key(event: Event, article: Article) -> Tuple[int, float, float]:
    """Sort by verification tier, relevance score, and recency."""
    age_hours = get_article_age_hours(article) or 999.0
    tier_rank = 0 if event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else 1
    return tier_rank, -float(event.verification_confidence or 0.0), -age_hours


def get_final_selectable_unique_events(
    ctx: PipelineContext,
    category: Optional[NewsCategory] = None,
) -> List[Event]:
    """
    Compute strictly distinct, final-selectable unique events per section (or all sections).
    """
    raw_candidates: List[Event] = []
    seen_ids: Set[str] = set()

    for ev in ctx.verified_events:
        if ev.id not in seen_ids:
            seen_ids.add(ev.id)
            raw_candidates.append(ev)
    for ev in ctx.high_confidence_single_candidates:
        if ev.id not in seen_ids:
            seen_ids.add(ev.id)
            raw_candidates.append(ev)

    def _cand_sort_key(ev: Event):
        is_two = 1 if (getattr(ev, "verification_tier", None) == VerificationTier.TWO_SOURCE_VERIFIED) else 0
        score = getattr(ev, "relevance_score", 0.0) or getattr(ev, "single_source_confidence_score", 0.0) or 0.0
        return (is_two, score)

    sorted_cands = sorted(raw_candidates, key=_cand_sort_key, reverse=True)

    if category is not None:
        sorted_cands = [e for e in sorted_cands if e.event_category == category]

    selectable: List[Event] = []
    selected_india_companies: Set[str] = set()

    for cand in sorted_cands:
        cand_art = ctx.articles_lookup.get(cand.article_ids[0]) if cand.article_ids else None
        if not cand_art:
            continue

        cand_cat = cand.event_category

        # 1. Semantic event deduplication against already selected events
        is_dup = False
        for sel in selectable:
            if sel.event_category != cand_cat:
                continue
            sel_art = ctx.articles_lookup.get(sel.article_ids[0]) if sel.article_ids else None
            if sel_art:
                is_same, _, _ = ctx.verifier.is_same_underlying_event(cand_art, sel_art, now_utc=ctx.run_reference_time)
                if is_same:
                    is_dup = True
                    break
        if is_dup:
            continue

        # 2. Section-specific constraints
        if cand_cat == NewsCategory.INDIA:
            cand_entities = EventQueryBuilder.extract_entities(cand_art, event=cand)
            clean_comps = sanitize_company_entities(
                (cand.companies_involved or []) + cand_entities,
                publisher=cand_art.source_name,
            )
            norm_comps = {normalize_entity_name(c) for c in clean_comps if normalize_entity_name(c) not in ("unspecified_entity", "")}
            if norm_comps and norm_comps.intersection(selected_india_companies):
                continue
            selectable.append(cand)
            selected_india_companies.update(norm_comps)
        else:
            selectable.append(cand)

    return selectable


def get_unique_candidate_events(ctx: PipelineContext) -> List[Event]:
    """Return distinct canonical events preserving verification tier precedence and semantic uniqueness."""
    return get_final_selectable_unique_events(ctx)


def count_unique_section_events(category: NewsCategory, ctx: PipelineContext) -> int:
    """Count unique eligible events belonging to a section category."""
    return len(get_final_selectable_unique_events(ctx, category=category))


def is_domestic_diversity_satisfied(domestic_events: List[Event], ctx: PipelineContext) -> bool:
    """
    Check if domestic candidate pool allows selecting 5 diversified stories:
    - >= 5 total domestic candidates
    - >= 3 distinct DomesticTopic categories
    - <= 2 COURT_JUDICIARY stories (i.e. at least 3 non-court stories exist)
    """
    if len(domestic_events) < 5:
        return False
    from app.verification.domestic_trending import classify_domestic_topic, DomesticTopic
    topics = []
    for ev in domestic_events:
        art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids and ev.article_ids[0] in ctx.articles_lookup else None
        title = ev.canonical_title or (art.title if art else "")
        body = (art.content_text if art else "") or ev.description or ""
        topics.append(classify_domestic_topic(title, body))
    
    court_count = topics.count(DomesticTopic.COURT_JUDICIARY)
    non_court_count = len(topics) - court_count
    distinct_topics = set(topics)
    
    return (len(distinct_topics) >= 3 and non_court_count >= 3)


def run_deduplication(ctx: PipelineContext) -> Tuple[List[Dict[str, Any]], Dict[str, Event]]:
    """
    Stage 6: Deduplication — 3-day SQLite lookback and cross-section deduplication.
    Returns (accepted_stories, event_by_id).
    """
    ctx.log_exec("=" * 60)
    ctx.log_exec("STAGE 6: Deduplication — 3-day SQLite lookback and cross-section deduplication")
    ctx.log_exec("=" * 60)

    candidate_stories = []
    event_by_id: Dict[str, Event] = {}
    
    all_candidate_events = get_unique_candidate_events(ctx)

    for event in all_candidate_events:
        event_by_id[event.id] = event
        prim_pub = ctx.articles_lookup[event.article_ids[0]].source_name if event.article_ids and event.article_ids[0] in ctx.articles_lookup else None
        cand_art = ctx.articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        extracted_entities = EventQueryBuilder.extract_entities(cand_art, event=event) if cand_art else []
        clean_comps = sanitize_company_entities(
            (event.companies_involved or []) + extracted_entities,
            publisher=prim_pub,
        )
        comp = clean_comps[0] if clean_comps else "unspecified"
        primary_aid = event.article_ids[0]
        event_type_str = ctx.class_map[primary_aid].event_type.value if primary_aid in ctx.class_map else "OTHER"
        cat_str = "domestic" if event.event_category == NewsCategory.DOMESTIC else (
            "india" if event.event_category == NewsCategory.INDIA else "international"
        )
        candidate_stories.append({
            "event_id":      event.id,
            "headline":      event.canonical_title,
            "company_name":   comp,
            "all_companies": clean_comps,
            "event_type":    event_type_str,
            "category":      cat_str,
            "key_facts":     event.financial_figures,
        })

    accepted_stories, rejected_stories = ctx.dedup_engine.filter_stories(
        candidate_stories=candidate_stories,
        target_date=date.today(),
        lookback_days=getattr(ctx.settings, "DEDUP_LOOKBACK_DAYS", 3),
    )
    ctx.log_exec(f"Stage 6 Summary:")
    ctx.log_exec(f"  Accepted: {len(accepted_stories)}")
    ctx.log_exec(f"  Removed:  {len(rejected_stories)}")

    return accepted_stories, event_by_id


def run_ranking_and_selection(
    ctx: PipelineContext,
    accepted_stories: List[Dict[str, Any]],
    event_by_id: Dict[str, Event],
) -> Tuple[Any, List[Any], List[Any], List[Any], bool, str]:
    """
    Stage 7: Ranking & Selection — deterministic relevance scores, publisher diversity, and topic diversity.
    Returns (candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, pipeline_status).
    """
    ctx.log_exec("=" * 60)
    ctx.log_exec("STAGE 7: Ranking — deterministic relevance scores across 3 sections")
    ctx.log_exec("=" * 60)

    accepted_events = [event_by_id[s["event_id"]] for s in accepted_stories if s["event_id"] in event_by_id]

    # Enrich scoring with freshness metadata from primary article
    for event in accepted_events:
        primary_aid = event.article_ids[0] if event.article_ids else None
        primary_art = ctx.articles_lookup.get(primary_aid) if primary_aid else None
        freshness = 0.8  # default
        if primary_art and primary_art.metadata:
            freshness = primary_art.metadata.get("freshness_score", 0.8)
        event.metadata = getattr(event, "metadata", {}) or {}
        try:
            event.metadata["freshness_score"] = freshness
        except Exception:
            pass

    # Rank all eligible events into Domestic, India, and International pools
    candidate_pool = ctx.ranker.rank_events(
        events=accepted_events,
        top_n=max(10, len(accepted_events)),
    )

    def _ladder_order(scored_event):
        event = scored_event.event
        article = ctx.articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        age_hours = get_article_age_hours(article, now_utc=ctx.run_reference_time) if article else None
        age_hours = age_hours if age_hours is not None else 999.0
        if age_hours <= 24:
            horizon_rank = 0
        elif age_hours <= 36:
            horizon_rank = 1
        elif age_hours <= 48:
            horizon_rank = 2
        else:
            horizon_rank = 3
        tier_rank = 0 if event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else 1
        return (horizon_rank, tier_rank, -scored_event.investment_score, -float(event.verification_confidence or 0.0), age_hours)

    # 1. Compute deterministic business relevance and topic bucket for all candidate events
    for scored in (candidate_pool.domestic_candidates + candidate_pool.india_candidates + candidate_pool.international_candidates):
        ev = scored.event
        art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
        b_score, _ = calculate_business_relevance_score(ev, art)
        ev.metadata = getattr(ev, "metadata", {}) or {}
        ev.metadata["business_relevance_score"] = b_score
        ev.metadata["topic_bucket"] = classify_topic_bucket(
            headline=ev.canonical_title,
            body=(art.content_text if art else "") or ev.description or "",
            entity=" ".join(ev.companies_involved or []),
        )

    # 2. Strict business relevance filter for India and International (score >= 70)
    def _filter_by_business_relevance(cands: List[ScoredEvent]) -> List[ScoredEvent]:
        passing = [s for s in cands if (s.event.metadata or {}).get("business_relevance_score", 0.0) >= 70.0]
        if len(passing) >= 5:
            return passing
        needed = 5 - len(passing)
        remaining = [s for s in cands if s not in passing]
        return passing + remaining[:needed]

    candidate_pool.india_candidates = _filter_by_business_relevance(candidate_pool.india_candidates)
    candidate_pool.international_candidates = _filter_by_business_relevance(candidate_pool.international_candidates)

    dom_ranked = sorted(
        [scored for scored in candidate_pool.domestic_candidates],
        key=_ladder_order,
    )
    india_ranked = sorted(
        [scored for scored in candidate_pool.india_candidates],
        key=_ladder_order,
    )
    intl_ranked = sorted(
        [scored for scored in candidate_pool.international_candidates],
        key=_ladder_order,
    )

    # Apply Domestic topic diversity and duplicate-event suppression
    candidate_pool.domestic_candidates = select_diverse_domestic_candidates(
        domestic_candidates=dom_ranked,
        articles_lookup=ctx.articles_lookup,
        target_count=max(5, len(dom_ranked)),
        max_court_stories=1,
    )
    candidate_pool.domestic_candidates = select_diverse_publisher_candidates(
        candidates=candidate_pool.domestic_candidates,
        articles_lookup=ctx.articles_lookup,
        target_count=5,
        max_per_publisher=2,
    )
    for rank, scored in enumerate(candidate_pool.domestic_candidates, 1):
        scored.rank = rank

    # Ensure India candidates strictly enforce 1 story per company and deduplicate semantic duplicates
    india_selected: List[ScoredEvent] = []
    seen_india_comps: Set[str] = set()
    for scored in india_ranked:
        ev = scored.event
        cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
        if not cand_art:
            continue
        is_dup = False
        for ex in india_selected:
            ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
            if ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                is_dup = True
                break
        if is_dup:
            continue

        extracted_entities = EventQueryBuilder.extract_entities(cand_art, event=ev) if cand_art else []
        clean_comps = sanitize_company_entities(
            (ev.companies_involved or []) + extracted_entities,
            publisher=cand_art.source_name if cand_art else None,
        )
        norm_comps = {normalize_entity_name(c) for c in clean_comps if normalize_entity_name(c) not in ("unspecified_entity", "")}
        if norm_comps and norm_comps.intersection(seen_india_comps):
            continue

        india_selected.append(scored)
        seen_india_comps.update(norm_comps)

    # Apply soft topic diversity preference, then publisher diversity preference to India candidates
    india_selected = select_diverse_topic_candidates(
        candidates=india_selected,
        articles_lookup=ctx.articles_lookup,
        target_count=5,
        max_per_topic=2,
    )
    india_selected = select_diverse_publisher_candidates(
        candidates=india_selected,
        articles_lookup=ctx.articles_lookup,
        target_count=5,
        max_per_publisher=2,
    )

    # Ensure International candidates deduplicate semantic / earnings duplicates
    intl_selected: List[ScoredEvent] = []
    for scored in intl_ranked:
        ev = scored.event
        cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
        if not cand_art:
            continue
        is_dup = False
        for ex in intl_selected:
            ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
            if ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                is_dup = True
                break
        if is_dup:
            continue
        intl_selected.append(scored)

    # Apply soft topic diversity preference, then publisher diversity preference to International candidates
    intl_selected = select_diverse_topic_candidates(
        candidates=intl_selected,
        articles_lookup=ctx.articles_lookup,
        target_count=5,
        max_per_topic=2,
    )
    intl_selected = select_diverse_publisher_candidates(
        candidates=intl_selected,
        articles_lookup=ctx.articles_lookup,
        target_count=5,
        max_per_publisher=2,
    )

    for rank, scored in enumerate(india_selected, 1):
        scored.rank = rank
    for rank, scored in enumerate(intl_selected, 1):
        scored.rank = rank

    candidate_pool.india_candidates = india_selected
    candidate_pool.international_candidates = intl_selected

    # Audit Topic Distribution for India and International
    audit_topic_distribution(candidate_pool.india_candidates, "INDIA")
    audit_topic_distribution(candidate_pool.international_candidates, "INTERNATIONAL")

    domestic_pool = candidate_pool.domestic_candidates
    india_pool = candidate_pool.india_candidates
    intl_pool = candidate_pool.international_candidates

    dom_two_count   = len([s for s in domestic_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    dom_sng_count   = len([s for s in domestic_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])
    india_two_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    india_sng_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])
    intl_two_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    intl_sng_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])

    dom_quality_level = get_quality_level(domestic_pool, dom_two_count, ctx.articles_lookup, now_utc=ctx.run_reference_time)
    india_quality_level = get_quality_level(india_pool, india_two_count, ctx.articles_lookup, now_utc=ctx.run_reference_time)
    intl_quality_level = get_quality_level(intl_pool, intl_two_count, ctx.articles_lookup, now_utc=ctx.run_reference_time)
    quality_levels = [dom_quality_level, india_quality_level, intl_quality_level]

    _QUALITY_LEVEL_HORIZONS: Dict[str, float] = {
        "STRICT_SUCCESS":        24.0,
        "FALLBACK_SUCCESS_24H":  24.0,
        "FALLBACK_SUCCESS_36H":  36.0,
        "FALLBACK_SUCCESS_48H":  48.0,
        "EMERGENCY_SUCCESS_72H": 72.0,
        "DATA_UNAVAILABLE":      24.0,
    }
    for section_pool, section_quality_level in [
        (domestic_pool, dom_quality_level),
        (india_pool, india_quality_level),
        (intl_pool, intl_quality_level),
    ]:
        horizon_hours = _QUALITY_LEVEL_HORIZONS.get(section_quality_level, 24.0)
        for scored in section_pool:
            ev = scored.event
            ev.metadata = getattr(ev, "metadata", {}) or {}
            try:
                ev.metadata["fallback_horizon_hours"] = horizon_hours
            except Exception:
                pass

    if "DATA_UNAVAILABLE" in quality_levels:
        pipeline_status = "DATA_UNAVAILABLE"
    elif "EMERGENCY_SUCCESS_72H" in quality_levels:
        pipeline_status = "EMERGENCY_SUCCESS_72H"
    elif "FALLBACK_SUCCESS_48H" in quality_levels:
        pipeline_status = "FALLBACK_SUCCESS_48H"
    elif "FALLBACK_SUCCESS_36H" in quality_levels:
        pipeline_status = "FALLBACK_SUCCESS_36H"
    elif "FALLBACK_SUCCESS_24H" in quality_levels:
        pipeline_status = "FALLBACK_SUCCESS_24H"
    else:
        pipeline_status = "STRICT_SUCCESS"

    ctx.log_exec(f"Stage 7 Summary (Quality Verification Model — 3 Sections):")
    ctx.log_exec(f"  Domestic pool:      {len(domestic_pool)} (Two-source: {dom_two_count}, Single-source: {dom_sng_count})")
    ctx.log_exec(f"  India pool:         {len(india_pool)} (Two-source: {india_two_count}, Single-source: {india_sng_count})")
    ctx.log_exec(f"  International pool: {len(intl_pool)} (Two-source: {intl_two_count}, Single-source: {intl_sng_count})")
    ctx.log_exec(f"  QualityLevels: Dom={dom_quality_level}, India={india_quality_level}, Intl={intl_quality_level}")

    # Sufficiency Gate
    dom_sufficient      = len(domestic_pool) >= 5
    india_sufficient    = len(india_pool) >= 5
    intl_sufficient     = len(intl_pool) >= 5
    sufficient = (dom_sufficient and india_sufficient and intl_sufficient)

    return candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, pipeline_status
