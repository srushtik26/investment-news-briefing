"""
Regression and consistency tests for India section reserve depth and land acquisition signals.

Verifies:
1. Land-acquisition regex tightening:
   - "M3M acquires Noida land for Rs 2,000 crore" -> land acquisition
   - "Ashiana Housing buys Gurugram land parcel" -> land acquisition
   - "government land policy debate" -> NOT land acquisition
   - "wetland conservation project" -> NOT land acquisition
   - "agricultural land reform discussion" -> NOT corporate acquisition unless acquisition verb exists
2. India reserve depth preservation after materiality >= 60 gate:
   - 6 valid India candidates (5 selected + 1 reserve, all with materiality >= 60.0)
   - One selected story fails final consistency (foreign subject, lacks Indian business nexus)
   - Reserve replaces it
   - Final India count strictly remains 5
"""

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection
from app.verification.materiality import evaluate_investment_materiality
from app.ai.headline_synthesis import (
    is_land_acquisition_signal,
    synthesize_investment_headline,
    LAND_ACQUISITION_PATTERN,
)


# =========================================================================
# 1. LAND-ACQUISITION REGEX TIGHTENING REGRESSIONS
# =========================================================================

def test_land_acquisition_signal_positive_m3m():
    """M3M acquires Noida land for Rs 2,000 crore -> explicit land acquisition."""
    text = "M3M acquires Noida land for Rs 2,000 crore"
    assert is_land_acquisition_signal(text) is True


def test_land_acquisition_signal_positive_ashiana():
    """Ashiana Housing buys Gurugram land parcel -> explicit land acquisition."""
    text = "Ashiana Housing buys Gurugram land parcel"
    assert is_land_acquisition_signal(text) is True


def test_land_acquisition_signal_negative_government_policy():
    """government land policy debate -> NOT land acquisition."""
    text = "government land policy debate"
    assert is_land_acquisition_signal(text) is False


def test_land_acquisition_signal_negative_wetland():
    """wetland conservation project -> NOT land acquisition."""
    text = "wetland conservation project"
    assert is_land_acquisition_signal(text) is False


def test_land_acquisition_signal_negative_agricultural_reform():
    """agricultural land reform discussion -> NOT corporate acquisition unless acquisition verb exists."""
    text = "agricultural land reform discussion"
    assert is_land_acquisition_signal(text) is False


def test_land_acquisition_signal_with_acquisition_verb_and_intervening_words():
    """Corporate acquisition verb with up to a few words before land is recognized."""
    # With acquisition verb: acquires / buys / purchases
    assert is_land_acquisition_signal("M3M acquires 73-acre Noida land") is True
    assert is_land_acquisition_signal("Godrej buys prime commercial land in Bengaluru") is True
    assert is_land_acquisition_signal("DLF purchases agricultural land for expansion") is True


def test_headline_synthesis_land_vs_non_land_routing():
    """Confirm headline synthesizer routes land acquisitions to Archetype 3 and avoids false positives."""
    now = datetime.now(timezone.utc)
    art_m3m = Article(
        id="art_m3m",
        title="M3M acquires Noida land for Rs 2,000 crore",
        content_text="M3M India acquires prime commercial land in Noida for Rs 2,000 crore to develop mixed-use project.",
        url="https://example.com/m3m",
        source_name="Mint",
        published_at=now,
    )
    ev_m3m = Event(
        id="ev_m3m",
        canonical_title=art_m3m.title,
        description=art_m3m.content_text,
        article_ids=[art_m3m.id],
        companies_involved=["M3M India"],
        financial_figures=["Rs 2,000 crore"],
        event_category=NewsCategory.INDIA,
    )
    hl_m3m = synthesize_investment_headline(art_m3m.title, event=ev_m3m, article=art_m3m)
    assert "Acquires Strategic Land Parcel" in hl_m3m
    assert "M3M" in hl_m3m

    # Non-acquisition policy debate should NOT generate land parcel headline
    art_policy = Article(
        id="art_pol",
        title="Parliament begins government land policy debate",
        content_text="Ministers debate national policy proposals regarding government land leasing norms.",
        url="https://example.com/pol",
        source_name="The Hindu",
        published_at=now,
    )
    ev_pol = Event(
        id="ev_pol",
        canonical_title=art_policy.title,
        description=art_policy.content_text,
        article_ids=[art_policy.id],
        companies_involved=[],
        financial_figures=[],
        event_category=NewsCategory.INDIA,
    )
    hl_pol = synthesize_investment_headline(art_policy.title, event=ev_pol, article=art_policy)
    assert "Acquires Strategic Land Parcel" not in hl_pol


# =========================================================================
# 2. INDIA RESERVE DEPTH PRESERVATION AFTER MATERIALITY GATE
# =========================================================================

def _make_candidate(
    ev_id: str,
    title: str,
    body: str,
    company: str,
    figures: list,
    now: datetime,
    source: str = "Mint",
    category: NewsCategory = NewsCategory.INDIA,
    age_hours: float = 2.0,
):
    art = Article(
        id=f"art_{ev_id}",
        title=title,
        content_text=body,
        url=f"https://example.com/{ev_id}",
        source_name=source,
        published_at=now - timedelta(hours=age_hours),
        category=category,
        feed_section=category.value.lower(),
        is_verified_url=True,
        metadata={"freshness_score": 0.95},
    )
    ev = Event(
        id=ev_id,
        canonical_title=title,
        description=body[:200],
        article_ids=[art.id],
        event_category=category,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=95.0,
        companies_involved=[company],
        financial_figures=figures,
        primary_publisher=source,
        primary_url=art.url,
        first_published_at=now - timedelta(hours=age_hours),
        last_published_at=now - timedelta(hours=age_hours),
        created_at=now - timedelta(hours=age_hours),
    )
    return art, ev


def test_india_reserve_replaces_story_failing_final_consistency():
    """
    Test scenario:
    - 6 valid India candidates (5 top-selected + 1 reserve).
    - All 6 have materiality score >= 60.0.
    - One selected story fails final consistency (has foreign subject, lacks Indian business nexus).
    - Expected: Reserve candidate replaces the failed story.
    - Final India count strictly remains 5.
    """
    now = datetime(2026, 9, 21, 6, 0, 0, tzinfo=timezone.utc)
    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )

    # 1. Tata Motors (Valid India capex)
    art1, ev1 = _make_candidate(
        "ev_tata",
        "Tata Motors approves Rs 4,000 crore capital expenditure for Tamil Nadu EV plant",
        "Tata Motors will invest Rs 4,000 crore to construct a new electric vehicle production line in Tamil Nadu.",
        "Tata Motors",
        ["Rs 4,000 crore"],
        now,
        source="Mint",
    )

    # 2. Foreign subject lacking Indian business nexus (High financial magnitude, but fails consistency)
    art2, ev2 = _make_candidate(
        "ev_foreign",
        "Tokyo Electronics expands semiconductor manufacturing in Japan with $1 billion plant",
        "Tokyo Electronics announced a $1 billion capital expenditure program to expand advanced chipmaking facilities in Japan.",
        "Tokyo Electronics",
        ["$1 billion"],
        now,
        source="Reuters",
    )

    # 3. Reliance Industries (Valid India green energy capex)
    art3, ev3 = _make_candidate(
        "ev_ril",
        "Reliance Industries commits Rs 7,500 crore to Jamnagar solar gigafactory",
        "Reliance Industries signed contracts to invest Rs 7,500 crore in its integrated clean energy manufacturing complex.",
        "Reliance Industries",
        ["Rs 7,500 crore"],
        now,
        source="Business Standard",
    )

    # 4. Larsen & Toubro (Valid India infrastructure contract)
    art4, ev4 = _make_candidate(
        "ev_lt",
        "Larsen & Toubro bags Rs 3,200 crore domestic infrastructure order from Indian Railways",
        "Larsen & Toubro secured a major domestic engineering and construction contract valued at Rs 3,200 crore.",
        "Larsen & Toubro",
        ["Rs 3,200 crore"],
        now,
        source="Economic Times",
    )

    # 5. Bharti Airtel (Valid India 5G network capex)
    art5, ev5 = _make_candidate(
        "ev_airtel",
        "Bharti Airtel boards approves Rs 5,000 crore network expansion capex across India",
        "Bharti Airtel announced board approval for Rs 5,000 crore capital expenditure to enhance 5G coverage across India.",
        "Bharti Airtel",
        ["Rs 5,000 crore"],
        now,
        source="Financial Express",
    )

    # 6. Reserve Candidate: Mahindra & Mahindra (Valid India EV expansion)
    art6, ev6 = _make_candidate(
        "ev_mm_reserve",
        "Mahindra & Mahindra invests Rs 3,000 crore in Maharashtra electric SUV plant",
        "Mahindra & Mahindra will deploy Rs 3,000 crore to scale up electric passenger vehicle assembly capacity in Pune.",
        "Mahindra & Mahindra",
        ["Rs 3,000 crore"],
        now,
        source="NDTV Profit",
    )

    articles = {a.id: a for a in [art1, art2, art3, art4, art5, art6]}
    events = {e.id: e for e in [ev1, ev2, ev3, ev4, ev5, ev6]}
    ctx.articles_lookup = articles

    # Confirm all 6 pass the materiality >= 60.0 threshold
    for ev in events.values():
        art = articles[ev.article_ids[0]]
        is_mat, mat_score, _ = evaluate_investment_materiality(ev, art, ctx=ctx)
        assert is_mat is True, f"Expected {ev.canonical_title} to pass materiality gate"
        assert mat_score >= 60.0, f"Expected {ev.canonical_title} score >= 60.0, got {mat_score}"

    # Setup ScoredEvents in candidate pool
    scored_candidates = []
    for idx, ev in enumerate([ev1, ev2, ev3, ev4, ev5, ev6]):
        scored = ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=85.0,
                market_impact=80.0,
                investor_relevance=80.0,
                corporate_significance=80.0,
                source_quality=80.0,
                total_score=82.0 - idx,
            ),
            investment_score=82.0 - idx,
            rank=idx + 1,
        )
        scored_candidates.append(scored)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=scored_candidates,
        international_candidates=[],
    )
    ctx.ranker = mock_ranker
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct events")

    accepted_stories = [{"event_id": eid, "category": "india"} for eid in events]

    # Execute Stage 7 selection
    pool, dom_pool, ind_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx,
        accepted_stories=accepted_stories,
        event_by_id=events,
    )

    # Assertions
    selected_ids = [s.event.id for s in ind_pool]

    # 1. Final India count must strictly remain 5
    assert len(ind_pool) == 5, f"Expected exactly 5 India stories, got {len(ind_pool)}"
    assert len(pool.india_candidates) == 5

    # 2. Failed consistency story (ev_foreign) must be excluded
    assert "ev_foreign" not in selected_ids, "Failed consistency story must not be selected in India pool"

    # 3. Reserve story (ev_mm_reserve) must have replaced the failed story
    assert "ev_mm_reserve" in selected_ids, "Reserve story must replace failed story in India pool"

    # 4. Valid stories 1, 3, 4, 5 must all be present
    assert "ev_tata" in selected_ids
    assert "ev_ril" in selected_ids
    assert "ev_lt" in selected_ids
    assert "ev_airtel" in selected_ids

    # 5. Diagnostic logs must verify rejection and backfill
    combined_logs = "\n".join(logs)
    assert "[INDIA_FINAL_ELIGIBILITY_REJECT]" in combined_logs
    assert "Tokyo Electronics" in combined_logs
    assert "BACKFILL:" in combined_logs
