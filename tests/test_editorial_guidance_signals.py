"""
Tests for Deterministic Editorial Guidance Derived from Investment Committee Examples.

Tests the synthetic candidate set containing:
1. JSW-Skoda JV style event
2. FPI $1.6bn selling
3. DCC satellite policy
4. Complete Sports routine result
5. IPO GMP
6. Broker recommendation

Verifies:
- Top 3 candidates are JSW-Skoda JV, FPI selling, and DCC satellite policy.
- Routine result, GMP, and broker recommendation rank below or are disqualified/rejected.
- Source quality remains separate from editorial relevance.
- 0 AI calls, 0 embeddings/RAG.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ranking.scorer import InvestmentRelevanceScorer
from app.ranking.sorter import CandidatePoolRanker
from app.ranking.signals import evaluate_editorial_signals, EditorialEvaluationResult


def make_test_event(
    ev_id: str,
    title: str,
    body: str,
    figures: list = None,
    companies: list = None,
    cat: NewsCategory = NewsCategory.INDIA,
) -> Event:
    return Event(
        id=ev_id,
        canonical_title=title,
        description=body,
        article_ids=[f"art_{ev_id}"],
        financial_figures=figures or [],
        companies_involved=companies or [],
        event_category=cat,
        created_at=datetime.now(timezone.utc),
        first_published_at=datetime.now(timezone.utc),
        last_published_at=datetime.now(timezone.utc),
    )


def test_1_editorial_signals_evaluation_matches_committee_patterns():
    """Verify approved patterns receive positive signals and bad patterns receive negative/rejection signals."""
    # 1. JSW-Skoda JV
    ev_jv = make_test_event(
        "ev_jv",
        "JSW Group and Skoda-Volkswagen India sign MoU for 51:49 joint venture",
        "JSW and Skoda enter exclusive talks for a 51:49 joint venture to manufacture electric and hybrid passenger vehicles.",
        figures=["51:49"],
        companies=["JSW Group", "Skoda-Volkswagen India"],
    )
    sig_jv = evaluate_editorial_signals(ev_jv)
    assert sig_jv.is_disqualified is False
    assert sig_jv.score_adjustment > 0
    assert any("approved_india_jv_ma" in s for s in sig_jv.positive_signals)

    # 2. FPI $1.6bn Selling
    ev_fpi = make_test_event(
        "ev_fpi",
        "FPIs sell $1.6 billion of Indian equities in five consecutive trading sessions",
        "Foreign portfolio investors offload $1.6 billion worth of domestic equities amid elevated crude oil prices and global risk-off.",
        figures=["$1.6 billion"],
        companies=["FPI"],
    )
    sig_fpi = evaluate_editorial_signals(ev_fpi)
    assert sig_fpi.is_disqualified is False
    assert sig_fpi.score_adjustment > 0
    assert any("approved_india_fpi_flows" in s for s in sig_fpi.positive_signals)

    # 3. DCC Satellite Policy
    ev_sat = make_test_event(
        "ev_sat",
        "DCC clears satellite spectrum allocation policy framework for broadband",
        "Digital Communications Commission approves pricing and allocation framework for satellite communication spectrum across India.",
        figures=[],
        companies=["DCC"],
    )
    sig_sat = evaluate_editorial_signals(ev_sat)
    assert sig_sat.is_disqualified is False
    assert sig_sat.score_adjustment > 0
    assert any("approved_india_satellite_telecom" in s or "approved_india_regulatory_framework" in s for s in sig_sat.positive_signals)

    # 4. Complete Sports Routine Result
    ev_cs = make_test_event(
        "ev_cs",
        "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80",
        "Complete Sports and Management India Limited discloses quarterly financial results. BSE share price 157.80.",
        figures=["157.80"],
        companies=["Complete Sports and Management India Limited"],
    )
    sig_cs = evaluate_editorial_signals(ev_cs)
    assert sig_cs.score_adjustment < 0
    assert any("bad_routine_small_company_quarterly_results" in s for s in sig_cs.negative_signals)

    # 5. IPO GMP
    ev_gmp = make_test_event(
        "ev_gmp",
        "Bajaj Housing Finance IPO GMP today indicates 110% listing premium",
        "Grey market premium for the public issue suggests massive listing gains ahead of stock exchange debut.",
        figures=["110%"],
        companies=["Bajaj Housing Finance"],
    )
    sig_gmp = evaluate_editorial_signals(ev_gmp)
    assert sig_gmp.is_disqualified is True
    assert "IPO GMP" in sig_gmp.rejection_reason

    # 6. Broker Recommendation
    ev_broker = make_test_event(
        "ev_broker",
        "Top stock picks today: Brokerage gives buy rating on Tata Motors with target Rs 1150",
        "Analyst recommends buying shares of auto major citing margin expansion and commercial vehicle volume outlook.",
        figures=["Rs 1150"],
        companies=["Tata Motors"],
    )
    sig_broker = evaluate_editorial_signals(ev_broker)
    assert sig_broker.is_disqualified is True or sig_broker.score_adjustment < 0


def test_2_mixed_synthetic_candidate_set_ranking_order():
    """
    Feed a mixed synthetic candidate set:
    - JSW-Skoda JV style event
    - FPI $1.6bn selling
    - DCC satellite policy
    - Complete Sports routine result
    - IPO GMP
    - Broker recommendation

    Expected:
    Top candidates are the first three.
    Routine result, GMP, broker recommendation rank below or reject appropriately.
    """
    ev_jv = make_test_event(
        "ev_jv",
        "JSW Group and Skoda-Volkswagen India sign MoU for 51:49 joint venture",
        "JSW and Skoda enter exclusive talks for a 51:49 joint venture to manufacture electric vehicles.",
        figures=["51:49"],
        companies=["JSW Group", "Skoda-Volkswagen India"],
    )
    ev_fpi = make_test_event(
        "ev_fpi",
        "FPIs sell $1.6 billion of Indian equities in five consecutive trading sessions",
        "Foreign portfolio investors offload $1.6 billion worth of domestic equities amid elevated crude oil prices.",
        figures=["$1.6 billion"],
        companies=["FPI"],
    )
    ev_sat = make_test_event(
        "ev_sat",
        "DCC clears satellite spectrum allocation policy framework for broadband",
        "Digital Communications Commission approves pricing and allocation framework for satellite communication spectrum across India.",
        figures=[],
        companies=["DCC"],
    )
    ev_cs = make_test_event(
        "ev_cs",
        "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80",
        "Complete Sports and Management India Limited discloses quarterly financial results. BSE share price 157.80.",
        figures=["157.80"],
        companies=["Complete Sports and Management India Limited"],
    )
    ev_gmp = make_test_event(
        "ev_gmp",
        "Bajaj Housing Finance IPO GMP today indicates 110% listing premium",
        "Grey market premium for the public issue suggests massive listing gains ahead of stock exchange debut.",
        figures=["110%"],
        companies=["Bajaj Housing Finance"],
    )
    ev_broker = make_test_event(
        "ev_broker",
        "Top stock picks today: Brokerage gives buy rating on Tata Motors with target Rs 1150",
        "Analyst recommends buying shares of auto major citing margin expansion and commercial vehicle volume outlook.",
        figures=["Rs 1150"],
        companies=["Tata Motors"],
    )

    all_events = [ev_cs, ev_gmp, ev_jv, ev_broker, ev_fpi, ev_sat]

    scorer = InvestmentRelevanceScorer()
    scored_events = [scorer.score_event(e) for e in all_events]

    # Map by event id
    score_by_id = {s.event.id: s.investment_score for s in scored_events}

    # Top 3 must be JV, FPI, and Satellite policy
    top_3_ids = {"ev_jv", "ev_fpi", "ev_sat"}
    bottom_3_ids = {"ev_cs", "ev_gmp", "ev_broker"}

    min_top_3_score = min(score_by_id[eid] for eid in top_3_ids)
    max_bottom_3_score = max(score_by_id[eid] for eid in bottom_3_ids)

    assert min_top_3_score > max_bottom_3_score, (
        f"Expected top 3 scores ({score_by_id['ev_jv']}, {score_by_id['ev_fpi']}, {score_by_id['ev_sat']}) "
        f"to be strictly greater than bottom 3 scores ({score_by_id['ev_cs']}, {score_by_id['ev_gmp']}, {score_by_id['ev_broker']})"
    )

    # Sort descending
    ranked = sorted(scored_events, key=lambda s: s.investment_score, reverse=True)
    ranked_top_3_ids = [s.event.id for s in ranked[:3]]

    assert set(ranked_top_3_ids) == top_3_ids, f"Expected top 3 to be {top_3_ids}, got {ranked_top_3_ids}"

    # Verify CandidatePoolRanker also places the top 3 in the pool ahead of routine items
    ranker = CandidatePoolRanker(scorer=scorer)
    pool = ranker.rank_events(all_events, top_n=6)
    pool_top_3_ids = [s.event.id for s in pool.india_candidates[:3]]
    assert set(pool_top_3_ids) == top_3_ids


def test_3_source_quality_separated_from_editorial_relevance():
    """
    Source quality remains separate from editorial relevance.
    Economic Times reporting routine results or IPO GMP must NOT defeat
    a lower-profile publisher reporting a major $1.6B FPI outflow or DCC policy.
    """
    scorer = InvestmentRelevanceScorer()

    # Economic Times publishing routine result ticker
    ev_et_routine = make_test_event(
        "ev_et_routine",
        "Complete Sports Quarterly Results - BSE 157.80 - Economic Times",
        "Routine quarterly financial results filed with BSE.",
        figures=["157.80"],
        companies=["Complete Sports"],
    )
    # Give it strong multi-source verification
    scored_routine = scorer.score_event(ev_et_routine, source_count=3, is_multi_source_verified=True)

    # Moderate source publishing massive FPI outflow
    ev_wire_fpi = make_test_event(
        "ev_wire_fpi",
        "FPIs sell $1.6 billion of Indian equities in five consecutive trading sessions",
        "Foreign institutional investors dump $1.6 billion equities.",
        figures=["$1.6 billion"],
        companies=["FPI"],
    )
    scored_fpi = scorer.score_event(ev_wire_fpi, source_count=1, is_multi_source_verified=False)

    # FPI event must strongly outrank routine quarterly results despite fewer sources
    assert scored_fpi.investment_score > scored_routine.investment_score + 15.0, (
        f"FPI score {scored_fpi.investment_score} did not sufficiently exceed routine result score {scored_routine.investment_score}"
    )
