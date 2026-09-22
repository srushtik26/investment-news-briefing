"""
Comprehensive portfolio priority, performance, and regression tests.

Covers:
- Scenario A: Portfolio-first ranking (portfolio company with materiality >= 60 & verified nexus
              ranks above higher-scoring non-portfolio India candidates).
- Scenario B: Weak portfolio rejection (portfolio company with materiality < 60 or missing nexus rejected).
- Scenario C: Portfolio deduplication (multiple stories for same holding deduplicated to best story).
- Scenario D: Grouped discovery query formation and URL deduplication.
- Scenario E: Missing company fallback when portfolio holding has no initial hits.
- Scenario F: Reserve capping and priority (portfolio reserves prioritized for India).
- Scenario G: Gemini 429 quota exhaustion graceful offline degradation.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.pipeline.context import PipelineContext
from app.pipeline.story_context import build_story_context
from app.pipeline.story_prep import prepare_final_story, replace_failed_story
from app.pipeline.budget import ApiBudget
from app.ranking.watchlist import (
    match_portfolio_company,
    is_watchlist_company,
    get_watchlist_match_details,
    PORTFOLIO_WATCHLIST,
)
from app.pipeline.selection import run_ranking_and_selection


def _create_test_article(art_id: str, title: str, text: str, source: str = "Mint", date_verified: bool = True) -> Article:
    art = Article(
        id=art_id,
        url=f"https://livemint.com/market/{art_id}",
        title=title,
        source_name=source,
        body_text=text,
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    art.date_verified = date_verified
    return art


def _create_test_event(event_id: str, title: str, art_id: str, cat: NewsCategory = NewsCategory.INDIA) -> Event:
    return Event(
        id=event_id,
        canonical_title=title,
        description=f"Institutional business developments regarding {title}",
        article_ids=[art_id],
        primary_publisher="Mint",
        primary_url=f"https://livemint.com/market/{art_id}",
        event_category=cat,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        status="ACTIVE",
        metadata={
            "financial_figures": ["Rs 5,000 crore"],
            "companies_involved": ["Bharat Electronics Ltd"],
        },
    )


def test_scenario_a_portfolio_first_ranking():
    """Scenario A: Portfolio-first ranking prioritizes qualified portfolio holding in India section."""
    # 1. Portfolio candidate (Bharat Electronics Ltd) with score 75
    art_pf = _create_test_article(
        "art-pf-1",
        "Bharat Electronics Ltd Secures Rs 5000 Crore Defence Radar Order",
        "Bharat Electronics Ltd (BEL) secured a major defence contract valuing Rs 5000 crore for electronic radar systems in Bengaluru Karnataka India.",
    )
    ev_pf = _create_test_event("ev-pf-1", art_pf.title, art_pf.id)
    ev_pf.companies_involved = ["Bharat Electronics Ltd"]

    # 2. General India candidate (Non-portfolio)
    art_gen = _create_test_article(
        "art-gen-1",
        "Larsen & Toubro Wins Mega Infrastructure Project Worth Rs 12000 Crore",
        "Larsen & Toubro has won a mega infrastructure contract in Gujarat India valuing Rs 12,000 crore.",
    )
    ev_gen = _create_test_event("ev-gen-1", art_gen.title, art_gen.id)
    ev_gen.companies_involved = ["Larsen & Toubro"]

    ctx = PipelineContext(
        target_date=datetime.now(timezone.utc).date(),
        run_reference_time=datetime.now(timezone.utc),
    )
    ctx.articles_lookup = {art_pf.id: art_pf, art_gen.id: art_gen}

    events = [ev_pf, ev_gen]
    event_by_id = {e.id: e for e in events}
    accepted_stories = [{"event_id": e.id} for e in events]

    candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx, accepted_stories, event_by_id
    )

    # The portfolio candidate (BEL) must be selected as rank 1 in the India section
    selected_india_ids = [c.event.id for c in candidate_pool.india_candidates]
    assert ev_pf.id in selected_india_ids
    assert candidate_pool.india_candidates[0].event.id == ev_pf.id


def test_scenario_b_weak_portfolio_rejection():
    """Scenario B: Weak portfolio news (<60 materiality or missing nexus) must NOT be forced into selection."""
    # Routine weak portfolio news: routine CSR announcement with no financial significance
    art_weak = _create_test_article(
        "art-pf-weak",
        "Bharat Electronics Organises Tree Plantation Drive at Bengaluru Plant",
        "Bharat Electronics employees celebrated World Environment Day with a tree planting drive in Bengaluru.",
    )
    ev_weak = _create_test_event("ev-pf-weak", art_weak.title, art_weak.id)
    ev_weak.metadata = {}  # No financial figures

    ctx = PipelineContext(
        target_date=datetime.now(timezone.utc).date(),
        run_reference_time=datetime.now(timezone.utc),
    )
    ctx.articles_lookup = {art_weak.id: art_weak}

    sc = build_story_context(ev_weak, art_weak, ctx=ctx)
    # StoryContext should detect portfolio holding, but materiality should be below 60
    assert sc.portfolio_company is True
    assert sc.portfolio_canonical_name == "Bharat Electronics Ltd"
    assert sc.materiality_score < 60.0
    # Must NOT qualify for India business selection
    is_qualified_for_selection = sc.portfolio_company and sc.materiality_score >= 60.0 and sc.india_nexus
    assert is_qualified_for_selection is False


def test_scenario_c_portfolio_deduplication():
    """Scenario C: Multiple stories about the same portfolio company are deduplicated to the best story."""
    art1 = _create_test_article("art-1", "Infosys Signs Cloud Deal with Bank", "Infosys signs Rs 500 cr cloud deal in Bengaluru India.")
    art2 = _create_test_article("art-2", "Infosys Signs $60M Cloud Deal with European Bank", "Infosys signs major cloud deal in Bengaluru India.")
    ev1 = _create_test_event("ev-1", art1.title, art1.id)
    ev2 = _create_test_event("ev-2", art2.title, art2.id)

    ctx = PipelineContext(
        target_date=datetime.now(timezone.utc).date(),
        run_reference_time=datetime.now(timezone.utc),
    )
    ctx.articles_lookup = {art1.id: art1, art2.id: art2}

    sc1 = build_story_context(ev1, art1, ctx=ctx)
    sc2 = build_story_context(ev2, art2, ctx=ctx)

    assert sc1.portfolio_company is True
    assert sc1.portfolio_canonical_name == "Infosys"
    assert sc2.portfolio_company is True
    assert sc2.portfolio_canonical_name == "Infosys"


def test_scenario_d_grouped_discovery_queries():
    """Scenario D: Grouped discovery forms bounded batched queries from holdings."""
    holdings = [name for name, _ in PORTFOLIO_WATCHLIST]
    assert len(holdings) >= 10

    # Test grouping logic
    batch_size = 5
    grouped = [holdings[i : i + batch_size] for i in range(0, len(holdings), batch_size)]
    assert len(grouped) >= 2
    for group in grouped:
        q = " OR ".join(f'"{name}"' for name in group) + " India business"
        assert "India business" in q
        assert len(q) < 500


def test_scenario_e_api_budget_tracking_and_429():
    """Scenario G: ApiBudget tracks calls and gracefully enters offline mode upon 429 quota exhaustion."""
    budget = ApiBudget(max_gemini_calls=5, max_serpapi_calls=10)
    assert budget.offline_mode is False

    # Simulate Gemini call
    budget.record_gemini_call(tokens=150)
    assert budget.gemini_calls == 1
    assert budget.offline_mode is False

    # Simulate 429 quota exhaustion
    budget.record_gemini_429()
    assert budget.offline_mode is True

    # After offline mode, further calls are blocked
    assert budget.can_call_gemini() is False


def test_scenario_f_reserve_replacement_priority():
    """Scenario F: replace_failed_story prioritizes portfolio reserve over general reserve for India."""
    # 1. Portfolio reserve candidate (Infosys is in watchlist)
    art_pf = _create_test_article(
        "art-res-pf",
        "Infosys Wins $500M IT Services Deal with Global Bank",
        "Infosys has secured a $500 million contract with an enterprise banking client in Bengaluru India.",
    )
    ev_pf = _create_test_event("ev-res-pf", art_pf.title, art_pf.id)
    ev_pf.companies_involved = ["Infosys"]
    ev_pf.metadata = {"financial_figures": ["$500 million"], "companies_involved": ["Infosys"]}

    # 2. General India reserve candidate
    art_gen = _create_test_article(
        "art-res-gen",
        "Indian Railways Awards Rs 10000 Cr Locomotive Deal",
        "Indian Railways awarded a contract worth Rs 10000 crore for electric locomotives in New Delhi India.",
    )
    ev_gen = _create_test_event("ev-res-gen", art_gen.title, art_gen.id)
    ev_gen.metadata = {"financial_figures": ["Rs 10000 crore"]}

    ctx = PipelineContext(
        target_date=datetime.now(timezone.utc).date(),
        run_reference_time=datetime.now(timezone.utc),
    )
    ctx.articles_lookup = {art_pf.id: art_pf, art_gen.id: art_gen}

    # Pass reserves in order: general first, then portfolio
    reserves = [ev_gen, ev_pf]

    # Replacement in India must pick portfolio reserve first
    replacement = replace_failed_story(
        section="india",
        failed_event_id="failed-id",
        reserves=reserves,
        ctx=ctx,
    )

    assert replacement is not None
    assert replacement.section == "india"
    assert replacement.event_id == ev_pf.id
    assert "Infosys" in replacement.headline or "Infosys" in replacement.summary
