"""
Second-source enrichment phase: free Google News RSS corroboration for single-source candidates.
"""

import re
from datetime import timezone
from typing import Dict, List
from urllib.parse import urlparse

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.models.entity_sanitizer import sanitize_company_entities
from app.filtering.rules import URLFilterRule, SourceFilterRule
from app.ranking.scorer import InvestmentRelevanceScorer
from app.verification.query_builder import EventQueryBuilder
from app.pipeline.context import PipelineContext


def run_second_source_enrichment(ctx: PipelineContext) -> None:
    """
    Search free Google News RSS for independent second sources for eligible single-source candidates.
    Upgrades matching candidates to TWO_SOURCE_VERIFIED.
    """
    ctx.metrics.start_timer("enrichment_seconds")
    ctx.log_exec("=" * 60)
    ctx.log_exec("SECOND-SOURCE ENRICHMENT: Searching FREE Google News RSS for independent second sources")
    ctx.log_exec("=" * 60)

    single_source_targets_raw = [
        e for e in (ctx.high_confidence_single_candidates + ctx.verified_events)
        if getattr(e, "verification_tier", None) == VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
        or len(e.article_ids) < 2
    ]

    scorer = InvestmentRelevanceScorer()
    def _score_cand_event(ev: Event) -> float:
        if not ev.article_ids or ev.article_ids[0] not in ctx.articles_lookup:
            return 0.0
        primary_art = ctx.articles_lookup[ev.article_ids[0]]
        freshness = 0.8
        if primary_art and primary_art.metadata:
            freshness = primary_art.metadata.get("freshness_score", 0.8)
        scored = scorer.score_event(
            ev,
            source_count=1,
            is_multi_source_verified=False,
            freshness_score=freshness,
        )
        return scored.investment_score

    section_candidates: Dict[NewsCategory, List[Event]] = {}
    for ev in single_source_targets_raw:
        section_candidates.setdefault(ev.event_category, []).append(ev)

    single_source_targets: List[Event] = []
    for cat, ev_list in section_candidates.items():
        sorted_evs = sorted(ev_list, key=_score_cand_event, reverse=True)
        single_source_targets.extend(sorted_evs[:7])

    ctx.log_exec(f"[ENRICHMENT] Found {len(single_source_targets_raw)} eligible single-source candidates. Targeted top {len(single_source_targets)} candidates (max 7 per section) for enrichment.")

    for ev in single_source_targets:
        if not ev.article_ids or ev.article_ids[0] not in ctx.articles_lookup:
            continue
        primary_art = ctx.articles_lookup[ev.article_ids[0]]
        prim_domain = urlparse(primary_art.url).netloc.lower().replace("www.", "")

        ev_horizon = (ev.metadata or {}).get("fallback_horizon_hours", 24.0)
        when_days = 1 if ev_horizon <= 36.0 else (2 if ev_horizon <= 48.0 else 3)
        when_param = f"when:{when_days}d"
        country_code = "IN" if ev.event_category in (NewsCategory.INDIA, NewsCategory.DOMESTIC) else "US"

        extracted_entities = EventQueryBuilder.extract_entities(primary_art, event=ev)
        comps = sanitize_company_entities(
            (ev.companies_involved or []) + extracted_entities,
            publisher=primary_art.source_name,
        )
        main_comp = comps[0] if comps else ""

        t_low = primary_art.title.lower()
        if any(w in t_low for w in ("acquisition", "acquire", "acquires", "buyout", "merger", "buys")):
            act_kw = "acquisition"
        elif any(w in t_low for w in ("earnings", "results", "quarterly profit", "revenue", "net profit", "q1", "q2", "q3", "q4")):
            act_kw = "earnings results"
        elif any(w in t_low for w in ("funding", "raises", "funding round", "investment")):
            act_kw = "funding"
        elif any(w in t_low for w in ("contract", "bags order", "wins order", "order win")):
            act_kw = "contract"
        else:
            act_kw = "business"

        enrich_queries = []
        if main_comp and main_comp.lower() not in ("unspecified", "unspecified_entity"):
            enrich_queries.append(f'"{main_comp}" {act_kw} {when_param}')
            enrich_queries.append(f'{main_comp} {act_kw} {when_param}')
        else:
            title_words = [w for w in re.findall(r"[a-zA-Z0-9]+", primary_art.title) if len(w) >= 4][:4]
            if len(title_words) >= 2:
                enrich_queries.append(f'{" ".join(title_words)} {when_param}')

        enriched = False
        for eq in enrich_queries:
            if enriched:
                break
            try:
                cand_items = ctx.discovery_service.provider.discover(query=eq, country=country_code, max_results=5)
                for cand_it in cand_items:
                    cand_url = cand_it.url.strip()
                    if not URLFilterRule.is_valid_url(cand_url)[0]:
                        continue
                    if cand_url.lower().rstrip("/") in ctx.seen_urls:
                        continue
                    cand_netloc = urlparse(cand_url).netloc.lower().replace("www.", "")
                    if cand_netloc == prim_domain or (prim_domain and prim_domain in cand_netloc):
                        continue
                    if ctx.extractor.is_domain_degraded(cand_netloc):
                        continue
                    src_rule = SourceFilterRule()
                    dummy_art = Article(
                        id="check_src",
                        title=cand_it.title,
                        url=cand_url,
                        source_name=cand_it.source_name,
                        category=ev.event_category,
                        is_verified_url=True,
                    )
                    if not src_rule.evaluate(dummy_art).is_accepted:
                        continue
                    ext_res = ctx.extractor.extract(
                        url=cand_url,
                        source_name=cand_it.source_name,
                        candidate_title=cand_it.title,
                        candidate_category=ev.event_category.value if hasattr(ev.event_category, "value") else str(ev.event_category),
                        candidate_pub_date=cand_it.published_at,
                    )
                    if not ext_res.success or not ext_res.article:
                        continue
                    sec_art = ext_res.article
                    ctx.seen_urls.add(sec_art.url.lower().rstrip("/"))
                    if ctx.verifier.get_publisher_group(primary_art) == ctx.verifier.get_publisher_group(sec_art):
                        continue
                    if sec_art.published_at:
                        s_age = max(0.0, (ctx.run_reference_time - sec_art.published_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600.0)
                        if s_age > ev_horizon:
                            continue
                    is_same, score, reason = ctx.verifier.is_same_underlying_event(
                        primary_art, sec_art, now_utc=ctx.run_reference_time, max_age_hours=ev_horizon
                    )
                    if not is_same:
                        continue
                    if ctx.verifier.is_syndicated_republication(primary_art, sec_art)[0]:
                        continue

                    # UPGRADE EVENT TO TWO_SOURCE_VERIFIED
                    ctx.articles_lookup[sec_art.id] = sec_art
                    ev.article_ids.append(sec_art.id)
                    ev.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
                    ev.verification_confidence = 95.0
                    ev.primary_publisher = primary_art.source_name
                    ev.primary_url = primary_art.url
                    ev.secondary_publisher = sec_art.source_name
                    ev.secondary_url = sec_art.url
                    ev.verification_reason = f"Corroborated by independent publishers ({primary_art.source_name} and {sec_art.source_name})."
                    if ev in ctx.high_confidence_single_candidates and ev not in ctx.verified_events:
                        ctx.verified_events.append(ev)
                    ctx.log_exec(f"[SECOND_SOURCE_ENRICHED] Upgraded '{ev.canonical_title[:40]}' to TWO_SOURCE_VERIFIED via {sec_art.source_name} ({sec_art.url})")
                    enriched = True
                    break
            except Exception as e:
                ctx.log_exec(f"[ENRICHMENT_ERROR] {e}")

    ctx.metrics.stop_timer("enrichment_seconds")
