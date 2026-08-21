"""
End-to-End Pipeline Runner and Tester.
Connects all system modules from discovery to formatting, running on today's real news.

Stages:
  1. Discovery
  2. Resolution & Extraction
  3. Hard Filtering
  4. Gemini AI Classification
  5. Two-Source Verification + Active Corroboration
  6. Deduplication & History
  7. Deterministic Ranking (freshness-aware)
  8. Gemini Editorial Selection (ONLY if 5+5 candidates exist)
  9. Final 20-Check Validation
  10. Formatter
  11. Output & URL Audit
"""

import os
import sys
import json
import logging
import time
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Set, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from config import get_settings
from app.logging_config import setup_logging, get_logger
from app.models import Article, Event, NewsCategory
from app.discovery import NewsDiscoveryService, GoogleNewsRSSDiscoveryProvider
from app.extraction import ArticleExtractor
from app.filtering import HardFilterEngine
from app.classification import AIArticleClassifier, GeminiRateLimitError
from app.verification import (
    TwoSourceVerifier,
    ActiveCorroborator,
    SerpAPICorroborator,
    reset_corroboration_counter,
    reset_serpapi_counter,
    get_corroboration_count,
    get_serpapi_count,
    get_serpapi_candidates_returned,
    get_serpapi_accepted_sources,
    get_serpapi_rejection_reasons,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)

from app.deduplication import DeduplicationEngine, HistoryStore
from app.ranking import CandidatePoolRanker, ArticlePreRanker, calculate_corroboration_priority
from app.ranking.scorer import InvestmentRelevanceScorer
from app.ai import GeminiEditorialEngine, BriefingEditorialPayload, GeminiUsageLogger, RATE_LIMITED_PREFIX, EditorialResult
from app.validation import FinalValidationEngine, ValidationStatus
from app.formatting.formatter import BriefingFormatter
from app.classification.region_classifier import EventRegionClassifier

logger = get_logger("pipeline.runner")

# --- Discovery expansion configuration ---
DISCOVERY_STEPS = [20, 30, 40, 50]  # candidate budgets per section per expansion step
MIN_VERIFIED_PER_SECTION = get_settings().MIN_VERIFIED_INDIA

# ---------------------------------------------------------------------------
# Helper: extract a batch of candidate articles
# ---------------------------------------------------------------------------

def _extract_candidates(
    candidates_with_country: List[Tuple[Any, str]],
    extractor: ArticleExtractor,
    seen_urls: Set[str],
    log_exec,
) -> Tuple[List[Article], List[Dict], int, int, int, int, int]:
    """
    Extract full text from a list of (DiscoveredArticle, country) candidates.
    Returns (extracted_articles, extraction_records, google_urls, resolved_ok, fallback_ok, pre_url_rejects, duplicate_seen).
    """
    total = len(candidates_with_country)
    extracted: List[Article] = []
    records: List[Dict] = []
    google_count = resolved_ok = fallback_ok = pre_url_rejects = duplicate_seen = 0

    for idx, (cand, country) in enumerate(candidates_with_country, 1):
        norm_url = cand.url.strip().lower().rstrip("/")
        if norm_url in seen_urls:
            duplicate_seen += 1
            continue
        seen_urls.add(norm_url)

        log_exec(f"[{idx}/{total}] Extracting ({country}): '{cand.title[:50]}' ({cand.source})")
        if extractor.resolver.is_google_news_url(cand.url):
            google_count += 1

        try:
            res = extractor.extract(
                url=cand.url,
                source_name=cand.source,
                candidate_title=cand.title,
                candidate_category=country,
                candidate_pub_date=cand.published_at,
            )

            rec = {
                "original_url":  res.original_url or cand.url,
                "resolved_url":  res.resolved_url or cand.url,
                "status_code":   res.status_code,
                "title":         res.article.title if res.article else cand.title,
                "publisher":     res.article.source_name if res.article else cand.source,
                "publication_date": (
                    res.article.published_at.isoformat()
                    if (res.article and res.article.published_at)
                    else None
                ),
                "word_count":         res.word_count,
                "extraction_method":  res.extraction_method,
                "success":            res.success,
                "failure_reason":     res.error_message,
            }
            records.append(rec)

            if res.success and res.article:
                extracted.append(res.article)
                if res.original_url != res.resolved_url:
                    resolved_ok += 1
                if res.extraction_method == "fallback":
                    fallback_ok += 1
                log_exec(f"  -> SUCCESS ({res.extraction_method}): {res.word_count} words | URL: {res.resolved_url[:60]}")
            else:
                if res.extraction_method in ("pre_url_filter", "blocked_cache") or (res.error_message and "PRE_EXTRACTION_URL_REJECTED" in res.error_message):
                    pre_url_rejects += 1
                    log_exec(f"  -> PRE_EXTRACTION_URL_REJECTED: {res.resolved_url[:60]} ({res.error_message})")
                else:
                    if res.original_url != res.resolved_url and not res.success:
                        pass  # resolved but extraction failed (paywall etc.)
                    log_exec(f"  -> FAILED ({res.extraction_method}): {res.error_message}")
        except Exception as e:
            log_exec(f"  -> ERROR during extraction: {e}")

    return extracted, records, google_count, resolved_ok, fallback_ok, pre_url_rejects, duplicate_seen


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(max_india: Optional[int] = None, max_international: Optional[int] = None) -> int:
    setup_logging()
    settings = get_settings()
    if max_india is None:
        max_india = settings.MAX_DISCOVERY_INDIA
    if max_international is None:
        max_international = settings.MAX_DISCOVERY_INTL

    data_dir = Path("./data")
    data_dir.mkdir(parents=True, exist_ok=True)

    execution_log_lines: List[str] = []

    def log_exec(msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{ts}] {msg}"
        execution_log_lines.append(formatted)
        print(formatted)
        logger.info(msg)

    log_exec("=====================================================================")
    log_exec(f"STARTING END-TO-END PIPELINE RUN (India: {max_india}, Intl: {max_international})")
    log_exec("=====================================================================")

    GeminiUsageLogger.reset()
    reset_corroboration_counter()
    reset_serpapi_counter()

    history_store = HistoryStore(db_path=settings.DATABASE_URL)
    log_exec(f"Initializing History Store: {settings.DATABASE_URL}")

    discovery_service = NewsDiscoveryService(provider=GoogleNewsRSSDiscoveryProvider())
    extractor = ArticleExtractor()
    reg_clf = EventRegionClassifier()

    # =========================================================================
    # STAGE 1 + 2: Discovery Reserve Pool & Extraction
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 1+2: Discovery Reserve Pool → Resolution → Extraction")
    log_exec("=" * 60)

    seen_urls: Set[str] = set()
    all_extracted: List[Article] = []
    all_records: List[Dict] = []
    total_google = total_resolved = total_fallback = total_pre_url_rejects = 0
    duplicate_seen_candidates = 0
    expansion_new_candidates = 0

    log_exec(f"Fetching discovery reserve pools (up to {max_india} India, {max_international} International)...")
    initial_discovery = discovery_service.discover_all(max_india=max_india, max_international=max_international)
    india_reserve_pool = initial_discovery.get("india", [])
    intl_reserve_pool  = initial_discovery.get("international", [])

    discovered_total = len(india_reserve_pool) + len(intl_reserve_pool)
    log_exec(f"Discovery Reserve Pool loaded: {len(india_reserve_pool)} India + {len(intl_reserve_pool)} International (Total: {discovered_total})")

    # Pass 1: Extract top candidates from reserve pool (e.g. up to 20 each)
    initial_india = min(len(india_reserve_pool), DISCOVERY_STEPS[0])
    initial_intl  = min(len(intl_reserve_pool), DISCOVERY_STEPS[0])

    pass1_candidates = (
        [(c, "india") for c in india_reserve_pool[:initial_india]] +
        [(c, "international") for c in intl_reserve_pool[:initial_intl]]
    )
    processed_india = initial_india
    processed_intl  = initial_intl

    log_exec(f"[Pass 1] Processing {initial_india} India + {initial_intl} International candidates from reserve...")
    batch_arts, batch_recs, gc, ro, fo, pur, dup = _extract_candidates(
        pass1_candidates, extractor, seen_urls, log_exec
    )
    all_extracted.extend(batch_arts)
    all_records.extend(batch_recs)
    total_google += gc; total_resolved += ro; total_fallback += fo; total_pre_url_rejects += pur
    duplicate_seen_candidates += dup
    processed_pass1 = len(batch_arts)
    reserve_remaining = (len(india_reserve_pool) - processed_india) + (len(intl_reserve_pool) - processed_intl)

    log_exec(f"Pass 1 extracted: {len(batch_arts)} articles (Reserve remaining: {reserve_remaining})")

    log_exec(f"Pass 1: {len(batch_arts)} articles extracted from {initial_india + initial_intl} candidates.")

    # =========================================================================
    # STAGE 3: Hard Filtering
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 3: Filtering — hard business event deterministic filter engine")
    log_exec("=" * 60)
    filter_engine = HardFilterEngine()
    accepted_articles, rejections = filter_engine.filter_candidates(all_extracted)

    india_accepted   = [a for a in accepted_articles if a.category == NewsCategory.INDIA]
    intl_accepted    = [a for a in accepted_articles if a.category == NewsCategory.INTERNATIONAL]

    log_exec(f"Stage 3 Summary:")
    log_exec(f"  Accepted:  {len(accepted_articles)} ({len(india_accepted)} India, {len(intl_accepted)} Intl)")
    log_exec(f"  Rejected:  {len(rejections)}")

    # Log per-rejection detail
    rejection_by_rule: Dict[str, int] = {}
    for r in rejections:
        rule = r.rule_failed or "UNKNOWN"
        rejection_by_rule[rule] = rejection_by_rule.get(rule, 0) + 1
        logger.debug(
            "REJECTED | TITLE: '%s' | PUBLISHER: %s | RULE: %s | REASON: %s",
            (r.article_title or "")[:60], "N/A", rule, r.rejection_reason,
        )
    for rule, count in sorted(rejection_by_rule.items()):
        log_exec(f"  Rejections by {rule}: {count}")

    # Save
    with open(data_dir / "filtered_candidates.json", "w", encoding="utf-8") as f:
        json.dump([a.model_dump() for a in accepted_articles], f, indent=2, default=str)
    with open(data_dir / "rejected_candidates.json", "w", encoding="utf-8") as f:
        json.dump(
            [{"url": r.article_url, "title": r.article_title, "rule_failed": r.rule_failed, "reason": r.rejection_reason}
             for r in rejections],
            f, indent=2, default=str
        )

    # =========================================================================
    # STAGE 4: Gemini AI Classification (Pre-ranked)
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 4: Classification — Gemini AI article classifier")
    log_exec("=" * 60)
    pre_ranker = ArticlePreRanker()
    gemini_candidates = pre_ranker.select_top_balanced_candidates(
        accepted_articles, max_total=settings.MAX_GEMINI_CLASSIFICATIONS
    )
    gemini_urls = {art.url for art in gemini_candidates}
    remaining_accepted = [art for art in accepted_articles if art.url not in gemini_urls]
    ordered_accepted_articles = gemini_candidates + remaining_accepted

    classifier = AIArticleClassifier(max_articles=settings.MAX_GEMINI_CLASSIFICATIONS)
    classified_articles: List[Tuple[Article, Any]] = []
    live_class_count = offline_class_count = 0

    for idx, art in enumerate(ordered_accepted_articles, 1):
        log_exec(f"[{idx}/{len(ordered_accepted_articles)}] Classifying: {art.title[:50]}")
        if idx > 1 and live_class_count < settings.MAX_GEMINI_CLASSIFICATIONS and not getattr(classifier, "_force_offline_mode", False):
            time.sleep(4)

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
            if c.is_hard_business_event and c.is_investment_relevant:
                classified_articles.append((art, c))
                mode_str = "Live Gemini" if res.attempts > 0 else "Offline Heuristic"
                log_exec(f"  -> ACCEPTED ({mode_str}): {c.event_type.value} | Companies: {c.company_names}")
            else:
                log_exec(f"  -> REJECTED BY AI: Event={c.event_type.value}, HardEvent={c.is_hard_business_event}, InvRel={c.is_investment_relevant}")
        else:
            err = res.error_message if res else "Unknown error"
            log_exec(f"  -> CLASSIFICATION FAILED: {err}")

    log_exec(f"Stage 4 Summary:")
    log_exec(f"  Passed AI filters: {len(classified_articles)}")
    log_exec(f"  Live Gemini calls: {live_class_count}")
    log_exec(f"  Offline heuristic: {offline_class_count}")

    # =========================================================================
    # STAGE 5: Two-Source Verification + Active Corroboration
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 5: Verification — two-source check + active corroboration")
    log_exec("=" * 60)
    from app.deduplication.clusterer import EventClusterer
    clusterer = EventClusterer()

    articles_to_cluster = [pair[0] for pair in classified_articles]
    raw_events = clusterer.cluster_articles_into_events(articles_to_cluster)

    class_map = {pair[0].id: pair[1] for pair in classified_articles}
    articles_lookup: Dict[str, Article] = {art.id: art for art in articles_to_cluster}

    # Enrich events with company/financial data from classifications
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
        event.financial_figures  = sorted(list(facts))[:5]
        primary_art = articles_lookup.get(event.article_ids[0])
        if primary_art:
            event.event_category = reg_clf.classify_event(event, [primary_art])

    verifier     = TwoSourceVerifier()
    corroborator = ActiveCorroborator(extractor=extractor)
    serpapi_corroborator = SerpAPICorroborator(extractor=extractor)

    verified_events: List[Event] = []
    single_source_events: List[Event] = []
    rejected_events_list: List[Dict] = []
    corroboration_searches = 0
    second_sources_found = 0
    # Stage 5 initial corroboration budget limits (leaves headroom for expansion passes)
    STAGE5_INITIAL_MAX_RSS = 12
    STAGE5_INITIAL_MAX_SERPAPI = 4

    # Separate verified multi-source vs single-source candidates by section
    india_single_source: List[Tuple[float, Event, Optional[Article]]] = []
    intl_single_source: List[Tuple[float, Event, Optional[Article]]] = []

    for event in raw_events:
        event_articles = [articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup]
        verif_res = verifier.verify_event(event, event_articles)

        if verif_res.is_verified:
            verified_events.append(event)
            log_exec(f"  -> VERIFIED: {event.canonical_title} ({len(event.article_ids)} sources)")
        elif verif_res.verification_status.value == "UNVERIFIED_SINGLE_SOURCE":
            primary_art = event_articles[0] if event_articles else None
            prio = calculate_corroboration_priority(event, primary_art)
            if event.event_category == NewsCategory.INDIA:
                india_single_source.append((prio, event, primary_art))
            else:
                intl_single_source.append((prio, event, primary_art))
        else:
            rejected_events_list.append({
                "event_title": event.canonical_title,
                "sources": [a.source_name for a in event_articles],
                "reason": verif_res.matching_details or "Failed two-source verification",
            })
            log_exec(f"  -> REJECTED: {event.canonical_title} — {verif_res.matching_details}")

    # Sort each section descending by corroboration priority
    india_single_source.sort(key=lambda x: x[0], reverse=True)
    intl_single_source.sort(key=lambda x: x[0], reverse=True)

    # Interleave fairly between India and International
    interleaved_candidates: List[Tuple[float, Event, Optional[Article]]] = []
    max_len = max(len(india_single_source), len(intl_single_source))
    for i in range(max_len):
        if i < len(india_single_source):
            interleaved_candidates.append(india_single_source[i])
        if i < len(intl_single_source):
            interleaved_candidates.append(intl_single_source[i])

    for prio, event, primary_art in interleaved_candidates:
        single_source_events.append(event)
        if prio < 40.0:
            log_exec(f"  -> LOW CORROBORATION PRIORITY ({prio:.0f}/100) — skipping search for: {event.canonical_title[:50]}")
            rejected_events_list.append({
                "event_title": event.canonical_title,
                "sources": [primary_art.source_name] if primary_art else [],
                "reason": f"LOW_CORROBORATION_PRIORITY: Score {prio:.0f}/100 below threshold (live quote/commentary/trend)",
            })
            continue

        if primary_art:
            # Check Stage 5 initial budget caps (leave headroom for expansion)
            rss_budget_ok = get_corroboration_count() < STAGE5_INITIAL_MAX_RSS
            serpapi_budget_ok = (
                serpapi_corroborator.has_api_key and
                get_serpapi_count() < STAGE5_INITIAL_MAX_SERPAPI
            )

            if not rss_budget_ok and not serpapi_budget_ok:
                log_exec(f"  -> Stage 5 initial budget reached (RSS={get_corroboration_count()}/{STAGE5_INITIAL_MAX_RSS}, SerpAPI={get_serpapi_count()}/{STAGE5_INITIAL_MAX_SERPAPI}) — saving remaining budget for expansion reserve.")
                continue

            log_exec(f"  -> SINGLE SOURCE (Priority {prio:.0f}/100) — attempting corroboration for: {event.canonical_title[:50]}")
            corr_result = None
            if rss_budget_ok:
                corr_result = corroborator.corroborate(event=event, primary_article=primary_art)
                corroboration_searches += corr_result.queries_fired

            # Optional SerpAPI fallback if normal Google News RSS corroboration missed
            if (corr_result is None or not corr_result.success) and serpapi_budget_ok:
                log_exec(f"  -> RSS missed — attempting SerpAPI fallback for: {event.canonical_title[:50]}")
                corr_result = serpapi_corroborator.corroborate(event=event, primary_article=primary_art)
                corroboration_searches += corr_result.queries_fired

            if corr_result and corr_result.success and corr_result.corroborating_article:
                second_sources_found += 1
                corr_art = corr_result.corroborating_article
                articles_lookup[corr_art.id] = corr_art
                event.article_ids.append(corr_art.id)

                # Re-verify with the new article
                re_verif = verifier.verify_event(event, [primary_art, corr_art])
                if re_verif.is_verified:
                    verified_events.append(event)
                    log_exec(
                        f"    CORROBORATION SUCCESS: {event.canonical_title[:45]} "
                        f"| Src1={primary_art.source_name} | Src2={corr_art.source_name}"
                    )
                else:
                    rejected_events_list.append({
                        "event_title": event.canonical_title,
                        "sources": [a.source_name for a in [primary_art, corr_art]],
                        "reason": re_verif.matching_details,
                    })
                    log_exec(f"    CORROBORATION: 2nd source found but still rejected: {re_verif.matching_details}")
            else:
                fail_reason = corr_result.failure_reason if corr_result else "Corroboration budget skipped"
                rejected_events_list.append({
                    "event_title": event.canonical_title,
                    "sources": [primary_art.source_name],
                    "reason": fail_reason,
                })
                log_exec(f"    CORROBORATION FAILED: {fail_reason}")
        else:
            rejected_events_list.append({
                "event_title": event.canonical_title,
                "sources": [],
                "reason": "Single source with no primary article available",
            })

    log_exec(f"Stage 5 Summary:")
    log_exec(f"  Events found:            {len(raw_events)}")
    log_exec(f"  Single-source events:    {len(single_source_events)}")
    log_exec(f"  Corroboration searches:  {corroboration_searches}")
    log_exec(f"  Second sources found:    {second_sources_found}")
    log_exec(f"  Verified events:         {len(verified_events)}")
    log_exec(f"  Verification failures:   {len(rejected_events_list)}")

    # Save
    with open(data_dir / "verified_events.json", "w", encoding="utf-8") as f:
        json.dump([e.model_dump() for e in verified_events], f, indent=2, default=str)
    with open(data_dir / "rejected_events.json", "w", encoding="utf-8") as f:
        json.dump(rejected_events_list, f, indent=2, default=str)

    # =========================================================================
    # DISCOVERY EXPANSION (if insufficient verified events)
    # =========================================================================
    for e in verified_events:
        e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
        e.event_category = reg_clf.classify_event(e, e_arts)

    india_verified = [e for e in verified_events if e.event_category == NewsCategory.INDIA]
    intl_verified  = [e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL]

    expansion_pass = 1
    max_expansion_passes = 4

    while expansion_pass <= max_expansion_passes:
        # Calculate section deficits
        india_deficit = max(0, settings.MIN_VERIFIED_INDIA - len(india_verified))
        intl_deficit  = max(0, settings.MIN_VERIFIED_INTL - len(intl_verified))

        if india_deficit == 0 and intl_deficit == 0:
            log_exec(f"Sufficiency gate satisfied: India={len(india_verified)}, Intl={len(intl_verified)} (no deficits).")
            break

        rss_available = get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN
        serpapi_available = (
            serpapi_corroborator.has_api_key and
            get_serpapi_count() < settings.MAX_SERPAPI_SEARCHES_PER_RUN
        )

        if not rss_available and not serpapi_available:
            log_exec("Both corroboration mechanisms exhausted (RSS & SerpAPI) — stopping discovery expansion.")
            break

        # Check unseen candidates in reserve pools
        unseen_india = india_reserve_pool[processed_india:] if processed_india < len(india_reserve_pool) else []
        unseen_intl  = intl_reserve_pool[processed_intl:] if processed_intl < len(intl_reserve_pool) else []

        step_india = min(len(unseen_india), min(20, max(5, india_deficit * 5))) if india_deficit > 0 else 0
        step_intl  = min(len(unseen_intl), min(20, max(5, intl_deficit * 5))) if intl_deficit > 0 else 0

        expansion_candidates = []
        if step_india > 0:
            expansion_candidates.extend([(c, "india") for c in unseen_india[:step_india]])
            processed_india += step_india
        if step_intl > 0:
            expansion_candidates.extend([(c, "international") for c in unseen_intl[:step_intl]])
            processed_intl += step_intl

        # If reserve pool is exhausted for a deficient section, perform targeted category search
        if step_india == 0 and india_deficit > 0:
            extra_india = discovery_service.discover_india_news(
                categories=["earnings_results", "acquisitions", "fundraises", "regulatory_actions"],
                max_candidates=10
            )
            unseen_extra_india = [c for c in extra_india if c.url.strip().lower().rstrip("/") not in seen_urls]
            if unseen_extra_india:
                expansion_candidates.extend([(c, "india") for c in unseen_extra_india[:10]])

        if step_intl == 0 and intl_deficit > 0:
            extra_intl = discovery_service.discover_international_news(
                categories=["us_earnings", "us_ma", "us_fundraises", "regulatory_actions"],
                max_candidates=10
            )
            unseen_extra_intl = [c for c in extra_intl if c.url.strip().lower().rstrip("/") not in seen_urls]
            if unseen_extra_intl:
                expansion_candidates.extend([(c, "international") for c in unseen_extra_intl[:10]])

        if not expansion_candidates:
            log_exec("No unseen candidates remaining in reserve or targeted expansion. Stopping expansion.")
            break

        expansion_pass += 1
        log_exec(
            f"[Pass {expansion_pass}] SECTION DEFICIT EXPANSION: "
            f"India verified={len(india_verified)} (Deficit={india_deficit}), "
            f"Intl verified={len(intl_verified)} (Deficit={intl_deficit}), "
            f"Extracting {len(expansion_candidates)} new candidates."
        )

        batch_arts2, batch_recs2, gc2, ro2, fo2, pur2, dup2 = _extract_candidates(
            expansion_candidates, extractor, seen_urls, log_exec
        )
        all_extracted.extend(batch_arts2)
        all_records.extend(batch_recs2)
        total_google += gc2; total_resolved += ro2; total_fallback += fo2; total_pre_url_rejects += pur2
        duplicate_seen_candidates += dup2
        expansion_new_candidates += len(batch_arts2)
        reserve_remaining = max(0, len(india_reserve_pool) - processed_india) + max(0, len(intl_reserve_pool) - processed_intl)

        if not batch_arts2:
            log_exec("  No new articles discovered in this expansion pass. Stopping.")
            break

        # Filter new batch
        new_accepted, new_rejections = filter_engine.filter_candidates(batch_arts2)
        rejections.extend(new_rejections)
        accepted_articles.extend(new_accepted)

        # Classify new accepted articles
        for art in new_accepted:
            log_exec(f"  [Expansion] Classifying: {art.title[:50]}")
            if live_class_count < settings.MAX_GEMINI_CLASSIFICATIONS:
                time.sleep(4)
            try:
                res = classifier.classify(art)
                if res.attempts > 0:
                    live_class_count += 1
                else:
                    offline_class_count += 1
            except Exception as e:
                log_exec(f"  -> ERROR classifying: {e}")
                continue

            if res and res.success and res.classification:
                c = res.classification
                if c.is_hard_business_event and c.is_investment_relevant:
                    classified_articles.append((art, c))
                    articles_to_cluster.append(art)
                    articles_lookup[art.id] = art
                    class_map[art.id] = c

                    # Cluster + verify this new article
                    new_raw = clusterer.cluster_articles_into_events([art])
                    for event in new_raw:
                        event.event_category = reg_clf.classify_event(event, [art])
                        for aid in event.article_ids:
                            existing_event = next(
                                (e for e in verified_events + single_source_events
                                 if aid in e.article_ids or
                                 verifier.is_same_underlying_event(
                                     articles_lookup.get(e.article_ids[0], art), art
                                 )[0]),
                                None
                            )
                            if existing_event:
                                if art.id not in existing_event.article_ids:
                                    existing_event.article_ids.append(art.id)
                                    # Re-verify
                                    ev_arts = [articles_lookup[i] for i in existing_event.article_ids if i in articles_lookup]
                                    rv = verifier.verify_event(existing_event, ev_arts)
                                    if rv.is_verified and existing_event not in verified_events:
                                        existing_event.event_category = reg_clf.classify_event(existing_event, ev_arts)
                                        verified_events.append(existing_event)
                                        log_exec(f"    EXPANSION VERIFIED: {existing_event.canonical_title[:45]}")
                            else:
                                # Prioritize corroboration only if section still has deficit
                                is_india_event = (event.event_category == NewsCategory.INDIA)
                                section_has_deficit = (india_deficit > 0 if is_india_event else intl_deficit > 0)
                                if not section_has_deficit:
                                    single_source_events.append(event)
                                    continue

                                ev_arts = [articles_lookup.get(aid2) for aid2 in event.article_ids if aid2 in articles_lookup]
                                ev_arts = [a for a in ev_arts if a]
                                prim = ev_arts[0] if ev_arts else art

                                corr = None
                                if get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN:
                                    corr = corroborator.corroborate(event=event, primary_article=prim)
                                    corroboration_searches += corr.queries_fired

                                if (corr is None or not corr.success) and serpapi_corroborator.has_api_key:
                                    if get_serpapi_count() < settings.MAX_SERPAPI_SEARCHES_PER_RUN:
                                        log_exec(f"  [Expansion] Attempting SerpAPI fallback for: {event.canonical_title[:50]}")
                                        corr = serpapi_corroborator.corroborate(event=event, primary_article=prim)
                                        corroboration_searches += corr.queries_fired

                                if corr and corr.success and corr.corroborating_article:
                                    second_sources_found += 1
                                    ca = corr.corroborating_article
                                    articles_lookup[ca.id] = ca
                                    event.article_ids.append(ca.id)
                                    rv2 = verifier.verify_event(event, [prim, ca])
                                    if rv2.is_verified:
                                        event.event_category = reg_clf.classify_event(event, [prim, ca])
                                        verified_events.append(event)
                                        log_exec(f"    EXPANSION CORROBORATED: {event.canonical_title[:45]} | Src2={ca.source_name}")

        for e in verified_events:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)

        india_verified = [e for e in verified_events if e.event_category == NewsCategory.INDIA]
        intl_verified  = [e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL]
        log_exec(f"  After expansion pass {expansion_pass}: India verified={len(india_verified)}, Intl verified={len(intl_verified)}")

    india_verified = [e for e in verified_events if e.event_category == NewsCategory.INDIA]
    intl_verified  = [e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL]

    # =========================================================================
    # STAGE 6: Deduplication & History
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 6: Deduplication — 3-day SQLite lookback and company restrictions")
    log_exec("=" * 60)
    dedup_engine = DeduplicationEngine(history_store=history_store)

    candidate_stories = []
    event_by_id: Dict[str, Event] = {}
    for event in verified_events:
        event_by_id[event.id] = event
        comp = event.companies_involved[0] if event.companies_involved else "unspecified"
        primary_aid = event.article_ids[0]
        event_type_str = class_map[primary_aid].event_type.value if primary_aid in class_map else "OTHER"
        candidate_stories.append({
            "event_id":    event.id,
            "headline":    event.canonical_title,
            "company_name": comp,
            "event_type":  event_type_str,
            "category":    "india" if event.event_category == NewsCategory.INDIA else "international",
            "key_facts":   event.financial_figures,
        })

    accepted_stories, rejected_stories = dedup_engine.filter_stories(
        candidate_stories=candidate_stories,
        target_date=date.today(),
        lookback_days=settings.STORY_LOOKBACK_DAYS,
    )
    log_exec(f"Stage 6 Summary:")
    log_exec(f"  Accepted: {len(accepted_stories)}")
    log_exec(f"  Removed:  {len(rejected_stories)}")

    # =========================================================================
    # STAGE 7: Ranking (freshness-aware)
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 7: Ranking — deterministic investment relevance scores (freshness-aware)")
    log_exec("=" * 60)
    ranker  = CandidatePoolRanker()
    scorer  = InvestmentRelevanceScorer()
    accepted_events = [event_by_id[s["event_id"]] for s in accepted_stories if s["event_id"] in event_by_id]

    # Enrich scoring with freshness metadata from primary article
    for event in accepted_events:
        primary_aid = event.article_ids[0] if event.article_ids else None
        primary_art = articles_lookup.get(primary_aid) if primary_aid else None
        freshness = 0.8  # default
        if primary_art and primary_art.metadata:
            freshness = primary_art.metadata.get("freshness_score", 0.8)
        event.metadata = getattr(event, "metadata", {}) or {}
        try:
            event.metadata["freshness_score"] = freshness
        except Exception:
            pass

    candidate_pool = ranker.rank_events(events=accepted_events, top_n=10)
    india_pool  = candidate_pool.india_candidates
    intl_pool   = candidate_pool.international_candidates

    log_exec(f"Stage 7 Summary:")
    log_exec(f"  India candidates:         {len(india_pool)}")
    log_exec(f"  International candidates: {len(intl_pool)}")

    # =========================================================================
    # PIPELINE SUFFICIENCY GATE
    # =========================================================================
    india_pool_count = len(india_pool)
    intl_pool_count  = len(intl_pool)
    sufficient = india_pool_count >= MIN_VERIFIED_PER_SECTION and intl_pool_count >= MIN_VERIFIED_PER_SECTION

    if not sufficient:
        log_exec("=" * 60)
        log_exec("PIPELINE SUFFICIENCY GATE: INSUFFICIENT VERIFIED STORIES")
        log_exec(f"  India verified candidates:  {india_pool_count} / {MIN_VERIFIED_PER_SECTION} required")
        log_exec(f"  International candidates:  {intl_pool_count} / {MIN_VERIFIED_PER_SECTION} required")
        log_exec("  Stage 8 Editorial:        SKIPPED")
        log_exec("  Stage 9 Final Validation: SKIPPED")
        log_exec("  Stage 10 Formatter:       SKIPPED")
        log_exec("  Pipeline Status:          INSUFFICIENT_VERIFIED_STORIES")
        log_exec("=" * 60)

        # Stage 8 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 8: Gemini Editorial — SKIPPED (Sufficiency gate failed)")
        log_exec("=" * 60)
        log_exec(f"  -> STAGE 8 SKIPPED: Insufficient stories (India={india_pool_count}, Intl={intl_pool_count}). Needs {MIN_VERIFIED_PER_SECTION} each.")
        selection_payload = BriefingEditorialPayload(india_stories=[], international_stories=[])

        # Stage 9 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 9: Final Validation — SKIPPED (Sufficiency gate failed)")
        log_exec("=" * 60)
        log_exec("  -> STAGE 9 SKIPPED: Zero candidate briefing payload. Validation skipped cleanly.")
        validation_report = None

        # Stage 10 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 10: Formatter — SKIPPED (Sufficiency gate failed)")
        log_exec("=" * 60)
        log_exec("  -> STAGE 10 SKIPPED: Briefing text not generated.")
        briefing_text = ""
    else:
        # =========================================================================
        # STAGE 8: Gemini Editorial (only if sufficient)
        # =========================================================================
        log_exec("=" * 60)
        log_exec("STAGE 8: Gemini Editorial — final editorial curation")
        log_exec("=" * 60)
        editorial_engine = GeminiEditorialEngine()
        time.sleep(2)
        try:
            editorial_res = editorial_engine.select_and_synthesize_briefing(candidate_pool, articles_lookup)
        except Exception as e:
            log_exec(f"  -> ERROR during editorial call: {e}")
            editorial_res = EditorialResult(success=False, error_message=str(e), attempts=1)

        if editorial_res and not editorial_res.success:
            if (editorial_res.error_message or "").startswith(RATE_LIMITED_PREFIX):
                log_exec(f"  -> RATE_LIMITED: {editorial_res.error_message}")

        selection_payload = None
        if editorial_res and editorial_res.success and editorial_res.selection:
            selection_payload = editorial_res.selection
            log_exec(f"Stage 8 Summary:")
            log_exec(f"  Gemini selected: {len(selection_payload.india_stories)} India + {len(selection_payload.international_stories)} International")

            with open(data_dir / "final_10_stories.json", "w", encoding="utf-8") as f:
                json.dump({
                    "india":         [s.model_dump() for s in selection_payload.india_stories],
                    "international": [s.model_dump() for s in selection_payload.international_stories],
                }, f, indent=2)
        else:
            err = editorial_res.error_message if editorial_res else "Unknown editorial error"
            log_exec(f"Stage 8 Summary: FAILED/SKIPPED: {err}")
            selection_payload = BriefingEditorialPayload(india_stories=[], international_stories=[])

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
            articles_lookup=articles_lookup,
            target_date=date.today(),
            strict_5_per_section=True,
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
                formatted = formatter.format(selection_payload, briefing_date=date.today())
                briefing_text = formatted.text
                log_exec("Briefing successfully formatted!")
                with open(data_dir / "final_briefing.txt", "w", encoding="utf-8") as f:
                    f.write(briefing_text)

                # Persist to history
                history_stories = []
                for s in selection_payload.india_stories + selection_payload.international_stories:
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
                log_exec("Saved selected stories to SQLite briefing history.")
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
    gemini_stats = GeminiUsageLogger.summary()

    with open(data_dir / "pipeline_execution_log.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(execution_log_lines))

    # =========================================================================
    # FINAL REPORT
    # =========================================================================
    india_discovered_total = len(india_reserve_pool)
    intl_discovered_total  = len(intl_reserve_pool)
    total_discovered = discovered_total

    print("\n" + "=" * 60)
    print(" PIPELINE EXECUTION COMPLETE — FULL REPORT")
    print("=" * 60)
    print("\nDISCOVERY & RESERVE POOL")
    print("-" * 30)
    print(f"  India reserve discovered:   {len(india_reserve_pool)}")
    print(f"  International discovered:   {len(intl_reserve_pool)}")
    print(f"  Total discovered:           {discovered_total}")
    print(f"  Processed in Pass 1:        {processed_pass1}")
    print(f"  Reserve remaining:          {reserve_remaining}")
    print(f"  Expansion new candidates:   {expansion_new_candidates}")
    print(f"  Duplicate seen candidates:  {duplicate_seen_candidates}")

    # Compute deficits
    india_deficit = max(0, MIN_VERIFIED_PER_SECTION - len(india_verified))
    intl_deficit  = max(0, MIN_VERIFIED_PER_SECTION - len(intl_verified))

    # Compute same-event rejection reason breakdown
    same_event_rejection_counts: Dict[str, int] = {}
    for rej in rejected_events_list:
        raw_reason = rej.get("reason", "Unknown")
        if ":" in raw_reason:
            code = raw_reason.split(":")[0].strip()
        else:
            code = raw_reason[:40]
        same_event_rejection_counts[code] = same_event_rejection_counts.get(code, 0) + 1

    print("\nEXTRACTION & DOMAIN METRICS")
    print("-" * 30)
    print(f"  Successful:                 {len(all_extracted)}")
    print(f"  Failed:                     {total_discovered - len(all_extracted)}")
    print(f"  Pre-extraction URL rejects: {total_pre_url_rejects}")
    print(f"  Google URLs resolved:       {total_resolved}")
    print(f"  Fallback extractions:       {total_fallback}")
    if extractor.domain_extraction_stats:
        print("  Per-domain extraction stats:")
        for dom, d_stats in sorted(extractor.domain_extraction_stats.items(), key=lambda x: -x[1]["success"]):
            print(f"    - {dom}: ok={d_stats['success']}, fail={d_stats['failed']}, blocked_401_403={d_stats['blocked_401_403']}")
    print("\nFILTERING")
    print("-" * 30)
    print(f"  Accepted:                   {len(accepted_articles)}")
    print(f"  Rejected:                   {len(rejections)}")
    for rule, count in sorted(rejection_by_rule.items()):
        print(f"    [{rule}]:                {count}")
    print("\nCLASSIFICATION")
    print("-" * 30)
    print(f"  Live Gemini calls:          {live_class_count}")
    print(f"  Offline heuristic:          {offline_class_count}")
    print(f"  Accepted by AI:             {len(classified_articles)}")
    print("\nVERIFICATION & CORROBORATION")
    print("-" * 30)
    print(f"  Events found:               {len(raw_events)}")
    print(f"  Single-source events:       {len(single_source_events)}")
    print(f"  RSS corroboration searches: {get_corroboration_count()} / {MAX_CORROBORATION_SEARCHES_PER_RUN}")
    print(f"  SerpAPI searches:           {get_serpapi_count()} / {settings.MAX_SERPAPI_SEARCHES_PER_RUN}")
    print(f"  SerpAPI candidates returned:{get_serpapi_candidates_returned()}")
    print(f"  SerpAPI accepted 2nd sources:{get_serpapi_accepted_sources()}")
    print(f"  Total 2nd sources found:    {second_sources_found}")
    print(f"  Verified events:            {len(verified_events)}")
    print(f"  Verification failures:      {len(rejected_events_list)}")
    if same_event_rejection_counts:
        print("  Same-event rejection reasons:")
        for r_code, r_cnt in sorted(same_event_rejection_counts.items(), key=lambda x: -x[1]):
            print(f"    - [{r_code}]: {r_cnt}")
    serpapi_rejections = get_serpapi_rejection_reasons()
    if serpapi_rejections:
        print("  SerpAPI candidate rejection reasons:")
        for r_code, r_cnt in sorted(serpapi_rejections.items(), key=lambda x: -x[1]):
            print(f"    - [{r_code}]: {r_cnt}")
    print("\nDEDUPLICATION")
    print("-" * 30)
    print(f"  Removed (history/dedup):    {len(rejected_stories)}")
    print(f"  Remaining:                  {len(accepted_stories)}")
    print("\nRANKING & SUFFICIENCY")
    print("-" * 30)
    print(f"  India candidates:           {india_pool_count}")
    print(f"  International candidates:   {intl_pool_count}")
    print(f"  India deficit:              {india_deficit}")
    print(f"  International deficit:      {intl_deficit}")
    print(f"  Sufficiency gate:           {'PASSED' if sufficient else 'FAILED (INSUFFICIENT_VERIFIED_STORIES)'}")
    print("\nEDITORIAL (Gemini)")
    print("-" * 30)
    if sufficient and editorial_res and editorial_res.success:
        editorial_calls = gemini_stats["by_stage"].get("editorial", 0)
        print(f"  Gemini editorial calls:     {editorial_calls}")
        print(f"  Selected India:             {len(selection_payload.india_stories)}")
        print(f"  Selected International:     {len(selection_payload.international_stories)}")
    else:
        print("  Status:                     SKIPPED (INSUFFICIENT_VERIFIED_STORIES)")

    print("\nVALIDATION")
    print("-" * 30)
    if validation_report:
        print(f"  Status:                     {validation_report.status.value}")
        print(f"  Passed checks:              {validation_report.passed_checks} / 20")
        print(f"  Failed checks:              {validation_report.failed_checks} / 20")
        if not validation_report.is_valid:
            print(f"  Failure reason:             {validation_report.failure_reason}")
    else:
        print("  Status:                     SKIPPED (INSUFFICIENT_VERIFIED_STORIES)")
    print("\nGEMINI API USAGE")
    print("-" * 30)
    print(f"  Total calls:                {gemini_stats['total_calls']}")
    print(f"  Successful:                 {gemini_stats['successful']}")
    print(f"  Failed:                     {gemini_stats['failed']}")
    print(f"  429 rate-limited:           {gemini_stats['rate_limited_429']}")
    print(f"  Retries:                    {gemini_stats['retries']}")
    print(f"  By stage:                   {gemini_stats['by_stage']}")
    print("=" * 60 + "\n")

    # URL Audit for selected final stories
    all_final = selection_payload.india_stories + selection_payload.international_stories
    if all_final:
        print("=== FINAL STORY AUDIT ===")
        for idx, story in enumerate(all_final, 1):
            ev = event_by_id.get(story.event_id)
            art1_id = ev.article_ids[0] if (ev and ev.article_ids) else None
            art2_id = ev.article_ids[1] if (ev and len(ev.article_ids) > 1) else None
            art1 = articles_lookup.get(art1_id) if art1_id else None
            art2 = articles_lookup.get(art2_id) if art2_id else None
            orig = art1.metadata.get("original_url", story.url) if art1 else story.url
            freshness_score = art1.metadata.get("freshness_score", "N/A") if art1 else "N/A"
            freshness_bucket = art1.metadata.get("freshness_bucket", "N/A") if art1 else "N/A"
            age_hours = art1.metadata.get("age_hours", "N/A") if art1 else "N/A"

            print(f"\n[{idx}] SECTION: {story.section.upper()}")
            print(f"  TITLE:               {story.headline}")
            print(f"  PUBLISHER:           {story.source}")
            print(f"  ORIGINAL RSS URL:    {orig}")
            print(f"  RESOLVED URL:        {story.url}")
            print(f"  EVENT TYPE:          {ev.event_category.value if ev else 'N/A'}")
            print(f"  FRESHNESS:           {freshness_bucket} ({age_hours}h, score={freshness_score})")
            if art1:
                print(f"  SOURCE 1:")
                print(f"    Publisher:         {art1.source_name}")
                print(f"    URL:               {art1.url}")
                print(f"    Date:              {art1.published_at.isoformat() if art1.published_at else 'N/A'}")
            if art2:
                print(f"  SOURCE 2:")
                print(f"    Publisher:         {art2.source_name}")
                print(f"    URL:               {art2.url}")
                print(f"    Date:              {art2.published_at.isoformat() if art2.published_at else 'N/A'}")
            print(f"  VERIFICATION:        {'VERIFIED (2 independent sources)' if (ev and len(ev.article_ids) >= 2) else 'SINGLE SOURCE'}")
    else:
        print("=== URL AUDIT: No stories selected ===")
        if not sufficient:
            print(f"\nSTATUS: INSUFFICIENT_VERIFIED_STORIES")
            print(f"  India verified:       {india_pool_count} / {MIN_VERIFIED_PER_SECTION} required")
            print(f"  International:        {intl_pool_count} / {MIN_VERIFIED_PER_SECTION} required")
            print("  Reason: Two-source verification could not be completed for enough stories.")
            print("  This is NOT a validation failure — it means the news cycle did not produce")
            print("  sufficient corroborated events today within the quality requirements.")

    if briefing_text:
        print("\n--- [FINAL BRIEFING] ---")
        print(briefing_text)
        print("------------------------\n")
    else:
        print(f"\nFINAL BRIEFING: NOT GENERATED")
        if not sufficient:
            print("Reason: INSUFFICIENT_VERIFIED_STORIES — fewer than 5+5 independently verified events found.")
        elif validation_report and validation_report.failure_reason:
            print(f"Reason: {validation_report.failure_reason}")

    return 0 if (sufficient and validation_report and validation_report.is_valid) else 1


if __name__ == "__main__":
    st = get_settings()
    max_in  = int(sys.argv[1]) if len(sys.argv) > 1 else st.MAX_DISCOVERY_INDIA
    max_int = int(sys.argv[2]) if len(sys.argv) > 2 else st.MAX_DISCOVERY_INTL
    sys.exit(run_pipeline(max_india=max_in, max_international=max_int))
