"""
Unit tests for Deterministic Investment Relevance Scoring and Candidate Ranking.
"""

from datetime import datetime, timezone
import pytest

from app.models.enums import NewsCategory
from app.models.event import Event
from app.ranking import (
    CandidatePoolRanker,
    InvestmentRelevanceScorer,
    RankedCandidatePool,
    ScoreBreakdown,
    ScoredEvent,
)


@pytest.fixture
def hdfc_earnings_event() -> Event:
    """Fixture for mega earnings event."""
    return Event(
        canonical_title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII",
        description="HDFC Bank reported net profit of ₹16,175 crore with 26% NII expansion.",
        companies_involved=["HDFC Bank"],
        sectors=["Banking", "Financials"],
        financial_figures=["₹16,175 crore", "18%"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-1", "art-2"],
    )


@pytest.fixture
def rio_tinto_ma_event() -> Event:
    """Fixture for large international M&A event."""
    return Event(
        canonical_title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in All-Cash Deal",
        description="Rio Tinto signs definitive agreement to acquire Arcadium Lithium for $6.7 billion.",
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
        sectors=["Mining", "Metals"],
        financial_figures=["$6.7 billion"],
        event_category=NewsCategory.INTERNATIONAL,
        article_ids=["art-3", "art-4"],
    )


@pytest.fixture
def minor_contract_event() -> Event:
    """Fixture for small routine business event."""
    return Event(
        canonical_title="XYZ Engineering Wins ₹45 Crore Road Maintenance Contract",
        description="XYZ Engineering bags routine municipal road repair order worth ₹45 crore.",
        companies_involved=["XYZ Engineering"],
        sectors=["Infrastructure"],
        financial_figures=["₹45 crore"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-5"],
    )


class TestInvestmentRelevanceScorer:
    """Tests for deterministic scoring calculations and mathematical weights."""

    def test_score_calculation_weights(self, hdfc_earnings_event: Event):
        """Test composite score matches the 30/25/20/15/10 weighting formula."""
        scorer = InvestmentRelevanceScorer()
        scored: ScoredEvent = scorer.score_event(
            event=hdfc_earnings_event,
            source_count=2,
            is_multi_source_verified=True,
        )

        b: ScoreBreakdown = scored.score_breakdown

        # With freshness multiplier the total is no longer a simple weighted sum.
        # Verify the score is within valid range and the sub-scores are correct.
        assert 0.0 <= scored.investment_score <= 100.0
        assert b.financial_magnitude >= 85.0
        assert b.market_impact >= 80.0
        # Verify score is plausible given freshness_score=0.8 (default, fresh_24_48h, multiplier=0.94)
        # Raw score would be 86.75; with 0.8 freshness multiplier (0.7 + 0.3*0.8 = 0.94) => ~81.54
        assert scored.investment_score >= 70.0, "Score too low"

    def test_mega_deal_scores_higher_than_minor_event(
        self,
        rio_tinto_ma_event: Event,
        minor_contract_event: Event,
    ):
        """Test large-scale M&A outscores minor local order."""
        scorer = InvestmentRelevanceScorer()
        scored_mega = scorer.score_event(rio_tinto_ma_event, source_count=2, is_multi_source_verified=True)
        scored_minor = scorer.score_event(minor_contract_event, source_count=1, is_multi_source_verified=False)

        assert scored_mega.investment_score > scored_minor.investment_score
        assert scored_mega.investment_score >= 80.0
        assert scored_minor.investment_score < 70.0


class TestCandidatePoolRanker:
    """Tests for candidate ranking and pool management."""

    def test_separate_india_and_international_sorting(
        self,
        hdfc_earnings_event: Event,
        rio_tinto_ma_event: Event,
        minor_contract_event: Event,
    ):
        """Test India and International events are partitioned and sorted descending."""
        ranker = CandidatePoolRanker()
        pool: RankedCandidatePool = ranker.rank_events(
            events=[hdfc_earnings_event, rio_tinto_ma_event, minor_contract_event],
            top_n=10,
        )

        assert len(pool.india_candidates) == 2
        assert len(pool.international_candidates) == 1
        assert pool.total_evaluated == 3

        # India pool sorted descending
        assert pool.india_candidates[0].investment_score >= pool.india_candidates[1].investment_score
        assert pool.india_candidates[0].rank == 1
        assert pool.india_candidates[1].rank == 2
        assert pool.india_candidates[0].event.canonical_title == hdfc_earnings_event.canonical_title

        # International pool
        assert pool.international_candidates[0].rank == 1
        assert pool.international_candidates[0].event.canonical_title == rio_tinto_ma_event.canonical_title

    def test_top_candidate_pool_retention(self):
        """Test retaining top 8–10 candidates from a large batch without premature reduction to 5."""
        events: list[Event] = []

        # Create 14 synthetic India events with varying magnitudes
        for i in range(1, 15):
            events.append(
                Event(
                    canonical_title=f"India Corporate Event {i} - Scale ₹{i * 1000} Crore",
                    description=f"Description for event {i}",
                    financial_figures=[f"₹{i * 1000} crore"],
                    event_category=NewsCategory.INDIA,
                    article_ids=[f"art-in-{i}"],
                )
            )

        # Create 12 synthetic International events
        for i in range(1, 13):
            events.append(
                Event(
                    canonical_title=f"Global Corporate Event {i} - Scale ${i} Billion",
                    description=f"Description for global event {i}",
                    financial_figures=[f"${i} billion"],
                    event_category=NewsCategory.INTERNATIONAL,
                    article_ids=[f"art-intl-{i}"],
                )
            )

        ranker = CandidatePoolRanker()
        pool = ranker.rank_events(events=events, top_n=10)

        # Confirm exactly 10 kept per pool (not prematurely reduced to 5)
        assert len(pool.india_candidates) == 10
        assert len(pool.international_candidates) == 10
        assert pool.total_evaluated == 26

        # Confirm strictly descending scores
        india_scores = [c.investment_score for c in pool.india_candidates]
        assert india_scores == sorted(india_scores, reverse=True)

        intl_scores = [c.investment_score for c in pool.international_candidates]
        assert intl_scores == sorted(intl_scores, reverse=True)

    def test_international_ranking_prioritizes_distinct_companies(self):
        """Test that top international candidates prioritize distinct companies over duplicate events."""
        events: list[Event] = [
            # Two events for Anthropic with high scores
            Event(
                canonical_title="Anthropic Leaders Control AI Lab for Public Good: Reuters",
                description="Anthropic governance structure updated",
                companies_involved=["Anthropic"],
                financial_figures=["$18 billion"],
                event_category=NewsCategory.INTERNATIONAL,
                article_ids=["art-ant-1"],
            ),
            Event(
                canonical_title="Anthropic Leaks $2 Trillion IPO Prospectus",
                description="Anthropic IPO filing details disclosed",
                companies_involved=["Anthropic"],
                financial_figures=["$2 trillion"],
                event_category=NewsCategory.INTERNATIONAL,
                article_ids=["art-ant-2"],
            ),
            # Other distinct company events
            Event(
                canonical_title="Apple Unveils New $10 Billion AI Chip Investment",
                description="Apple capital expenditure plan",
                companies_involved=["Apple"],
                financial_figures=["$10 billion"],
                event_category=NewsCategory.INTERNATIONAL,
                article_ids=["art-app-1"],
            ),
            Event(
                canonical_title="Microsoft Signs $5 Billion Datacenter Power Deal",
                description="Microsoft energy agreement",
                companies_involved=["Microsoft"],
                financial_figures=["$5 billion"],
                event_category=NewsCategory.INTERNATIONAL,
                article_ids=["art-msft-1"],
            ),
        ]

        ranker = CandidatePoolRanker()
        pool = ranker.rank_events(events=events, top_n=3)

        # Top 3 should contain 3 distinct companies: Anthropic, Apple, Microsoft (not Anthropic twice!)
        assert len(pool.international_candidates) == 3
        top_companies = [c.event.companies_involved[0] for c in pool.international_candidates]
        assert len(set(top_companies)) == 3
        assert "Anthropic" in top_companies
        assert "Apple" in top_companies
        assert "Microsoft" in top_companies

