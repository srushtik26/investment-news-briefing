"""
Fallback manager: reserve expansion loop, final-mile discovery, and per-section quality fallback ladder (24h -> 36h -> 48h -> 72h).
"""

import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Set, Optional, Callable

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.filtering.rules import URLFilterRule
from app.verification.single_source import SingleSourceEvaluator, is_multi_event_roundup
from app.ranking import calculate_corroboration_priority
from app.verification import (
    get_corroboration_count,
    increment_corroboration_count,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    get_serpapi_count,
    MAX_SERPAPI_SEARCHES_PER_RUN,
)
from app.pipeline.context import PipelineContext
from app.pipeline.candidate_processing import (
    get_article_age_hours,
    process_candidate_item,
    populate_event_companies,
)


def evaluate_single_source_for_horizon(
    event: Event,
    article: Article,
    evaluator: SingleSourceEvaluator,
    horizon_hours: float = 24.0,
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, float, str]:
    """Apply unchanged single-source rules enforcing <=horizon_hours freshness."""
    age_hours = get_article_age_hours(article, now_utc=now_utc)
    if age_hours is None or age_hours > horizon_hours:
        return False, 0.0, f"REJECT: Stale publication date ({age_hours if age_hours is not None else 'unknown'}h > {horizon_hours:.0f}h)"
    return evaluator.evaluate_event(event, article, now_utc=now_utc, max_age_hours=horizon_hours)


def get_quality_level(
    scored_events: List[Any],
    two_source_count: int,
    articles_lookup: Dict[str, Article],
    now_utc: Optional[datetime] = None,
) -> str:
    """Return the strongest quality level represented by a selected section."""
    if len(scored_events) < 5:
        return "DATA_UNAVAILABLE"
    ages = []
    for scored in scored_events:
        event = scored.event
        article = articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        age = get_article_age_hours(article, now_utc=now_utc) if article else None
        ages.append(age if age is not None else 999.0)
    oldest = max(ages, default=0.0)
    if oldest > 72.0:
        return "DATA_UNAVAILABLE"
    if oldest <= 24.0 and two_source_count >= 3:
        return "STRICT_SUCCESS"
    if oldest <= 24.0:
        return "FALLBACK_SUCCESS_24H"
    if oldest <= 36.0:
        return "FALLBACK_SUCCESS_36H"
    if oldest <= 48.0:
        return "FALLBACK_SUCCESS_48H"
    return "EMERGENCY_SUCCESS_72H"


def get_section_quality_state(
    events: List[Event],
    high_conf_single_candidates: List[Event],
    category: NewsCategory,
) -> Dict[str, Any]:
    """Authoritative helper for section quality state under QUALITY VERIFICATION MODEL."""
    two_source = [e for e in events if e.event_category == category and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    single_source = [e for e in high_conf_single_candidates if e.event_category == category and e.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE and e not in two_source]
    two_src_cnt = len(two_source)
    sng_src_cnt = len(single_source)
    eligible_total = two_src_cnt + sng_src_cnt
    slot_deficit = max(0, 5 - eligible_total)
    section_complete = (eligible_total >= 5)
    return {
        "two_source_count": two_src_cnt,
        "single_source_count": sng_src_cnt,
        "eligible_total": eligible_total,
        "slot_deficit": slot_deficit,
        "two_source_min_deficit": 0,
        "single_source_capacity": max(0, 5 - sng_src_cnt),
        "section_complete": section_complete,
    }


def prioritize_intl_final_mile_candidates(
    pending_events: List[Event],
    articles_lookup: Dict[str, Article],
    evaluator: SingleSourceEvaluator,
) -> List[Tuple[float, Event, Article]]:
    """Order International final-mile candidates by single-source quality first."""
    prioritized = []
    for event in pending_events:
        primary_article = next(
            (articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup),
            None,
        )
        if not primary_article:
            continue

        if event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE:
            quality_group = 0
        else:
            is_eligible, _, _ = evaluator.evaluate_event(event, primary_article)
            quality_group = 1 if is_eligible else 2

        priority = calculate_corroboration_priority(event, primary_article)
        prioritized.append((quality_group, -priority, event, primary_article))

    prioritized.sort(key=lambda item: (item[0], item[1]))
    return [(priority, event, article) for _, negative_priority, event, article in prioritized for priority in [-negative_priority]]


def get_intl_serpapi_reservation(
    intl_state: Dict[str, Any],
    total_cap: int,
    upgrade_available: bool = True,
) -> int:
    """Reserve existing SerpAPI attempts for the remaining International floor."""
    if intl_state["two_source_count"] >= 3 or not upgrade_available:
        return 0
    return min(2, max(0, 3 - intl_state["two_source_count"]), total_cap)


def get_serpapi_event_key(event: Event) -> str:
    """Build a stable run-wide key for a SerpAPI corroboration attempt."""
    title = re.sub(r"\W+", " ", event.canonical_title.lower()).strip()
    companies = "|".join(sorted(re.sub(r"\W+", " ", company.lower()).strip() for company in event.companies_involved))
    return f"{event.id}|{companies}|{title}"


def has_unattempted_intl_upgrade(
    pending_events: List[Event],
    articles_lookup: Dict[str, Article],
    evaluator: SingleSourceEvaluator,
    attempted_keys: Set[str],
) -> bool:
    """Return whether an eligible International upgrade remains unattempted."""
    for event in pending_events:
        if get_serpapi_event_key(event) in attempted_keys:
            continue
        primary_article = next(
            (articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup),
            None,
        )
        if primary_article and (
            event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
            or evaluator.evaluate_event(event, primary_article)[0]
        ):
            return True
    return False


def get_general_serpapi_limit(
    intl_state: Dict[str, Any],
    total_cap: int,
    upgrade_available: bool = True,
) -> int:
    """Limit non-upgrade work while International needs two-source events."""
    return max(0, total_cap - get_intl_serpapi_reservation(intl_state, total_cap, upgrade_available))


def should_stop_expansion(
    expansion_candidates: List[Any],
    intl_state: Dict[str, Any],
    serpapi_enabled: bool,
    serpapi_used: int,
    serpapi_cap: int,
    eligible_upgrade_available: bool,
) -> bool:
    """Stop only when no candidates or permitted International final-mile work remains."""
    if expansion_candidates:
        return False
    intl_search_available = (
        intl_state["two_source_count"] < 3
        and serpapi_enabled
        and serpapi_used < serpapi_cap
    )
    return not (eligible_upgrade_available or intl_search_available)


def reconsider_date_deferred_candidates(
    target_cat: NewsCategory,
    active_horizon: float,
    ctx: PipelineContext,
) -> int:
    """Re-evaluate candidates deferred for date in previous passes against expanded horizon."""
    added = 0
    cand_arts = [
        a for a in ctx.date_deferred_articles
        if (target_cat == NewsCategory.DOMESTIC and a.category == NewsCategory.DOMESTIC) or
           (target_cat == NewsCategory.INDIA and a.category == NewsCategory.INDIA) or
           (target_cat == NewsCategory.INTERNATIONAL and a.category not in (NewsCategory.DOMESTIC, NewsCategory.INDIA))
    ]
    for art in cand_arts:
        age_h = get_article_age_hours(art, now_utc=ctx.run_reference_time)
        if age_h is None or age_h > active_horizon:
            continue

        engine = ctx.domestic_filter_engine if target_cat == NewsCategory.DOMESTIC else ctx.business_filter_engine
        filt = engine.filter_article(art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
        if not filt.is_accepted:
            continue

        c = ctx.class_map.get(art.id)
        if not c:
            class_res = ctx.classifier.classify(art)
            if not class_res.success or not class_res.classification:
                continue
            c = class_res.classification
            ctx.class_map[art.id] = c

        if target_cat != NewsCategory.DOMESTIC and not c.is_hard_business_event:
            continue

        existing_fb_event = next((ev for ev in ctx.fallback_events if ev.article_ids and ev.article_ids[0] == art.id), None)
        if not existing_fb_event:
            classified_companies = populate_event_companies(art, c.company_names)
            existing_fb_event = Event(
                canonical_title=art.title,
                article_ids=[art.id],
                event_category=target_cat,
                description=art.content_text[:300] if art.content_text else "",
                companies_involved=classified_companies,
                financial_figures=c.financial_numbers,
                percentages=c.percentages,
            )
            ctx.fallback_events.append(existing_fb_event)

        ev = existing_fb_event
        ev.event_category = ctx.reg_clf.classify_event(ev, [art])
        if is_multi_event_roundup(ev.canonical_title):
            continue

        matched = next(
            (e for e in (ctx.verified_events + ctx.high_confidence_single_candidates + ctx.single_source_events)
             if e.article_ids and e.id != ev.id and
             ctx.verifier.is_same_underlying_event(
                 ctx.articles_lookup.get(e.article_ids[0], art), art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon
             )[0]),
            None
        )
        if matched:
            if art.id not in matched.article_ids:
                matched.article_ids.append(art.id)
                ev_arts = [ctx.articles_lookup[i] for i in matched.article_ids if i in ctx.articles_lookup]
                rv = ctx.verifier.verify_event(matched, ev_arts, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
                if rv.is_verified:
                    matched.event_category = ctx.reg_clf.classify_event(matched, ev_arts)
                    matched.metadata = getattr(matched, "metadata", {}) or {}
                    matched.metadata["fallback_horizon_hours"] = max(matched.metadata.get("fallback_horizon_hours", 24.0), active_horizon)
                    if matched not in ctx.verified_events:
                        ctx.verified_events.append(matched)
                    if matched in ctx.high_confidence_single_candidates:
                        ctx.high_confidence_single_candidates.remove(matched)
                    added += 1
        else:
            if ev.event_category == NewsCategory.DOMESTIC:
                is_elig, conf, rsn = ctx.domestic_evaluator.evaluate(ev, art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
            else:
                is_elig, conf, rsn = ctx.single_source_evaluator.evaluate_event(ev, art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)

            if is_elig:
                ev.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                ev.verification_confidence = conf
                ev.single_source_confidence_score = conf
                ev.primary_publisher = art.source_name
                ev.primary_url = art.url
                ev.secondary_publisher = None
                ev.secondary_url = None
                ev.article_ids = [art.id]
                ev.verification_reason = rsn
                ev.metadata = getattr(ev, "metadata", {}) or {}
                ev.metadata["fallback_horizon_hours"] = active_horizon
                if ev not in ctx.high_confidence_single_candidates and ev not in ctx.verified_events:
                    ctx.high_confidence_single_candidates.append(ev)
                    added += 1
                    prefix = f"[{ev.event_category.value.upper()}_QUALIFIED_{int(active_horizon)}H]"
                    ctx.log_exec(f"    {prefix} {ev.canonical_title[:55]} | {rsn}")
    return added


def run_expansion_and_fallbacks(
    ctx: PipelineContext,
    get_final_selectable_unique_events_fn: Callable[[Optional[NewsCategory]], List[Event]],
    is_domestic_diversity_satisfied_fn: Callable[[List[Event]], bool],
    count_unique_section_events_fn: Callable[[NewsCategory], int],
    get_unique_candidate_events_fn: Callable[[], List[Event]],
) -> str:
    """
    Execute the multi-pass discovery expansion loop and the multi-horizon quality fallback ladder.
    Returns the overall pipeline_status string.
    """
    expansion_pass = 1
    max_expansion_passes = 6
    executed_final_mile_queries: Set[str] = set()

    dom_frozen = False
    india_frozen = False
    intl_target_met = False

    while expansion_pass <= max_expansion_passes:
        # Re-classify regions for verified & quality single events
        for e in ctx.verified_events:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)
        for e in ctx.high_confidence_single_candidates:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)

        cur_dom_events     = get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC)
        dom_unique_count   = len(cur_dom_events)
        india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
        intl_unique_count  = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
        total_unique       = len(get_unique_candidate_events_fn())
        
        has_unseen_dom = any(c.url.strip().lower().rstrip("/") not in ctx.seen_urls for c in ctx.domestic_reserve_pool)

        # Independent section freezing with early stopping (target 5 + buffer 2 = 7)
        EARLY_STOP_TARGET = 7

        dom_diversity_met = is_domestic_diversity_satisfied_fn(cur_dom_events)
        if dom_diversity_met:
            if not dom_frozen:
                ctx.metrics.increment("early_stop_count")
            dom_frozen = True
        elif not has_unseen_dom and dom_unique_count >= 5:
            dom_frozen = True  # Quality fallback: day genuinely only has court stories, permit 5 stories
        else:
            dom_frozen = False

        if india_unique_count >= 5:
            if not india_frozen:
                ctx.metrics.increment("early_stop_count")
            india_frozen = True
        else:
            india_frozen = False

        if intl_unique_count >= 5:
            if not intl_target_met:
                ctx.metrics.increment("early_stop_count")
            intl_target_met = True
        else:
            intl_target_met = False

        if dom_frozen and india_frozen and intl_target_met and total_unique >= 15:
            ctx.log_exec(
                f"[EXPANSION_COMPLETE] Quality candidate targets satisfied: "
                f"Domestic={dom_unique_count}/5 (FROZEN); "
                f"India={india_unique_count}/5 (FROZEN); "
                f"Intl={intl_unique_count}/5 (FROZEN) (Total unique: {total_unique})."
            )
            break

        unseen_dom      = [c for c in ctx.domestic_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls] if not dom_frozen else []
        unseen_india    = [c for c in ctx.india_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls] if not india_frozen else []
        unseen_intl     = [c for c in ctx.intl_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls]
        ctx.log_exec(f"RESERVE_STATE: Domestic unseen={len(unseen_dom)} ({dom_unique_count}/5 {'[FROZEN]' if dom_frozen else ''}), India unseen={len(unseen_india)} ({india_unique_count}/5 {'[FROZEN]' if india_frozen else ''}), Intl unseen={len(unseen_intl)} ({intl_unique_count}/5 {'[FROZEN]' if intl_target_met else ''})")

        step_dom      = min(len(unseen_dom), 15) if not dom_frozen else 0
        step_india    = min(len(unseen_india), 15) if not india_frozen else 0
        step_intl     = min(len(unseen_intl), 15) if not intl_target_met else 0

        # Process reserve candidates immediately
        if step_dom > 0:
            for c in unseen_dom[:step_dom]:
                process_candidate_item(c, "domestic", ctx)
                cur_dom = get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC)
                if is_domestic_diversity_satisfied_fn(cur_dom) and len(cur_dom) >= EARLY_STOP_TARGET:
                    ctx.metrics.increment("early_stop_count")
                    ctx.log_exec(f"[DOMESTIC_EARLY_STOP] Domestic reached {len(cur_dom)} quality stories; stopping reserve processing.")
                    break
        if step_india > 0:
            for c in unseen_india[:step_india]:
                process_candidate_item(c, "india", ctx)
                cur_in = count_unique_section_events_fn(NewsCategory.INDIA)
                if cur_in >= EARLY_STOP_TARGET:
                    ctx.metrics.increment("early_stop_count")
                    ctx.log_exec(f"[INDIA_EARLY_STOP] India reached {cur_in}/5 quality stories; stopping reserve processing.")
                    break
        if step_intl > 0:
            for c in unseen_intl[:step_intl]:
                process_candidate_item(c, "international", ctx)
                cur_int = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
                if cur_int >= EARLY_STOP_TARGET:
                    ctx.metrics.increment("early_stop_count")
                    ctx.log_exec(f"[INTL_EARLY_STOP] International reached {cur_int}/5 quality stories; stopping reserve processing.")
                    break

        # Re-evaluate quality counts after reserve processing
        for e in ctx.verified_events:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)
        for e in ctx.high_confidence_single_candidates:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)

        dom_unique_count   = count_unique_section_events_fn(NewsCategory.DOMESTIC)
        india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
        intl_unique_count  = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
        total_unique       = len(get_unique_candidate_events_fn())

        # Final-mile RSS Discovery (only if International < 5 and RSS search budget remains)
        if intl_unique_count < 5:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            if rss_rem > 0:
                ctx.log_exec(f"[INTL_FINAL_MILE_DISCOVERY] Searching for NEW International events (current={intl_unique_count}/5)...")
                INTL_SOURCE_GROUPS = [
                    "(site:prnewswire.com OR site:globenewswire.com OR site:businesswire.com)",
                    "(site:cnbc.com OR site:apnews.com OR site:bbc.com)",
                    "(site:marketwatch.com OR site:fortune.com OR site:theguardian.com)",
                    "(site:bloomberg.com OR site:reuters.com OR site:finance.yahoo.com)",
                ]
                INTL_EVENT_TEMPLATES = [
                    '"reports quarterly results" when:1d',
                    '"reports financial results" when:1d',
                    '"reports first half results" when:1d',
                    '"announces acquisition" when:1d',
                    '"acquires" when:1d',
                    '"closes acquisition" when:1d',
                    '"raises financing" when:1d',
                    '"announces investment" when:1d',
                    '"raises guidance" when:1d',
                    '"updates guidance" when:1d',
                    '"board approves buyback" when:1d',
                    '"share repurchase program" when:1d',
                    '"regulatory approval antitrust" when:1d',
                    '"central bank interest rate decision" when:1d',
                    '"announces joint venture" when:1d',
                    '"secures contract million" when:1d',
                ]
                stop_rss_discovery = False
                for s_group in INTL_SOURCE_GROUPS:
                    if stop_rss_discovery:
                        break
                    for tmpl in INTL_EVENT_TEMPLATES:
                        if get_corroboration_count() >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                            stop_rss_discovery = True
                            break
                        query_str = f"{tmpl} {s_group}"
                        q_norm = query_str.lower().strip()
                        if q_norm in executed_final_mile_queries:
                            continue
                        executed_final_mile_queries.add(q_norm)
                        items = ctx.discovery_service.provider.discover(query=query_str, country="US", max_results=10)
                        increment_corroboration_count(1)
                        ctx.corroboration_searches += 1
                        ctx.rss_international_used += 1
                        for it in items:
                            u = it.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in ctx.seen_urls:
                                process_candidate_item(it, "international", ctx)
                                if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                                    ctx.log_exec(f"[INTL_RESCUE_TARGET_MET] International unique stories reached {count_unique_section_events_fn(NewsCategory.INTERNATIONAL)}/5. Stopping RSS discovery.")
                                    stop_rss_discovery = True
                                    break
                        if stop_rss_discovery:
                            break

        # Check India if India is still < 5 (only if not frozen)
        if india_unique_count < 5:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            if rss_rem > 0:
                ctx.log_exec(f"[INDIA_FINAL_MILE_DISCOVERY] Searching for NEW India events (current={india_unique_count}/5)...")
                INDIA_SOURCE_GROUPS = [
                    "(site:business-standard.com OR site:livemint.com OR site:moneycontrol.com)",
                    "(site:businesstoday.in OR site:financialexpress.com OR site:thehindubusinessline.com)",
                    "(site:economictimes.indiatimes.com OR site:ndtvprofit.com)",
                ]
                INDIA_EVENT_TEMPLATES = [
                    'quarterly results net profit revenue crore when:1d',
                    'acquires acquisition deal buyout stake when:1d',
                    'block deal stake sale crore when:1d',
                    'raises funds equity funding crore when:1d',
                    'RBI penalty order bank NBFC when:1d',
                    'IPO DRHP filed India when:1d',
                    'to buy acquisition stake crore when:1d',
                    'promoter stake sale block deal when:1d',
                    'investment funding crore when:1d',
                ]
                stop_india_discovery = False
                for s_group in INDIA_SOURCE_GROUPS:
                    if stop_india_discovery:
                        break
                    for tmpl in INDIA_EVENT_TEMPLATES:
                        if get_corroboration_count() >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                            stop_india_discovery = True
                            break
                        query_str = f"{tmpl} {s_group}"
                        q_norm = query_str.lower().strip()
                        if q_norm in executed_final_mile_queries:
                            continue
                        executed_final_mile_queries.add(q_norm)
                        items = ctx.discovery_service.provider.discover(query=query_str, country="India", max_results=10)
                        increment_corroboration_count(1)
                        ctx.corroboration_searches += 1
                        ctx.rss_india_used += 1
                        for it in items:
                            u = it.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in ctx.seen_urls:
                                process_candidate_item(it, "india", ctx)
                                if count_unique_section_events_fn(NewsCategory.INDIA) >= 5:
                                    ctx.log_exec(f"[INDIA_TARGET_MET] India unique stories reached {count_unique_section_events_fn(NewsCategory.INDIA)}/5. Stopping India discovery.")
                                    stop_india_discovery = True
                                    break
                        if stop_india_discovery:
                            break

        # Re-check section quality
        for e in ctx.verified_events:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)
        for e in ctx.high_confidence_single_candidates:
            e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
            e.event_category = ctx.reg_clf.classify_event(e, e_arts)

        dom_unique_count   = count_unique_section_events_fn(NewsCategory.DOMESTIC)
        india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
        intl_unique_count  = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
        total_unique       = len(get_unique_candidate_events_fn())
        
        if dom_unique_count >= 5 and india_unique_count >= 5 and intl_unique_count >= 5 and total_unique >= 15:
            ctx.log_exec(f"[EXPANSION_COMPLETE] All 3 sections reached sufficiency: Domestic={dom_unique_count}/5, India={india_unique_count}/5, Intl={intl_unique_count}/5 (Total unique: {total_unique}).")
            break

        expansion_pass += 1

    # =========================================================================
    # PER-SECTION QUALITY FALLBACK LADDER (24h -> 36h -> 48h -> 72h)
    # =========================================================================
    for e in ctx.verified_events:
        e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
        e.event_category = ctx.reg_clf.classify_event(e, e_arts)
    for e in ctx.high_confidence_single_candidates:
        e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
        e.event_category = ctx.reg_clf.classify_event(e, e_arts)

    dom_unique_count   = len(get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC))
    india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
    intl_unique_count  = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)

    dom_frozen = (dom_unique_count >= 5)
    india_frozen = (india_unique_count >= 5)
    intl_frozen = (intl_unique_count >= 5)

    FALLBACK_HORIZONS = [36.0, 48.0, 72.0]

    for horizon in FALLBACK_HORIZONS:
        if dom_frozen and india_frozen and intl_frozen:
            break

        tag = "[FALLBACK_36H]" if horizon == 36.0 else ("[FALLBACK_48H]" if horizon == 48.0 else "[EMERGENCY_72H]")

        # 1. India Fallback
        if not india_frozen:
            ctx.log_exec(f"{tag} India deficient ({india_unique_count}/5) — expanding horizon to {int(horizon)}h")
            reconsider_date_deferred_candidates(NewsCategory.INDIA, horizon, ctx)
            india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
            if india_unique_count < 5:
                unseen_india = [c for c in ctx.india_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls]
                for c in unseen_india[:15]:
                    process_candidate_item(c, "india", ctx, active_horizon=horizon)
                    if count_unique_section_events_fn(NewsCategory.INDIA) >= 5:
                        break
                india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)

            # FREE horizon-aware Google News RSS discovery for deficient India
            if india_unique_count < 5:
                when_days = 1 if horizon <= 36.0 else (2 if horizon <= 48.0 else 3)
                when_param = f"when:{when_days}d"
                INDIA_FALLBACK_QUERIES = [
                    f"site:business-standard.com India acquisition {when_param}",
                    f"site:economictimes.indiatimes.com India earnings {when_param}",
                    f"site:livemint.com India funding {when_param}",
                    f"India company results {when_param}",
                    f"India company acquisition {when_param}",
                    f"India regulatory approval {when_param}",
                    f"India contract award {when_param}",
                ]
                for ifq in INDIA_FALLBACK_QUERIES:
                    if count_unique_section_events_fn(NewsCategory.INDIA) >= 5:
                        ctx.log_exec("[INDIA_TARGET_MET] India reached 5/5 quality candidates.")
                        break
                    q_norm = f"{ifq.lower().strip()}_{int(horizon)}"
                    if q_norm in executed_final_mile_queries:
                        continue
                    executed_final_mile_queries.add(q_norm)
                    try:
                        rss_items = ctx.discovery_service.provider.discover(query=ifq, country="India", max_results=10)
                        ctx.rss_india_used += 1
                        for rit in rss_items:
                            u = rit.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in ctx.seen_urls:
                                process_candidate_item(rit, "india", ctx, active_horizon=horizon)
                                if count_unique_section_events_fn(NewsCategory.INDIA) >= 5:
                                    ctx.log_exec("[INDIA_TARGET_MET] India reached 5/5 quality candidates.")
                                    break
                    except Exception as e:
                        ctx.log_exec(f"[INDIA_FALLBACK_RSS_ERROR] {e}")

            india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
            if india_unique_count >= 5:
                india_frozen = True
                ctx.log_exec(f"[FALLBACK_SUCCESS] India reached {india_unique_count}/5 at {int(horizon)}h")

        # 2. International Fallback
        if not intl_frozen:
            ctx.log_exec(f"{tag} International deficient ({intl_unique_count}/5) — expanding horizon to {int(horizon)}h")
            reconsider_date_deferred_candidates(NewsCategory.INTERNATIONAL, horizon, ctx)
            intl_unique_count = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
            if intl_unique_count < 5:
                unseen_intl = [c for c in ctx.intl_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls]
                for c in unseen_intl[:15]:
                    process_candidate_item(c, "international", ctx, active_horizon=horizon)
                    if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                        break
                intl_unique_count = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)

            # FREE horizon-aware Google News RSS discovery for deficient International
            if intl_unique_count < 5:
                ctx.log_exec(f"[INTL_FALLBACK_RSS] International unique={intl_unique_count}/5 at {int(horizon)}h. Searching Google News RSS...")
                when_days = 1 if horizon <= 36.0 else (2 if horizon <= 48.0 else 3)
                when_param = f"when:{when_days}d"
                if ctx.extractor.is_domain_degraded("reuters.com"):
                    INTL_FALLBACK_QUERIES = [
                        f"site:cnbc.com company earnings {when_param}",
                        f"site:cnbc.com acquisition {when_param}",
                        f"site:ft.com company acquisition {when_param}",
                        f"site:markets.ft.com company earnings {when_param}",
                        f"site:apnews.com business company acquisition {when_param}",
                        f"site:bbc.com business company results {when_param}",
                        f"company results CNBC {when_param}",
                        f"company acquisition CNBC {when_param}",
                        f"company earnings Financial Times {when_param}",
                        f"company acquisition Bloomberg {when_param}",
                        f"company results BBC business {when_param}",
                        f"company merger AP business {when_param}",
                        f"company financing Bloomberg {when_param}",
                        f"company contract award AP business {when_param}",
                        f"company earnings markets FT {when_param}",
                    ]
                else:
                    INTL_FALLBACK_QUERIES = [
                        f"site:reuters.com company acquisition {when_param}",
                        f"site:reuters.com company earnings {when_param}",
                        f"site:cnbc.com company earnings {when_param}",
                        f"site:cnbc.com acquisition {when_param}",
                        f"site:ft.com company acquisition {when_param}",
                        f"company earnings Reuters {when_param}",
                        f"company acquisition Reuters {when_param}",
                        f"company funding Reuters {when_param}",
                        f"company results CNBC {when_param}",
                        f"company acquisition CNBC {when_param}",
                        f"company earnings Financial Times {when_param}",
                        f"company acquisition Bloomberg {when_param}",
                        f"company results BBC business {when_param}",
                        f"company merger AP business {when_param}",
                        f"company regulatory approval Reuters {when_param}",
                        f"company contract award Reuters {when_param}",
                        f"company financing Reuters {when_param}",
                    ]
                for ifq in INTL_FALLBACK_QUERIES:
                    if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                        ctx.log_exec("[INTL_TARGET_MET] International reached 5/5 quality candidates.")
                        break
                    q_norm = f"{ifq.lower().strip()}_{int(horizon)}"
                    if q_norm in executed_final_mile_queries:
                        continue
                    executed_final_mile_queries.add(q_norm)
                    try:
                        rss_items = ctx.discovery_service.provider.discover(query=ifq, country="US", max_results=10)
                        ctx.rss_international_used += 1
                        for rit in rss_items:
                            u = rit.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in ctx.seen_urls:
                                process_candidate_item(rit, "international", ctx, active_horizon=horizon)
                                if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                                    ctx.log_exec("[INTL_TARGET_MET] International reached 5/5 quality candidates.")
                                    break
                    except Exception as e:
                        ctx.log_exec(f"[INTL_FALLBACK_RSS_ERROR] {e}")

            # SerpAPI Discovery for International at active horizon
            intl_unique_count = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
            if intl_unique_count < 5:
                serp_key = getattr(ctx.settings, "SERPAPI_API_KEY", None) or os.environ.get("SERPAPI_API_KEY")
                if serp_key and serp_key.strip():
                    serp_corrob = SerpAPICorroborator(extractor=ctx.extractor, api_key=serp_key)
                    if get_serpapi_count() < MAX_SERPAPI_SEARCHES_PER_RUN:
                        ctx.log_exec(f"[SERPAPI_INTL_DISCOVERY] International unique={intl_unique_count}/5 at {int(horizon)}h. Searching SerpAPI (budget: {get_serpapi_count()}/{MAX_SERPAPI_SEARCHES_PER_RUN})...")
                        if horizon <= 36.0:
                            SERP_DISCOVERY_QUERIES = [
                                "today company earnings",
                                "today acquisition",
                                "today company financial results",
                            ]
                        elif horizon <= 48.0:
                            SERP_DISCOVERY_QUERIES = [
                                "company earnings results",
                                "company acquisition deal",
                                "company funding round",
                            ]
                        else:
                            SERP_DISCOVERY_QUERIES = [
                                "company quarterly earnings results",
                                "acquisition merger agreement",
                                "company financial guidance",
                            ]
                        for sq in SERP_DISCOVERY_QUERIES:
                            if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                                ctx.log_exec("[SERPAPI_TARGET_MET] International reached 5/5 quality candidates.")
                                break
                            if get_serpapi_count() >= MAX_SERPAPI_SEARCHES_PER_RUN:
                                break
                            try:
                                serp_items = serp_corrob.discover(sq, active_horizon=horizon)
                                for sit in serp_items:
                                    process_candidate_item(sit, "international", ctx, active_horizon=horizon)
                                    if count_unique_section_events_fn(NewsCategory.INTERNATIONAL) >= 5:
                                        ctx.log_exec("[SERPAPI_TARGET_MET] International reached 5/5 quality candidates.")
                                        break
                            except Exception as e:
                                ctx.log_exec(f"[SERPAPI_DISCOVERY_ERROR] {e}")

            intl_unique_count = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
            if intl_unique_count >= 5:
                intl_frozen = True
                ctx.log_exec(f"[FALLBACK_SUCCESS] International reached {intl_unique_count}/5 at {int(horizon)}h")

        # 3. Domestic Fallback (only if not frozen and < 5)
        if not dom_frozen and dom_unique_count < 5:
            ctx.log_exec(f"{tag} Domestic deficient ({dom_unique_count}/5) — expanding horizon to {int(horizon)}h")
            reconsider_date_deferred_candidates(NewsCategory.DOMESTIC, horizon, ctx)
            dom_unique_count = len(get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC))
            if dom_unique_count < 5:
                unseen_dom = [c for c in ctx.domestic_reserve_pool if c.url.strip().lower().rstrip("/") not in ctx.seen_urls]
                for c in unseen_dom[:15]:
                    process_candidate_item(c, "domestic", ctx, active_horizon=horizon)
                    if len(get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC)) >= 5:
                        break
            dom_unique_count = len(get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC))
            if dom_unique_count >= 5:
                dom_frozen = True
                ctx.log_exec(f"[FALLBACK_SUCCESS] Domestic reached {dom_unique_count}/5 at {int(horizon)}h")

        # Re-check section freezing
        dom_unique_count   = len(get_final_selectable_unique_events_fn(NewsCategory.DOMESTIC))
        india_unique_count = count_unique_section_events_fn(NewsCategory.INDIA)
        intl_unique_count  = count_unique_section_events_fn(NewsCategory.INTERNATIONAL)
        if dom_unique_count >= 5:
            dom_frozen = True
        if india_unique_count >= 5:
            india_frozen = True
        if intl_unique_count >= 5:
            intl_frozen = True

    # Check terminal exhaustion
    if india_unique_count < 5:
        ctx.log_exec(f"[EXHAUSTED] India remained {india_unique_count}/5 after 72h -> DATA_UNAVAILABLE")
    if intl_unique_count < 5:
        ctx.log_exec(f"[EXHAUSTED] International remained {intl_unique_count}/5 after 72h -> DATA_UNAVAILABLE")
    if dom_unique_count < 5:
        ctx.log_exec(f"[EXHAUSTED] Domestic remained {dom_unique_count}/5 after 72h -> DATA_UNAVAILABLE")

    # Reclassify and update categories
    for e in ctx.verified_events:
        e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
        e.event_category = ctx.reg_clf.classify_event(e, e_arts)
    for e in ctx.high_confidence_single_candidates:
        e_arts = [ctx.articles_lookup[aid] for aid in e.article_ids if aid in ctx.articles_lookup]
        e.event_category = ctx.reg_clf.classify_event(e, e_arts)

    # Determine status
    if india_unique_count >= 5 and intl_unique_count >= 5 and dom_unique_count >= 5:
        pipeline_status = "STRICT_SUCCESS"
    else:
        pipeline_status = "DATA_UNAVAILABLE"

    return pipeline_status
