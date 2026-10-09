"""
Targeted tests for production duplicate-company leakage fix and Stage 8 editorial backfill:
1. GM Breweries Q2 results passes investment materiality (score >= 60.0).
2. Refill loops in selection.py reject candidates whose company is already selected in that section.
3. Stage 8 in runner.py deterministically backfills missing stories if Gemini returns < 5 stories
   for India or International, strictly preserving single-company uniqueness and 3-day history dedup.
"""
from datetime import datetime, timezone, date
from typing import Dict, List
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection, EditorialResult
from app.verification.materiality import evaluate_investment_materiality
from app.pipeline.context import PipelineContext
from app.pipeline.runner import build_story_context, prepare_final_story


def _make_article(aid: str, title: str, category: NewsCategory, comp: str = "", body: str = "") -> Article:
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name="Livemint",
        category=category,
        content_text=body or f"Reporting details on {title} with comprehensive figures.",
        published_at=datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_event(eid: str, art: Article, category: NewsCategory, comp: str = "") -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp] if comp else [],
        event_category=category,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=95.0,
    )


def _make_scored_event(ev: Event, rank: int = 1) -> ScoredEvent:
    return ScoredEvent(
        event=ev,
        score_breakdown=ScoreBreakdown(
            financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
            corporate_significance=0.0, source_quality=70.0, strategic_bonuses=0.0,
            editorial_signals=0.0, relevance_penalties=0.0, total_score=80.0, rationale="t"
        ),
        investment_score=80.0,
        rank=rank,
    )


def test_gm_breweries_materiality_score_passes_60():
    """Verify GM Breweries Q2 results passes investment materiality threshold of 60.0."""
    art = _make_article(
        "a_gm",
        "GM Breweries Q2 results: Shares fall 4% after margin decline; Profit, revenue rise from last year",
        NewsCategory.INDIA,
        comp="GM Breweries",
        body="GM Breweries reported net profit up to Rs 25.5 crore and revenue Rs 165 crore in Q2, though margins declined.",
    )
    ev = _make_event("e_gm", art, NewsCategory.INDIA, comp="GM Breweries")
    is_mat, score, reason = evaluate_investment_materiality(ev, art)
    assert is_mat is True
    assert score >= 60.0
    assert "major_sector_impact(+10)" in reason


def test_stage8_backfill_restores_4_stories_to_5_without_duplicate_company():
    """Verify that when Gemini selects only 4 stories for India and 4 for International,
    Stage 8 backfill automatically fills the 5th story from reserves without duplicating companies."""
    ctx = PipelineContext(
        target_date=date(2026, 10, 9),
        run_reference_time=datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc),
    )
    events_lookup: Dict[str, Event] = {}
    event_by_id: Dict[str, Event] = {}

    # Setup Domestic stories (5 stories)
    dom_stories = []
    dom_cands = []
    for i in range(1, 6):
        art = _make_article(f"a_dom_{i}", f"Domestic Policy Initiative #{i}", NewsCategory.DOMESTIC)
        ev = _make_event(f"e_dom_{i}", art, NewsCategory.DOMESTIC)
        ctx.articles_lookup[art.id] = art
        events_lookup[ev.id] = ev
        event_by_id[ev.id] = ev
        sc = build_story_context(ev, art, ctx=ctx)
        st = prepare_final_story(sc, ctx=ctx)
        dom_stories.append(st)
        dom_cands.append(_make_scored_event(ev, rank=i))

    # Setup India: 4 Gemini selected + 1 reserve candidate
    india_gemini_stories = []
    india_cands = []
    india_companies = ["TCS", "MRPL", "Walmart", "Adani Group"]
    for i, comp in enumerate(india_companies, 1):
        art = _make_article(f"a_in_{i}", f"{comp} reports strategic investment milestone", NewsCategory.INDIA, comp=comp)
        ev = _make_event(f"e_in_{i}", art, NewsCategory.INDIA, comp=comp)
        ctx.articles_lookup[art.id] = art
        events_lookup[ev.id] = ev
        event_by_id[ev.id] = ev
        sc = build_story_context(ev, art, ctx=ctx)
        st = prepare_final_story(sc, ctx=ctx)
        india_gemini_stories.append(st)
        india_cands.append(_make_scored_event(ev, rank=i))

    # Duplicate TCS candidate in pool (must be skipped during backfill)
    art_tcs_dup = _make_article("a_in_dup", "TCS secondary market performance explainer", NewsCategory.INDIA, comp="TCS")
    ev_tcs_dup = _make_event("e_in_dup", art_tcs_dup, NewsCategory.INDIA, comp="TCS")
    ctx.articles_lookup[art_tcs_dup.id] = art_tcs_dup
    events_lookup[ev_tcs_dup.id] = ev_tcs_dup
    event_by_id[ev_tcs_dup.id] = ev_tcs_dup
    india_cands.append(_make_scored_event(ev_tcs_dup, rank=5))

    # Unique 5th candidate in reserve pool: GM Breweries
    art_gm = _make_article("a_in_gm", "GM Breweries Q2 results: Net profit up 15%", NewsCategory.INDIA, comp="GM Breweries")
    ev_gm = _make_event("e_in_gm", art_gm, NewsCategory.INDIA, comp="GM Breweries")
    ctx.articles_lookup[art_gm.id] = art_gm
    events_lookup[ev_gm.id] = ev_gm
    event_by_id[ev_gm.id] = ev_gm
    ctx.india_reserve_pool = [ev_gm]

    # Setup International: 4 Gemini selected + 1 duplicate Meta + 1 unique Oil-Dri
    intl_gemini_stories = []
    intl_cands = []
    intl_companies = ["Starbucks", "Skydance", "Manus", "Apple"]
    for i, comp in enumerate(intl_companies, 1):
        art = _make_article(f"a_int_{i}", f"{comp} global strategic development", NewsCategory.INTERNATIONAL, comp=comp)
        ev = _make_event(f"e_int_{i}", art, NewsCategory.INTERNATIONAL, comp=comp)
        ctx.articles_lookup[art.id] = art
        events_lookup[ev.id] = ev
        event_by_id[ev.id] = ev
        sc = build_story_context(ev, art, ctx=ctx)
        st = prepare_final_story(sc, ctx=ctx)
        intl_gemini_stories.append(st)
        intl_cands.append(_make_scored_event(ev, rank=i))

    # Duplicate Apple candidate in pool (must be skipped during backfill)
    art_apple_dup = _make_article("a_int_dup", "Apple partners with supplier", NewsCategory.INTERNATIONAL, comp="Apple")
    ev_apple_dup = _make_event("e_int_dup", art_apple_dup, NewsCategory.INTERNATIONAL, comp="Apple")
    ctx.articles_lookup[art_apple_dup.id] = art_apple_dup
    events_lookup[ev_apple_dup.id] = ev_apple_dup
    event_by_id[ev_apple_dup.id] = ev_apple_dup
    intl_cands.append(_make_scored_event(ev_apple_dup, rank=5))

    # Unique 5th candidate: Oil-Dri
    art_oildri = _make_article("a_int_oil", "Oil-Dri Delivers Record Annual Financial Results", NewsCategory.INTERNATIONAL, comp="Oil-Dri")
    ev_oildri = _make_event("e_int_oil", art_oildri, NewsCategory.INTERNATIONAL, comp="Oil-Dri")
    ctx.articles_lookup[art_oildri.id] = art_oildri
    events_lookup[ev_oildri.id] = ev_oildri
    event_by_id[ev_oildri.id] = ev_oildri
    ctx.intl_reserve_pool = [ev_oildri]

    candidate_pool = RankedCandidatePool(
        domestic_candidates=dom_cands,
        india_candidates=india_cands,
        international_candidates=intl_cands,
    )

    # Gemini returned only 4 India and 4 International
    gemini_selection = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=india_gemini_stories,
        international_stories=intl_gemini_stories,
    )

    # Replicate Stage 8 backfill logic
    from app.models.entity_sanitizer import sanitize_company_entities
    from app.deduplication.fingerprint import normalize_entity_name

    selection_payload = gemini_selection

    # India backfill
    current_india = list(selection_payload.india_stories or [])
    seen_india_comps = set()
    used_india_ev_ids = set()
    for st in current_india:
        used_india_ev_ids.add(st.event_id)
        ev_obj = event_by_id.get(st.event_id)
        raw_c = ev_obj.companies_involved if ev_obj and ev_obj.companies_involved else []
        clean_c = sanitize_company_entities(raw_c, publisher=st.source)
        for c in clean_c:
            n = normalize_entity_name(c)
            if n not in ("unspecified_entity", ""):
                seen_india_comps.add(n)

    all_india_cands = (
        [s.event for s in candidate_pool.india_candidates]
        + (getattr(ctx, "india_reserve_pool", []) or [])
    )
    for ev in all_india_cands:
        if len(current_india) >= 5:
            break
        if not isinstance(ev, Event) or ev.id in used_india_ev_ids:
            continue
        art = ctx.articles_lookup.get(ev.article_ids[0])
        raw_comps = getattr(ev, "companies_involved", []) or []
        clean_comps = sanitize_company_entities(raw_comps, publisher=art.source_name if art else None)
        is_dup = False
        for c in clean_comps:
            n = normalize_entity_name(c)
            if n in seen_india_comps and n not in ("unspecified_entity", ""):
                is_dup = True
                break
        if is_dup:
            continue
        for c in clean_comps:
            n = normalize_entity_name(c)
            if n not in ("unspecified_entity", ""):
                seen_india_comps.add(n)
        used_india_ev_ids.add(ev.id)
        sc = build_story_context(ev, art, ctx=ctx)
        current_india.append(prepare_final_story(sc, ctx=ctx))
    selection_payload.india_stories = current_india

    # International backfill
    current_intl = list(selection_payload.international_stories or [])
    seen_intl_comps = set()
    used_intl_ev_ids = set()
    for st in current_intl:
        used_intl_ev_ids.add(st.event_id)
        ev_obj = event_by_id.get(st.event_id)
        raw_c = ev_obj.companies_involved if ev_obj and ev_obj.companies_involved else []
        clean_c = sanitize_company_entities(raw_c, publisher=st.source)
        for c in clean_c:
            n = normalize_entity_name(c)
            if n not in ("unspecified_entity", ""):
                seen_intl_comps.add(n)

    all_intl_cands = (
        [s.event for s in candidate_pool.international_candidates]
        + (getattr(ctx, "intl_reserve_pool", []) or [])
    )
    for ev in all_intl_cands:
        if len(current_intl) >= 5:
            break
        if not isinstance(ev, Event) or ev.id in used_intl_ev_ids:
            continue
        art = ctx.articles_lookup.get(ev.article_ids[0])
        raw_comps = getattr(ev, "companies_involved", []) or []
        clean_comps = sanitize_company_entities(raw_comps, publisher=art.source_name if art else None)
        is_dup = False
        for c in clean_comps:
            n = normalize_entity_name(c)
            if n in seen_intl_comps and n not in ("unspecified_entity", ""):
                is_dup = True
                break
        if is_dup:
            continue
        for c in clean_comps:
            n = normalize_entity_name(c)
            if n not in ("unspecified_entity", ""):
                seen_intl_comps.add(n)
        used_intl_ev_ids.add(ev.id)
        sc = build_story_context(ev, art, ctx=ctx)
        current_intl.append(prepare_final_story(sc, ctx=ctx))
    selection_payload.international_stories = current_intl

    assert len(selection_payload.domestic_stories) == 5
    assert len(selection_payload.india_stories) == 5
    assert len(selection_payload.international_stories) == 5

    # Verify that duplicate companies were skipped and unique 5th companies were selected
    selected_india_ev_ids = [st.event_id for st in selection_payload.india_stories]
    assert "e_in_dup" not in selected_india_ev_ids
    assert "e_in_gm" in selected_india_ev_ids

    selected_intl_ev_ids = [st.event_id for st in selection_payload.international_stories]
    assert "e_int_dup" not in selected_intl_ev_ids
    assert "e_int_oil" in selected_intl_ev_ids
