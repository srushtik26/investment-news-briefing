"""
High-Level Pipeline Runner and Orchestrator.
Connects all system stages from discovery to formatting.
"""

import json
import logging
import time
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import get_settings
from app.logging_config import setup_logging, get_logger
from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.discovery import NewsDiscoveryService
from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
from app.extraction import ArticleExtractor
from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
from app.classification import AIArticleClassifier, GeminiRateLimitError
from app.classification.region_classifier import EventRegionClassifier
from app.verification import (
    TwoSourceVerifier,
    ActiveCorroborator,
    reset_corroboration_counter,
    reset_serpapi_counter,
    get_corroboration_count,
    increment_corroboration_count,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
    DOMESTIC_RESERVED_RSS_SEARCHES,
)
from app.deduplication import DeduplicationEngine, HistoryStore
from app.deduplication.clusterer import EventClusterer
from app.ranking import CandidatePoolRanker, ArticlePreRanker
from app.ranking.scorer import InvestmentRelevanceScorer
from app.ai import (
    GeminiEditorialEngine,
    BriefingEditorialPayload,
    GeminiUsageLogger,
    RATE_LIMITED_PREFIX,
    EditorialResult,
    EditorialStorySelection,
)
from app.ai.editor import generate_deterministic_summary
from app.ai.summary_grounding import validate_summary_grounding
from app.validation import FinalValidationEngine
from app.formatting.formatter import BriefingFormatter
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.verification.single_source import SingleSourceEvaluator, is_multi_event_roundup
from app.utils.performance_metrics import PipelineMetrics

from app.pipeline.context import PipelineContext
from app.pipeline.candidate_processing import (
    _extract_candidates,
    populate_event_companies,
)
from app.pipeline.discovery_stage import discover_initial_reserves
from app.pipeline.fallback_manager import run_expansion_and_fallbacks
from app.pipeline.enrichment import run_second_source_enrichment
from app.pipeline.selection import (
    get_final_selectable_unique_events,
    is_domestic_diversity_satisfied,
    count_unique_section_events,
    count_quality_eligible_domestic,
    get_unique_candidate_events,
    run_deduplication,
    run_post_dedup_refill,
    run_ranking_and_selection,
)
from app.pipeline.reporting import (
    print_candidate_audit,
    print_final_story_audit,
    save_json_artifact,
)

logger = get_logger("pipeline.runner")


def run_pipeline(
    max_india: Optional[int] = None,
    max_international: Optional[int] = None,
    run_reference_time: Optional[datetime] = None,
) -> int:
    """Run the automated investment news briefing pipeline end-to-end."""
    settings = get_settings()
    data_dir = Path("data")
    logs_dir = Path("logs")
    data_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    setup_logging(log_level=settings.LOG_LEVEL, log_file=logs_dir / "app.log")

    run_reference_time = run_reference_time or datetime.now(timezone.utc)
    date_str = run_reference_time.strftime("%Y-%m-%d")

    execution_log_lines: List[str] = []

    def log_exec(msg: str) -> None:
        logger.info(msg)
        execution_log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    log_exec("=" * 60)
    log_exec(f"PIPELINE RUN: {date_str} (Reference Time: {run_reference_time.isoformat()})")
    log_exec("=" * 60)

    max_india = max_india or settings.MAX_DISCOVERY_INDIA
    max_international = max_international or settings.MAX_DISCOVERY_INTL
    max_domestic = getattr(settings, "MAX_DISCOVERY_DOMESTIC", 40)

    # Initialize performance metrics
    metrics = PipelineMetrics.reset()
    metrics.start_timer("total_seconds")

    reset_corroboration_counter()
    reset_serpapi_counter()
    GeminiUsageLogger.reset()

    # Instantiate services
    history_store = HistoryStore()
    discovery_service = NewsDiscoveryService(
        provider=GoogleNewsRSSDiscoveryProvider()
    )
    extractor = ArticleExtractor()
    extractor.reset_run_health()

    business_filter_engine = HardFilterEngine()
    domestic_filter_engine = DomesticHardFilterEngine()
    classifier = AIArticleClassifier()
    reg_clf = EventRegionClassifier()
    verifier = TwoSourceVerifier()
    active_corroborator = ActiveCorroborator(extractor=extractor)
    single_source_evaluator = SingleSourceEvaluator()
    domestic_evaluator = DomesticTrendingEvaluator()
    dedup_engine = DeduplicationEngine(history_store=history_store)
    ranker = CandidatePoolRanker()
    scorer = InvestmentRelevanceScorer()
    editorial_engine = GeminiEditorialEngine()
    validator = FinalValidationEngine(history_store=history_store)
    formatter = BriefingFormatter()

    ctx = PipelineContext(
        run_reference_time=run_reference_time,
        data_dir=data_dir,
        logs_dir=logs_dir,
        settings=settings,
        max_india=max_india,
        max_international=max_international,
        log_exec=log_exec,
        discovery_service=discovery_service,
        extractor=extractor,
        business_filter_engine=business_filter_engine,
        domestic_filter_engine=domestic_filter_engine,
        classifier=classifier,
        reg_clf=reg_clf,
        verifier=verifier,
        active_corroborator=active_corroborator,
        single_source_evaluator=single_source_evaluator,
        domestic_evaluator=domestic_evaluator,
        history_store=history_store,
        dedup_engine=dedup_engine,
        ranker=ranker,
        scorer=scorer,
        editorial_engine=editorial_engine,
        validator=validator,
        formatter=formatter,
        metrics=metrics,
    )

    # =========================================================================
    # STAGE 1 + 2: Discovery Reserve Pool & Extraction
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 1+2: Discovery Reserve Pool → Resolution → Extraction")
    log_exec("=" * 60)

    pass1_candidates, initial_dom, initial_india, initial_intl = discover_initial_reserves(
        ctx, max_domestic, max_india, max_international
    )

    metrics.start_timer("extraction_seconds")
    log_exec(f"[Pass 1] Processing {initial_dom} Domestic + {initial_india} India + {initial_intl} International candidates from reserve...")
    batch_arts, batch_recs, gc, ro, fo, pur, dup = _extract_candidates(
        pass1_candidates, extractor, ctx.seen_urls, log_exec
    )
    metrics.stop_timer("extraction_seconds")

    ctx.all_extracted.extend(batch_arts)
    ctx.all_records.extend(batch_recs)
    ctx.articles_lookup = {a.id: a for a in ctx.all_extracted}
    processed_pass1 = len(batch_arts)
    duplicate_seen_candidates = dup
    total_pre_url_rejects = pur
    total_resolved = ro
    total_fallback = fo

    reserve_remaining = (
        (len(ctx.domestic_reserve_pool) - initial_dom) +
        (len(ctx.india_reserve_pool) - initial_india) +
        (len(ctx.intl_reserve_pool) - initial_intl)
    )
    log_exec(f"Pass 1 extracted: {len(batch_arts)} articles (Reserve remaining: {reserve_remaining})")
    log_exec(f"Pass 1: {len(batch_arts)} articles extracted from {initial_dom + initial_india + initial_intl} candidates.")

    # =========================================================================
    # STAGE 3: Hard Filtering
    # =========================================================================
    metrics.start_timer("filtering_seconds")
    log_exec("=" * 60)
    log_exec("STAGE 3: Filtering — deterministic filter engine (Domestic + Business)")
    log_exec("=" * 60)

    domestic_raw = [a for a in ctx.all_extracted if a.category == NewsCategory.DOMESTIC]
    business_raw = [a for a in ctx.all_extracted if a.category != NewsCategory.DOMESTIC]

    dom_accepted, dom_rejections = domestic_filter_engine.filter_candidates(domestic_raw)
    biz_accepted, biz_rejections = business_filter_engine.filter_candidates(business_raw)

    accepted_articles = dom_accepted + biz_accepted
    rejections = dom_rejections + biz_rejections
    ctx.rejections = rejections

    date_deferred_urls = {
        rejection.article_url
        for rejection in rejections
        if rejection.rule_failed == "DATE"
    }
    ctx.date_deferred_articles = [
        article for article in ctx.all_extracted
        if article.url in date_deferred_urls and article.published_at and getattr(article, "date_verified", True)
    ]

    dom_accepted_list = [a for a in accepted_articles if a.category == NewsCategory.DOMESTIC]
    india_accepted    = [a for a in accepted_articles if a.category == NewsCategory.INDIA]
    intl_accepted     = [a for a in accepted_articles if a.category == NewsCategory.INTERNATIONAL]

    log_exec(f"Stage 3 Summary:")
    log_exec(f"  Accepted:  {len(accepted_articles)} ({len(dom_accepted_list)} Domestic, {len(india_accepted)} India, {len(intl_accepted)} Intl)")
    log_exec(f"  Rejected:  {len(rejections)}")

    rejection_by_rule: Dict[str, int] = {}
    for r in rejections:
        rule = r.rule_failed or "UNKNOWN"
        rejection_by_rule[rule] = rejection_by_rule.get(rule, 0) + 1

    save_json_artifact(
        data_dir / "rejected_candidates.json",
        [{"url": r.article_url, "title": r.article_title, "rule_failed": r.rule_failed, "reason": r.rejection_reason}
         for r in rejections]
    )
    metrics.stop_timer("filtering_seconds")

    # =========================================================================
    # STAGE 4: Gemini AI Classification (Pre-ranked)
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 4: Gemini AI Classification — business event classification")
    log_exec("=" * 60)

    pre_ranker = ArticlePreRanker()
    gemini_candidates = pre_ranker.select_top_balanced_candidates(
        accepted_articles,
        max_total=settings.MAX_GEMINI_CLASSIFICATIONS,
    )
    gemini_urls = {art.url for art in gemini_candidates}
    remaining_accepted = [art for art in accepted_articles if art.url not in gemini_urls]
    ordered_accepted_articles = gemini_candidates + remaining_accepted

    classified_articles: List[Article] = []
    class_map: Dict[str, Any] = {}
    live_class_count = offline_class_count = rate_limited_count = 0

    for idx, art in enumerate(ordered_accepted_articles, 1):
        log_exec(f"[{idx}/{len(ordered_accepted_articles)}] Classifying: '{art.title[:50]}' ({art.source_name})")
        try:
            res = classifier.classify(art)
            if res.attempts > 0:
                live_class_count += 1
            else:
                offline_class_count += 1
        except Exception as e:
            log_exec(f"  -> ERROR during classification: {e}")
            continue

        if res and res.success and res.classification:
            c = res.classification
            class_map[art.id] = c
            art.metadata = art.metadata or {}
            art.metadata["classification"] = c.model_dump()
            is_dom_candidate = (art.category == NewsCategory.DOMESTIC)
            if is_dom_candidate or (c.is_hard_business_event and c.is_investment_relevant):
                classified_articles.append(art)
                mode_str = "Live Gemini" if res.attempts > 0 else "Offline Heuristic"
                log_exec(f"  -> ACCEPTED ({mode_str}): {c.event_type.value} | Companies: {c.company_names}")
            else:
                log_exec(f"  -> REJECTED BY AI: Event={c.event_type.value}, HardEvent={c.is_hard_business_event}, InvRel={c.is_investment_relevant}")
        else:
            err = res.error_message if res else "Unknown error"
            log_exec(f"  -> CLASSIFICATION FAILED: {err}")
    ctx.class_map = class_map

    dom_class = [a for a in classified_articles if a.category == NewsCategory.DOMESTIC]
    india_class = [a for a in classified_articles if a.category == NewsCategory.INDIA]
    intl_class = [a for a in classified_articles if a.category == NewsCategory.INTERNATIONAL]

    log_exec(f"Stage 4 Summary:")
    log_exec(f"  Live Gemini:      {live_class_count}")
    log_exec(f"  Offline fallback: {offline_class_count}")
    log_exec(f"  Hard events:      {len(classified_articles)} ({len(dom_class)} Domestic, {len(india_class)} India, {len(intl_class)} Intl)")

    # Cluster articles into events
    clusterer = EventClusterer()
    raw_events = clusterer.cluster_articles_into_events(classified_articles)
    log_exec(f"Clustered {len(classified_articles)} articles into {len(raw_events)} distinct events.")

    for event in raw_events:
        companies: Set[str] = set()
        facts: Set[str] = set(event.financial_figures)
        for aid in event.article_ids:
            if aid in class_map:
                c = class_map[aid]
                if c.company_names:
                    companies.update(c.company_names)
                if c.financial_numbers:
                    facts.update(c.financial_numbers)
                if c.percentages:
                    facts.update(c.percentages)
        event.companies_involved = sorted(list(companies))
        event.financial_figures = sorted(list(facts))[:5]
        primary_art = ctx.articles_lookup.get(event.article_ids[0])
        if primary_art:
            event.event_category = reg_clf.classify_event(event, [primary_art])

    # =========================================================================
    # STAGE 5: Two-Source Verification + Active Corroboration
    # =========================================================================
    metrics.start_timer("verification_seconds")
    verified_events: List[Event] = []
    single_source_events: List[Event] = []
    high_confidence_single_candidates: List[Event] = []
    rejected_events_list: List[Dict] = []
    corroboration_searches = 0
    second_sources_found = 0
    organic_second_sources_found = 0

    articles_lookup = {a.id: a for a in ctx.all_extracted}
    ctx.articles_lookup = articles_lookup

    for event in raw_events:
        ev_arts = [articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup]
        if not ev_arts:
            continue
        v_res = verifier.verify_event(event, ev_arts, now_utc=run_reference_time, max_age_hours=24.0)
        if v_res.is_verified:
            event.event_category = reg_clf.classify_event(event, ev_arts)
            verified_events.append(event)
            organic_second_sources_found += 1
            log_exec(f"  -> ORGANIC TWO-SOURCE VERIFIED: {event.canonical_title} ({len(event.article_ids)} sources)")
        else:
            single_source_events.append(event)
            event.event_category = reg_clf.classify_event(event, ev_arts)

    # Active corroboration loop on single source events with Domestic reservation policy
    dom_count = count_quality_eligible_domestic(
        verified_events=verified_events,
        high_confidence_single_candidates=high_confidence_single_candidates,
        single_source_events=single_source_events,
        articles_lookup=articles_lookup,
        domestic_evaluator=domestic_evaluator,
        verifier=verifier,
        run_reference_time=run_reference_time,
    )
    for event in single_source_events:
        reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_count < 5 else 0
        if get_corroboration_count() >= (MAX_CORROBORATION_SEARCHES_PER_RUN - reserve):
            log_exec(f"  -> [CORROBORATION_BUDGET] Reserving {reserve} free RSS searches for Domestic (current dom={dom_count}/5). Stopping Stage 3 corroboration.")
            break
        primary_art = articles_lookup.get(event.article_ids[0])
        if not primary_art:
            continue
        corrob_res = active_corroborator.corroborate(event=event, primary_article=primary_art)
        corroboration_searches += 1
        increment_corroboration_count(1)
        if corrob_res.success and corrob_res.corroborating_article:
            sec_art = corrob_res.corroborating_article
            articles_lookup[sec_art.id] = sec_art
            event.article_ids.append(sec_art.id)
            ev_arts = [primary_art, sec_art]
            v_res = verifier.verify_event(event, ev_arts, now_utc=run_reference_time, max_age_hours=24.0)
            if v_res.is_verified:
                event.event_category = reg_clf.classify_event(event, ev_arts)
                verified_events.append(event)
                second_sources_found += 1
                dom_count = count_quality_eligible_domestic(
                    verified_events=verified_events,
                    high_confidence_single_candidates=high_confidence_single_candidates,
                    single_source_events=single_source_events,
                    articles_lookup=articles_lookup,
                    domestic_evaluator=domestic_evaluator,
                    verifier=verifier,
                    run_reference_time=run_reference_time,
                )

    # Single-Source Quality Gate Evaluation
    for event in single_source_events:
        if event in verified_events:
            continue
        primary_art = articles_lookup.get(event.article_ids[0])
        if not primary_art:
            continue
        if is_multi_event_roundup(event.canonical_title):
            rejected_events_list.append({"title": event.canonical_title, "reason": "ROUNDUP_SUMMARY"})
            continue
        event.event_category = reg_clf.classify_event(event, [primary_art])
        if event.event_category == NewsCategory.DOMESTIC:
            is_elig, conf, rsn = domestic_evaluator.evaluate(event, primary_art, now_utc=run_reference_time, max_age_hours=24.0)
        else:
            is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art, now_utc=run_reference_time, max_age_hours=24.0)

        if is_elig:
            event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
            event.verification_confidence = conf
            event.single_source_confidence_score = conf
            event.primary_publisher = primary_art.source_name
            event.primary_url = primary_art.url
            event.secondary_publisher = None
            event.secondary_url = None
            event.verification_reason = rsn
            high_confidence_single_candidates.append(event)
        else:
            rejected_events_list.append({"title": event.canonical_title, "reason": rsn})

    ctx.verified_events = verified_events
    ctx.high_confidence_single_candidates = high_confidence_single_candidates
    ctx.single_source_events = single_source_events
    ctx.rejected_events_list = rejected_events_list
    ctx.organic_second_sources_found = organic_second_sources_found
    ctx.second_sources_found = second_sources_found
    ctx.corroboration_searches = corroboration_searches

    save_json_artifact(data_dir / "verified_events.json", [e.model_dump() for e in verified_events])
    save_json_artifact(data_dir / "rejected_events.json", rejected_events_list)
    metrics.stop_timer("verification_seconds")

    # =========================================================================
    # DISCOVERY EXPANSION & QUALITY FALLBACK LADDER
    # =========================================================================
    pipeline_status = run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat=None: get_final_selectable_unique_events(ctx, category=cat),
        is_domestic_diversity_satisfied_fn=lambda evs: is_domestic_diversity_satisfied(evs, ctx),
        count_unique_section_events_fn=lambda cat: count_unique_section_events(cat, ctx),
        get_unique_candidate_events_fn=lambda: get_unique_candidate_events(ctx),
    )

    # =========================================================================
    # SECOND-SOURCE ENRICHMENT PHASE (Free Google News RSS)
    # =========================================================================
    run_second_source_enrichment(ctx)

    # =========================================================================
    # STAGE 6: Deduplication & History
    # =========================================================================
    accepted_stories, event_by_id = run_deduplication(ctx)

    # =========================================================================
    # POST-DEDUP REFILL (if any section < 5 after history dedup)
    # =========================================================================
    accepted_stories, event_by_id = run_post_dedup_refill(ctx, accepted_stories, event_by_id)

    # =========================================================================
    # STAGE 7: Ranking & Selection
    # =========================================================================
    candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, pipeline_status = run_ranking_and_selection(
        ctx, accepted_stories, event_by_id
    )

    # Candidate audit manifest
    print_candidate_audit(domestic_pool, india_pool, intl_pool, ctx.articles_lookup)

    if not sufficient:
        log_exec(f"  -> STAGE 8 SKIPPED: Insufficient stories (Dom={len(domestic_pool)}, India={len(india_pool)}, Intl={len(intl_pool)}).")
        selection_payload = BriefingEditorialPayload(domestic_stories=[], india_stories=[], international_stories=[])
        validation_report = None
        briefing_text = ""
    else:
        # =========================================================================
        # STAGE 8: Gemini Editorial
        # =========================================================================
        metrics.start_timer("editorial_seconds")
        log_exec("=" * 60)
        log_exec("STAGE 8: Gemini Editorial — final editorial curation across 3 sections")
        log_exec("=" * 60)
        editorial_engine = GeminiEditorialEngine()
        time.sleep(2)
        try:
            editorial_res = editorial_engine.select_and_synthesize_briefing(candidate_pool, ctx.articles_lookup)
        except Exception as e:
            log_exec(f"  -> ERROR during editorial call: {e}")
            editorial_res = EditorialResult(success=False, error_message=str(e), attempts=1)

        # Prepare deterministic Top 5 Domestic stories
        dom_stories_selected = []
        for s in candidate_pool.domestic_candidates[:5]:
            ev = s.event
            art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
            src = ev.primary_publisher or (art.source_name if art else "The Hindu")
            u = ev.primary_url or (art.url if art else f"https://example.com/dom-{ev.id}")
            sum_text = generate_deterministic_summary(art, ev, ev.canonical_title)
            sec_src = ev.secondary_publisher if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
            sec_u = ev.secondary_url if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
            dom_stories_selected.append(EditorialStorySelection(
                section="domestic",
                event_id=ev.id,
                headline=ev.canonical_title,
                summary=sum_text,
                source=src,
                url=u,
                secondary_source=sec_src,
                secondary_url=sec_u,
            ))

        selection_payload = None
        if editorial_res and editorial_res.success and editorial_res.selection:
            selection_payload = editorial_res.selection
            if not getattr(selection_payload, "domestic_stories", None) or len(selection_payload.domestic_stories) < 5:
                selection_payload.domestic_stories = dom_stories_selected
            log_exec(f"Stage 8 Summary:")
            log_exec(f"  Gemini selected: {len(selection_payload.domestic_stories)} Domestic + {len(selection_payload.india_stories)} India + {len(selection_payload.international_stories)} International")
        else:
            err = editorial_res.error_message if editorial_res else "Unknown editorial error"
            log_exec(f"Stage 8 Gemini unavailable/rate-limited ({err}) — using deterministic editorial fallback.")
            india_stories_selected = []
            for s in candidate_pool.india_candidates[:5]:
                ev = s.event
                art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                src = ev.primary_publisher or (art.source_name if art else "Business Standard")
                u = ev.primary_url or (art.url if art else f"https://example.com/india-{ev.id}")
                sum_text = generate_deterministic_summary(art, ev, ev.canonical_title)
                sec_src = ev.secondary_publisher if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
                sec_u = ev.secondary_url if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
                india_stories_selected.append(EditorialStorySelection(
                    section="india",
                    event_id=ev.id,
                    headline=ev.canonical_title,
                    summary=sum_text,
                    source=src,
                    url=u,
                    secondary_source=sec_src,
                    secondary_url=sec_u,
                ))
            intl_stories_selected = []
            for s in candidate_pool.international_candidates[:5]:
                ev = s.event
                art = ctx.articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                src = ev.primary_publisher or (art.source_name if art else "Reuters")
                u = ev.primary_url or (art.url if art else f"https://example.com/intl-{ev.id}")
                sum_text = generate_deterministic_summary(art, ev, ev.canonical_title)
                sec_src = ev.secondary_publisher if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
                sec_u = ev.secondary_url if ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else None
                intl_stories_selected.append(EditorialStorySelection(
                    section="international",
                    event_id=ev.id,
                    headline=ev.canonical_title,
                    summary=sum_text,
                    source=src,
                    url=u,
                    secondary_source=sec_src,
                    secondary_url=sec_u,
                ))
            selection_payload = BriefingEditorialPayload(
                domestic_stories=dom_stories_selected,
                india_stories=india_stories_selected,
                international_stories=intl_stories_selected,
            )

        # Summary grounding validation
        for story in (selection_payload.domestic_stories + selection_payload.india_stories + selection_payload.international_stories):
            ev = event_by_id.get(story.event_id)
            art = ctx.articles_lookup.get(ev.article_ids[0]) if ev and ev.article_ids else None
            if not getattr(story, "summary", None):
                story.summary = generate_deterministic_summary(art, ev, story.headline)
            else:
                is_grounded, g_reason = validate_summary_grounding(story.summary, story.headline, event=ev, article=art)
                if not is_grounded:
                    logger.warning("Summary grounding failed for '%s' (%s) — using deterministic fallback", story.headline, g_reason)
                    story.summary = generate_deterministic_summary(art, ev, story.headline)
            if ev and ev.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED and ev.secondary_publisher and ev.secondary_url:
                story.secondary_source = ev.secondary_publisher
                story.secondary_url = ev.secondary_url

        save_json_artifact(data_dir / "final_15_stories.json", {
            "domestic":      [s.model_dump() for s in selection_payload.domestic_stories],
            "india":         [s.model_dump() for s in selection_payload.india_stories],
            "international": [s.model_dump() for s in selection_payload.international_stories],
        })
        save_json_artifact(data_dir / "final_10_stories.json", {
            "india":         [s.model_dump() for s in selection_payload.india_stories],
            "international": [s.model_dump() for s in selection_payload.international_stories],
        })
        metrics.stop_timer("editorial_seconds")

        # =========================================================================
        # STAGE 9: Final Validation
        # =========================================================================
        log_exec("=" * 60)
        log_exec("STAGE 9: Final Validation — 20-check deterministic gatekeeper")
        log_exec("=" * 60)
        validator = FinalValidationEngine(history_store=history_store)
        validation_report = validator.validate_briefing(
            payload=selection_payload,
            events_lookup=event_by_id,
            articles_lookup=ctx.articles_lookup,
            target_date=date.today(),
            strict_5_per_section=True,
            quality_ladder_mode=True,
            run_reference_time=run_reference_time,
        )
        log_exec(f"Stage 9 Summary:")
        log_exec(f"  Status:        {validation_report.status.value}")
        log_exec(f"  Passed checks: {validation_report.passed_checks} / 20")
        log_exec(f"  Failed checks: {validation_report.failed_checks} / 20")

        # =========================================================================
        # STAGE 10: Formatter
        # =========================================================================
        log_exec("=" * 60)
        log_exec("STAGE 10: Formatter — generating final briefing text")
        log_exec("=" * 60)
        briefing_text = ""
        if validation_report.is_valid:
            try:
                formatter = BriefingFormatter()
                formatted = formatter.format(selection_payload, briefing_date=date.today(), shorten_urls=False)
                briefing_text = formatted.text
                log_exec("Briefing successfully formatted!")
                with open(data_dir / "final_briefing.txt", "w", encoding="utf-8") as f:
                    f.write(briefing_text)

                history_stories = []
                for s in (selection_payload.domestic_stories + selection_payload.india_stories + selection_payload.international_stories):
                    ev = event_by_id.get(s.event_id)
                    comp = ev.companies_involved[0] if (ev and ev.companies_involved) else "unspecified"
                    history_stories.append({
                        "event_id":          s.event_id,
                        "event_fingerprint": ev.canonical_title if ev else s.headline,
                        "headline":          s.headline,
                        "company_name":      comp,
                        "category":          s.section,
                        "source_count":      len(ev.article_ids) if ev else 1,
                        "published_date":    date.today(),
                    })
                history_store.save_briefing(date.today(), history_stories)
                log_exec(f"Saved {len(history_stories)} selected stories to SQLite briefing history.")
            except Exception as e:
                log_exec(f"Error during briefing formatting: {e}")
        else:
            log_exec(f"Briefing NOT formatted — validation failed: {validation_report.failure_reason}")

    # =========================================================================
    # STAGE 11: Output & URL Audit
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 11: Output & URL Audit")
    log_exec("=" * 60)
    GeminiUsageLogger.print_summary()

    with open(data_dir / "pipeline_execution_log.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(execution_log_lines))

    # Audit final stories
    all_final = (
        (selection_payload.domestic_stories if selection_payload else []) +
        (selection_payload.india_stories if selection_payload else []) +
        (selection_payload.international_stories if selection_payload else [])
    )
    print_final_story_audit(all_final, event_by_id, ctx.articles_lookup)

    if briefing_text:
        print("\n" + "=" * 60)
        print("FINAL BRIEFING OUTPUT")
        print("=" * 60)
        print(briefing_text)
        with open(data_dir / "briefing_output.txt", "w", encoding="utf-8") as f:
            f.write(briefing_text)
    else:
        print("\nFINAL BRIEFING: NOT GENERATED")
        if not sufficient:
            deficient_sections = []
            if len(domestic_pool) < 5:
                deficient_sections.append(f"Domestic ({len(domestic_pool)}/5)")
            if len(india_pool) < 5:
                deficient_sections.append(f"India ({len(india_pool)}/5)")
            if len(intl_pool) < 5:
                deficient_sections.append(f"International ({len(intl_pool)}/5)")
            section_msg = f"{', '.join(deficient_sections)} remained below 5 quality-eligible stories after fallback exhaustion ({pipeline_status})."
            print(f"Reason: {section_msg}")
        elif validation_report and validation_report.failure_reason:
            print(f"Reason: {validation_report.failure_reason}")

    metrics.stop_timer("total_seconds")
    summary_metrics = metrics.format_summary()
    print("\n" + summary_metrics + "\n")
    logger.info("\n%s", summary_metrics)

    return 0 if (sufficient and validation_report and validation_report.is_valid) else 1
