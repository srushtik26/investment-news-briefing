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
from datetime import datetime, date, timezone, timedelta
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


def get_article_age_hours(article: Article, now_utc: Optional[datetime] = None) -> Optional[float]:
    """Return the verified article age in hours, or None when no timestamp exists."""
    if not article.published_at:
        return None
    current_time = now_utc or datetime.now(timezone.utc)
    pub_time = article.published_at
    if pub_time.tzinfo is None:
        pub_time = pub_time.replace(tzinfo=timezone.utc)
    return max(0.0, (current_time - pub_time).total_seconds() / 3600.0)


def get_fallback_search_window(expansion_pass: int = 1) -> str:
    """Return the search window for discovery queries (strictly when:1d)."""
    return "when:1d"


def evaluate_single_source_for_horizon(
    event: Event,
    article: Article,
    evaluator: SingleSourceEvaluator,
    horizon_hours: float = 24.0,
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, float, str]:
    """Apply unchanged single-source rules enforcing <=24h freshness."""
    age_hours = get_article_age_hours(article, now_utc=now_utc)
    if age_hours is None or age_hours > horizon_hours:
        return False, 0.0, f"REJECT: Stale publication date ({age_hours if age_hours is not None else 'unknown'}h > {horizon_hours:.0f}h)"
    return evaluator.evaluate_event(event, article, now_utc=now_utc)


def ladder_quality_key(event: Event, article: Article) -> Tuple[int, float, float]:
    """Sort by verification tier, relevance score, and recency."""
    age_hours = get_article_age_hours(article) or 999.0
    tier_rank = 0 if event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED else 1
    return tier_rank, -float(event.verification_confidence or 0.0), -age_hours


def get_quality_level(
    scored_events: List[Any],
    two_source_count: int,
    articles_lookup: Dict[str, Article],
) -> str:
    """Return the strongest quality level represented by a selected section."""
    if len(scored_events) < 5:
        return "DATA_UNAVAILABLE"
    ages = []
    for scored in scored_events:
        event = scored.event
        article = articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        age = get_article_age_hours(article) if article else None
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
    from app.models.entity_sanitizer import normalize_publisher_name

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

        rss_published_at = get_candidate_published_at(cand)
        canonical_source = normalize_publisher_name(cand.source)
        log_exec(f"[{idx}/{total}] Extracting ({country}): '{cand.title[:50]}' ({canonical_source})")
        if extractor.resolver.is_google_news_url(cand.url):
            google_count += 1

        try:
            res = extractor.extract(
                url=cand.url,
                source_name=canonical_source,
                candidate_title=cand.title,
                candidate_category=country,
                candidate_pub_date=rss_published_at,
            )

            rec = {
                "original_url":  res.original_url or cand.url,
                "resolved_url":  res.resolved_url or cand.url,
                "status_code":   res.status_code,
                "title":         res.article.title if res.article else cand.title,
                "publisher":     res.article.source_name if res.article else canonical_source,
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
    date_deferred_urls = {
        rejection.article_url
        for rejection in rejections
        if rejection.rule_failed == "DATE"
    }
    date_deferred_articles = [
        article for article in all_extracted
        if article.url in date_deferred_urls and article.published_at and getattr(article, "date_verified", True)
    ]

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

    fallback_classified_articles: List[Tuple[Article, Any]] = []
    if date_deferred_articles:
        log_exec(f"[FALLBACK_36H_START] Retained {len(date_deferred_articles)} date-deferred extracted candidates")
    for art in date_deferred_articles:
        try:
            res = classifier.classify(art)
        except Exception as exc:
            log_exec(f"  -> FALLBACK CLASSIFICATION FAILED: {exc}")
            continue
        if res and res.success and res.classification and res.classification.is_hard_business_event and res.classification.is_investment_relevant:
            fallback_classified_articles.append((art, res.classification))
    log_exec(f"  Date-deferred fallback candidates: {len(fallback_classified_articles)}")

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
    fallback_events: List[Event] = []
    for art, classification in fallback_classified_articles:
        event = Event(
            canonical_title=art.title,
            article_ids=[art.id],
            event_category=art.category or NewsCategory.INTERNATIONAL,
            description=art.content_text[:300] if art.content_text else "",
            companies_involved=classification.company_names,
            financial_figures=classification.financial_numbers,
            percentages=classification.percentages,
        )
        fallback_events.append(event)
        class_map[art.id] = classification
        articles_lookup[art.id] = art

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
            
    verifier = TwoSourceVerifier()
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

    for event in raw_events:
        event_articles = [articles_lookup[aid] for aid in event.article_ids if aid in articles_lookup]
        verif_res = verifier.verify_event(event, event_articles)

        if verif_res.is_verified:
            verified_events.append(event)
            organic_second_sources_found += 1
            log_exec(f"  -> ORGANIC TWO-SOURCE VERIFIED: {event.canonical_title} ({len(event.article_ids)} sources)")
        else:
            single_source_events.append(event)
            primary_art = event_articles[0] if event_articles else None
            if primary_art:
                if not event.companies_involved:
                    event.companies_involved = populate_event_companies(primary_art, [])
                if is_multi_event_roundup(event.canonical_title):
                    log_exec(f"  -> REJECTED MULTI_EVENT_ROUNDUP: {event.canonical_title[:50]}")
                    rejected_events_list.append({
                        "event_title": event.canonical_title,
                        "sources": [primary_art.source_name],
                        "reason": "Multi-event roundup headline",
                    })
                    continue

                is_elig, conf, rsn = single_source_evaluator.evaluate_event(event, primary_art)
                if is_elig:
                    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                    event.verification_confidence = conf
                    event.single_source_confidence_score = conf
                    event.primary_publisher = primary_art.source_name
                    event.primary_url = primary_art.url
                    event.verification_reason = rsn
                    high_confidence_single_candidates.append(event)
                    log_exec(f"  -> QUALIFIED QUALITY_VERIFIED SINGLE SOURCE: {event.canonical_title[:45]} | {rsn}")
                else:
                    rejected_events_list.append({
                        "event_title": event.canonical_title,
                        "sources": [primary_art.source_name],
                        "reason": f"Single-source reject: {rsn}",
                    })
                    log_exec(f"  -> REJECTED: {event.canonical_title} — {rsn}")
            else:
                rejected_events_list.append({
                    "event_title": event.canonical_title,
                    "sources": [],
                    "reason": "Single source with no primary article available",
                })

    log_exec(f"Stage 5 Summary (Quality Verification Model):")
    log_exec(f"  Events found:                  {len(raw_events)}")
    log_exec(f"  Organic second sources found:  {organic_second_sources_found}")
    log_exec(f"  Two-source verified events:    {len(verified_events)}")
    log_exec(f"  High-confidence single events: {len(high_confidence_single_candidates)}")

    # Save
    with open(data_dir / "verified_events.json", "w", encoding="utf-8") as f:
        json.dump([e.model_dump() for e in verified_events], f, indent=2, default=str)
    with open(data_dir / "rejected_events.json", "w", encoding="utf-8") as f:
        json.dump(rejected_events_list, f, indent=2, default=str)

    # =========================================================================
    # DISCOVERY EXPANSION (if insufficient quality events)
    # =========================================================================
    for e in verified_events:
        e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
        e.event_category = reg_clf.classify_event(e, e_arts)
    for e in high_confidence_single_candidates:
        e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
        e.event_category = reg_clf.classify_event(e, e_arts)

    def process_candidate_item(cand: Any, cand_section: str) -> Optional[Event]:
        nonlocal organic_second_sources_found
        u_norm = cand.url.strip().lower().rstrip("/")
        if u_norm in seen_urls:
            return None
        seen_urls.add(u_norm)

        rss_pub = get_candidate_published_at(cand)
        if rss_pub:
            pub_time = rss_pub
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            age_hours = max(0.0, (now_utc - pub_time).total_seconds() / 3600.0)
            max_freshness_hours = getattr(get_settings(), "STORY_FRESHNESS_HOURS", 24.0)
            if age_hours > max_freshness_hours:
                log_exec(f"  [Candidate] STALE_PRE_REJECT: '{cand.title[:50]}' ({age_hours:.1f}h old > {max_freshness_hours:.0f}h limit)")
                return None

        from app.models.entity_sanitizer import normalize_publisher_name
        canonical_source = normalize_publisher_name(cand.source)

        try:
            ext_res = extractor.extract(
                url=cand.url,
                source_name=canonical_source,
                candidate_title=cand.title,
                candidate_category="India" if cand_section == "india" else "International",
                candidate_pub_date=rss_pub,
            )
            rec = {
                "original_url": ext_res.original_url or cand.url,
                "resolved_url": ext_res.resolved_url or cand.url,
                "status_code": ext_res.status_code,
                "title": ext_res.article.title if ext_res.article else cand.title,
                "publisher": ext_res.article.source_name if ext_res.article else canonical_source,
                "publication_date": ext_res.article.published_at.isoformat() if (ext_res.article and ext_res.article.published_at) else None,
                "word_count": ext_res.word_count,
                "extraction_method": ext_res.extraction_method,
                "success": ext_res.success,
                "failure_reason": ext_res.error_message,
            }
            all_records.append(rec)
        except Exception as e:
            log_exec(f"  [Extraction Error] {cand.url[:60]}: {e}")
            return None

        if not ext_res.success or not ext_res.article:
            return None

        art = ext_res.article
        all_extracted.append(art)
        articles_lookup[art.id] = art

        filt_res = filter_engine.filter_article(art)
        if not filt_res.is_accepted:
            return None

        class_res = classifier.classify(art)
        if not class_res.success or not class_res.classification or not class_res.classification.is_hard_business_event:
            return None

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

        # Check if this matches existing event organically
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
                ev_arts = [articles_lookup[i] for i in existing_event.article_ids if i in articles_lookup]
                rv = verifier.verify_event(existing_event, ev_arts)
                if rv.is_verified and existing_event not in verified_events:
                    existing_event.event_category = reg_clf.classify_event(existing_event, ev_arts)
                    verified_events.append(existing_event)
                    organic_second_sources_found += 1
                    log_exec(f"    EXPANSION ORGANICALLY VERIFIED: {existing_event.canonical_title[:45]}")
            return existing_event
        else:
            single_source_events.append(event)
            event.event_category = reg_clf.classify_event(event, [art])
            if not is_multi_event_roundup(event.canonical_title):
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
                        prefix = "[INTL_RESCUE_QUALIFIED]" if event.event_category == NewsCategory.INTERNATIONAL else "[INDIA_RESCUE_QUALIFIED]"
                        log_exec(f"    {prefix} {event.canonical_title[:55]} | {rsn}")
            return event

    expansion_pass = 1
    max_expansion_passes = 6
    executed_final_mile_queries: Set[str] = set()

    while expansion_pass <= max_expansion_passes:
        # Re-classify regions for verified & quality single events
        for e in verified_events:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)
        for e in high_confidence_single_candidates:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)

        india_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
        intl_qual_events  = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
        
        # Freezing India completely if quality >= 5
        india_frozen = (len(india_qual_events) >= 5)
        intl_target_met = (len(intl_qual_events) >= 8)

        if india_frozen and intl_target_met:
            log_exec(
                f"Quality candidate targets satisfied: "
                f"India={len(india_qual_events)}/5 quality candidates (FROZEN); "
                f"Intl={len(intl_qual_events)}/8 quality candidates."
            )
            break

        unseen_india = [c for c in india_reserve_pool if c.url.strip().lower().rstrip("/") not in seen_urls] if not india_frozen else []
        unseen_intl  = [c for c in intl_reserve_pool if c.url.strip().lower().rstrip("/") not in seen_urls]
        log_exec(f"RESERVE_STATE: India unseen={len(unseen_india)} (quality={len(india_qual_events)}/5 {'[FROZEN]' if india_frozen else ''}), Intl unseen={len(unseen_intl)} (quality={len(intl_qual_events)}/8)")

        step_india = min(len(unseen_india), 15) if not india_frozen else 0
        step_intl  = min(len(unseen_intl), 15) if not intl_target_met else 0

        # Process reserve candidates immediately
        if step_india > 0:
            for c in unseen_india[:step_india]:
                process_candidate_item(c, "india")
                current_in_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
                if len(current_in_qual) >= 5:
                    log_exec(f"[INDIA_TARGET_MET] India quality stories reached {len(current_in_qual)}/5 from reserve pool.")
                    break
        if step_intl > 0:
            for c in unseen_intl[:step_intl]:
                process_candidate_item(c, "international")
                intl_qual_now = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
                if len(intl_qual_now) >= 8:
                    break

        # Re-evaluate quality counts after reserve processing
        for e in verified_events:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)
        for e in high_confidence_single_candidates:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)
        india_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
        intl_qual_events  = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]

        # Final-mile RSS Discovery (only if International < 5 and RSS search budget remains)
        if len(intl_qual_events) < 5:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            if rss_rem > 0:
                log_exec(f"[INTL_FINAL_MILE_DISCOVERY] Searching for NEW International events (current={len(intl_qual_events)}/5)...")
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
                        items = discovery_service.provider.discover(query=query_str, country="US", max_results=10)
                        increment_corroboration_count(1)
                        corroboration_searches += 1
                        rss_international_used += 1
                        for it in items:
                            u = it.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in seen_urls:
                                process_candidate_item(it, "international")
                                current_intl_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
                                if len(current_intl_qual) >= 5:
                                    log_exec(f"[INTL_RESCUE_TARGET_MET] International quality stories reached {len(current_intl_qual)}/5. Stopping RSS discovery.")
                                    stop_rss_discovery = True
                                    break
                        if stop_rss_discovery:
                            break

        # Check India if India is still < 5 (only if not frozen)
        if len(india_qual_events) < 5:
            rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
            if rss_rem > 0:
                log_exec(f"[INDIA_FINAL_MILE_DISCOVERY] Searching for NEW India events (current={len(india_qual_events)}/5)...")
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
                        items = discovery_service.provider.discover(query=query_str, country="India", max_results=10)
                        increment_corroboration_count(1)
                        corroboration_searches += 1
                        rss_india_used += 1
                        for it in items:
                            u = it.url.strip()
                            if URLFilterRule.is_valid_url(u)[0] and u.lower().rstrip("/") not in seen_urls:
                                process_candidate_item(it, "india")
                                current_in_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
                                if len(current_in_qual) >= 5:
                                    log_exec(f"[INDIA_TARGET_MET] India quality stories reached {len(current_in_qual)}/5. Stopping India discovery.")
                                    stop_india_discovery = True
                                    break
                        if stop_india_discovery:
                            break

        # Re-check section quality
        for e in verified_events:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)
        for e in high_confidence_single_candidates:
            e_arts = [articles_lookup[aid] for aid in e.article_ids if aid in articles_lookup]
            e.event_category = reg_clf.classify_event(e, e_arts)
        india_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
        intl_qual_events  = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
        
        if len(india_qual_events) >= 5 and len(intl_qual_events) >= 5:
            log_exec(f"[EXPANSION_COMPLETE] Both sections reached sufficiency: India={len(india_qual_events)}/5, Intl={len(intl_qual_events)}/5.")
            break

        expansion_pass += 1

    # SerpAPI Secondary Discovery (Fix 8) — ONLY if International < 5 after RSS discovery
    intl_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
    if len(intl_qual_events) < 5:
        serp_key = getattr(get_settings(), "SERPAPI_API_KEY", None) or os.environ.get("SERPAPI_API_KEY")
        if serp_key and serp_key.strip():
            log_exec(f"[SERPAPI_INTL_DISCOVERY] International quality={len(intl_qual_events)}/5. Searching SerpAPI for NEW International events...")
            from app.verification.serpapi_corroborator import SerpAPICorroborator
            serp_corrob = SerpAPICorroborator(extractor=extractor, api_key=serp_key)
            SERP_DISCOVERY_QUERIES = [
                "today company earnings",
                "today acquisition",
                "today company financial results",
                "today funding",
                "today corporate guidance",
            ]
            for sq in SERP_DISCOVERY_QUERIES:
                current_intl_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
                if len(current_intl_qual) >= 5:
                    log_exec(f"[SERPAPI_TARGET_MET] International reached 5/5 quality candidates.")
                    break
                try:
                    serp_items = serp_corrob.discover(sq)
                    for sit in serp_items:
                        process_candidate_item(sit, "international")
                        current_intl_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
                        if len(current_intl_qual) >= 5:
                            log_exec(f"[SERPAPI_TARGET_MET] International reached 5/5 quality candidates.")
                            break
                except Exception as e:
                    log_exec(f"[SERPAPI_DISCOVERY_ERROR] {e}")

    # Emergency Manual Seed Fallback — India & International
    # 1. India Manual Seeds (submission_seed_india_urls.txt)
    india_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
    if len(india_qual_events) < 5:
        seed_in_path = Path("submission_seed_india_urls.txt")
        if seed_in_path.exists():
            log_exec(f"[MANUAL_SEED_DISCOVERY] India quality={len(india_qual_events)}/5. Reading emergency URLs from {seed_in_path}...")
            try:
                with open(seed_in_path, "r", encoding="utf-8") as f:
                    seed_urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                for s_url in seed_urls:
                    current_in_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INDIA]
                    if len(current_in_qual) >= 5:
                        log_exec(f"[MANUAL_SEED_TARGET_MET] India reached 5/5 quality candidates via manual seeds.")
                        break
                    from app.discovery.models import DiscoveredArticle
                    s_cand = DiscoveredArticle(
                        title="",
                        url=s_url,
                        source="",
                        country="India",
                        search_query="manual_seed_india",
                    )
                    ev = process_candidate_item(s_cand, "india")
                    if ev and (ev in high_confidence_single_candidates or ev in verified_events):
                        log_exec(f"[MANUAL_SEED_QUALIFIED] {ev.canonical_title[:55]}")
            except Exception as e:
                log_exec(f"[MANUAL_SEED_ERROR] Failed to process India seed file: {e}")

    # 2. International Manual Seeds (submission_seed_international_urls.txt or submission_seed_urls.txt)
    intl_qual_events = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
    if len(intl_qual_events) < 5:
        seed_intl_path = Path("submission_seed_international_urls.txt")
        if not seed_intl_path.exists():
            seed_intl_path = Path("submission_seed_urls.txt")
        if seed_intl_path.exists():
            log_exec(f"[MANUAL_SEED_DISCOVERY] International quality={len(intl_qual_events)}/5. Reading emergency URLs from {seed_intl_path}...")
            try:
                with open(seed_intl_path, "r", encoding="utf-8") as f:
                    seed_urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                for s_url in seed_urls:
                    current_intl_qual = [e for e in verified_events + high_confidence_single_candidates if e.event_category == NewsCategory.INTERNATIONAL]
                    if len(current_intl_qual) >= 5:
                        log_exec(f"[MANUAL_SEED_TARGET_MET] International reached 5/5 quality candidates via manual seeds.")
                        break
                    from app.discovery.models import DiscoveredArticle
                    s_cand = DiscoveredArticle(
                        title="",
                        url=s_url,
                        source="",
                        country="US",
                        search_query="manual_seed_intl",
                    )
                    ev = process_candidate_item(s_cand, "international")
                    if ev and (ev in high_confidence_single_candidates or ev in verified_events):
                        log_exec(f"[MANUAL_SEED_QUALIFIED] {ev.canonical_title[:55]}")
            except Exception as e:
                log_exec(f"[MANUAL_SEED_ERROR] Failed to process International seed file: {e}")

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

    # Rank all eligible events, then apply the quality ladder without capping singles at two.
    candidate_pool = ranker.rank_events(
        events=accepted_events,
        top_n=max(10, len(accepted_events)),
    )

    def _ladder_order(scored_event):
        event = scored_event.event
        article = articles_lookup.get(event.article_ids[0]) if event.article_ids else None
        age_hours = get_article_age_hours(article) if article else None
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

    india_ranked = sorted(
        [scored for scored in candidate_pool.india_candidates],
        key=_ladder_order,
    )
    intl_ranked = sorted(
        [scored for scored in candidate_pool.international_candidates],
        key=_ladder_order,
    )
    for rank, scored in enumerate(india_ranked, 1):
        scored.rank = rank
    for rank, scored in enumerate(intl_ranked, 1):
        scored.rank = rank
    candidate_pool.india_candidates = india_ranked[:5]
    candidate_pool.international_candidates = intl_ranked[:5]
    india_pool = candidate_pool.india_candidates
    intl_pool = candidate_pool.international_candidates

    india_two_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    india_sng_count = len([s for s in india_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])
    intl_two_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED])
    intl_sng_count  = len([s for s in intl_pool if s.event.verification_tier == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE])
    india_quality_level = get_quality_level(india_pool, india_two_count, articles_lookup)
    intl_quality_level = get_quality_level(intl_pool, intl_two_count, articles_lookup)
    quality_levels = [india_quality_level, intl_quality_level]
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

    log_exec(f"Stage 7 Summary (Quality Verification Model):")
    log_exec(f"  India pool:         {len(india_pool)} (Two-source: {india_two_count}, Single-source: {india_sng_count})")
    log_exec(f"  International pool: {len(intl_pool)} (Two-source: {intl_two_count}, Single-source: {intl_sng_count})")
    log_exec(f"[GUARANTEED_MODE] Strict 24h result: India={india_two_count + india_sng_count}, Intl={intl_two_count + intl_sng_count}")
    log_exec(f"  India QualityLevel={india_quality_level}; International QualityLevel={intl_quality_level}")

    # =========================================================================
    # PIPELINE SUFFICIENCY GATE (QUALITY VERIFICATION MODEL)
    # =========================================================================
    india_sufficient = len(india_pool) >= 5
    intl_sufficient  = len(intl_pool) >= 5
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
    print(f"India Quality Candidates:         {len(india_pool)}/5 (Two-source: {india_two_count}, Singles: {india_sng_count})")
    print(f"International Quality Candidates: {len(intl_pool)}/5 (Two-source: {intl_two_count}, Singles: {intl_sng_count})")
    print("=" * 60 + "\n")

    if not sufficient:
        log_exec("=" * 60)
        log_exec("PIPELINE SUFFICIENCY GATE: INSUFFICIENT QUALITY STORIES")
        log_exec(f"  India pool:  {len(india_pool)} / 5")
        log_exec(f"  Intl pool:   {len(intl_pool)} / 5")
        log_exec("  Stage 8 Editorial:        SKIPPED")
        log_exec("  Stage 9 Final Validation: SKIPPED")
        log_exec("  Stage 10 Formatter:       SKIPPED")
        log_exec(f"  Pipeline Status:          {pipeline_status}")
        log_exec("=" * 60)

        # Stage 8 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 8: Gemini Editorial — SKIPPED (No 5+5 eligible stories)")
        log_exec("=" * 60)
        log_exec(f"  -> STAGE 8 SKIPPED: Insufficient stories (India={len(india_pool)}, Intl={len(intl_pool)}).")
        selection_payload = BriefingEditorialPayload(india_stories=[], international_stories=[])

        # Stage 9 SKIPPED
        log_exec("=" * 60)
        log_exec("STAGE 9: Final Validation — SKIPPED (No 5+5 eligible stories)")
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
        else:
            err = editorial_res.error_message if editorial_res else "Unknown editorial error"
            log_exec(f"Stage 8 Gemini unavailable/rate-limited ({err}) — using deterministic editorial fallback.")
            india_stories_selected = []
            for s in candidate_pool.india_candidates[:5]:
                ev = s.event
                art = articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                src = ev.primary_publisher or (art.source_name if art else "Business Standard")
                u = ev.primary_url or (art.url if art else f"https://example.com/india-{ev.id}")
                india_stories_selected.append(EditorialStorySelection(
                    section="india",
                    event_id=ev.id,
                    headline=ev.canonical_title,
                    source=src,
                    url=u,
                ))
            intl_stories_selected = []
            for s in candidate_pool.international_candidates[:5]:
                ev = s.event
                art = articles_lookup.get(ev.article_ids[0]) if ev.article_ids else None
                src = ev.primary_publisher or (art.source_name if art else "Reuters")
                u = ev.primary_url or (art.url if art else f"https://example.com/intl-{ev.id}")
                intl_stories_selected.append(EditorialStorySelection(
                    section="international",
                    event_id=ev.id,
                    headline=ev.canonical_title,
                    source=src,
                    url=u,
                ))
            selection_payload = BriefingEditorialPayload(
                india_stories=india_stories_selected,
                international_stories=intl_stories_selected,
            )

        with open(data_dir / "final_10_stories.json", "w", encoding="utf-8") as f:
            json.dump({
                "india":         [s.model_dump() for s in selection_payload.india_stories],
                "international": [s.model_dump() for s in selection_payload.international_stories],
            }, f, indent=2)

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
            quality_ladder_mode=True,
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
    print(f"  Failed:                     {max(0, len(all_records) - len(all_extracted))}")
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
    print("\nVERIFICATION & QUALITY AUDIT (QUALITY VERIFICATION MODEL)")
    print("-" * 30)
    print(f"  Events found:               {len(raw_events)}")
    print(f"  Organic 2nd sources:        {organic_second_sources_found}")
    print(f"  Two-source verified:        {len(verified_events)}")
    print(f"  Quality-verified singles:   {len(high_confidence_single_candidates)}")
    print(f"  RSS discovery queries used: {get_corroboration_count()} / {MAX_CORROBORATION_SEARCHES_PER_RUN} (India: {rss_india_used}, Intl: {rss_international_used})")
    print(f"  Verification failures:      {len(rejected_events_list)}")
    if same_event_rejection_counts:
        print("  Same-event rejection reasons:")
        for r_code, r_cnt in sorted(same_event_rejection_counts.items(), key=lambda x: -x[1]):
            print(f"    - [{r_code}]: {r_cnt}")
    print("\nDEDUPLICATION")
    print("-" * 30)
    print(f"  Removed (history/dedup):    {len(rejected_stories)}")
    print(f"  Remaining:                  {len(accepted_stories)}")
    print("\nRANKING & SUFFICIENCY (QUALITY VERIFICATION MODEL)")
    print("-" * 30)
    print(f"  India quality candidates:   {len(india_pool)} / 5")
    print(f"  International quality candidates: {len(intl_pool)} / 5")
    print(f"  Sufficiency gate:           {'PASSED' if sufficient else 'FAILED (' + pipeline_status + ')'}")
    print("\nEDITORIAL")
    print("-" * 30)
    if sufficient and selection_payload:
        print(f"  Selected India:             {len(selection_payload.india_stories)}")
        print(f"  Selected International:     {len(selection_payload.international_stories)}")
    elif not sufficient:
        print(f"  Status:                     SKIPPED ({pipeline_status})")
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
        print(f"  Status:                     SKIPPED ({pipeline_status})")
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
            print(f"  Reason: Internal processing errors ({internal_pipeline_errors}) prevented candidate extraction.")
        elif not sufficient:
            print(f"\nSTATUS: {pipeline_status}")
            print(f"  India pool:           {len(india_pool)} / 5")
            print(f"  International pool:   {len(intl_pool)} / 5")
            print("  Reason: Fewer than 5 quality-eligible <=24h stories were discovered.")

    if briefing_text:
        print("\n--- [FINAL BRIEFING] ---")
        print(briefing_text)
        print("------------------------\n")

    else:
        print(f"\nFINAL BRIEFING: NOT GENERATED")
        if internal_pipeline_errors > 0:
            print(f"Reason: INSUFFICIENT_VERIFIED_STORIES_WITH_PROCESSING_ERRORS — {internal_pipeline_errors} internal error(s) occurred during candidate processing.")
        elif not sufficient:
            print(f"Reason: {pipeline_status} — Fewer than 5 quality-eligible <=24h stories were discovered.")
        elif validation_report and validation_report.failure_reason:
            print(f"Reason: {validation_report.failure_reason}")

    return 0 if (sufficient and validation_report and validation_report.is_valid) else 1


if __name__ == "__main__":
    st = get_settings()
    max_in  = int(sys.argv[1]) if len(sys.argv) > 1 else st.MAX_DISCOVERY_INDIA
    max_int = int(sys.argv[2]) if len(sys.argv) > 2 else st.MAX_DISCOVERY_INTL
    sys.exit(run_pipeline(max_india=max_in, max_international=max_int))
