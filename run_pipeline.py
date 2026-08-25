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
import re
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
    increment_corroboration_count,
    get_serpapi_count,
    get_serpapi_candidates_returned,
    get_serpapi_accepted_sources,
    get_serpapi_rejection_reasons,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)

from app.deduplication import DeduplicationEngine, HistoryStore
from app.deduplication.clusterer import EventClusterer
from app.filtering.rules import URLFilterRule
from app.models.entity_sanitizer import sanitize_company_entities
from app.ranking import CandidatePoolRanker, ArticlePreRanker, calculate_corroboration_priority
from app.ranking.scorer import InvestmentRelevanceScorer
from app.ai import GeminiEditorialEngine, BriefingEditorialPayload, GeminiUsageLogger, RATE_LIMITED_PREFIX, EditorialResult
from app.validation import FinalValidationEngine, ValidationStatus
from app.formatting.formatter import BriefingFormatter
from app.classification.region_classifier import EventRegionClassifier
from app.models.enums import VerificationTier
from app.verification.single_source import SingleSourceEvaluator, is_multi_event_roundup
from app.verification.query_builder import EventQueryBuilder, GENERIC_ENTITY_BLACKLIST

logger = get_logger("pipeline.runner")

# --- Discovery expansion configuration ---
DISCOVERY_STEPS = [20, 30, 40, 50]  # candidate budgets per section per expansion step
MIN_VERIFIED_PER_SECTION = get_settings().MIN_VERIFIED_INDIA


def _score_discovery_candidate(title: str) -> float:
    """Score candidate discovery headline to prioritize corporate actions and hard events."""
    t_low = title.lower()
    score = 50.0
    if re.search(r"\b(crore|cr|billion|million|\$|₹|\d+%)\b", t_low):
        score += 20.0
    if re.search(r"\b(acquires?|acquisition|stake|block deal|bought|buys|takeover|merger|amalgamation)\b", t_low):
        score += 25.0
    if re.search(r"\b(net profit|q[1-4] results|earnings|ebitda|quarterly profit)\b", t_low):
        score += 20.0
    if re.search(r"\b(ipo|drhp|anchor investors|raises funding|funding round)\b", t_low):
        score += 20.0
    if re.search(r"\b(rbi|sebi|cci|penalty|order|fine|probe)\b", t_low):
        score += 20.0
    if re.search(r"\b(stock to buy|target price|brokerage|recommendation|share price today|market wrap|sensex|nifty|live updates)\b", t_low):
        score -= 40.0
    return score


def get_candidate_published_at(candidate: Any) -> Optional[datetime]:
    """Centralized helper to safely get published_at from a DiscoveredArticle."""
    return getattr(candidate, "published_at", None)


def populate_event_companies(article: Article, classified_companies: List[str]) -> List[str]:
    """Populate clean primary companies for events created from expansion articles."""
    companies = sanitize_company_entities(classified_companies, publisher=article.source_name)
    if companies:
        return companies

    subject_match = re.match(
        r"^(.+?)\s+(?:bolsters|targets|acquires?|buys|sells|offloads|raises?|files|reports?|announces?|appoints?|resigns?|merges?)\b",
        article.title or "",
        flags=re.IGNORECASE,
    )
    if subject_match:
        subject = subject_match.group(1).strip(" ,:;-")
        companies = sanitize_company_entities([subject], publisher=article.source_name)
        if companies:
            return companies

    extracted_companies = [
        entity for entity in EventQueryBuilder.extract_entities(article)
        if entity.lower().strip() not in GENERIC_ENTITY_BLACKLIST
    ]
    return sanitize_company_entities(extracted_companies, publisher=article.source_name)


def get_section_quality_state(
    events: List[Event],
    high_conf_single_candidates: List[Event],
    category: NewsCategory,
) -> Dict[str, Any]:
    """Authoritative helper for hybrid section quality state."""
    two_source = [e for e in events if e.event_category == category and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    single_source = [e for e in high_conf_single_candidates if e.event_category == category and e.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE and e not in two_source]
    two_src_cnt = len(two_source)
    sng_src_cnt = len(single_source)
    eligible_total = two_src_cnt + min(2, sng_src_cnt)
    slot_deficit = max(0, 5 - eligible_total)
    two_source_min_deficit = max(0, 3 - two_src_cnt)
    single_source_capacity = max(0, 2 - sng_src_cnt)
    section_complete = (eligible_total >= 5 and two_src_cnt >= 3 and sng_src_cnt <= 2)
    return {
        "two_source_count": two_src_cnt,
        "single_source_count": sng_src_cnt,
        "eligible_total": eligible_total,
        "slot_deficit": slot_deficit,
        "two_source_min_deficit": two_source_min_deficit,
        "single_source_capacity": single_source_capacity,
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

        # Pre-extraction freshness check (if RSS published_at is available)
        pub_at = get_candidate_published_at(cand)
        if pub_at:
            pub_time = pub_at
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            age_hours = max(0.0, (now_utc - pub_time).total_seconds() / 3600.0)
            max_freshness_hours = getattr(get_settings(), "STORY_FRESHNESS_HOURS", 24.0)
            if age_hours > max_freshness_hours:
                log_exec(f"[{idx}/{total}] FINAL_MILE_STALE_PRE_REJECT: '{cand.title[:50]}' ({age_hours:.1f}h old > {max_freshness_hours:.0f}h limit)")
                continue

        log_exec(f"[{idx}/{total}] Extracting ({country}): '{cand.title[:50]}' ({cand.source})")
        if extractor.resolver.is_google_news_url(cand.url):
            google_count += 1

        try:
            res = extractor.extract(
                url=cand.url,
                source_name=cand.source,
                candidate_title=cand.title,
                candidate_category=country,
                candidate_pub_date=pub_at,
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
    internal_pipeline_errors = 0

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
    single_source_evaluator = SingleSourceEvaluator()

    verified_events: List[Event] = []
    single_source_events: List[Event] = []
    high_confidence_single_candidates: List[Event] = []
    rejected_events_list: List[Dict] = []
    corroboration_searches = 0
    second_sources_found = 0
    organic_second_sources_found = 0
    rss_india_used = 0
    rss_international_used = 0
    serpapi_india_used = 0
    serpapi_international_used = 0
    serpapi_attempted_event_keys: Set[str] = set()

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
            organic_second_sources_found += 1
            log_exec(f"  -> ORGANIC TWO-SOURCE VERIFIED: {event.canonical_title} ({len(event.article_ids)} sources)")
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
        is_india = (event.event_category == NewsCategory.INDIA)
        india_curr_verified = len([e for e in verified_events if e.event_category == NewsCategory.INDIA])
        intl_curr_verified = len([e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL])

        # Freeze section if already satisfied (>=5 verified)
        if is_india and india_curr_verified >= settings.MIN_VERIFIED_INDIA:
            log_exec(f"  -> SECTION_FROZEN_SKIP: India already {india_curr_verified}/5 — skipping search: {event.canonical_title[:50]}")
            if primary_art:
                is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art)
                if is_elig:
                    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    event.verification_confidence = conf
                    event.single_source_confidence_score = conf
                    event.primary_publisher = primary_art.source_name
                    event.primary_url = primary_art.url
                    event.verification_reason = rsn
                    high_confidence_single_candidates.append(event)
            continue
        if not is_india and intl_curr_verified >= settings.MIN_VERIFIED_INTL:
            log_exec(f"  -> SECTION_FROZEN_SKIP: International already {intl_curr_verified}/5 — skipping search: {event.canonical_title[:50]}")
            if primary_art:
                is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art)
                if is_elig:
                    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    event.verification_confidence = conf
                    event.single_source_confidence_score = conf
                    event.primary_publisher = primary_art.source_name
                    event.primary_url = primary_art.url
                    event.verification_reason = rsn
                    high_confidence_single_candidates.append(event)
            continue

        # Reject multi-event roundups before search
        if is_multi_event_roundup(event.canonical_title):
            log_exec(f"  -> REJECTED MULTI_EVENT_ROUNDUP: {event.canonical_title[:50]}")
            continue

        if prio < 50.0:
            log_exec(f"  -> LOW CORROBORATION PRIORITY ({prio:.0f}/100 < 50) — skipping search for: {event.canonical_title[:50]}")
            if primary_art:
                is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art)
                if is_elig:
                    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    event.verification_confidence = conf
                    event.single_source_confidence_score = conf
                    event.primary_publisher = primary_art.source_name
                    event.primary_url = primary_art.url
                    event.verification_reason = rsn
                    high_confidence_single_candidates.append(event)
                    log_exec(f"    QUALIFIED HIGH_CONFIDENCE_SINGLE_SOURCE: {event.canonical_title[:45]} | {rsn}")
            continue

        if primary_art:
            # Check Stage 5 initial budget caps (leave headroom for expansion)
            rss_budget_ok = get_corroboration_count() < STAGE5_INITIAL_MAX_RSS
            serpapi_budget_ok = (
                serpapi_corroborator.has_api_key and
                get_serpapi_count() < STAGE5_INITIAL_MAX_SERPAPI and
                prio >= 60.0
            )

            corr_result = None
            if rss_budget_ok:
                log_exec(f"  -> SINGLE SOURCE (Priority {prio:.0f}/100) — attempting RSS corroboration for: {event.canonical_title[:50]}")
                corr_result = corroborator.corroborate(event=event, primary_article=primary_art)
                if corr_result:
                    corroboration_searches += corr_result.queries_fired
                if is_india:
                    rss_india_used += corr_result.queries_fired
                else:
                    rss_international_used += corr_result.queries_fired

            # Optional SerpAPI fallback if normal Google News RSS corroboration missed
            if (corr_result is None or not corr_result.success) and serpapi_budget_ok:
                log_exec(f"  -> RSS missed — attempting SerpAPI fallback (Priority {prio:.0f}>=60) for: {event.canonical_title[:50]}")
                attempt_key = get_serpapi_event_key(event)
                if attempt_key in serpapi_attempted_event_keys:
                    log_exec(f"    SERPAPI_ALREADY_ATTEMPTED_SKIP: {event.canonical_title[:50]}")
                    corr_result = None
                else:
                    serpapi_attempted_event_keys.add(attempt_key)
                    serpapi_before = get_serpapi_count()
                    corr_result = serpapi_corroborator.corroborate(event=event, primary_article=primary_art)
                    serpapi_delta = get_serpapi_count() - serpapi_before
                    if is_india:
                        serpapi_india_used += serpapi_delta
                    else:
                        serpapi_international_used += serpapi_delta
                if corr_result:
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
                # Assess as High-Confidence Single-Source fallback
                is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art)
                if is_elig:
                    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    event.verification_confidence = conf
                    event.single_source_confidence_score = conf
                    event.primary_publisher = primary_art.source_name
                    event.primary_url = primary_art.url
                    event.verification_reason = rsn
                    high_confidence_single_candidates.append(event)
                    log_exec(f"    QUALIFIED HIGH_CONFIDENCE_SINGLE_SOURCE: {event.canonical_title[:45]} | {rsn}")
                else:
                    fail_reason = corr_result.failure_reason if corr_result else "Corroboration budget skipped"
                    rejected_events_list.append({
                        "event_title": event.canonical_title,
                        "sources": [primary_art.source_name],
                        "reason": f"{fail_reason}; Single-source reject: {rsn}",
                    })
                    log_exec(f"    CORROBORATION FAILED & SINGLE-SOURCE REJECTED: {rsn}")
        else:
            rejected_events_list.append({
                "event_title": event.canonical_title,
                "sources": [],
                "reason": "Single source with no primary article available",
            })

    log_exec(f"Stage 5 Summary:")
    log_exec(f"  Events found:                  {len(raw_events)}")
    log_exec(f"  Organic second sources found:  {organic_second_sources_found}")
    log_exec(f"  Active search 2nd sources:     {second_sources_found}")
    log_exec(f"  Two-source verified events:    {len(verified_events)}")
    log_exec(f"  High-confidence single events: {len(high_confidence_single_candidates)}")
    log_exec(f"  Corroboration searches used:   {corroboration_searches} (RSS: {rss_india_used + rss_international_used}, SerpAPI: {serpapi_india_used + serpapi_international_used})")

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
    max_expansion_passes = 6
    executed_final_mile_queries: Set[str] = set()

    while expansion_pass <= max_expansion_passes:
        # 1. Authoritative centralized hybrid quality state
        india_state = get_section_quality_state(verified_events, high_confidence_single_candidates, NewsCategory.INDIA)
        intl_state  = get_section_quality_state(verified_events, high_confidence_single_candidates, NewsCategory.INTERNATIONAL)

        if india_state["section_complete"] and intl_state["section_complete"]:
            log_exec(
                f"Sufficiency gate satisfied under Hybrid Policy: "
                f"India={india_state['two_source_count']} two-source + {min(2, india_state['single_source_count'])} single-source; "
                f"Intl={intl_state['two_source_count']} two-source + {min(2, intl_state['single_source_count'])} single-source."
            )
            break

        is_india_final_mile = (not india_state["section_complete"] and intl_state["section_complete"])
        is_intl_final_mile  = (not intl_state["section_complete"] and india_state["section_complete"])
        is_balanced_mode    = (not india_state["section_complete"] and not intl_state["section_complete"])
        pending_intl_upgrade_events = [
            event for event in single_source_events
            if event.event_category == NewsCategory.INTERNATIONAL and event not in verified_events
        ]
        intl_upgrade_available = has_unattempted_intl_upgrade(
            pending_intl_upgrade_events,
            articles_lookup,
            single_source_evaluator,
            serpapi_attempted_event_keys,
        )

        if is_intl_final_mile:
            log_exec(
                f"[INTL_FINAL_MILE_MODE] SlotsNeeded={intl_state['slot_deficit']} "
                f"TwoSourceNeeded={intl_state['two_source_min_deficit']} "
                f"SingleCapacity={intl_state['single_source_capacity']}"
            )
        elif is_india_final_mile:
            log_exec(
                f"[INDIA_FINAL_MILE_MODE] SlotsNeeded={india_state['slot_deficit']} "
                f"TwoSourceNeeded={india_state['two_source_min_deficit']} "
                f"SingleCapacity={india_state['single_source_capacity']}"
            )
        else:
            log_exec(
                f"[BALANCED_EXPANSION] India SlotsNeeded={india_state['slot_deficit']}, "
                f"Intl SlotsNeeded={intl_state['slot_deficit']}"
            )

        rss_available = get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN
        general_serpapi_limit = get_general_serpapi_limit(
            intl_state,
            settings.MAX_SERPAPI_SEARCHES_PER_RUN,
            upgrade_available=intl_upgrade_available,
        )
        serpapi_available = (
            serpapi_corroborator.has_api_key and
            get_serpapi_count() < general_serpapi_limit
        )
        serpapi_final_mile_available = (
            serpapi_corroborator.has_api_key and
            get_serpapi_count() < settings.MAX_SERPAPI_SEARCHES_PER_RUN
        )

        if not rss_available and not serpapi_available:
            log_exec(
                f"Active search budget reached (RSS: {get_corroboration_count()}/{MAX_CORROBORATION_SEARCHES_PER_RUN}, "
                f"SerpAPI: {get_serpapi_count()}/{settings.MAX_SERPAPI_SEARCHES_PER_RUN}). "
                f"Continuing reserve candidate processing, organic clustering, and single-source assessment."
            )

        # 2. Check unseen candidates in reserve pools with actual candidate ID tracking
        unseen_india = [c for c in india_reserve_pool if c.url.strip().lower().rstrip("/") not in seen_urls]
        unseen_intl  = [c for c in intl_reserve_pool if c.url.strip().lower().rstrip("/") not in seen_urls]
        log_exec(f"RESERVE_STATE: India unseen={len(unseen_india)}, Intl unseen={len(unseen_intl)}")

        step_india = min(len(unseen_india), min(20, max(5, india_state["slot_deficit"] * 5))) if not india_state["section_complete"] else 0
        step_intl  = min(len(unseen_intl), min(20, max(5, intl_state["slot_deficit"] * 5))) if not intl_state["section_complete"] else 0

        expansion_candidates = []
        if step_india > 0:
            expansion_candidates.extend([(c, "india") for c in unseen_india[:step_india]])
        if step_intl > 0:
            expansion_candidates.extend([(c, "international") for c in unseen_intl[:step_intl]])

        # 3. Fallback SerpAPI on pending single-source events when section is in final-mile
        if intl_state["two_source_count"] < 3 and serpapi_final_mile_available:
            pending_intl_singles = [
                e for e in single_source_events
                if e.event_category == NewsCategory.INTERNATIONAL
                and e not in verified_events
                and get_serpapi_event_key(e) not in serpapi_attempted_event_keys
            ]
            if pending_intl_singles:
                log_exec(f"[INTL_FINAL_MILE_MODE] Attempting SerpAPI on top pending International single-source events ({len(pending_intl_singles)} candidates)...")
                scored_pending = prioritize_intl_final_mile_candidates(
                    pending_intl_singles,
                    articles_lookup,
                    single_source_evaluator,
                )
                scored_pending = [
                    item for item in scored_pending
                    if item[1].verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    or single_source_evaluator.evaluate_event(item[1], item[2])[0]
                ]

                for pr, ev, prim_a in scored_pending:
                    live_intl_two_source_count = len([
                        item for item in verified_events
                        if item.event_category == NewsCategory.INTERNATIONAL
                        and item.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED
                    ])
                    if get_serpapi_count() >= settings.MAX_SERPAPI_SEARCHES_PER_RUN or live_intl_two_source_count >= 3 or pr < 60.0:
                        break
                    attempt_key = get_serpapi_event_key(ev)
                    if attempt_key in serpapi_attempted_event_keys:
                        log_exec(f"    SERPAPI_ALREADY_ATTEMPTED_SKIP: {ev.canonical_title[:50]}")
                        continue
                    serpapi_attempted_event_keys.add(attempt_key)
                    log_exec(f"  [INTL_FINAL_MILE] SerpAPI fallback on priority {pr:.0f}/100 event: {ev.canonical_title[:50]}")
                    serpapi_before = get_serpapi_count()
                    corr = serpapi_corroborator.corroborate(event=ev, primary_article=prim_a)
                    corroboration_searches += corr.queries_fired
                    serpapi_international_used += get_serpapi_count() - serpapi_before

                    if corr and corr.success and corr.corroborating_article:
                        second_sources_found += 1
                        ca = corr.corroborating_article
                        articles_lookup[ca.id] = ca
                        ev.article_ids.append(ca.id)
                        rv2 = verifier.verify_event(ev, [prim_a, ca])
                        if rv2.is_verified:
                            ev.event_category = reg_clf.classify_event(ev, [prim_a, ca])
                            verified_events.append(ev)
                            log_exec(f"    FINAL MILE VERIFIED: {ev.canonical_title[:45]} | Src2={ca.source_name}")
                    else:
                        is_elig, conf, rsn = single_source_evaluator.evaluate_event(ev, prim_a)
                        if is_elig:
                            ev.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                            ev.verification_confidence = conf
                            ev.single_source_confidence_score = conf
                            ev.primary_publisher = prim_a.source_name
                            ev.primary_url = prim_a.url
                            ev.verification_reason = rsn
                            if ev not in high_confidence_single_candidates:
                                high_confidence_single_candidates.append(ev)
                            log_exec(f"    FINAL MILE QUALIFIED SINGLE SOURCE: {ev.canonical_title[:45]} | {rsn}")

        if is_india_final_mile and not expansion_candidates and india_state["two_source_count"] < 3 and serpapi_available:
            pending_india_singles = [
                e for e in single_source_events
                if e.event_category == NewsCategory.INDIA and e not in verified_events
            ]
            if pending_india_singles:
                log_exec(f"[INDIA_FINAL_MILE_MODE] Attempting SerpAPI on top pending India single-source events ({len(pending_india_singles)} candidates)...")
                scored_pending = []
                for ev in pending_india_singles:
                    ev_arts = [articles_lookup.get(aid) for aid in ev.article_ids if aid in articles_lookup]
                    prim_a = ev_arts[0] if ev_arts else None
                    if prim_a:
                        pr = calculate_corroboration_priority(ev, prim_a)
                        scored_pending.append((pr, ev, prim_a))
                scored_pending.sort(key=lambda x: x[0], reverse=True)

                for pr, ev, prim_a in scored_pending:
                    if get_serpapi_count() >= general_serpapi_limit or india_state["two_source_count"] >= 3 or pr < 60.0:
                        break
                    log_exec(f"  [INDIA_FINAL_MILE] SerpAPI fallback on priority {pr:.0f}/100 event: {ev.canonical_title[:50]}")
                    attempt_key = get_serpapi_event_key(ev)
                    if attempt_key in serpapi_attempted_event_keys:
                        log_exec(f"    SERPAPI_ALREADY_ATTEMPTED_SKIP: {ev.canonical_title[:50]}")
                        continue
                    serpapi_attempted_event_keys.add(attempt_key)
                    serpapi_before = get_serpapi_count()
                    corr = serpapi_corroborator.corroborate(event=ev, primary_article=prim_a)
                    corroboration_searches += corr.queries_fired
                    serpapi_india_used += get_serpapi_count() - serpapi_before

                    if corr and corr.success and corr.corroborating_article:
                        second_sources_found += 1
                        ca = corr.corroborating_article
                        articles_lookup[ca.id] = ca
                        ev.article_ids.append(ca.id)
                        rv2 = verifier.verify_event(ev, [prim_a, ca])
                        if rv2.is_verified:
                            ev.event_category = reg_clf.classify_event(ev, [prim_a, ca])
                            verified_events.append(ev)
                            log_exec(f"    FINAL MILE VERIFIED: {ev.canonical_title[:45]} | Src2={ca.source_name}")

        # 4. INTERNATIONAL_FINAL_MILE_DISCOVERY: Rotated discovery query templates & source groups
        if not intl_state["section_complete"] and not expansion_candidates:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            serpapi_rem = general_serpapi_limit - get_serpapi_count() if serpapi_corroborator.has_api_key else 0

            if rss_rem > 0 or serpapi_rem > 0:
                log_exec(f"[INTL_FINAL_MILE_DISCOVERY] Searching for NEW International events (Remaining RSS={rss_rem}, SerpAPI={serpapi_rem})...")
                INTL_SOURCE_GROUPS = [
                    "site:cnbc.com OR site:apnews.com OR site:bbc.com",
                    "site:marketwatch.com OR site:fortune.com OR site:theguardian.com",
                    "site:bloomberg.com OR site:reuters.com OR site:finance.yahoo.com",
                ]
                INTL_EVENT_TEMPLATES = [
                    "company acquisition merger deal when:1d",
                    "company quarterly earnings profit revenue when:1d",
                    "company investment stake sale when:1d",
                    "company antitrust penalty probe regulator when:1d",
                    "company CEO appointed resigns when:1d",
                    "company contract order partnership when:1d",
                    "company funding round IPO valuation when:1d",
                    "central bank interest rate decision when:1d",
                ]
                discovered_intl_fm = []
                for s_group in INTL_SOURCE_GROUPS:
                    for tmpl in INTL_EVENT_TEMPLATES:
                        if get_corroboration_count() >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                            if not serpapi_corroborator.has_api_key or get_serpapi_count() >= general_serpapi_limit:
                                log_exec("  [INTL_FINAL_MILE] Search budget exhausted — stopping final-mile discovery queries.")
                                break
                        query_str = f"{tmpl} {s_group}"
                        q_norm = query_str.lower().strip()
                        if q_norm in executed_final_mile_queries:
                            continue
                        executed_final_mile_queries.add(q_norm)
                        log_exec(f"  [INTL_FINAL_MILE_QUERY] Executing variant: {tmpl[:40]}...")
                        items = []
                        if get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN:
                            items = discovery_service.provider.discover(query=query_str, country="US", max_results=10)
                            increment_corroboration_count(1)
                            corroboration_searches += 1
                            rss_international_used += 1
                        elif serpapi_corroborator.has_api_key and get_serpapi_count() < general_serpapi_limit:
                            clean_serp_q = query_str.replace("when:1d", "").strip()
                            serpapi_before = get_serpapi_count()
                            items = serpapi_corroborator.discover(clean_serp_q)
                            serpapi_international_used += get_serpapi_count() - serpapi_before
                            corroboration_searches += 1

                        new_in_batch = 0
                        for it in items:
                            u = it.url.strip()
                            u_norm = u.lower().rstrip("/")
                            is_valid_u, _ = URLFilterRule.is_valid_url(u)
                            if is_valid_u and u_norm not in seen_urls:
                                discovered_intl_fm.append(it)
                                new_in_batch += 1

                        if items and new_in_batch == 0:
                            log_exec(f"  [FINAL_MILE_BATCH_ALL_SEEN] Query '{tmpl[:30]}' returned only seen URLs; rotating to next variant.")

                        if len(discovered_intl_fm) >= 10:
                            break
                    if len(discovered_intl_fm) >= 10:
                        break

                if discovered_intl_fm:
                    scored_fm = []
                    for c in discovered_intl_fm:
                        score = _score_discovery_candidate(c.title or "")
                        scored_fm.append((score, c))
                    scored_fm.sort(key=lambda x: x[0], reverse=True)
                    expansion_candidates.extend([(c, "international") for score, c in scored_fm[:15]])

        # 5. INDIA_FINAL_MILE_DISCOVERY: Rotated discovery query templates & source groups
        if not india_state["section_complete"] and not expansion_candidates:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            serpapi_rem = general_serpapi_limit - get_serpapi_count() if serpapi_corroborator.has_api_key else 0

            if rss_rem > 0 or serpapi_rem > 0:
                log_exec(f"[INDIA_FINAL_MILE_DISCOVERY] Searching for NEW India events (Remaining RSS={rss_rem}, SerpAPI={serpapi_rem})...")
                INDIA_SOURCE_GROUPS = [
                    "site:business-standard.com OR site:livemint.com OR site:moneycontrol.com",
                    "site:businesstoday.in OR site:financialexpress.com OR site:thehindubusinessline.com",
                    "site:economictimes.indiatimes.com OR site:ndtvprofit.com OR site:thehindu.com",
                ]
                INDIA_EVENT_TEMPLATES = [
                    "quarterly results net profit revenue crore when:1d",
                    "acquires acquisition deal buyout stake when:1d",
                    "block deal stake sale crore when:1d",
                    "raises funds equity funding crore when:1d",
                    "RBI penalty order bank NBFC when:1d",
                    "IPO DRHP filed India when:1d",
                    "capex plant investment crore India when:1d",
                    "majority stake acquisition India when:1d",
                    "minority stake investment crore when:1d",
                    "equity changes hands promoter stake when:1d",
                    "IPO anchor investors crore when:1d",
                    "Q1 results net profit revenue crore when:1d",
                    "quarterly earnings EBITDA India when:1d",
                    "CCI approves acquisition when:1d",
                    "SEBI order company when:1d",
                ]
                discovered_final_mile = []
                for s_group in INDIA_SOURCE_GROUPS:
                    for tmpl in INDIA_EVENT_TEMPLATES:
                        if get_corroboration_count() >= MAX_CORROBORATION_SEARCHES_PER_RUN:
                            if not serpapi_corroborator.has_api_key or get_serpapi_count() >= general_serpapi_limit:
                                log_exec("  [FINAL_MILE_DISCOVERY] Search budget exhausted — stopping final-mile discovery queries.")
                                break
                        query_str = f"{tmpl} {s_group}"
                        q_norm = query_str.lower().strip()
                        if q_norm in executed_final_mile_queries:
                            continue
                        executed_final_mile_queries.add(q_norm)
                        log_exec(f"  [FINAL_MILE_QUERY] Executing variant: {tmpl[:40]}...")
                        items = []
                        if get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN:
                            items = discovery_service.provider.discover(query=query_str, country="India", max_results=10)
                            increment_corroboration_count(1)
                            corroboration_searches += 1
                            rss_india_used += 1
                        elif serpapi_corroborator.has_api_key and get_serpapi_count() < general_serpapi_limit:
                            clean_serp_q = query_str.replace("when:1d", "").strip()
                            serpapi_before = get_serpapi_count()
                            items = serpapi_corroborator.discover(clean_serp_q)
                            serpapi_india_used += get_serpapi_count() - serpapi_before
                            corroboration_searches += 1

                        new_in_batch = 0
                        for it in items:
                            u = it.url.strip()
                            u_norm = u.lower().rstrip("/")
                            is_valid_u, _ = URLFilterRule.is_valid_url(u)
                            if is_valid_u and u_norm not in seen_urls:
                                discovered_final_mile.append(it)
                                new_in_batch += 1

                        if items and new_in_batch == 0:
                            log_exec(f"  [FINAL_MILE_BATCH_ALL_SEEN] Query '{tmpl[:30]}' returned only seen URLs; rotating to next variant.")

                        if len(discovered_final_mile) >= 10:
                            break
                    if len(discovered_final_mile) >= 10:
                        break
                if discovered_final_mile:
                    scored_fm = []
                    for c in discovered_final_mile:
                        score = _score_discovery_candidate(c.title or "")
                        scored_fm.append((score, c))
                    scored_fm.sort(key=lambda x: x[0], reverse=True)
                    expansion_candidates.extend([(c, "india") for score, c in scored_fm[:15]])

        if should_stop_expansion(
            expansion_candidates,
            intl_state,
            serpapi_corroborator.has_api_key,
            get_serpapi_count(),
            settings.MAX_SERPAPI_SEARCHES_PER_RUN,
            intl_upgrade_available,
        ):
            log_exec("No unseen candidates remaining in reserve or final-mile discovery. Stopping expansion.")
            break
        if not expansion_candidates:
            expansion_pass += 1
            continue

        expansion_pass += 1
        expansion_new_candidates += len(expansion_candidates)
        log_exec(
            f"[Pass {expansion_pass}] SECTION DEFICIT EXPANSION: "
            f"India (2-source={india_state['two_source_count']}, singles={india_state['single_source_count']}), "
            f"Intl (2-source={intl_state['two_source_count']}, singles={intl_state['single_source_count']}), "
            f"Extracting {len(expansion_candidates)} new candidates."
        )

        # Process this batch of expansion candidates
        log_exec(f"  Processing {len(expansion_candidates)} candidates in expansion pass {expansion_pass}...")
        for cand, cand_section in expansion_candidates:
            u_norm = cand.url.strip().lower().rstrip("/")
            if u_norm in seen_urls:
                continue
            seen_urls.add(u_norm)

            # Pre-extraction freshness check (if RSS published_at is available)
            pub_at = get_candidate_published_at(cand)
            if pub_at:
                pub_time = pub_at
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                age_hours = max(0.0, (now_utc - pub_time).total_seconds() / 3600.0)
                max_freshness_hours = getattr(get_settings(), "STORY_FRESHNESS_HOURS", 24.0)
                if age_hours > max_freshness_hours:
                    log_exec(f"  [Expansion] STALE_PRE_REJECT: '{cand.title[:50]}' ({age_hours:.1f}h old > {max_freshness_hours:.0f}h limit)")
                    continue

            # Extract article
            try:
                ext_res = extractor.extract(
                    url=cand.url,
                    source_name=cand.source,
                    candidate_title=cand.title,
                    candidate_category="India" if cand_section == "india" else "International",
                    candidate_pub_date=pub_at,
                )
            except (AttributeError, NameError, TypeError, UnboundLocalError) as e:
                internal_pipeline_errors += 1
                log_exec(f"  [CRITICAL_PIPELINE_ERROR] Programming bug in expansion candidate {cand.url[:60]}: {e}")
                continue
            except Exception as e:
                log_exec(f"  [Extraction Error] {cand.url[:60]}: {e}")
                continue

            if not ext_res.success or not ext_res.article:
                continue

            art = ext_res.article
            articles_lookup[art.id] = art

            # Filter
            filt_res = filter_engine.filter_article(art)
            if not filt_res.is_accepted:
                continue

            # Classify
            class_res = classifier.classify(art)
            if not class_res.success or not class_res.classification or not class_res.classification.is_hard_business_event:
                continue

            # Cluster / Verify
            art_event_cat = NewsCategory.INDIA if cand_section == "india" else NewsCategory.INTERNATIONAL
            classified_companies = populate_event_companies(
                art,
                class_res.classification.company_names,
            )
            event = Event(
                canonical_title=art.title,
                article_ids=[art.id],
                event_category=art_event_cat,
                description=art.content_text[:300] if art.content_text else "",
                companies_involved=classified_companies,
                financial_figures=class_res.classification.financial_numbers,
                percentages=class_res.classification.percentages,
            )

            # Check if this article matches any existing single-source event organically
            existing_event = next(
                (e for e in single_source_events if e.article_ids and
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
                        organic_second_sources_found += 1
                        if existing_event.event_category == NewsCategory.INDIA:
                            india_verified.append(existing_event)
                        elif existing_event.event_category == NewsCategory.INTERNATIONAL:
                            intl_verified.append(existing_event)
                        log_exec(f"    EXPANSION ORGANICALLY VERIFIED: {existing_event.canonical_title[:45]} (Region={existing_event.event_category.value})")
            else:
                single_source_events.append(event)
                event.event_category = reg_clf.classify_event(event, [art])
                is_india_event = (event.event_category == NewsCategory.INDIA)
                cur_india_v = len([e for e in verified_events if e.event_category == NewsCategory.INDIA])
                cur_intl_v  = len([e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL])

                # Skip search if section already satisfied with 5 two-source verified
                if not is_india_event and cur_intl_v >= 5:
                    is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, art)
                    if is_elig:
                        event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                        event.verification_confidence = conf
                        event.single_source_confidence_score = conf
                        event.primary_publisher = art.source_name
                        event.primary_url = art.url
                        event.verification_reason = rsn
                        high_confidence_single_candidates.append(event)
                    continue
                if is_india_event and cur_india_v >= 5:
                    is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, art)
                    if is_elig:
                        event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                        event.verification_confidence = conf
                        event.single_source_confidence_score = conf
                        event.primary_publisher = art.source_name
                        event.primary_url = art.url
                        event.verification_reason = rsn
                        high_confidence_single_candidates.append(event)
                    continue

                if is_multi_event_roundup(event.canonical_title):
                    log_exec(f"  [Expansion] MULTI_EVENT_ROUNDUP REJECT: {event.canonical_title[:50]}")
                    continue

                prio = calculate_corroboration_priority(event, art)
                corr = None
                if get_corroboration_count() < MAX_CORROBORATION_SEARCHES_PER_RUN and prio >= 50.0:
                    corr = corroborator.corroborate(event=event, primary_article=art)
                    corroboration_searches += corr.queries_fired
                    if is_india_event:
                        rss_india_used += corr.queries_fired
                    else:
                        rss_international_used += corr.queries_fired

                if (corr is None or not corr.success) and serpapi_corroborator.has_api_key:
                    if get_serpapi_count() < general_serpapi_limit and prio >= 60.0:
                        log_exec(f"  [Expansion] Attempting SerpAPI fallback for: {event.canonical_title[:50]}")
                        attempt_key = get_serpapi_event_key(event)
                        if attempt_key in serpapi_attempted_event_keys:
                            log_exec(f"    SERPAPI_ALREADY_ATTEMPTED_SKIP: {event.canonical_title[:50]}")
                            corr = None
                        else:
                            serpapi_attempted_event_keys.add(attempt_key)
                            serpapi_before = get_serpapi_count()
                            corr = serpapi_corroborator.corroborate(event=event, primary_article=art)
                            serpapi_delta = get_serpapi_count() - serpapi_before
                            if is_india_event:
                                serpapi_india_used += serpapi_delta
                            else:
                                serpapi_international_used += serpapi_delta
                        if corr:
                            corroboration_searches += corr.queries_fired

                if corr and corr.success and corr.corroborating_article:
                    second_sources_found += 1
                    ca = corr.corroborating_article
                    articles_lookup[ca.id] = ca
                    event.article_ids.append(ca.id)
                    rv2 = verifier.verify_event(event, [art, ca])
                    if rv2.is_verified:
                        event.event_category = reg_clf.classify_event(event, [art, ca])
                        verified_events.append(event)
                        if event.event_category == NewsCategory.INDIA:
                            india_verified.append(event)
                        elif event.event_category == NewsCategory.INTERNATIONAL:
                            intl_verified.append(event)
                        log_exec(f"    EXPANSION CORROBORATED: {event.canonical_title[:45]} | Src2={ca.source_name} (Region={event.event_category.value})")
                else:
                    # Assess single source fallback
                    is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, art)
                    if is_elig:
                        event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                        event.verification_confidence = conf
                        event.single_source_confidence_score = conf
                        event.primary_publisher = art.source_name
                        event.primary_url = art.url
                        event.verification_reason = rsn
                        if event not in high_confidence_single_candidates:
                            high_confidence_single_candidates.append(event)
                        log_exec(f"    EXPANSION QUALIFIED SINGLE SOURCE: {event.canonical_title[:45]} | {rsn}")
                    else:
                        log_exec(f"    EXPANSION SINGLE SOURCE REJECTED: {event.canonical_title[:45]} | {rsn}")

        for e in verified_events:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)

        india_two_source = [e for e in verified_events if e.event_category == NewsCategory.INDIA and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
        intl_two_source  = [e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
        india_single_src = [e for e in high_confidence_single_candidates if e.event_category == NewsCategory.INDIA and e not in verified_events]
        intl_single_src  = [e for e in high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL and e not in verified_events]
        log_exec(
            f"  After expansion pass {expansion_pass}: "
            f"India (2-source={len(india_two_source)}, single={len(india_single_src)}), "
            f"Intl (2-source={len(intl_two_source)}, single={len(intl_single_src)})"
        )

    # Reclassify and update categories
    for e in verified_events:
        e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
        e.event_category = reg_clf.classify_event(e, e_arts)
    for e in high_confidence_single_candidates:
        e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
        e.event_category = reg_clf.classify_event(e, e_arts)

    india_two_source = [e for e in verified_events if e.event_category == NewsCategory.INDIA and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    intl_two_source  = [e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL and e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    india_single_src = [e for e in high_confidence_single_candidates if e.event_category == NewsCategory.INDIA and e not in verified_events]
    intl_single_src  = [e for e in high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL and e not in verified_events]

    # =========================================================================
    # STAGE 6: Deduplication & History
    # =========================================================================
    log_exec("=" * 60)
    log_exec("STAGE 6: Deduplication — 3-day SQLite lookback and company restrictions")
    log_exec("=" * 60)
    dedup_engine = DeduplicationEngine(history_store=history_store)

    candidate_stories = []
    event_by_id: Dict[str, Event] = {}
    from app.models.entity_sanitizer import sanitize_company_entities
    
    all_candidate_events = verified_events + [e for e in high_confidence_single_candidates if e not in verified_events]

    for event in all_candidate_events:
        event_by_id[event.id] = event
        prim_pub = articles_lookup[event.article_ids[0]].source_name if event.article_ids and event.article_ids[0] in articles_lookup else None
        clean_comps = sanitize_company_entities(event.companies_involved, publisher=prim_pub)
        comp = clean_comps[0] if clean_comps else "unspecified"
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
        lookback_days=getattr(settings, "DEDUP_LOOKBACK_DAYS", 3),
    )
    log_exec(f"Stage 6 Summary:")
    log_exec(f"  Accepted: {len(accepted_stories)}")
    log_exec(f"  Removed:  {len(rejected_stories)}")

    # =========================================================================
    # STAGE 7: Ranking (freshness-aware & hybrid verification aware)
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

    # Separate India and International accepted events
    india_acc_events = [e for e in accepted_events if e.event_category == NewsCategory.INDIA]
    intl_acc_events  = [e for e in accepted_events if e.event_category == NewsCategory.INTERNATIONAL]

    # Partition two-source vs single-source per section
    india_two_acc = [e for e in india_acc_events if e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    india_sng_acc = [e for e in india_acc_events if e not in india_two_acc and e.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE]

    intl_two_acc  = [e for e in intl_acc_events if e.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED]
    intl_sng_acc  = [e for e in intl_acc_events if e not in intl_two_acc and e.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE]

    # Build section candidate pools according to Hybrid policy (min 3 two-source, max 2 single-source)
    # 1. Take all available two-source verified events (up to 5)
    # 2. If two-source < 5, fill missing slots with top single-source (up to max 2)
    selected_india_events = list(india_two_acc[:5])
    needed_india_singles = max(0, min(2, 5 - len(selected_india_events)))
    selected_india_events.extend(india_sng_acc[:needed_india_singles])

    selected_intl_events = list(intl_two_acc[:5])
    needed_intl_singles = max(0, min(2, 5 - len(selected_intl_events)))
    selected_intl_events.extend(intl_sng_acc[:needed_intl_singles])

    # Rank both sections
    candidate_pool = ranker.rank_events(events=selected_india_events + selected_intl_events, top_n=10)
    india_pool  = candidate_pool.india_candidates
    intl_pool   = candidate_pool.international_candidates

    india_two_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    india_sng_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])
    intl_two_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    intl_sng_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])

    log_exec(f"Stage 7 Summary:")
    log_exec(f"  India pool:         {len(india_pool)} (Two-source: {india_two_count}, Single-source: {india_sng_count})")
    log_exec(f"  International pool: {len(intl_pool)} (Two-source: {intl_two_count}, Single-source: {intl_sng_count})")

    # =========================================================================
    # PIPELINE SUFFICIENCY GATE (HYBRID RULES)
    # =========================================================================
    india_sufficient = (len(india_pool) >= 5 and india_two_count >= 3 and india_sng_count <= 2)
    intl_sufficient  = (len(intl_pool) >= 5 and intl_two_count >= 3 and intl_sng_count <= 2)
    sufficient = (india_sufficient and intl_sufficient)

    # Print Candidate Audit Manifest
    print("\n" + "=" * 60)
    print("=== VERIFIED CANDIDATE AUDIT ===")
    print("=" * 60)
    for scored in india_pool + intl_pool:
        ev = scored.event
        sec_name = "INDIA" if ev.event_category == NewsCategory.INDIA else "INTERNATIONAL"
        tier_str = ev.verification_tier.value if ev.verification_tier else "TWO_SOURCE_VERIFIED"
        prim_pub = ev.primary_publisher or (articles_lookup[ev.article_ids[0]].source_name if ev.article_ids and ev.article_ids[0] in articles_lookup else "N/A")
        prim_u   = ev.primary_url or (articles_lookup[ev.article_ids[0]].url if ev.article_ids and ev.article_ids[0] in articles_lookup else "N/A")
        print(f"\n[{sec_name}] {ev.canonical_title}")
        print(f"  Tier:       {tier_str} (Confidence: {ev.verification_confidence:.1f}/100)")
        print(f"  Primary:    {prim_pub} ({prim_u})")
        if ev.secondary_publisher:
            print(f"  Secondary:  {ev.secondary_publisher} ({ev.secondary_url})")
        elif ev.verification_reason:
            print(f"  Reason:     {ev.verification_reason}")
    print("\n=== CANDIDATE POOL SUMMARY ===")
    print(f"India:         Two-source: {india_two_count}, Single-source: {india_sng_count}, Total: {len(india_pool)}")
    print(f"International: Two-source: {intl_two_count}, Single-source: {intl_sng_count}, Total: {len(intl_pool)}")
    print("=" * 60 + "\n")

    if not sufficient:
        log_exec("=" * 60)
        log_exec("PIPELINE SUFFICIENCY GATE: INSUFFICIENT QUALITY STORIES")
        log_exec(f"  India pool:  {len(india_pool)} / 5 (Two-source: {india_two_count}/3 required)")
        log_exec(f"  Intl pool:   {len(intl_pool)} / 5 (Two-source: {intl_two_count}/3 required)")
        log_exec("  Stage 8 Editorial:        SKIPPED")
        log_exec("  Stage 9 Final Validation: SKIPPED")
        log_exec("  Stage 10 Formatter:       SKIPPED")
        log_exec("  Pipeline Status:          INSUFFICIENT_QUALITY_STORIES")
        log_exec("=" * 60)

        # Stage 8 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 8: Gemini Editorial — SKIPPED (Sufficiency gate failed)")
        log_exec("=" * 60)
        log_exec(f"  -> STAGE 8 SKIPPED: Insufficient stories (India={len(india_pool)}, Intl={len(intl_pool)}).")
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
    total_discovered = len(india_reserve_pool) + len(intl_reserve_pool)
    reserve_rem = total_discovered - processed_pass1 - expansion_new_candidates

    print("\n" + "=" * 60)
    print(" PIPELINE EXECUTION COMPLETE — FULL REPORT")
    print("=" * 60)
    print("\nDISCOVERY & RESERVE POOL")
    print("-" * 30)
    print(f"  reserve_discovered:         {total_discovered} (India: {len(india_reserve_pool)}, Intl: {len(intl_reserve_pool)})")
    print(f"  pass1_processed:            {processed_pass1}")
    print(f"  expansion_processed:        {expansion_new_candidates}")
    print(f"  reserve_remaining:          {max(0, reserve_rem)}")
    print(f"  organic_second_sources_found:{organic_second_sources_found}")
    print(f"  high_confidence_single_candidates: {len(high_confidence_single_candidates)}")
    print(f"  duplicate_seen_candidates:  {duplicate_seen_candidates}")

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
    print("\nVERIFICATION & CORROBORATION (HYBRID MODEL)")
    print("-" * 30)
    print(f"  Events found:               {len(raw_events)}")
    print(f"  Organic 2nd sources:        {organic_second_sources_found}")
    print(f"  Active search 2nd sources:  {second_sources_found}")
    print(f"  Two-source verified:        {len(verified_events)}")
    print(f"  High-confidence singles:    {len(high_confidence_single_candidates)}")
    print(f"  RSS searches used:          {get_corroboration_count()} / {MAX_CORROBORATION_SEARCHES_PER_RUN} (India: {rss_india_used}, Intl: {rss_international_used})")
    print(f"  SerpAPI searches used:      {get_serpapi_count()} / {settings.MAX_SERPAPI_SEARCHES_PER_RUN} (India: {serpapi_india_used}, Intl: {serpapi_international_used})")
    print(f"  SerpAPI candidates returned:{get_serpapi_candidates_returned()}")
    print(f"  SerpAPI accepted 2nd sources:{get_serpapi_accepted_sources()}")
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
    print("\nRANKING & SUFFICIENCY (HYBRID MODEL)")
    print("-" * 30)
    print(f"  India candidates:           {len(india_pool)} (Two-source: {india_two_count}/3 min, Singles: {india_sng_count}/2 max)")
    print(f"  International candidates:   {len(intl_pool)} (Two-source: {intl_two_count}/3 min, Singles: {intl_sng_count}/2 max)")
    print(f"  Sufficiency gate:           {'PASSED' if sufficient else 'FAILED (INSUFFICIENT_QUALITY_STORIES)'}")
    print("\nEDITORIAL (Gemini)")
    print("-" * 30)
    if sufficient and editorial_res and editorial_res.success:
        editorial_calls = gemini_stats["by_stage"].get("editorial", 0)
        print(f"  Gemini editorial calls:     {editorial_calls}")
        print(f"  Selected India:             {len(selection_payload.india_stories)}")
        print(f"  Selected International:     {len(selection_payload.international_stories)}")
    elif not sufficient:
        print("  Status:                     SKIPPED (INSUFFICIENT_QUALITY_STORIES)")
    else:
        err = editorial_res.error_message if editorial_res else "EDITORIAL_VALIDATION_FAILED"
        print(f"  Status:                     FAILED ({err})")

    print("\nVALIDATION")
    print("-" * 30)
    if validation_report:
        print(f"  Status:                     {validation_report.status.value}")
        print(f"  Passed checks:              {validation_report.passed_checks} / 20")
        print(f"  Failed checks:              {validation_report.failed_checks} / 20")
        if not validation_report.is_valid:
            print(f"  Failure reason:             {validation_report.failure_reason}")
    elif not sufficient:
        print("  Status:                     SKIPPED (INSUFFICIENT_QUALITY_STORIES)")
    else:
        print("  Status:                     FAILED (EDITORIAL_VALIDATION_FAILED)")
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
            tier_val = ev.verification_tier.value if (ev and ev.verification_tier) else "UNKNOWN"

            print(f"\n[{idx}] SECTION: {story.section.upper()}")
            print(f"  TITLE:               {story.headline}")
            print(f"  PUBLISHER:           {story.source}")
            print(f"  ORIGINAL RSS URL:    {orig}")
            print(f"  RESOLVED URL:        {story.url}")
            print(f"  EVENT TYPE:          {ev.event_category.value if ev else 'N/A'}")
            print(f"  VERIFICATION TIER:   {tier_val}")
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
    else:
        print("=== URL AUDIT: No stories selected ===")
        if internal_pipeline_errors > 0:
            print(f"\nSTATUS: INSUFFICIENT_VERIFIED_STORIES_WITH_PROCESSING_ERRORS")
            print(f"  Internal pipeline errors: {internal_pipeline_errors}")
            print(f"  India verified:       {india_two_count} / 3 min required")
            print(f"  International:        {intl_two_count} / 3 min required")
            print(f"  Reason: Internal processing errors ({internal_pipeline_errors}) prevented candidate extraction.")
        elif not sufficient:
            print(f"\nSTATUS: INSUFFICIENT_QUALITY_STORIES")
            print(f"  India pool:           {len(india_pool)} / 5 (Two-source: {india_two_count}/3 min, Singles: {india_sng_count}/2 max)")
            print(f"  International pool:   {len(intl_pool)} / 5 (Two-source: {intl_two_count}/3 min, Singles: {intl_sng_count}/2 max)")
            print("  Reason: Insufficient quality stories found to meet the hybrid verification requirements (min 3 two-source, max 2 single-source per section).")

    if briefing_text:
        print("\n--- [FINAL BRIEFING] ---")
        print(briefing_text)
        print("------------------------\n")

    else:
        print(f"\nFINAL BRIEFING: NOT GENERATED")
        if internal_pipeline_errors > 0:
            print(f"Reason: INSUFFICIENT_VERIFIED_STORIES_WITH_PROCESSING_ERRORS — {internal_pipeline_errors} internal error(s) occurred during candidate processing.")
        elif not sufficient:
            print("Reason: INSUFFICIENT_QUALITY_STORIES — Fewer than 5+5 quality-eligible hybrid stories were found while maintaining minimum 3 two-source stories per section.")
        elif validation_report and validation_report.failure_reason:
            print(f"Reason: {validation_report.failure_reason}")

    return 0 if (sufficient and validation_report and validation_report.is_valid) else 1


if __name__ == "__main__":
    st = get_settings()
    max_in  = int(sys.argv[1]) if len(sys.argv) > 1 else st.MAX_DISCOVERY_INDIA
    max_int = int(sys.argv[2]) if len(sys.argv) > 2 else st.MAX_DISCOVERY_INTL
    sys.exit(run_pipeline(max_india=max_in, max_international=max_int))
