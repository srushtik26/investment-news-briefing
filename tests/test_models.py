"""
Unit tests for core Pydantic data models.
"""

from datetime import date, datetime, timezone
import pytest
from pydantic import ValidationError

from app.models import (
    Article,
    Briefing,
    BriefingStatus,
    BriefingStory,
    Event,
    NewsCategory,
    RelevanceGrade,
    SourceVerification,
)


class TestArticleModel:
    """Tests for Article data model."""

    def test_valid_article_creation(self, sample_article: Article):
        """Test valid article properties."""
        assert sample_article.title.startswith("Tata Motors")
        assert sample_article.category == NewsCategory.INDIA
        assert sample_article.is_verified_url is True
        assert sample_article.word_count > 10
        assert sample_article.has_content is True

    def test_invalid_url_scheme(self):
        """Test that invalid URL schemes are rejected."""
        with pytest.raises(ValidationError) as exc:
            Article(
                title="Test Title",
                url="ftp://invalid.com/article",
                source_name="Reuters",
                content_text="Sample content here...",
            )
        assert "Invalid URL scheme" in str(exc.value)

    def test_invalid_url_netloc(self):
        """Test that empty domain URLs are rejected."""
        with pytest.raises(ValidationError) as exc:
            Article(
                title="Test Title",
                url="http://",
                source_name="Reuters",
                content_text="Sample content here...",
            )
        assert "valid domain network location" in str(exc.value)

    def test_empty_title_rejected(self):
        """Test that empty or blank titles fail validation."""
        with pytest.raises(ValidationError):
            Article(
                title="   ",
                url="https://example.com/news/1",
                source_name="Reuters",
                content_text="Sample content",
            )

    def test_article_serialization(self, sample_article: Article):
        """Test model dump and JSON serialization."""
        data = sample_article.model_dump()
        assert data["id"] == sample_article.id
        assert data["category"] == "india"

        json_str = sample_article.model_dump_json()
        assert "Tata Motors" in json_str


class TestEventModel:
    """Tests for Event data model."""

    def test_valid_event_creation(self, sample_event: Event):
        """Test valid event fields and properties."""
        assert "Tata Motors" in sample_event.companies_involved
        assert sample_event.source_count == 1
        assert sample_event.relevance_score == 8.5
        assert sample_event.is_duplicate is False

    def test_empty_event_title_fails(self):
        """Test that whitespace-only title is rejected."""
        with pytest.raises(ValidationError):
            Event(
                canonical_title="   ",
                description="Valid description here",
            )


class TestSourceVerificationModel:
    """Tests for SourceVerification data model."""

    def test_auto_sync_sources(self):
        """Test automatic deduplication and count calculation."""
        verification = SourceVerification(
            event_id="evt-12345",
            independent_sources=["Reuters", "Bloomberg", "Reuters", "Financial Times"],
        )
        assert verification.sources_count == 3
        assert set(verification.independent_sources) == {"Reuters", "Bloomberg", "Financial Times"}
        assert verification.is_multi_source_verified is True

    def test_single_source_not_multi_verified(self):
        """Test that single source sets is_multi_source_verified to False."""
        verification = SourceVerification(
            event_id="evt-999",
            independent_sources=["Single Source"],
        )
        assert verification.sources_count == 1
        assert verification.is_multi_source_verified is False


class TestBriefingStoryModel:
    """Tests for BriefingStory model."""

    def test_valid_briefing_story(self, sample_briefing_story: BriefingStory):
        """Test valid briefing story creation and score ranges."""
        assert sample_briefing_story.primary_company == "Tata Motors"
        assert sample_briefing_story.investment_relevance_score == 9.2
        assert sample_briefing_story.rank == 1

    def test_invalid_relevance_score(self):
        """Test that score > 10.0 or < 0.0 is rejected."""
        with pytest.raises(ValidationError):
            BriefingStory(
                event_id="evt-1",
                headline="Valid headline here",
                category=NewsCategory.INDIA,
                investment_relevance_score=15.0,  # Exceeds max 10.0
            )


class TestBriefingModel:
    """Tests for Briefing model."""

    def test_briefing_story_counts(self, sample_briefing_story: BriefingStory):
        """Test total_stories_count auto calculation."""
        intl_story = BriefingStory(
            event_id="evt-intl-1",
            headline="Federal Reserve signals pause on interest rate hikes",
            category=NewsCategory.INTERNATIONAL,
            key_points=["Key point 1", "Key point 2"],
            primary_company="US Federal Reserve",
            investment_relevance_score=8.0,
            rank=1,
        )

        briefing = Briefing(
            title="Daily Investment Committee Briefing",
            india_stories=[sample_briefing_story],
            international_stories=[intl_story],
        )

        assert briefing.total_stories_count == 2
        assert len(briefing.india_stories) == 1
        assert len(briefing.international_stories) == 1
        assert briefing.status == BriefingStatus.DRAFT
        assert not briefing.has_duplicate_india_company()

    def test_duplicate_india_company_detection(self, sample_briefing_story: BriefingStory):
        """Test detection of duplicate companies in India section."""
        duplicate_story = BriefingStory(
            event_id="evt-2",
            headline="Tata Motors reports record EV deliveries in Q1",
            category=NewsCategory.INDIA,
            primary_company="Tata Motors",  # Same company as sample_briefing_story
            investment_relevance_score=7.5,
            rank=2,
        )

        briefing = Briefing(
            title="Test Briefing",
            india_stories=[sample_briefing_story, duplicate_story],
        )

        assert briefing.has_duplicate_india_company() is True
