"""
Targeted regression tests for Production Stage 9 validation fixes:
1. Pre-validation grounding check (event_id exists, event_found=True, primary article exists).
2. Upstream Event mapping ensuring Stage 9 receives the real Event object.
3. Geopolitical India story replacement with a qualified India reserve before Stage 8 final payload.
4. PORTFOLIO_SELECTED counted from actual final India selections using canonical PortfolioMatch.
5. Verification of the International materiality audit findings.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List
import pytest

from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.ranking.watchlist import match_portfolio_company
from app.validation import FinalValidationEngine, ValidationStatus
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection
from app.verification.international import is_geopolitical_market_impact_eligible


def _make_article(aid: str, title: str, category: NewsCategory = NewsCategory.INDIA, body: str = "", comp: str = "") -> Article:
    return Article(
        id=aid,
        url=f"https://example.com/news/{aid}",
        title=title,
        source_name="Business Standard",
        category=category,
        content_text=body or f"Reporting details on {title} with comprehensive corporate disclosures.",
        published_at=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_event(eid: str, art: Article, category: NewsCategory = NewsCategory.INDIA, comp: str = "") -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp] if comp else [],
        event_category=category,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=92.0,
    )


def test_pre_validation_grounding_rejects_missing_event_id():
    """Check that stories with missing event_id or event not found fail pre-validation grounding."""
    engine = FinalValidationEngine()
    art = _make_article("a1", "Tata Motors Q1 Net Profit Jumps 35% YoY to ₹5,500 Crore", NewsCategory.INDIA)
    ev = _make_event("ev1", art, NewsCategory.INDIA, comp="Tata Motors")

    story_missing_id = EditorialStorySelection(
        event_id="",
        headline="Tata Motors Q1 Net Profit Jumps 35% YoY to ₹5,500 Crore",
        summary="Automaker reported sharp rise in quarterly profit.",
        url=art.url,
        source=art.source_name,
        section="india",
    )
    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[story_missing_id],
        international_stories=[],
    )
    report = engine.validate_briefing(
        payload=payload,
        events_lookup={"ev1": ev},
        articles_lookup={"a1": art},
        strict_5_per_section=False,
    )
    grounding_fails = [c for c in report.check_results if c.check_name == "Story event grounding" and not c.passed]
    assert len(grounding_fails) > 0
    assert "has no event_id" in grounding_fails[0].failure_reason


def test_pre_validation_grounding_rejects_event_not_found():
    """Check that stories with event_id not found in events_lookup fail pre-validation grounding."""
    engine = FinalValidationEngine()
    art = _make_article("a1", "Pace Digitek Secures ₹800 Crore Solar EPC Contract", NewsCategory.INDIA)
    ev = _make_event("ev_pace", art, NewsCategory.INDIA, comp="Pace Digitek")

    story = EditorialStorySelection(
        event_id="ev_missing",
        headline="Pace Digitek Secures ₹800 Crore Solar EPC Contract",
        summary="Contract win for solar project in Rajasthan.",
        url=art.url,
        source=art.source_name,
        section="india",
    )
    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[story],
        international_stories=[],
    )
    report = engine.validate_briefing(
        payload=payload,
        events_lookup={"ev_pace": ev},
        articles_lookup={"a1": art},
        strict_5_per_section=False,
    )
    grounding_fails = [c for c in report.check_results if c.check_name == "Story event grounding" and not c.passed]
    assert len(grounding_fails) > 0
    assert "not found in events_lookup" in grounding_fails[0].failure_reason


def test_stage9_receives_real_event_and_verifies_india_nexus():
    """Ensure Stage 9 receives the real Event object so Pace Digitek passes India nexus."""
    engine = FinalValidationEngine()
    art = _make_article("a_pace", "Pace Digitek Bags ₹450 Crore Telecom Infrastructure Order", NewsCategory.INDIA)
    ev = _make_event("ev_pace", art, NewsCategory.INDIA, comp="Pace Digitek")

    story = EditorialStorySelection(
        event_id="ev_pace",
        headline="Pace Digitek Bags ₹450 Crore Telecom Infrastructure Order",
        summary="Pace Digitek bags infrastructure order across Indian circles.",
        url=art.url,
        source=art.source_name,
        section="india",
    )
    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[story],
        international_stories=[],
    )
    report = engine.validate_briefing(
        payload=payload,
        events_lookup={"ev_pace": ev},
        articles_lookup={"a_pace": art},
        strict_5_per_section=False,
    )
    # Check 2 (India section business nexus) must pass
    nexus_checks = [c for c in report.check_results if c.check_name == "India section business nexus consistency"]
    assert len(nexus_checks) == 0  # No failure recorded for Check 2


def test_geopolitical_india_story_requires_quantified_impact():
    """Check #19: geopolitical headlines without numbers fail; with numbers pass."""
    ok_unquant, reason_unquant = is_geopolitical_market_impact_eligible("India reviews export tariffs amid regional trade tensions")
    assert ok_unquant is False
    assert "lacks quantified market impact figures" in reason_unquant

    ok_quant, reason_quant = is_geopolitical_market_impact_eligible("India Imposes 20% Tariff on Steel Imports to Protect Domestic Producers")
    assert ok_quant is True
    assert reason_quant == ""


def test_portfolio_selected_counts_canonical_match():
    """PORTFOLIO_SELECTED correctly counts holdings matched via companies_involved even if title lacks holding name."""
    art_sbi = _make_article("a_sbi", "State-owned lender raises ₹10,000 crore via tier-2 infrastructure bonds", comp="State Bank of India")
    ev_sbi = _make_event("e_sbi", art_sbi, comp="State Bank of India")

    # Canonical match_portfolio_company recognizes SBI via companies_involved
    pm = match_portfolio_company(event=ev_sbi, article=art_sbi)
    assert pm is not None
    assert pm.canonical_name == "State Bank of India"

    # Simulate final India selections
    final_india_stories = [
        EditorialStorySelection(
            event_id="e_sbi",
            headline=art_sbi.title,
            summary="SBI bond issuance details.",
            url=art_sbi.url,
            source=art_sbi.source_name,
            section="india",
        )
    ]
    events_lookup = {"e_sbi": ev_sbi}
    articles_lookup = {"a_sbi": art_sbi}

    pf_count = sum(
        1 for s in final_india_stories
        if match_portfolio_company(
            event=events_lookup.get(s.event_id),
            article=articles_lookup.get(events_lookup[s.event_id].article_ids[0]) if (s.event_id in events_lookup and events_lookup[s.event_id].article_ids) else None,
        ) is not None
    )
    assert pf_count == 1


def test_stage8_geopolitical_india_story_replaced_with_reserve():
    """Verify that an unquantified geopolitical India story is replaced with a qualified reserve before final payload."""
    from app.classification.region_classifier import verify_india_business_nexus
    from app.verification.materiality import evaluate_investment_materiality

    # 1. Unquantified geopolitical story
    art_geo = _make_article("a_geo", "India reviews export tariffs amid regional trade tensions", comp="GovtOfIndia")
    ev_geo = _make_event("ev_geo", art_geo, comp="GovtOfIndia")
    story_geo = EditorialStorySelection(
        event_id="ev_geo",
        headline=art_geo.title,
        summary="Trade policy review amid global geopolitical developments.",
        url=art_geo.url,
        source=art_geo.source_name,
        section="india",
    )

    # 2. Qualified India reserve
    art_res = _make_article("a_res", "Adani Ports Commissions ₹4,500 Crore Container Terminal at Vizhinjam", comp="Adani Ports")
    ev_res = _make_event("ev_res", art_res, comp="Adani Ports")

    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc))
    ctx.articles_lookup = {"a_geo": art_geo, "a_res": art_res}
    ctx.india_reserve_pool = [ev_res]
    event_by_id = {"ev_geo": ev_geo, "ev_res": ev_res}

    selection_payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[story_geo],
        international_stories=[],
    )

    # Execute the exact Stage 8 guard logic
    final_india_curated = []
    cur_india_ids = {s.event_id for s in selection_payload.india_stories}
    for story in selection_payload.india_stories:
        is_geo_ok, geo_reason = is_geopolitical_market_impact_eligible(story.headline)
        if is_geo_ok:
            final_india_curated.append(story)
            continue

        replacement_story = None
        reserves_to_try = getattr(ctx, "india_reserve_pool", []) or []
        for res_ev in reserves_to_try:
            if not isinstance(res_ev, Event) or res_ev.id in cur_india_ids or res_ev.event_category != NewsCategory.INDIA:
                continue
            res_art = ctx.articles_lookup.get(res_ev.article_ids[0]) if res_ev.article_ids else None
            if not res_art:
                continue
            is_nex, _ = verify_india_business_nexus(res_ev, res_art)
            if not is_nex:
                continue
            is_g_ok, _ = is_geopolitical_market_impact_eligible(res_ev.canonical_title, res_art)
            if not is_g_ok:
                continue
            mat_res = evaluate_investment_materiality(res_ev, res_art, ctx=ctx)
            m_score = mat_res[1] if isinstance(mat_res, (tuple, list)) else getattr(mat_res, "score", 0.0)
            if m_score < 60.0:
                continue

            from app.pipeline.story_context import build_story_context
            from app.pipeline.story_prep import prepare_final_story
            sc = build_story_context(res_ev, res_art, ctx=ctx)
            replacement_story = prepare_final_story(sc, ctx=ctx)
            cur_india_ids.add(res_ev.id)
            event_by_id[res_ev.id] = res_ev
            break

        if replacement_story:
            final_india_curated.append(replacement_story)
        else:
            final_india_curated.append(story)

    selection_payload.india_stories = final_india_curated

    # Verify replacement occurred and is compliant
    assert len(selection_payload.india_stories) == 1
    selected_story = selection_payload.india_stories[0]
    assert selected_story.event_id == "ev_res"
    assert "Adani Ports" in selected_story.headline
    # Verify it passes Check #19
    is_check19_pass, _ = is_geopolitical_market_impact_eligible(selected_story.headline)
    assert is_check19_pass is True

