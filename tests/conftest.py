"""
Pytest configuration and shared test fixtures.
"""

from datetime import datetime, timezone
import pytest

from config import Settings
from app.models import (
    Article,
    Briefing,
    BriefingStatus,
    BriefingStory,
    Event,
    NewsCategory,
    SourceVerification,
)


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """Provide isolated Settings pointing to temporary directories."""
    return Settings(
        APP_ENV="testing",
        LOG_LEVEL="DEBUG",
        DATA_PATH=tmp_path / "data",
        LOGS_PATH=tmp_path / "logs",
        GEMINI_API_KEY="test-mock-key-12345",
        DATABASE_URL=f"sqlite:///{tmp_path}/test.db",
        MAX_INDIA_STORIES=5,
        MAX_INTERNATIONAL_STORIES=5,
        STORY_LOOKBACK_DAYS=3,
        MIN_INDEPENDENT_SOURCES=2,
    )


@pytest.fixture
def sample_article() -> Article:
    """Fixture providing a standard valid Article."""
    return Article(
        title="Tata Motors Approves Split of Commercial and Passenger Vehicle Businesses",
        url="https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger/articleshow/108192301.cms",
        source_name="The Economic Times",
        published_at=datetime(2026, 8, 18, 9, 30, 0, tzinfo=timezone.utc),
        content_text=(
            "Tata Motors on Tuesday approved the demerger of its commercial vehicle and passenger vehicle businesses "
            "into two separate listed companies to better exploit growth opportunities."
        ),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        is_valid_date=True,
        metadata={"author": "Staff Reporter", "tags": ["Automotive", "Demerger"]},
    )


@pytest.fixture
def sample_event(sample_article) -> Event:
    """Fixture providing a standard valid Event."""
    return Event(
        canonical_title="Tata Motors demerges into CV and PV listed entities",
        description="Tata Motors board approves proposal to split commercial vehicles and passenger vehicles into two listed companies.",
        companies_involved=["Tata Motors"],
        sectors=["Automotive", "Manufacturing"],
        financial_figures=["2 separate listed companies"],
        event_category=NewsCategory.INDIA,
        article_ids=[sample_article.id],
        relevance_score=8.5,
    )


@pytest.fixture
def sample_verification(sample_event) -> SourceVerification:
    """Fixture providing a valid SourceVerification."""
    return SourceVerification(
        event_id=sample_event.id,
        independent_sources=["The Economic Times", "Reuters", "Mint"],
    )


@pytest.fixture
def sample_briefing_story(sample_event) -> BriefingStory:
    """Fixture providing a valid BriefingStory."""
    return BriefingStory(
        event_id=sample_event.id,
        headline="Tata Motors board approves split into separate commercial and passenger vehicle listed entities",
        category=NewsCategory.INDIA,
        key_points=[
            "Demerger into two distinct listed entities for commercial and passenger vehicles.",
            "Aims to unlock shareholder value and provide focused capital allocation.",
        ],
        impact_analysis="Significant restructuring for India's leading automaker, improving capital efficiency and strategic focus.",
        primary_company="Tata Motors",
        source_citations=["The Economic Times", "Reuters"],
        source_urls=[
            "https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger/articleshow/108192301.cms",
            "https://www.reuters.com/business/autos-transportation/tata-motors-split-2026-08-18/",
        ],
        investment_relevance_score=9.2,
        rank=1,
    )
