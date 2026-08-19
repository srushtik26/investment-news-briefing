"""
Unit tests for Gemini Final Editorial Selection and Synthesis Engine.
"""

from datetime import datetime, timezone
import json
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event
from app.ranking.models import RankedCandidatePool, ScoreBreakdown, ScoredEvent
from app.ai import (
    BriefingEditorialPayload,
    EditorialResult,
    EditorialStorySelection,
    GeminiEditorialEngine,
)


@pytest.fixture
def sample_candidate_pool_and_articles():
    """Fixture generating a pool of 6 India and 6 International candidate events with articles."""
    articles_map: dict[str, Article] = {}
    india_candidates: list[ScoredEvent] = []
    intl_candidates: list[ScoredEvent] = []

    # 6 India Events (Companies 1 to 6)
    for i in range(1, 7):
        art_id = f"art-in-{i}"
        art_url = f"https://www.business-standard.com/india-story-{i}.html"
        company = f"IndiaCorp{i}"

        art = Article(
            id=art_id,
            title=f"{company} Q1 Net Profit Jumps {10 + i}% to ₹{i * 1000} Crore",
            url=art_url,
            source_name="Business Standard",
            published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
            content_text=f"{company} reported quarterly profit growth of {10 + i}%.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        articles_map[art_id] = art

        event = Event(
            id=f"evt-in-{i}",
            canonical_title=art.title,
            description=art.content_text,
            companies_involved=[company],
            financial_figures=[f"₹{i * 1000} crore", f"{10 + i}%"],
            event_category=NewsCategory.INDIA,
            article_ids=[art_id],
        )

        breakdown = ScoreBreakdown(
            financial_magnitude=80.0,
            market_impact=80.0,
            investor_relevance=80.0,
            corporate_significance=80.0,
            source_quality=85.0,
            total_score=80.5,
        )
        india_candidates.append(ScoredEvent(event=event, score_breakdown=breakdown, investment_score=80.5, rank=i))

    # 6 International Events (Companies 1 to 6)
    for i in range(1, 7):
        art_id = f"art-intl-{i}"
        art_url = f"https://www.reuters.com/global-story-{i}.html"
        company = f"GlobalCorp{i}"

        art = Article(
            id=art_id,
            title=f"{company} Agrees ${i}.5 Billion Acquisition Deal",
            url=art_url,
            source_name="Reuters",
            published_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
            content_text=f"{company} signed definitive acquisition agreement.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        articles_map[art_id] = art

        event = Event(
            id=f"evt-intl-{i}",
            canonical_title=art.title,
            description=art.content_text,
            companies_involved=[company],
            financial_figures=[f"${i}.5 billion"],
            event_category=NewsCategory.INTERNATIONAL,
            article_ids=[art_id],
        )

        breakdown = ScoreBreakdown(
            financial_magnitude=85.0,
            market_impact=85.0,
            investor_relevance=85.0,
            corporate_significance=80.0,
            source_quality=85.0,
            total_score=84.5,
        )
        intl_candidates.append(ScoredEvent(event=event, score_breakdown=breakdown, investment_score=84.5, rank=i))

    pool = RankedCandidatePool(
        india_candidates=india_candidates,
        international_candidates=intl_candidates,
        total_evaluated=12,
    )
    return pool, articles_map


class TestGeminiEditorialEngine:
    """Tests for GeminiEditorialEngine service."""

    def test_editorial_selection_success(self, sample_candidate_pool_and_articles):
        """Test successful selection of 5 India and 5 International stories with exact URLs."""
        pool, articles_map = sample_candidate_pool_and_articles

        # Construct valid mock response picking 5 India and 5 International
        mock_response = json.dumps({
            "india_stories": [
                {
                    "section": "india",
                    "event_id": f"evt-in-{i}",
                    "headline": f"IndiaCorp{i} Q1 Profit Surges {10 + i}% to ₹{i * 1000} Cr",
                    "source": "Business Standard",
                    "url": f"https://www.business-standard.com/india-story-{i}.html",
                }
                for i in range(1, 6)
            ],
            "international_stories": [
                {
                    "section": "international",
                    "event_id": f"evt-intl-{i}",
                    "headline": f"GlobalCorp{i} Inks ${i}.5B Acquisition Agreement",
                    "source": "Reuters",
                    "url": f"https://www.reuters.com/global-story-{i}.html",
                }
                for i in range(1, 6)
            ],
        })

        engine = GeminiEditorialEngine(mock_responder=lambda p: mock_response)
        result: EditorialResult = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is True
        assert result.selection is not None
        assert len(result.selection.india_stories) == 5
        assert len(result.selection.international_stories) == 5

        # Verify exact URL matching
        for story in result.selection.india_stories:
            assert story.url == articles_map[f"art-in-{story.event_id[-1]}"].url
            assert any(c.isdigit() for c in story.headline)  # Verifies numbers present

    def test_rejection_on_hallucinated_url(self, sample_candidate_pool_and_articles):
        """Test programmatic rejection when model returns an invented or mutated URL."""
        pool, articles_map = sample_candidate_pool_and_articles

        mock_response = json.dumps({
            "india_stories": [
                {
                    "section": "india",
                    "event_id": "evt-in-1",
                    "headline": "IndiaCorp1 Net Profit Up 11% to ₹1,000 Cr",
                    "source": "Business Standard",
                    "url": "https://www.business-standard.com/FABRICATED-URL-12345.html",  # INVENTED URL
                }
            ],
            "international_stories": [],
        })

        engine = GeminiEditorialEngine(mock_responder=lambda p: mock_response)
        result = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is False
        assert "does not match any supplied verified candidate URL" in (result.error_message or "")

    def test_rejection_on_unauthorized_event_id(self, sample_candidate_pool_and_articles):
        """Test programmatic rejection when model returns an unsupplied event ID."""
        pool, articles_map = sample_candidate_pool_and_articles

        mock_response = json.dumps({
            "india_stories": [
                {
                    "section": "india",
                    "event_id": "evt-fake-999",  # FAKE EVENT ID
                    "headline": "Fake Company Announces ₹5,000 Cr Deal",
                    "source": "Business Standard",
                    "url": "https://www.business-standard.com/india-story-1.html",
                }
            ],
            "international_stories": [],
        })

        engine = GeminiEditorialEngine(mock_responder=lambda p: mock_response)
        result = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is False
        assert "was not in the verified candidate list" in (result.error_message or "")

    def test_rejection_on_duplicate_india_company(self, sample_candidate_pool_and_articles):
        """Test programmatic rejection when model selects the same company twice in India section."""
        pool, articles_map = sample_candidate_pool_and_articles

        # Force candidate 2 to share company name with candidate 1
        pool.india_candidates[1].event.companies_involved = ["IndiaCorp1"]

        mock_response = json.dumps({
            "india_stories": [
                {
                    "section": "india",
                    "event_id": "evt-in-1",
                    "headline": "IndiaCorp1 Q1 Net Profit Up 11% to ₹1,000 Cr",
                    "source": "Business Standard",
                    "url": "https://www.business-standard.com/india-story-1.html",
                },
                {
                    "section": "india",
                    "event_id": "evt-in-2",
                    "headline": "IndiaCorp1 Announces New Strategic JV",
                    "source": "Business Standard",
                    "url": "https://www.business-standard.com/india-story-2.html",
                },
            ],
            "international_stories": [],
        })

        engine = GeminiEditorialEngine(mock_responder=lambda p: mock_response)
        result = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is False
        assert "duplicate company: 'IndiaCorp1'" in (result.error_message or "")

    def test_retry_on_malformed_json_success(self, sample_candidate_pool_and_articles):
        """Test recovery on attempt 2 when attempt 1 produces malformed JSON."""
        pool, articles_map = sample_candidate_pool_and_articles
        attempt_count = 0

        def flaky_responder(p: RankedCandidatePool) -> str:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                return "{malformed: invalid json string..."
            return json.dumps({
                "india_stories": [
                    {
                        "section": "india",
                        "event_id": "evt-in-1",
                        "headline": "IndiaCorp1 Q1 Net Profit Up 11% to ₹1,000 Cr",
                        "source": "Business Standard",
                        "url": "https://www.business-standard.com/india-story-1.html",
                    }
                ],
                "international_stories": [
                    {
                        "section": "international",
                        "event_id": "evt-intl-1",
                        "headline": "GlobalCorp1 Inks $1.5B Acquisition",
                        "source": "Reuters",
                        "url": "https://www.reuters.com/global-story-1.html",
                    }
                ],
            })

        engine = GeminiEditorialEngine(mock_responder=flaky_responder)
        result = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is True
        assert result.attempts == 2
        assert len(result.selection.india_stories) == 1

    def test_offline_editorial_fallback(self, sample_candidate_pool_and_articles):
        """Test deterministic offline fallback when no API key is supplied.

        Uses api_key="" (empty string) to prevent the engine from falling back
        to any GEMINI_API_KEY that may be present in the environment / .env file.
        """
        pool, articles_map = sample_candidate_pool_and_articles

        engine = GeminiEditorialEngine(api_key="", mock_responder=None)
        result = engine.select_and_synthesize_briefing(pool, articles_map)

        assert result.success is True
        assert len(result.selection.india_stories) == 5
        assert len(result.selection.international_stories) == 5
        assert result.selection.india_stories[0].url == "https://www.business-standard.com/india-story-1.html"
