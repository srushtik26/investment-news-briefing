"""
Selection, deduplication, ranking, and topic diversity coordination.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple, Set, Optional

try:
    from zoneinfo import ZoneInfo
    IST_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    IST_TZ = timezone(timedelta(hours=5, minutes=30))


def to_ist_date(dt: Optional[datetime]) -> Optional[date]:
    """Convert a UTC or offset-aware datetime (or assume UTC if naive) to Asia/Kolkata calendar date."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(IST_TZ).date()
    except Exception:
        # Fallback to fixed IST offset
        return dt.astimezone(timezone(timedelta(hours=5, minutes=30))).date()

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
from app.verification.materiality import evaluate_investment_materiality
from app.verification.query_builder import EventQueryBuilder
from app.pipeline.context import PipelineContext
from app.pipeline.candidate_processing import get_article_age_hours, process_candidate_item
from app.pipeline.fallback_manager import get_quality_level
from app.logging_config import get_logger

logger = get_logger("pipeline.selection")


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
    selected_portfolio_companies: Set[str] = set()

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
            from app.ranking.watchlist import is_watchlist_company
            cand_text = f"{cand.canonical_title} {cand.description or ''} {' '.join(cand.companies_involved or [])}"
            if cand_art:
                cand_text += f" {cand_art.title} {cand_art.content_text[:300]}"
            is_pf, pf_comp = is_watchlist_company(cand_text)
            if is_pf and pf_comp in selected_portfolio_companies:
                continue

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
            if is_pf:
                selected_portfolio_companies.add(pf_comp)
        elif cand_cat == NewsCategory.INTERNATIONAL:
            from app.verification.international import is_geopolitical_market_impact_eligible
            is_geo_elig, _ = is_geopolitical_market_impact_eligible(cand, cand_art)
            if not is_geo_elig:
                continue
            selectable.append(cand)
        else:
            selectable.append(cand)

    return selectable


def get_unique_candidate_events(ctx: PipelineContext) -> List[Event]:
    """Return distinct canonical events preserving verification tier precedence and semantic uniqueness."""
    return get_final_selectable_unique_events(ctx)


def count_unique_section_events(category: NewsCategory, ctx: PipelineContext) -> int:
    """Count unique eligible events belonging to a section category."""
    return len(get_final_selectable_unique_events(ctx, category=category))


def count_quality_eligible_domestic(
    verified_events: List[Event],
    high_confidence_single_candidates: List[Event],
    single_source_events: List[Event],
    articles_lookup: Dict[str, Article],
    domestic_evaluator: Any,
    verifier: Any,
    run_reference_time: Optional[datetime] = None,
) -> int:
    """
    Canonical definition of quality-eligible Domestic candidates for reservation logic:
    Combines two-source verified domestic events, high-confidence single source candidates,
    and single-source domestic events scoring >= 60.0 on the DomesticTrendingEvaluator,
    semantically deduplicated against each other.
    """
    from app.verification.single_source import is_multi_event_roundup

    eligible_events: List[Event] = []
    seen_ids: Set[str] = set()

    for ev in verified_events:
        if ev.event_category == NewsCategory.DOMESTIC and ev.id not in seen_ids:
            seen_ids.add(ev.id)
            eligible_events.append(ev)

    for ev in high_confidence_single_candidates:
        if ev.event_category == NewsCategory.DOMESTIC and ev.id not in seen_ids:
            seen_ids.add(ev.id)
            eligible_events.append(ev)

    if domestic_evaluator and articles_lookup:
        for ev in single_source_events:
            if ev.event_category == NewsCategory.DOMESTIC and ev.id not in seen_ids:
                if is_multi_event_roundup(ev.canonical_title):
                    continue
                prim_art = articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                if not prim_art:
                    continue
                is_elig, score, _ = domestic_evaluator.evaluate(
                    ev, prim_art, now_utc=run_reference_time, max_age_hours=72.0
                )
                if is_elig and score >= 60.0:
                    seen_ids.add(ev.id)
                    eligible_events.append(ev)

    # Semantically deduplicate to count strictly distinct events
    unique_eligible: List[Event] = []
    for cand in eligible_events:
        cand_art = articles_lookup.get(cand.article_ids[0]) if cand.article_ids else None
        if not cand_art:
            continue
        is_dup = False
        if verifier:
            for ex in unique_eligible:
                ex_art = articles_lookup.get(ex.article_ids[0]) if ex.article_ids else None
                if ex_art and verifier.is_same_underlying_event(cand_art, ex_art, now_utc=run_reference_time)[0]:
                    is_dup = True
                    break
        if not is_dup:
            unique_eligible.append(cand)

    return len(unique_eligible)


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

    target_date = to_ist_date(ctx.run_reference_time) if ctx.run_reference_time else date.today()
    accepted_stories, rejected_stories = ctx.dedup_engine.filter_stories(
        candidate_stories=candidate_stories,
        target_date=target_date,
        lookback_days=getattr(ctx.settings, "DEDUP_LOOKBACK_DAYS", 3),
    )
    ctx.stage6_rejected_stories = rejected_stories
    ctx.log_exec(f"Stage 6 Summary:")
    ctx.log_exec(f"  Accepted: {len(accepted_stories)}")
    ctx.log_exec(f"  Removed:  {len(rejected_stories)}")

    return accepted_stories, event_by_id


def _make_candidate_story_dict(event: Event, ctx: PipelineContext) -> Dict[str, Any]:
    prim_pub = ctx.articles_lookup[event.article_ids[0]].source_name if event.article_ids and event.article_ids[0] in ctx.articles_lookup else None
    cand_art = ctx.articles_lookup.get(event.article_ids[0]) if event.article_ids else None
    extracted_entities = EventQueryBuilder.extract_entities(cand_art, event=event) if cand_art else []
    clean_comps = sanitize_company_entities(
        (event.companies_involved or []) + extracted_entities,
        publisher=prim_pub,
    )
    comp = clean_comps[0] if clean_comps else "unspecified"
    primary_aid = event.article_ids[0] if event.article_ids else None
    event_type_str = ctx.class_map[primary_aid].event_type.value if primary_aid and primary_aid in ctx.class_map else "OTHER"
    cat_str = "domestic" if event.event_category == NewsCategory.DOMESTIC else (
        "india" if event.event_category == NewsCategory.INDIA else "international"
    )
    return {
        "event_id":      event.id,
        "headline":      event.canonical_title,
        "company_name":   comp,
        "all_companies": clean_comps,
        "event_type":    event_type_str,
        "category":      cat_str,
        "key_facts":     event.financial_figures,
    }


def check_refill_candidate_safety(
    ev: Event,
    cand_story: Dict[str, Any],
    sec_str: str,
    accepted_stories: List[Dict[str, Any]],
    event_by_id: Dict[str, Event],
    ctx: PipelineContext,
    target_date: date,
    lookback_days: int = 3,
) -> Tuple[bool, str]:
    """
    Validates whether a candidate can safely be appended during POST_DEDUP_REFILL:
    1. 3_DAY_HISTORY dedup (using deterministic target_date and lookback_days)
    2. Semantic same-event duplicate check against ALL accepted stories (both intra-section and cross-section)
    3. India one-story-per-company rule, portfolio diversity, and investment materiality when section == 'india'
    4. Normal verification / quality gates (approved tier and threshold: >=60 for Domestic, >=80 for India/Intl HCSS)
    """
    # 1. 3_DAY_HISTORY Deduplication
    if ctx.dedup_engine:
        acc, _ = ctx.dedup_engine.filter_stories(
            candidate_stories=[cand_story],
            target_date=target_date,
            lookback_days=lookback_days,
        )
        if not acc:
            return False, "3_DAY_HISTORY"

    # 2. Semantic same-event check against ALL accepted stories (intra-section AND cross-section)
    cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
    if cand_art and ctx.verifier:
        for ex_s in accepted_stories:
            ex_ev = event_by_id.get(ex_s.get("event_id"))
            ex_art = ctx.articles_lookup.get(ex_ev.article_ids[0]) if ex_ev and ex_ev.article_ids else None
            if ex_art:
                is_same, _, _ = ctx.verifier.is_same_underlying_event(
                    cand_art, ex_art, now_utc=ctx.run_reference_time
                )
                if is_same:
                    return False, "SAME_UNDERLYING_EVENT"

    # 3. Normal verification / quality gates
    tier = getattr(ev, "verification_tier", None)
    if tier not in (VerificationTier.TWO_SOURCE_VERIFIED, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE):
        return False, "INVALID_TIER"

    conf = float(getattr(ev, "verification_confidence", 0.0) or getattr(ev, "single_source_confidence_score", 0.0) or 0.0)
    if sec_str == "domestic":
        from app.verification.domestic_trending import is_domestic_final_eligible
        is_elig, dom_score, dom_reason = is_domestic_final_eligible(
            ev,
            cand_art,
            now_utc=ctx.run_reference_time,
            max_age_hours=72.0,
            evaluator=getattr(ctx, "domestic_evaluator", None),
        )
        if not is_elig or dom_score < 60.0:
            return False, f"DOMESTIC_QUALITY_REJECT ({dom_reason}, score={dom_score:.1f})"
    elif sec_str in ("india", "international"):
        if tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE and conf < 80.0:
            return False, f"LOW_CONFIDENCE (conf={conf})"
        from app.verification.international import is_geopolitical_market_impact_eligible
        is_geo_elig, geo_reason = is_geopolitical_market_impact_eligible(ev, cand_art)
        if not is_geo_elig:
            return False, f"GEOPOLITICAL_UNQUANTIFIED ({geo_reason})"

    # 4. India one-story-per-company and portfolio diversity rule
    if sec_str == "india":
        from app.ranking.watchlist import is_watchlist_company
        cand_text = f"{ev.canonical_title} {ev.description or ''} {' '.join(cand_story.get('all_companies') or [cand_story.get('company_name', '')])}"
        if cand_art:
            cand_text += f" {cand_art.title} {cand_art.content_text[:300]}"
        is_pf, pf_company = is_watchlist_company(cand_text)

        for ex_s in accepted_stories:
            cat = ex_s.get("category")
            cat_str = cat.value if hasattr(cat, "value") else str(cat).lower()
            if cat_str == "india":
                ex_ev = event_by_id.get(ex_s.get("event_id"))
                ex_art = ctx.articles_lookup.get(ex_ev.article_ids[0]) if (ex_ev and ex_ev.article_ids) else None
                ex_text = f"{ex_s.get('headline', '')} {' '.join(ex_s.get('all_companies') or [ex_s.get('company_name', '')])}"
                if ex_art:
                    ex_text += f" {ex_art.title} {ex_art.content_text[:300]}"
                ex_is_pf, ex_pf_company = is_watchlist_company(ex_text)
                if is_pf and ex_is_pf and ex_pf_company == pf_company:
                    ctx.log_exec(
                        f"[PORTFOLIO_DIVERSITY_SKIP]\n"
                        f'company="{pf_company}"\n'
                        f'kept_title="{ex_s.get("headline", "")}"\n'
                        f'skipped_title="{ev.canonical_title}"\n'
                        f'reason="max 1 portfolio story per canonical company"'
                    )
                    return False, "PORTFOLIO_DIVERSITY_SKIP"

        existing_india_comps = {
            normalize_entity_name(ex_s.get("company_name", ""))
            for ex_s in accepted_stories if (ex_s.get("category").value if hasattr(ex_s.get("category"), "value") else str(ex_s.get("category")).lower()) == "india"
        }
        cand_comp = normalize_entity_name(cand_story.get("company_name", ""))
        if cand_comp and cand_comp not in ("unspecified_entity", "", "unspecified") and cand_comp in existing_india_comps:
            return False, "COMPANY_DIVERSITY_SKIP"

        # India Materiality Gate (>= 60.0)
        from app.verification.materiality import evaluate_investment_materiality
        is_mat, mat_score, _ = evaluate_investment_materiality(ev, cand_art, ctx=ctx)
        if not is_mat or mat_score < 60.0:
            return False, f"MATERIALITY_REJECT (score={mat_score})"

    return True, "OK"


def is_refill_candidate_safe(
    ev: Event,
    cand_story: Dict[str, Any],
    sec_str: str,
    accepted_stories: List[Dict[str, Any]],
    event_by_id: Dict[str, Event],
    ctx: PipelineContext,
    target_date: date,
    lookback_days: int = 3,
) -> bool:
    safe, _ = check_refill_candidate_safety(
        ev=ev,
        cand_story=cand_story,
        sec_str=sec_str,
        accepted_stories=accepted_stories,
        event_by_id=event_by_id,
        ctx=ctx,
        target_date=target_date,
        lookback_days=lookback_days,
    )
    return safe


def run_post_dedup_refill(
    ctx: PipelineContext,
    accepted_stories: List[Dict[str, Any]],
    event_by_id: Dict[str, Event],
) -> Tuple[List[Dict[str, Any]], Dict[str, Event]]:
    """
    [POST_DEDUP_REFILL]
    Narrow, bounded refill phase AFTER Stage 6 deduplication if any section falls below 5.
    Prevents sections from failing when 3-day history deduplication removes a repeat story.

    Priority order:
    1. Already-discovered but not-yet-selected valid candidates in memory
    2. Current/today candidates (Asia/Kolkata date)
    3. <=24h valid candidates
    4. Free Google News RSS final-mile search if still deficient
    5. Fallback ladder (36h -> 48h -> 72h) only if still necessary
    """
    from app.verification import MAX_CORROBORATION_SEARCHES_PER_RUN, get_corroboration_count, increment_corroboration_count
    from app.filtering.rules import URLFilterRule
    from app.pipeline.candidate_processing import process_candidate_item
    from app.pipeline.fallback_manager import reconsider_date_deferred_candidates

    from app.verification.domestic_trending import is_domestic_final_eligible
    dom_count = 0
    valid_accepted_stories = []
    for s in accepted_stories:
        cat = s.get("category")
        cat_str = cat.value if hasattr(cat, "value") else str(cat).lower()
        if cat_str == "domestic":
            ev = event_by_id.get(s.get("event_id")) if event_by_id else None
            if ev is not None:
                art = ctx.articles_lookup.get(ev.article_ids[0]) if (getattr(ctx, "articles_lookup", None) and ev.article_ids) else None
                is_elig, score, reason = is_domestic_final_eligible(
                    ev, art, now_utc=ctx.run_reference_time, max_age_hours=72.0, evaluator=getattr(ctx, "domestic_evaluator", None)
                )
                if is_elig and score >= 60.0:
                    dom_count += 1
                    valid_accepted_stories.append(s)
                else:
                    ctx.log_exec(f"[POST_DEDUP_PRUNE_DOMESTIC] Dropping non-eligible story '{s.get('headline')}' (score={score:.1f}, reason={reason})")
            else:
                dom_count += 1
                valid_accepted_stories.append(s)
        else:
            valid_accepted_stories.append(s)
    accepted_stories = valid_accepted_stories

    india_count = len([s for s in accepted_stories if (s.get("category").value if hasattr(s.get("category"), "value") else str(s.get("category")).lower()) == "india"])
    intl_count = len([s for s in accepted_stories if (s.get("category").value if hasattr(s.get("category"), "value") else str(s.get("category")).lower()) == "international"])

    if dom_count >= 5 and india_count >= 5 and intl_count >= 5:
        ctx.log_exec(f"[POST_DEDUP_REFILL] All sections sufficient (Dom={dom_count}/5, India={india_count}/5, Intl={intl_count}/5). No refill needed.")
        return accepted_stories, event_by_id

    ctx.log_exec("=" * 60)
    ctx.log_exec(f"[POST_DEDUP_REFILL] Deficit detected after Stage 6: Domestic={dom_count}/5, India={india_count}/5, Intl={intl_count}/5")
    ctx.log_exec("=" * 60)

    accepted_event_ids: Set[str] = {s["event_id"] for s in accepted_stories if "event_id" in s}
    target_date = to_ist_date(ctx.run_reference_time) if ctx.run_reference_time else date.today()
    ref_date_ist = target_date
    lookback = getattr(ctx.settings, "DEDUP_LOOKBACK_DAYS", 3)

    sections = [
        ("domestic", NewsCategory.DOMESTIC),
        ("india", NewsCategory.INDIA),
        ("international", NewsCategory.INTERNATIONAL),
    ]

    for sec_str, sec_cat in sections:
        current_section_count = len([s for s in accepted_stories if s.get("category") == sec_str])
        needed = 5 - current_section_count
        if needed <= 0:
            continue

        ctx.log_exec(f"[POST_DEDUP_REFILL] Section '{sec_str.upper()}' needs {needed} story/stories (current={current_section_count}/5)")

        # Priority 1, 2, 3: Already-discovered valid candidates in memory
        selectable_events = get_final_selectable_unique_events(ctx, category=sec_cat)
        candidates_in_mem = [e for e in selectable_events if e.id not in accepted_event_ids]

        def _refill_sort_key(ev: Event):
            art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
            art_date = to_ist_date(art.published_at) if (art and art.published_at) else None
            is_today = (art_date == ref_date_ist) if (art_date and ref_date_ist) else False
            age_h = get_article_age_hours(art, now_utc=ctx.run_reference_time) if art else 999.0
            day_rank = 0 if is_today else (1 if age_h <= 24.0 else 2)
            tier_rank = 0 if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else 1
            conf = float(ev.verification_confidence or 0.0)
            return (day_rank, tier_rank, -conf, age_h)

        candidates_in_mem.sort(key=_refill_sort_key)

        for ev in candidates_in_mem:
            if needed <= 0:
                break
            cand_story = _make_candidate_story_dict(ev, ctx)
            if not is_refill_candidate_safe(ev, cand_story, sec_str, accepted_stories, event_by_id, ctx, target_date, lookback):
                continue

            accepted_stories.append(cand_story)
            event_by_id[ev.id] = ev
            accepted_event_ids.add(ev.id)
            needed -= 1
            ctx.log_exec(f"[POST_DEDUP_REFILL] Added existing candidate to {sec_str.upper()}: '{ev.canonical_title}'")

        # Priority 4: Free RSS Final-Mile Search if still deficient
        if needed > 0 and ctx.discovery_service and getattr(ctx.discovery_service, "provider", None):
            rem_rss = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            if rem_rss > 0:
                ctx.log_exec(f"[POST_DEDUP_REFILL] Deficient {sec_str.upper()} still needs {needed}. Running targeted RSS search (budget rem: {rem_rss})...")
                if sec_str == "domestic":
                    TEMPLATES = [
                        (False, "", "India government major decision when:1d"),
                        (False, "", "India Cabinet Parliament policy when:1d"),
                        (False, "", "India politics election major development when:1d"),
                        (False, "", "India economy inflation GDP jobs when:1d"),
                        (False, "", "India economic policy tax trade rupee when:1d"),
                    ]
                    SOURCE_GROUP = "(site:thehindu.com OR site:indianexpress.com OR site:hindustantimes.com OR site:ndtv.com)"
                    country = "India"
                elif sec_str == "india":
                    from app.discovery.queries import PORTFOLIO_DISCOVERY_GROUPS
                    TEMPLATES = [
                        (True, grp_name, f"{grp_expr} when:1d")
                        for grp_name, grp_expr in PORTFOLIO_DISCOVERY_GROUPS.items()
                    ] + [
                        (False, "", tmpl) for tmpl in [
                            'quarterly results net profit revenue crore when:1d',
                            'acquires acquisition deal buyout stake when:1d',
                            'block deal stake sale crore when:1d',
                            'raises funds equity funding crore when:1d',
                        ]
                    ]
                    SOURCE_GROUP = "(site:business-standard.com OR site:livemint.com OR site:moneycontrol.com OR site:economictimes.indiatimes.com)"
                    country = "India"
                else:
                    TEMPLATES = [
                        (False, "", '"reports quarterly results" when:1d'),
                        (False, "", '"announces acquisition" when:1d'),
                        (False, "", '"closes acquisition" when:1d'),
                        (False, "", '"raises financing" when:1d'),
                    ]
                    SOURCE_GROUP = "(site:prnewswire.com OR site:globenewswire.com OR site:businesswire.com OR site:cnbc.com)"
                    country = "US"

                for item in TEMPLATES:
                    if isinstance(item, tuple):
                        is_pf_query, grp_name, tmpl = item
                    else:
                        is_pf_query, grp_name, tmpl = False, "", item
                    if needed <= 0 or get_corroboration_count() >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                        break
                    query = f"{tmpl} {SOURCE_GROUP}"
                    if is_pf_query:
                        ctx.log_exec(f'[PORTFOLIO_DISCOVERY_QUERY] group={grp_name} query="{query}"')
                        ctx.portfolio_discovery_executed = True
                    items = ctx.discovery_service.provider.discover(query=query, country=country, max_results=10)
                    increment_corroboration_count(1)
                    ctx.corroboration_searches += 1
                    for it in items:
                        u = it.url.strip()
                        if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in ctx.seen_urls:
                            ctx.seen_urls.add(u.lower().rstrip("/"))
                            process_candidate_item(it, sec_str, ctx)
                            new_events = [
                                e for e in (ctx.verified_events + ctx.high_confidence_single_candidates)
                                if e.event_category == sec_cat and e.id not in accepted_event_ids
                            ]
                            for ev in new_events:
                                if needed <= 0:
                                    break
                                cand_story = _make_candidate_story_dict(ev, ctx)
                                if not is_refill_candidate_safe(ev, cand_story, sec_str, accepted_stories, event_by_id, ctx, target_date, lookback):
                                    continue

                                accepted_stories.append(cand_story)
                                event_by_id[ev.id] = ev
                                accepted_event_ids.add(ev.id)
                                needed -= 1
                                ctx.log_exec(f"[POST_DEDUP_REFILL] Added RSS-discovered candidate to {sec_str.upper()}: '{ev.canonical_title}'")
                                if needed <= 0:
                                    break
                        if needed <= 0:
                            break

        # Priority 5: Fallback ladder (36h -> 48h -> 72h) only if still necessary
        if needed > 0:
            for horizon in [36.0, 48.0, 72.0]:
                if needed <= 0:
                    break
                reconsider_date_deferred_candidates(sec_cat, horizon, ctx)
                cand_events = [
                    e for e in (ctx.verified_events + ctx.high_confidence_single_candidates)
                    if e.event_category == sec_cat and e.id not in accepted_event_ids
                ]
                for ev in cand_events:
                    if needed <= 0:
                        break
                    cand_story = _make_candidate_story_dict(ev, ctx)
                    if not is_refill_candidate_safe(ev, cand_story, sec_str, accepted_stories, event_by_id, ctx, target_date, lookback):
                        continue

                    accepted_stories.append(cand_story)
                    event_by_id[ev.id] = ev
                    accepted_event_ids.add(ev.id)
                    needed -= 1
                    ctx.log_exec(f"[POST_DEDUP_REFILL] Added fallback {int(horizon)}h candidate to {sec_str.upper()}: '{ev.canonical_title}'")

    final_dom = len([s for s in accepted_stories if s.get("category") == "domestic"])
    final_in = len([s for s in accepted_stories if s.get("category") == "india"])
    final_int = len([s for s in accepted_stories if s.get("category") == "international"])
    ctx.log_exec(f"[POST_DEDUP_REFILL] Completed: Domestic={final_dom}/5, India={final_in}/5, Intl={final_int}/5")

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

    ref_date_ist = to_ist_date(ctx.run_reference_time)

    def _ladder_order(scored_event):
        event = scored_event.event
        article = ctx.articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        age_hours = get_article_age_hours(article, now_utc=ctx.run_reference_time) if article else None
        age_hours = age_hours if age_hours is not None else 999.0

        art_date_ist = to_ist_date(article.published_at) if (article and article.published_at) else None
        is_today = (art_date_ist == ref_date_ist) if (art_date_ist and ref_date_ist) else False

        if is_today:
            horizon_rank = 0
        elif age_hours <= 24:
            horizon_rank = 1
        elif age_hours <= 36:
            horizon_rank = 2
        elif age_hours <= 48:
            horizon_rank = 3
        tier_rank = 0 if event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else 1
        has_penalty = bool(getattr(scored_event.score_breakdown, "relevance_penalties", 0.0) > 0.0)
        penalty_rank = 1 if has_penalty else 0
        return (horizon_rank, penalty_rank, -scored_event.investment_score, tier_rank, -float(event.verification_confidence or 0.0), age_hours)

    # 1. Compute deterministic business relevance and topic bucket for all candidate events
    for scored in (candidate_pool.domestic_candidates + candidate_pool.india_candidates + candidate_pool.international_candidates):
        ev = scored.event
        art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
        b_score, _ = calculate_business_relevance_score(ev, art)
        ev.metadata = getattr(ev, "metadata", {}) or {}
        ev.metadata["business_relevance_score"] = b_score
        if ev.event_category == NewsCategory.INDIA:
            _, mat_score, _ = evaluate_investment_materiality(ev, art, ctx=ctx)
            ev.metadata["investment_materiality_score"] = mat_score
        ev.metadata["topic_bucket"] = classify_topic_bucket(
            headline=ev.canonical_title,
            body=(art.content_text if art else "") or ev.description or "",
            entity=" ".join(ev.companies_involved or []),
        )

    # 2. Strict business relevance filter for India and International (score >= 70)
    def _filter_by_business_relevance(cands: List[ScoredEvent], min_pool: int = 5) -> List[ScoredEvent]:
        passing = [s for s in cands if (s.event.metadata or {}).get("business_relevance_score", 0.0) >= 70.0]
        if len(passing) >= min_pool:
            return passing
        needed = min_pool - len(passing)
        remaining = [s for s in cands if s not in passing]
        return passing + remaining[:needed]

    candidate_pool.india_candidates = _filter_by_business_relevance(candidate_pool.india_candidates, min_pool=15)
    candidate_pool.international_candidates = _filter_by_business_relevance(candidate_pool.international_candidates, min_pool=5)

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

    # Split candidates by calendar day (Asia/Kolkata reference date)
    def _split_today_and_older(cands: List[ScoredEvent]) -> Tuple[List[ScoredEvent], List[ScoredEvent]]:
        today_cands = []
        older_cands = []
        for s in cands:
            art = ctx.articles_lookup.get(s.event.article_ids[0]) if s.event.article_ids else None
            art_date = to_ist_date(art.published_at) if (art and art.published_at) else None
            if art_date and ref_date_ist and art_date == ref_date_ist:
                today_cands.append(s)
            else:
                older_cands.append(s)
        return today_cands, older_cands

    dom_today, dom_older = _split_today_and_older(dom_ranked)
    india_today, india_older = _split_today_and_older(india_ranked)
    intl_today, intl_older = _split_today_and_older(intl_ranked)

    dom_older_24 = [s for s in dom_older if (get_article_age_hours(ctx.articles_lookup.get(s.event.article_ids[0]), now_utc=ctx.run_reference_time) or 999.0) <= 24.0]
    india_older_24 = [s for s in india_older if (get_article_age_hours(ctx.articles_lookup.get(s.event.article_ids[0]), now_utc=ctx.run_reference_time) or 999.0) <= 24.0]
    intl_older_24 = [s for s in intl_older if (get_article_age_hours(ctx.articles_lookup.get(s.event.article_ids[0]), now_utc=ctx.run_reference_time) or 999.0) <= 24.0]

    ctx.log_exec("[TODAY_POOL]")
    ctx.log_exec(f"Domestic today={len(dom_today)} Domestic <=24h older={len(dom_older_24)}")
    ctx.log_exec(f"India today={len(india_today)} India <=24h older={len(india_older_24)}")
    ctx.log_exec(f"International today={len(intl_today)} International <=24h older={len(intl_older_24)}")

    # -------------------------------------------------------------
    # DOMESTIC TODAY-FIRST SELECTION
    # -------------------------------------------------------------
    from app.verification.domestic_trending import is_domestic_final_eligible, get_effective_domestic_max_age_hours
    effective_domestic_horizon = get_effective_domestic_max_age_hours(ctx)

    rejected_domestic_event_ids: Set[str] = set()

    def _domestic_select(cands: List[ScoredEvent], existing: List[ScoredEvent], target_count: int) -> List[ScoredEvent]:
        filtered = []
        for s in cands:
            s_art = ctx.articles_lookup.get(s.event.article_ids[0]) if s.event.article_ids else None

            # ----------------------------------------------------------------
            # CANONICAL DOMESTIC QUALITY GATE — same evaluator used by Stage 9
            # TWO_SOURCE_VERIFIED does NOT bypass this threshold (threshold = 60)
            # ----------------------------------------------------------------
            is_elig, dom_score, dom_reason = is_domestic_final_eligible(
                s.event,
                s_art,
                now_utc=ctx.run_reference_time,
                max_age_hours=effective_domestic_horizon,
                evaluator=getattr(ctx, "domestic_evaluator", None),
                ctx=ctx,
            )
            if not is_elig:
                rejected_domestic_event_ids.add(s.event.id)
                ctx.log_exec(
                    f"[DOMESTIC_FINAL_ELIGIBILITY_REJECT]\n"
                    f'title="{s.event.canonical_title}"\n'
                    f"score={dom_score:.1f}\n"
                    f"threshold=60.0\n"
                    f'reason="fails same DomesticTrendingEvaluator used by Stage 9: {dom_reason}"'
                )
                logger.info(
                    "[DOMESTIC_FINAL_ELIGIBILITY_REJECT] title=%s score=%.1f reason=%s",
                    s.event.canonical_title,
                    dom_score,
                    dom_reason,
                )
                continue

            is_dup = False
            for ex in (existing + filtered):
                ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
                if s_art and ex_art and ctx.verifier.is_same_underlying_event(s_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                    is_dup = True
                    rejected_domestic_event_ids.add(s.event.id)
                    break
            if not is_dup:
                filtered.append(s)

        div = select_diverse_domestic_candidates(
            domestic_candidates=filtered,
            articles_lookup=ctx.articles_lookup,
            target_count=target_count,
            max_court_stories=1,
        )
        pub_div = select_diverse_publisher_candidates(
            candidates=div,
            articles_lookup=ctx.articles_lookup,
            target_count=target_count,
            max_per_publisher=2,
        )
        return pub_div

    dom_final = _domestic_select(dom_today, existing=[], target_count=5)
    if len(dom_final) < 5:
        needed = 5 - len(dom_final)
        older_selected = _domestic_select(dom_older, existing=dom_final, target_count=needed)
        for s in older_selected[:needed]:
            s_art = ctx.articles_lookup.get(s.event.article_ids[0]) if s.event.article_ids else None
            s_date = to_ist_date(s_art.published_at) if (s_art and s_art.published_at) else "unknown"
            ctx.log_exec(f"[OLDER_BACKFILL] section=DOMESTIC today_count={len(dom_final)} needed={needed} article_date={s_date}")
            dom_final.append(s)

    # If still below 5 after initial selection, run a bounded recovery pass over existing candidates
    if len(dom_final) < 5:
        from app.pipeline.candidate_processing import process_candidate_item
        from app.pipeline.fallback_manager import reconsider_date_deferred_candidates

        needed = 5 - len(dom_final)
        ctx.log_exec(
            f"[DOMESTIC_RECOVERY_PASS_START] current={len(dom_final)} needed={needed} max_age_hours={effective_domestic_horizon}"
        )
        selected_event_ids = {s.event.id for s in dom_final}

        # 1. Reconsider date-deferred candidates using effective domestic horizon
        reconsider_date_deferred_candidates(NewsCategory.DOMESTIC, effective_domestic_horizon, ctx)

        # 2. Check all verified, single candidate, and single source events in memory
        all_mem_events = list(ctx.verified_events) + list(ctx.high_confidence_single_candidates) + list(ctx.single_source_events)
        for ev in all_mem_events:
            if len(dom_final) >= 5:
                break
            if ev.id in selected_event_ids or ev.id in rejected_domestic_event_ids:
                continue
            if ev.event_category != NewsCategory.DOMESTIC:
                continue
            cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
            if not cand_art:
                continue

            is_dup = False
            for ex in dom_final:
                ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
                if ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                    is_dup = True
                    break
            if is_dup:
                rejected_domestic_event_ids.add(ev.id)
                continue

            is_elig, dom_score, dom_reason = is_domestic_final_eligible(
                ev,
                cand_art,
                now_utc=ctx.run_reference_time,
                max_age_hours=effective_domestic_horizon,
                evaluator=getattr(ctx, "domestic_evaluator", None),
                ctx=ctx,
            )
            if not is_elig or dom_score < 60.0:
                rejected_domestic_event_ids.add(ev.id)
                continue

            scored = ScoredEvent(
                event=ev,
                score_breakdown=ScoreBreakdown(
                    financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                    corporate_significance=0.0, source_quality=70.0, strategic_bonuses=0.0,
                    editorial_signals=0.0, relevance_penalties=0.0, total_score=dom_score, rationale="recovery",
                ),
                investment_score=dom_score,
                rank=len(dom_final) + 1,
            )
            dom_final.append(scored)
            selected_event_ids.add(ev.id)
            ctx.log_exec(
                f"[DOMESTIC_RECOVERY_ACCEPTED] title=\"{ev.canonical_title}\" score={dom_score:.1f}"
            )

        # 3. Process unseen reserve pool items in memory (no network calls)
        if len(dom_final) < 5:
            unseen_dom = [c for c in (getattr(ctx, "domestic_reserve_pool", []) or []) if c.url.strip().lower().rstrip("/") not in ctx.seen_urls]
            for c in unseen_dom[:20]:
                if len(dom_final) >= 5:
                    break
                ctx.seen_urls.add(c.url.strip().lower().rstrip("/"))
                process_candidate_item(c, "domestic", ctx, active_horizon=effective_domestic_horizon)
                new_events = [
                    e for e in (ctx.verified_events + ctx.high_confidence_single_candidates)
                    if e.event_category == NewsCategory.DOMESTIC and e.id not in selected_event_ids and e.id not in rejected_domestic_event_ids
                ]
                for ev in new_events:
                    if len(dom_final) >= 5:
                        break
                    cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                    if not cand_art:
                        continue
                    is_dup = False
                    for ex in dom_final:
                        ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
                        if ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                            is_dup = True
                            break
                    if is_dup:
                        rejected_domestic_event_ids.add(ev.id)
                        continue
                    is_elig, dom_score, dom_reason = is_domestic_final_eligible(
                        ev,
                        cand_art,
                        now_utc=ctx.run_reference_time,
                        max_age_hours=effective_domestic_horizon,
                        evaluator=getattr(ctx, "domestic_evaluator", None),
                        ctx=ctx,
                    )
                    if not is_elig or dom_score < 60.0:
                        rejected_domestic_event_ids.add(ev.id)
                        continue
                    scored = ScoredEvent(
                        event=ev,
                        score_breakdown=ScoreBreakdown(
                            financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                            corporate_significance=0.0, source_quality=70.0, strategic_bonuses=0.0,
                            editorial_signals=0.0, relevance_penalties=0.0, total_score=dom_score, rationale="recovery",
                        ),
                        investment_score=dom_score,
                        rank=len(dom_final) + 1,
                    )
                    dom_final.append(scored)
                    selected_event_ids.add(ev.id)
                    ctx.log_exec(
                        f"[DOMESTIC_RECOVERY_ACCEPTED] title=\"{ev.canonical_title}\" score={dom_score:.1f}"
                    )

        ctx.log_exec(f"[DOMESTIC_RECOVERY_PASS_COMPLETE] final_count={len(dom_final)}")

    for scored in dom_final:
        if scored.event and getattr(scored.event, "metadata", None) is not None:
            scored.event.metadata["fallback_horizon_hours"] = effective_domestic_horizon

    if len(dom_final) >= 5:
        dom_final = dom_final[:5]
    for rank, scored in enumerate(dom_final, 1):
        scored.rank = rank
    candidate_pool.domestic_candidates = dom_final


    # -------------------------------------------------------------
    # INDIA TODAY-FIRST SELECTION (PORTFOLIO-FIRST, NOT PORTFOLIO-ONLY)
    # -------------------------------------------------------------
    def _india_select(cands: List[ScoredEvent], existing: List[ScoredEvent], target_count: int) -> List[ScoredEvent]:
        from app.ranking.watchlist import get_portfolio_company_role, format_portfolio_role_log
        seen_comps: Set[str] = set()
        seen_portfolio_companies: Dict[str, str] = {}
        for ex in existing:
            ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
            ex_text = f"{ex.event.canonical_title} {ex.event.description or ''} {' '.join(ex.event.companies_involved or [])}"
            if ex_art:
                ex_text += f" {ex_art.title} {ex_art.content_text[:300]}"
            ex_is_pf, ex_pf_company, ex_role, ex_eligible = get_portfolio_company_role(ex.event.canonical_title, ex_art.content_text if ex_art else "")
            if ex_is_pf and ex_eligible:
                seen_portfolio_companies[ex_pf_company] = ex.event.canonical_title

            extracted = EventQueryBuilder.extract_entities(ex_art, event=ex.event) if ex_art else []
            clean = sanitize_company_entities((ex.event.companies_involved or []) + extracted, publisher=ex_art.source_name if ex_art else None)
            seen_comps.update({normalize_entity_name(c) for c in clean if normalize_entity_name(c) not in ("unspecified_entity", "")})

        # Separate candidates into portfolio watchlist candidates and general India candidates, preserving ranking order
        portfolio_cands: List[Tuple[ScoredEvent, str]] = []
        general_cands: List[ScoredEvent] = []

        for scored in cands:
            ev = scored.event
            cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
            if not cand_art:
                continue
            is_pf, pf_company, pf_role, pf_eligible = get_portfolio_company_role(
                ev.canonical_title,
                cand_art.content_text if cand_art else "",
            )
            mat_score = (ev.metadata or {}).get("investment_materiality_score")
            if mat_score is None:
                from app.verification.materiality import evaluate_investment_materiality
                mat_res = evaluate_investment_materiality(ev, cand_art, ctx=ctx)
                mat_score = mat_res.score
                if ev.metadata is None:
                    ev.metadata = {}
                ev.metadata["investment_materiality_score"] = mat_score

            if is_pf:
                role_log = format_portfolio_role_log(pf_company, pf_role, pf_eligible and (mat_score >= 60.0))
                ctx.log_exec(role_log)
                logger.info(role_log)

            if is_pf and pf_eligible and mat_score >= 60.0:
                portfolio_cands.append((scored, pf_company))
            else:
                general_cands.append(scored)

        selected: List[ScoredEvent] = []

        # Phase 1: Portfolio-first selection respecting MAX_PORTFOLIO_STORIES_PER_COMPANY = 1
        for scored, pf_company in portfolio_cands:
            if len(selected) >= target_count:
                break
            ev = scored.event
            cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None

            # Same-event dedup against existing + selected
            is_dup = False
            for ex in (existing + selected):
                ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
                if cand_art and ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                    is_dup = True
                    break
            if is_dup:
                continue

            # Diversity Rule: MAX_PORTFOLIO_STORIES_PER_COMPANY = 1
            if pf_company in seen_portfolio_companies:
                kept_title = seen_portfolio_companies[pf_company]
                ctx.log_exec(
                    f"[PORTFOLIO_DIVERSITY_SKIP]\n"
                    f'company="{pf_company}"\n'
                    f'kept_title="{kept_title}"\n'
                    f'skipped_title="{ev.canonical_title}"\n'
                    f'reason="max 1 portfolio story per canonical company"'
                )
                continue

            extracted_entities = EventQueryBuilder.extract_entities(cand_art, event=ev) if cand_art else []
            clean_comps = sanitize_company_entities(
                (ev.companies_involved or []) + extracted_entities,
                publisher=cand_art.source_name if cand_art else None,
            )
            norm_comps = {normalize_entity_name(c) for c in clean_comps if normalize_entity_name(c) not in ("unspecified_entity", "")}
            if norm_comps and norm_comps.intersection(seen_comps):
                continue

            selected.append(scored)
            seen_portfolio_companies[pf_company] = ev.canonical_title
            seen_comps.update(norm_comps)
            ctx.log_exec(
                f"[PORTFOLIO_SELECTED]\n"
                f'company="{pf_company}"\n'
                f'title="{ev.canonical_title}"\n'
                f'score={scored.investment_score}'
            )

        # Phase 2: Fill remaining slots with highest-quality general India stories
        if len(selected) < target_count:
            needed_slots = target_count - len(selected)
            filtered_general: List[ScoredEvent] = []
            for scored in general_cands:
                ev = scored.event
                cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                if not cand_art:
                    continue

                is_dup = False
                for ex in (existing + selected + filtered_general):
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
                if norm_comps and norm_comps.intersection(seen_comps):
                    continue

                filtered_general.append(scored)

            div_general = select_diverse_topic_candidates(
                candidates=filtered_general,
                articles_lookup=ctx.articles_lookup,
                target_count=needed_slots,
                max_per_topic=2,
            )
            pub_div_general = select_diverse_publisher_candidates(
                candidates=div_general,
                articles_lookup=ctx.articles_lookup,
                target_count=needed_slots,
                max_per_publisher=2,
            )

            for g_scored in pub_div_general[:needed_slots]:
                selected.append(g_scored)
                g_ev = g_scored.event
                g_art = ctx.articles_lookup.get(g_ev.article_ids[0]) if g_ev.article_ids else None
                g_ext = EventQueryBuilder.extract_entities(g_art, event=g_ev) if g_art else []
                g_clean = sanitize_company_entities(
                    (g_ev.companies_involved or []) + g_ext,
                    publisher=g_art.source_name if g_art else None,
                )
                seen_comps.update({normalize_entity_name(c) for c in g_clean if normalize_entity_name(c) not in ("unspecified_entity", "")})

        return selected

    india_final = _india_select(india_today, existing=[], target_count=5)
    if len(india_final) < 5:
        needed = 5 - len(india_final)
        older_selected = _india_select(india_older, existing=india_final, target_count=needed)
        for s in older_selected[:needed]:
            s_art = ctx.articles_lookup.get(s.event.article_ids[0]) if s.event.article_ids else None
            s_date = to_ist_date(s_art.published_at) if (s_art and s_art.published_at) else "unknown"
            ctx.log_exec(f"[OLDER_BACKFILL] section=INDIA today_count={len(india_final)} needed={needed} article_date={s_date}")
            india_final.append(s)

    for rank, scored in enumerate(india_final, 1):
        scored.rank = rank
    candidate_pool.india_candidates = india_final

    # [PORTFOLIO_FUNNEL] Structured Monotonic Funnel Logging
    from app.ranking.watchlist import get_portfolio_company_role

    # 1. Discovered
    pf_discovered_list = [
        c for c in (getattr(ctx, "india_reserve_pool", []) or []) + (getattr(ctx, "portfolio_discovered_candidates", []) or [])
        if get_portfolio_company_role(getattr(c, "title", ""))[0]
    ]
    pf_discovered = max(
        len(pf_discovered_list),
        sum(1 for art in (ctx.articles_lookup.values() if ctx.articles_lookup else []) if get_portfolio_company_role(art.title)[0])
    )

    # 2. Extracted (monotonic: <= discovered)
    pf_extracted_count = sum(
        1 for art in (ctx.articles_lookup.values() if ctx.articles_lookup else [])
        if get_portfolio_company_role(art.title)[0]
    )
    pf_extracted = min(pf_discovered, pf_extracted_count)

    # 3. Filtered pass (monotonic: <= extracted)
    rej_art_ids = set()
    for cat_rejs in getattr(ctx, "rejected_articles", {}).values():
        for rej in cat_rejs:
            if isinstance(rej, dict) and "id" in rej:
                rej_art_ids.add(rej["id"])
    pf_filtered_count = sum(
        1 for art in (ctx.articles_lookup.values() if ctx.articles_lookup else [])
        if art.id not in rej_art_ids and get_portfolio_company_role(art.title)[0]
    )
    pf_filtered_pass = min(pf_extracted, pf_filtered_count)

    # 4. India routed (monotonic: <= filtered_pass)
    all_events = (ctx.verified_events or []) + (ctx.high_confidence_single_candidates or [])
    pf_india_routed_count = sum(
        1 for ev in all_events
        if ev.event_category == NewsCategory.INDIA and get_portfolio_company_role(ev.canonical_title)[0]
    )
    pf_india_routed = min(pf_filtered_pass, pf_india_routed_count)

    # 5. HCSS pass (monotonic: <= india_routed)
    pf_hcss_count = sum(
        1 for ev in all_events
        if ev.event_category == NewsCategory.INDIA
        and ev.verification_tier in (VerificationTier.TWO_SOURCE_VERIFIED, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)
        and float(getattr(ev, "verification_confidence", 0.0) or getattr(ev, "single_source_confidence_score", 0.0) or 0.0) >= (
            80.0 if ev.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE else 60.0
        )
        and get_portfolio_company_role(ev.canonical_title)[0]
    )
    pf_hcss_pass = min(pf_india_routed, pf_hcss_count)

    # 6. Materiality pass (monotonic: <= hcss_pass, requires score >= 60 AND subject-aware eligible_for_priority)
    pf_mat_count = sum(
        1 for ev in all_events
        if ev.event_category == NewsCategory.INDIA
        and get_portfolio_company_role(ev.canonical_title)[3]
        and (ev.metadata or {}).get("investment_materiality_score", 0.0) >= 60.0
    )
    pf_materiality_pass = min(pf_hcss_pass, pf_mat_count)

    # 7. Dedup pass (monotonic: <= materiality_pass)
    pf_dedup_count = sum(
        1 for s in (accepted_stories or [])
        if (s.get("category").value if hasattr(s.get("category"), "value") else str(s.get("category")).lower()) == "india"
        and get_portfolio_company_role(s.get("headline", ""))[3]
        and (event_by_id.get(s.get("event_id")) and (event_by_id.get(s.get("event_id")).metadata or {}).get("investment_materiality_score", 0.0) >= 60.0)
    )
    pf_dedup_pass = min(pf_materiality_pass, pf_dedup_count)

    # 8. Final candidates selected in India 5 (monotonic: <= dedup_pass)
    pf_final_count = sum(
        1 for s in (candidate_pool.india_candidates[:5] if candidate_pool.india_candidates else [])
        if get_portfolio_company_role(s.event.canonical_title)[3]
        and (s.event.metadata or {}).get("investment_materiality_score", 0.0) >= 60.0
    )
    pf_final_candidates = min(pf_dedup_pass, pf_final_count)

    funnel_log = (
        f"[PORTFOLIO_FUNNEL]\n"
        f"discovered={pf_discovered}\n"
        f"extracted={pf_extracted}\n"
        f"filtered_pass={pf_filtered_pass}\n"
        f"india_routed={pf_india_routed}\n"
        f"hcss_pass={pf_hcss_pass}\n"
        f"materiality_pass={pf_materiality_pass}\n"
        f"dedup_pass={pf_dedup_pass}\n"
        f"final_candidates={pf_final_candidates}"
    )
    ctx.log_exec(funnel_log)
    logger.info(funnel_log)

    # -------------------------------------------------------------
    # INTERNATIONAL TODAY-FIRST SELECTION
    # -------------------------------------------------------------
    from app.verification.international import is_geopolitical_market_impact_eligible

    def _intl_select(cands: List[ScoredEvent], existing: List[ScoredEvent], target_count: int) -> List[ScoredEvent]:
        filtered = []
        for scored in cands:
            ev = scored.event
            cand_art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
            if not cand_art:
                continue

            # ----------------------------------------------------------------
            # CANONICAL INTERNATIONAL QUALITY GATE — same check used by Stage 9
            # Geopolitical stories require quantified market impact figures
            # ----------------------------------------------------------------
            is_geo_elig, geo_reason = is_geopolitical_market_impact_eligible(ev, cand_art)
            if not is_geo_elig:
                ctx.log_exec(
                    f"[INTERNATIONAL_FINAL_ELIGIBILITY_REJECT]\n"
                    f'title="{ev.canonical_title}"\n'
                    f'reason="fails geopolitical quantified market-impact requirement"'
                )
                logger.info(
                    "[INTERNATIONAL_FINAL_ELIGIBILITY_REJECT] title=%s reason=%s",
                    ev.canonical_title,
                    geo_reason,
                )
                continue

            is_dup = False
            for ex in (existing + filtered):
                ex_art = ctx.articles_lookup.get(ex.event.article_ids[0]) if ex.event.article_ids else None
                if ex_art and ctx.verifier.is_same_underlying_event(cand_art, ex_art, now_utc=ctx.run_reference_time)[0]:
                    is_dup = True
                    break
            if is_dup:
                continue
            filtered.append(scored)

        div = select_diverse_topic_candidates(
            candidates=filtered,
            articles_lookup=ctx.articles_lookup,
            target_count=target_count,
            max_per_topic=2,
        )
        pub_div = select_diverse_publisher_candidates(
            candidates=div,
            articles_lookup=ctx.articles_lookup,
            target_count=target_count,
            max_per_publisher=2,
        )
        return pub_div

    intl_final = _intl_select(intl_today, existing=[], target_count=5)
    if len(intl_final) < 5:
        needed = 5 - len(intl_final)
        older_selected = _intl_select(intl_older, existing=intl_final, target_count=needed)
        for s in older_selected[:needed]:
            s_art = ctx.articles_lookup.get(s.event.article_ids[0]) if s.event.article_ids else None
            s_date = to_ist_date(s_art.published_at) if (s_art and s_art.published_at) else "unknown"
            ctx.log_exec(f"[OLDER_BACKFILL] section=INTERNATIONAL today_count={len(intl_final)} needed={needed} article_date={s_date}")
            intl_final.append(s)

    for rank, scored in enumerate(intl_final, 1):
        scored.rank = rank
    candidate_pool.international_candidates = intl_final

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
