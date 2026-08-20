"""
Unit tests for Event-Level Two-Source Verification.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event
from app.verification import (
    EventSourceVerification,
    TwoSourceVerifier,
    VerificationStatus,
)


@pytest.fixture
def sample_event() -> Event:
    """Fixture providing a base event."""
    return Event(
        canonical_title="HDFC Bank Q1 Net Profit Surges 18% to ₹16,175 Crore",
        description="HDFC Bank posted 18% YoY net profit growth for Q1 driven by strong NII and stable asset quality.",
        companies_involved=["HDFC Bank"],
        sectors=["Banking", "Financial Services"],
        financial_figures=["₹16,175 crore", "18%"],
        event_category=NewsCategory.INDIA,
    )


@pytest.fixture
def hdfc_bs_article() -> Article:
    """Fixture for Business Standard article on HDFC Bank."""
    return Article(
        title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII",
        url="https://www.business-standard.com/companies/results/hdfc-bank-q1-results-12345.html",
        source_name="Business Standard",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        content_text=(
            "HDFC Bank on Tuesday reported an 18 per cent year-on-year growth in standalone net profit "
            "at ₹16,175 crore for the first quarter. Net interest income grew 26 per cent to ₹29,837 crore."
        ),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


@pytest.fixture
def hdfc_et_article() -> Article:
    """Fixture for Economic Times article on HDFC Bank."""
    return Article(
        title="HDFC Bank Q1 Results: Net Profit Rises 18% to ₹16,175 Crore; GNPA Stable at 1.33%",
        url="https://economictimes.indiatimes.com/industry/banking/hdfc-bank-q1-profit-12345.cms",
        source_name="The Economic Times",
        published_at=datetime(2026, 8, 18, 9, 15, tzinfo=timezone.utc),
        content_text=(
            "Private lender HDFC Bank on Tuesday announced an 18% rise in its net profit to ₹16,175 crore. "
            "Asset quality remained stable with gross NPAs coming in at 1.33%."
        ),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


class TestTwoSourceVerifier:
    """Tests for TwoSourceVerifier corroboration engine."""

    def test_two_independent_sources_verified(
        self,
        sample_event: Event,
        hdfc_bs_article: Article,
        hdfc_et_article: Article,
    ):
        """Test verification passes with 2 distinct independent publishers."""
        verifier = TwoSourceVerifier()
        result: EventSourceVerification = verifier.verify_event(
            event=sample_event,
            articles=[hdfc_bs_article, hdfc_et_article],
        )

        assert result.is_verified is True
        assert result.verification_status == VerificationStatus.VERIFIED
        assert result.source_count == 2
        assert result.is_independent is True
        assert result.primary_source == "Business Standard"
        assert result.secondary_source == "The Economic Times"
        assert result.confidence_score >= 0.90

    def test_same_source_twice_rejected(
        self,
        sample_event: Event,
        hdfc_et_article: Article,
    ):
        """Test multiple articles from the same publication / media group fail verification."""
        # Create second article from the same publisher (Economic Times / Times Group)
        second_et_article = hdfc_et_article.model_copy(deep=True)
        second_et_article.url = "https://economictimes.indiatimes.com/markets/stocks/hdfc-bank-analysis/67890.cms"
        second_et_article.title = "HDFC Bank Q1 Profit Jumps 18% to ₹16,175 Cr - Key Takeaways"

        verifier = TwoSourceVerifier()
        result = verifier.verify_event(
            event=sample_event,
            articles=[hdfc_et_article, second_et_article],
        )

        assert result.is_verified is False
        assert result.verification_status == VerificationStatus.REJECTED_SAME_PUBLISHER
        assert result.is_independent is False
        assert "same publisher/media group" in (result.matching_details or "")

    def test_syndicated_article_rejected(
        self,
        sample_event: Event,
        hdfc_bs_article: Article,
    ):
        """Test syndicated wire copies (e.g. verbatim PTI feed in two outlets) are rejected."""
        # Outlet 1 carries PTI feed
        art1 = hdfc_bs_article.model_copy(deep=True)
        art1.source_name = "Livemint"
        art1.url = "https://www.livemint.com/market/hdfc-pti-feed-1"
        art1.content_text = "PTI / New Delhi: HDFC Bank on Tuesday posted an 18 per cent rise in standalone net profit to Rs 16,175 crore for Q1. Net interest income stood at Rs 29,837 crore according to regulatory filings."

        # Outlet 2 carries same verbatim PTI feed
        art2 = hdfc_bs_article.model_copy(deep=True)
        art2.source_name = "Financial Express"
        art2.url = "https://www.financialexpress.com/industry/hdfc-pti-feed-2"
        art2.content_text = "PTI / New Delhi: HDFC Bank on Tuesday posted an 18 per cent rise in standalone net profit to Rs 16,175 crore for Q1. Net interest income stood at Rs 29,837 crore according to regulatory filings."

        verifier = TwoSourceVerifier()
        result = verifier.verify_event(
            event=sample_event,
            articles=[art1, art2],
        )

        assert result.is_verified is False
        assert result.verification_status == VerificationStatus.REJECTED_SYNDICATED
        assert result.is_independent is False
        assert "Syndicated wire copy" in (result.matching_details or "")

    def test_unrelated_articles_rejected(
        self,
        sample_event: Event,
        hdfc_bs_article: Article,
    ):
        """Test two unrelated articles discussing different entities are rejected."""
        unrelated_art = Article(
            title="Nvidia Q2 Revenue Surges 122% to $30 Billion on AI Data Center Demand",
            url="https://www.reuters.com/technology/nvidia-earnings-12345",
            source_name="Reuters",
            published_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
            content_text="Nvidia reported second-quarter revenue of $30.04 billion beating estimates on surging Blackwell chip orders.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        result = verifier.verify_event(
            event=sample_event,
            articles=[hdfc_bs_article, unrelated_art],
        )

        assert result.is_verified is False
        assert result.verification_status == VerificationStatus.REJECTED_UNRELATED
        assert result.is_independent is False
        assert "different underlying events" in (result.matching_details or "")

    def test_one_source_event_unverified(
        self,
        sample_event: Event,
        hdfc_bs_article: Article,
    ):
        """Test event with only 1 source is marked UNVERIFIED_SINGLE_SOURCE without fabricating a second source."""
        verifier = TwoSourceVerifier()
        result = verifier.verify_event(
            event=sample_event,
            articles=[hdfc_bs_article],
        )

        assert result.is_verified is False
        assert result.verification_status == VerificationStatus.UNVERIFIED_SINGLE_SOURCE
        assert result.source_count == 1
        assert result.secondary_source is None
        assert result.is_independent is False
        assert "only 1 source" in (result.matching_details or "")


def test_package_exports_and_instantiates_two_source_verifier():
    """Test importing TwoSourceVerifier directly from app.verification package."""
    from app.verification import TwoSourceVerifier as PackageVerifier
    verifier = PackageVerifier()
    assert isinstance(verifier, PackageVerifier)

