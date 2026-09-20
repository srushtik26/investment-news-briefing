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


def _recover_alternate_source(
    cand: Any,
    cand_section: str,
    ctx: PipelineContext,
    active_horizon: float = 24.0,
) -> Optional[Article]:
    """
    When a candidate publisher returns 401/403 or is degraded,
    search for alternate approved coverage of the same underlying event.
    Priority:
    1. Already discovered / extracted candidates or reserves
    2. Google News RSS discovery
    3. SerpAPI only when still needed
    """
    title = getattr(cand, "title", "") or ""
    cand_tokens = set(re.findall(r"\w{4,}", title.lower()))
    if not cand_tokens:
        return None

    blocked_source = normalize_publisher_name(getattr(cand, "source", "") or "")

    # 1. Check already extracted articles from non-blocked publishers
    for existing_art in ctx.all_extracted:
        if existing_art.source_name == blocked_source:
            continue
        art_tokens = set(re.findall(r"\w{4,}", existing_art.title.lower()))
        overlap = len(cand_tokens & art_tokens) / max(1, len(cand_tokens))
        if overlap >= 0.4:
            age_h = get_article_age_hours(existing_art, now_utc=ctx.run_reference_time)
            if age_h is not None and age_h <= active_horizon:
                ctx.log_exec(f"  [ALTERNATE_SOURCE_RECOVERED] Existing coverage from '{existing_art.source_name}' matches blocked '{blocked_source}'")
                return existing_art

    # 2. Check reserves for alternate publisher coverage
    reserves = (
        ctx.india_reserve_pool if cand_section == "india"
        else (ctx.intl_reserve_pool if cand_section == "international" else ctx.domestic_reserve_pool)
    )
    for res_cand in reserves:
        res_source = normalize_publisher_name(getattr(res_cand, "source", "") or "")
        if res_source == blocked_source:
            continue
        res_tokens = set(re.findall(r"\w{4,}", (getattr(res_cand, "title", "") or "").lower()))
        overlap = len(cand_tokens & res_tokens) / max(1, len(cand_tokens))
        if overlap >= 0.4:
            res_url_norm = res_cand.url.strip().lower().rstrip("/")
            if res_url_norm not in ctx.seen_urls:
                ctx.seen_urls.add(res_url_norm)
                try:
                    alt_res = ctx.extractor.extract(
                        url=res_cand.url,
                        source_name=res_source,
                        candidate_title=res_cand.title,
                        candidate_category=cand_section.title(),
                        max_age_hours=active_horizon,
                    )
                    if alt_res.success and alt_res.article:
                        ctx.all_extracted.append(alt_res.article)
                        ctx.articles_lookup[alt_res.article.id] = alt_res.article
                        ctx.log_exec(f"  [ALTERNATE_SOURCE_RECOVERED] Reserve candidate from '{res_source}' replaces blocked '{blocked_source}'")
                        return alt_res.article
                except Exception:
                    pass

    # 3. Google News RSS search for alternative coverage
    if hasattr(ctx, "discovery_service") and ctx.discovery_service and getattr(ctx.discovery_service, "provider", None):
        top_tokens = [t for t in re.findall(r"\b[A-Za-z0-9]{4,}\b", title) if t.lower() not in {"today", "reports", "after", "before", "about", "could", "would", "first", "second"}]
        if len(top_tokens) >= 2:
            alt_query = " ".join(top_tokens[:4])
            country = "India" if cand_section in ("india", "domestic") else "US"
            try:
                items = ctx.discovery_service.provider.discover(query=alt_query, country=country, max_results=5)
                for it in items:
                    it_source = normalize_publisher_name(it.source)
                    if it_source == blocked_source:
                        continue
                    it_url_norm = it.url.strip().lower().rstrip("/")
                    if it_url_norm in ctx.seen_urls:
                        continue
                    ctx.seen_urls.add(it_url_norm)
                    alt_res = ctx.extractor.extract(
                        url=it.url,
                        source_name=it_source,
                        candidate_title=it.title,
                        candidate_category=cand_section.title(),
                        max_age_hours=active_horizon,
                    )
                    if alt_res.success and alt_res.article:
                        ctx.all_extracted.append(alt_res.article)
                        ctx.articles_lookup[alt_res.article.id] = alt_res.article
                        ctx.log_exec(f"  [ALTERNATE_SOURCE_RECOVERED] RSS coverage from '{it_source}' replaces blocked '{blocked_source}' for '{title[:45]}'")
                        return alt_res.article
            except Exception as e:
                ctx.log_exec(f"  [ALTERNATE_SOURCE_ERROR] {e}")

    return None


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
    except Exception as e:
        if cand_section == "domestic":
            ctx.log_exec(
                f'[DOM_DIAG_TARGET_EXTRACTION] title="{cand.title}" success=False method="unknown" word_count=0 error="{e}"'
            )
        ctx.log_exec(f"  [Extraction Error] {cand.url[:60]}: {e}")
        return None

    if cand_section == "domestic":
        w_cnt = ext_res.word_count or 0
        meth = ext_res.extraction_method or "unknown"
        err = ext_res.error_message or ""
        ctx.log_exec(
            f'[DOM_DIAG_TARGET_EXTRACTION] title="{cand.title}" success={ext_res.success} method="{meth}" word_count={w_cnt} error="{err}"'
        )

    if not ext_res.success or not ext_res.article:
        is_blocked = (
            ext_res.status_code in (401, 403)
            or (
                ext_res.error_message
                and any(
                    code in ext_res.error_message.lower()
                    for code in ("401", "403", "forbidden", "unauthorized", "degraded", "blocked")
                )
            )
        )
        if is_blocked:
            alt_article = _recover_alternate_source(cand, cand_section, ctx, active_horizon)
            if alt_article:
                art = alt_article
            else:
                return None
        else:
            return None
    else:
        art = ext_res.article

    ctx.all_extracted.append(art)
    ctx.articles_lookup[art.id] = art

    if cand_section == "domestic":
        filt_res = ctx.domestic_filter_engine.filter_article(art, max_age_hours=active_horizon)
        engine_name = "domestic_filter_engine"
        _filt_reason = (
            getattr(filt_res, "rejection_reason", None)
            or getattr(filt_res, "rule_failed", None)
            or ""
        )
        ctx.log_exec(
            f'[DOM_DIAG_TARGET_FILTER] title="{art.title}" passed={filt_res.is_accepted} engine="{engine_name}" reason="{_filt_reason}"'
        )
    else:
        filt_res = ctx.business_filter_engine.filter_article(art, max_age_hours=active_horizon)
        engine_name = "business_filter_engine"

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
        if cand_section == "domestic":
            cand_cat_str = "domestic"
            art_cat_str = art.category.value if hasattr(art.category, "value") else str(art.category)
            ev_cat_str = event.event_category.value if hasattr(event.event_category, "value") else str(event.event_category)
            ctx.log_exec(
                f'[DOM_CATEGORY_DIAG] title="{art.title}" candidate_category={cand_cat_str} article_category={art_cat_str} event_category={ev_cat_str} filter_engine={engine_name}'
            )

        if not is_multi_event_roundup(event.canonical_title):
            if event.event_category == NewsCategory.DOMESTIC:
                is_elig, conf, rsn = ctx.domestic_evaluator.evaluate(event, art, now_utc=ctx.run_reference_time, max_age_hours=active_horizon)
                if cand_section == "domestic":
                    ctx.log_exec(
                        f'[DOM_DIAG_TARGET_HCSS] title="{event.canonical_title}" score={conf:.1f} passed={is_elig}'
                    )
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
