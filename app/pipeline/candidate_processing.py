"""
Candidate processing, extraction, company population, and single candidate verification.
"""

import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Set, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.models.entity_sanitizer import sanitize_company_entities, normalize_publisher_name
from app.extraction import ArticleExtractor
from app.filtering.rules import URLFilterRule
from app.verification.single_source import is_multi_event_roundup
from app.verification.query_builder import EventQueryBuilder, GENERIC_ENTITY_BLACKLIST
from app.pipeline.context import PipelineContext


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


def _extract_candidates(
    candidates_with_country: List[Tuple[Any, str]],
    extractor: ArticleExtractor,
    seen_urls: Set[str],
    log_exec: Callable[[str], None],
) -> Tuple[List[Article], List[Dict[str, Any]], int, int, int, int, int]:
    """
    Extract full text from a list of (DiscoveredArticle, country) candidates.
    Uses bounded concurrency (max 5 threads) and preserves deterministic ordering.
    Returns (extracted_articles, extraction_records, google_urls, resolved_ok, fallback_ok, pre_url_rejects, duplicate_seen).
    """
    total = len(candidates_with_country)
    extracted: List[Article] = []
    records: List[Dict[str, Any]] = []
    google_count = resolved_ok = fallback_ok = pre_url_rejects = duplicate_seen = 0

    # Step 1: Filter duplicates against seen_urls in deterministic sequence
    to_extract: List[Tuple[int, Any, str, str, Optional[datetime]]] = []
    for idx, (cand, country) in enumerate(candidates_with_country, 1):
        norm_url = cand.url.strip().lower().rstrip("/")
        if norm_url in seen_urls:
            duplicate_seen += 1
            continue
        seen_urls.add(norm_url)
        rss_published_at = get_candidate_published_at(cand)
        canonical_source = normalize_publisher_name(cand.source)
        to_extract.append((idx, cand, country, canonical_source, rss_published_at))

    if not to_extract:
        return extracted, records, google_count, resolved_ok, fallback_ok, pre_url_rejects, duplicate_seen

    # Step 2: Extract concurrently with at most 5 threads
    def _worker(item):
        idx, cand, country, canonical_source, rss_pub_at = item
        try:
            res = extractor.extract(
                url=cand.url,
                source_name=canonical_source,
                candidate_title=cand.title,
                candidate_category=country,
                candidate_pub_date=rss_pub_at,
                max_age_hours=72.0,
            )
            return (idx, cand, country, canonical_source, rss_pub_at, res, None)
        except Exception as exc:
            return (idx, cand, country, canonical_source, rss_pub_at, None, exc)

    max_workers = min(5, len(to_extract))
    results_map = {}
    if max_workers <= 1:
        for item in to_extract:
            r = _worker(item)
            results_map[r[0]] = r
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_worker, item) for item in to_extract]
            for fut in as_completed(futures):
                r = fut.result()
                results_map[r[0]] = r

    # Step 3: Process results in strictly deterministic original order (by idx)
    for item in to_extract:
        idx = item[0]
        _, cand, country, canonical_source, rss_published_at, res, exc = results_map[idx]
        log_exec(f"[{idx}/{total}] Extracting ({country}): '{cand.title[:50]}' ({canonical_source})")
        if extractor.resolver.is_google_news_url(cand.url):
            google_count += 1

        if exc is not None:
            log_exec(f"  -> ERROR during extraction: {exc}")
            continue

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
            if res.extraction_method in ("pre_url_filter", "blocked_cache", "degraded_domain_skip", "pre_filter_stale_date", "pre_filter_generic_headline") or (res.error_message and "PRE_EXTRACTION" in res.error_message):
                pre_url_rejects += 1
                log_exec(f"  -> PRE_EXTRACTION_REJECTED: {res.resolved_url[:60]} ({res.error_message})")
            else:
                log_exec(f"  -> FAILED ({res.extraction_method}): {res.error_message}")

    return extracted, records, google_count, resolved_ok, fallback_ok, pre_url_rejects, duplicate_seen


def process_candidate_item(
    cand: Any,
    cand_section: str,
    ctx: PipelineContext,
    active_horizon: float = 24.0,
) -> Optional[Event]:
    """Process a single reserve candidate item: extract, filter, classify, and verify."""
    u_norm = cand.url.strip().lower().rstrip("/")
    if u_norm in ctx.seen_urls:
        return None
    ctx.seen_urls.add(u_norm)

    rss_pub = get_candidate_published_at(cand)
    if rss_pub:
        pub_time = rss_pub
        if pub_time.tzinfo is None:
            pub_time = pub_time.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        age_hours = max(0.0, (now_utc - pub_time).total_seconds() / 3600.0)
        if age_hours > active_horizon:
            ctx.log_exec(f"  [Candidate] STALE_PRE_REJECT: '{cand.title[:50]}' ({age_hours:.1f}h old > {active_horizon:.0f}h limit)")
            return None

    canonical_source = normalize_publisher_name(cand.source)

    try:
        ext_res = ctx.extractor.extract(
            url=cand.url,
            source_name=canonical_source,
            candidate_title=cand.title,
            candidate_category=cand_section.title(),
            candidate_pub_date=rss_pub,
            max_age_hours=active_horizon,
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
        ctx.all_records.append(rec)
    except Exception as e:
        ctx.log_exec(f"  [Extraction Error] {cand.url[:60]}: {e}")
        return None

    if not ext_res.success or not ext_res.article:
        return None

    art = ext_res.article
    ctx.all_extracted.append(art)
    ctx.articles_lookup[art.id] = art

    if cand_section == "domestic":
        filt_res = ctx.domestic_filter_engine.filter_article(art, max_age_hours=active_horizon)
    else:
        filt_res = ctx.business_filter_engine.filter_article(art, max_age_hours=active_horizon)

    if not filt_res.is_accepted:
        if filt_res.rule_failed == "DATE":
            ctx.date_deferred_articles.append(art)
        return None

    class_res = ctx.classifier.classify(art)
    if not class_res.success or not class_res.classification:
        return None

    if cand_section != "domestic" and not class_res.classification.is_hard_business_event:
        return None

    art_event_cat = NewsCategory.DOMESTIC if cand_section == "domestic" else (
        NewsCategory.INDIA if cand_section == "india" else NewsCategory.INTERNATIONAL
    )
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
        (e for e in (ctx.verified_events + ctx.high_confidence_single_candidates + ctx.single_source_events) if e.article_ids and
         ctx.verifier.is_same_underlying_event(
             ctx.articles_lookup.get(e.article_ids[0], art), art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon
          )[0]),
        None
    )
    if existing_event:
        if art.id not in existing_event.article_ids:
            existing_event.article_ids.append(art.id)
            ev_arts = [ctx.articles_lookup[i] for i in existing_event.article_ids if i in ctx.articles_lookup]
            rv = ctx.verifier.verify_event(existing_event, ev_arts, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
            if rv.is_verified:
                existing_event.event_category = ctx.reg_clf.classify_event(existing_event, ev_arts)
                existing_event.metadata = getattr(existing_event, "metadata", {}) or {}
                existing_event.metadata["fallback_horizon_hours"] = max(existing_event.metadata.get("fallback_horizon_hours", 24.0), active_horizon)
                if existing_event not in ctx.verified_events:
                    ctx.verified_events.append(existing_event)
                    ctx.organic_second_sources_found += 1
                    ctx.log_exec(f"    EXPANSION ORGANICALLY VERIFIED: {existing_event.canonical_title[:45]}")
                if existing_event in ctx.high_confidence_single_candidates:
                    ctx.high_confidence_single_candidates.remove(existing_event)
            else:
                if art.id in existing_event.article_ids:
                    existing_event.article_ids.remove(art.id)
        return existing_event
    else:
        ctx.single_source_events.append(event)
        event.event_category = ctx.reg_clf.classify_event(event, [art])
        if not is_multi_event_roundup(event.canonical_title):
            if event.event_category == NewsCategory.DOMESTIC:
                is_elig, conf, rsn = ctx.domestic_evaluator.evaluate(event, art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
            else:
                is_elig, conf, rsn = ctx.single_source_evaluator.evaluate_event(event, art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)

            if is_elig:
                event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
                event.verification_confidence = conf
                event.single_source_confidence_score = conf
                event.primary_publisher = art.source_name
                event.primary_url = art.url
                event.secondary_publisher = None
                event.secondary_url = None
                event.article_ids = [art.id]
                event.verification_reason = rsn
                event.metadata = getattr(event, "metadata", {}) or {}
                event.metadata["fallback_horizon_hours"] = active_horizon
                if event not in ctx.high_confidence_single_candidates and event not in ctx.verified_events:
                    ctx.high_confidence_single_candidates.append(event)
                    prefix = f"[{event.event_category.value.upper()}_QUALIFIED]"
                    ctx.log_exec(f"    {prefix} {event.canonical_title[:55]} | {rsn}")
        return event
