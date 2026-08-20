"""
Unit tests for SerpAPI Secondary Corroboration Fallback using Mocks.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_count,
)


@pytest.fixture(autouse=True)
def reset_counter():
    """Reset SerpAPI run counter before each test."""
    reset_serpapi_counter()


@pytest.fixture
def sample_event() -> Event:
    """Fixture providing a sample single-source event."""
    return Event(
        canonical_title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium",
        description="Rio Tinto agreed to acquire Arcadium Lithium for $6.7 billion.",
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
        financial_figures=["$6.7 billion"],
        event_category=NewsCategory.INTERNATIONAL,
    )


@pytest.fixture
def primary_article() -> Article:
    """Fixture for primary article."""
    return Article(
        title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium",
        url="https://www.reuters.com/rio-tinto-arcadium",
        source_name="Reuters",
        published_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
        content_text="Rio Tinto has reached a definitive agreement to acquire Arcadium Lithium for $6.7 billion.",
        category=NewsCategory.INTERNATIONAL,
    )


def test_serpapi_disabled_when_no_api_key(sample_event: Event, primary_article: Article):
    """Test that SerpAPI corroborator is cleanly disabled when api_key is None or empty."""
    corroborator = SerpAPICorroborator(api_key="")
    assert corroborator.has_api_key is False

    result = corroborator.corroborate(sample_event, primary_article)
    assert result.success is False
    assert "disabled" in (result.failure_reason or "")
    assert get_serpapi_count() == 0


@patch("requests.get")
def test_serpapi_success_with_mocked_response(
    mock_get, sample_event: Event, primary_article: Article
):
    """Test successful SerpAPI corroboration returning an independent second source."""
    # Mock SerpAPI response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "news_results": [
            {
                "title": "Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in Cash Deal",
                "link": "https://www.cnbc.com/rio-tinto-arcadium-deal",
                "source": {"name": "CNBC"},
                "date": "2026-08-18T08:00:00Z",
            }
        ]
    }
    mock_get.return_value = mock_response

    # Mock ArticleExtractor
    mock_extractor = MagicMock()
    mock_extract_result = MagicMock()
    mock_extract_result.success = True
    mock_extract_result.article = Article(
        title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in Cash Deal",
        url="https://www.cnbc.com/rio-tinto-arcadium-deal",
        source_name="CNBC",
        published_at=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        content_text="Mining giant Rio Tinto announced it will purchase US-based lithium producer Arcadium Lithium in a deal valued at 6.7 billion dollars. The all-cash offer represents a 90% premium to Arcadium's closing price, expanding Rio's portfolio in energy transition metals.",
        category=NewsCategory.INTERNATIONAL,
    )
    mock_extractor.extract.return_value = mock_extract_result

    corroborator = SerpAPICorroborator(extractor=mock_extractor, api_key="fake_test_key", max_searches=5)
    result = corroborator.corroborate(sample_event, primary_article)

    assert result.success is True, f"Failure reason: {result.failure_reason}, score: {result.verification_score}"
    assert result.corroborating_article is not None
    assert result.corroborating_article.source_name == "CNBC"
    assert get_serpapi_count() == 1


def test_serpapi_max_searches_budget_limit(sample_event: Event, primary_article: Article):
    """Test that SerpAPI search counter strictly obeys max_searches cap."""
    corroborator = SerpAPICorroborator(api_key="fake_test_key", max_searches=0)
    result = corroborator.corroborate(sample_event, primary_article)

    assert result.success is False
    assert "budget exhausted" in (result.failure_reason or "")
    assert get_serpapi_count() == 0


@patch("requests.get")
def test_serpapi_query_caching(mock_get, sample_event: Event, primary_article: Article):
    """Test that identical SerpAPI queries use in-memory cache and don't re-fire HTTP requests."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"news_results": []}
    mock_get.return_value = mock_response

    corroborator = SerpAPICorroborator(api_key="fake_test_key", max_searches=5)

    # First call fires HTTP request
    corroborator.corroborate(sample_event, primary_article)
    assert mock_get.call_count == 1
    assert get_serpapi_count() == 1

    # Second call for same article hits cache
    corroborator.corroborate(sample_event, primary_article)
    assert mock_get.call_count == 1  # Proves HTTP call count didn't increase
    assert get_serpapi_count() == 1
