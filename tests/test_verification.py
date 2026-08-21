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


class TestSameEventVerificationQualityRegression:
    """Regression tests ensuring strict same-underlying-event validation."""

    def test_lt_nxt_infra_vs_nxt_infra_earnings_rejected(self):
        """
        Regression Test: Exact live bug scenario.
        Primary: 'L&T buys over 2 crore units of Nxt-Infra Trust for ₹250 Crore' (ACQUISITION/PURCHASE)
        Secondary: 'Nxt-Infra Trust consolidated net profit rises 71.83% in June 2026 quarter' (EARNINGS)
        Must be rejected with EVENT_TYPE_MISMATCH.
        """
        art_deal = Article(
            title="L&T buys over 2 crore units of Nxt-Infra Trust for ₹250 Crore",
            url="https://www.livemint.com/market/lt-buys-nxt-infra",
            source_name="Livemint",
            content_text="Larsen & Toubro has acquired over 2 crore units of Nxt-Infra Trust in a block deal worth ₹250 crore.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art_earnings = Article(
            title="Nxt-Infra Trust consolidated net profit rises 71.83% in June 2026 quarter",
            url="https://www.business-standard.com/companies/nxt-infra-q1-results",
            source_name="Business Standard",
            content_text="Nxt-Infra Trust posted a 71.83% increase in consolidated net profit for the quarter ended June 2026.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        is_same, score, reason = verifier.is_same_underlying_event(art_deal, art_earnings)

        assert is_same is False
        assert "EVENT_TYPE_MISMATCH" in reason
        assert "ACQUISITION_M_A" in reason or "EARNINGS" in reason

    def test_acquisition_vs_same_target_acquisition_accepted(self):
        """Test: Acquisition of same target by same buyer across two publishers is ACCEPTED."""
        art1 = Article(
            title="Rio Tinto Agrees $6.7 Billion All-Cash Acquisition of Arcadium Lithium",
            url="https://www.reuters.com/rio-tinto-arcadium",
            source_name="Reuters",
            content_text="Mining giant Rio Tinto has agreed to acquire Arcadium Lithium for $6.7 billion in cash.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            title="Rio Tinto to Buy Arcadium Lithium for $6.7 Billion",
            url="https://www.cnbc.com/rio-arcadium-deal",
            source_name="CNBC",
            content_text="Rio Tinto announced a takeover of Arcadium Lithium in a deal valued at $6.7 billion.",
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        is_same, score, reason = verifier.is_same_underlying_event(art1, art2)

        assert is_same is True
        assert score >= 0.80
        assert "Corroborated" in reason

    def test_same_quarter_earnings_accepted(self):
        """Test: Earnings for the same company and same quarter are ACCEPTED."""
        art1 = Article(
            title="HDFC Bank Q1 Net Profit Surges 18% to ₹16,175 Crore",
            url="https://www.business-standard.com/hdfc-q1",
            source_name="Business Standard",
            content_text="HDFC Bank reported net profit of ₹16,175 crore for Q1.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            title="HDFC Bank Q1 Profit Rises 18% YoY",
            url="https://economictimes.indiatimes.com/hdfc-q1",
            source_name="The Economic Times",
            content_text="HDFC Bank announced an 18% rise in Q1 profit.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        is_same, score, reason = verifier.is_same_underlying_event(art1, art2)

        assert is_same is True
        assert score >= 0.80

    def test_different_reporting_quarters_rejected_financial_fact_mismatch(self):
        """Test: Same company but Q1 vs Q2 earnings are rejected with FINANCIAL_FACT_MISMATCH."""
        art_q1 = Article(
            title="Tata Motors Q1 Net Profit Surges 74% to ₹5,566 Crore",
            url="https://www.livemint.com/tata-motors-q1",
            source_name="Livemint",
            content_text="Tata Motors posted strong Q1 results.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art_q2 = Article(
            title="Tata Motors Q2 Net Profit Falls 10% to ₹3,200 Crore",
            url="https://economictimes.indiatimes.com/tata-motors-q2",
            source_name="The Economic Times",
            content_text="Tata Motors reported Q2 profit drop.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        is_same, score, reason = verifier.is_same_underlying_event(art_q1, art_q2)

        assert is_same is False
        assert "FINANCIAL_FACT_MISMATCH" in reason

    def test_same_company_unrelated_events_rejected_insufficient_overlap(self):
        """Test: Same company mentioned without matching event facts or counterpart is REJECTED with INSUFFICIENT_EVENT_OVERLAP."""
        art1 = Article(
            title="Reliance Retail Expands Footprint Across Tier-2 Cities",
            url="https://www.livemint.com/reliance-expansion",
            source_name="Livemint",
            content_text="Reliance Retail announced plans to open 200 new stores across India.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        art2 = Article(
            title="Reliance Retail Partners with Global Fashion Brand",
            url="https://economictimes.indiatimes.com/reliance-fashion",
            source_name="The Economic Times",
            content_text="Reliance Retail inked an exclusive distribution agreement with an Italian luxury label.",
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )

        verifier = TwoSourceVerifier()
        is_same, score, reason = verifier.is_same_underlying_event(art1, art2)

        assert is_same is False
        assert "INSUFFICIENT_EVENT_OVERLAP" in reason

    def test_canonical_rejection_codes_present(self):
        """Test that TwoSourceVerifier produces expected canonical error codes for observability."""
        verifier = TwoSourceVerifier()

        # 1. Event Type Mismatch
        art_earn = Article(
            title="Nxt-Infra Trust Consolidated Net Profit Rises 71.83% in June 2026 Quarter",
            url="https://www.livemint.com/nxt-infra-q1",
            source_name="Livemint",
            content_text="Q1 earnings rose.",
        )
        art_mna = Article(
            title="L&T buys over 2 crore units of Nxt-Infra Trust",
            url="https://economictimes.indiatimes.com/lt-nxt-infra",
            source_name="The Economic Times",
            content_text="L&T purchased units in block deal.",
        )
        is_same1, _, reason1 = verifier.is_same_underlying_event(art_earn, art_mna)
        assert is_same1 is False
        assert "EVENT_TYPE_MISMATCH" in reason1

        # 2. Same Publisher Group
        art_p1 = Article(title="Test", url="https://economictimes.indiatimes.com/a", source_name="The Economic Times")
        art_p2 = Article(title="Test", url="https://timesofindia.indiatimes.com/b", source_name="Times of India")
        grp1 = verifier.get_publisher_group(art_p1)
        grp2 = verifier.get_publisher_group(art_p2)
        assert grp1 == grp2 == "times_group"


