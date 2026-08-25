"""
Unit tests for EventRegionClassifier.

Verifies deterministic geographic categorization (INDIA vs INTERNATIONAL)
independent of publisher origin.
"""

import pytest

from app.classification.region_classifier import EventRegionClassifier
from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event


@pytest.fixture
def classifier() -> EventRegionClassifier:
    return EventRegionClassifier()


class TestEventRegionClassifier:
    """Test suite for EventRegionClassifier."""

    def test_indian_corporate_deal_classified_india(self, classifier: EventRegionClassifier):
        """Test Indian companies/deals are classified as INDIA."""
        cat = classifier.classify(
            title="L&T buys over 2 crore units of Nxt-Infra Trust for ₹250 Crore",
            content="Larsen & Toubro acquired units in an infrastructure investment trust.",
            financial_figures=["₹250 crore", "2 crore units"],
        )
        assert cat == NewsCategory.INDIA

    def test_us_tech_acquisition_from_indian_publisher_classified_international(
        self, classifier: EventRegionClassifier
    ):
        """
        Test: Stripe acquiring OpenRouter reported by ET CIO must NOT be classified as India.
        Publisher location must NOT determine event geography.
        """
        cat = classifier.classify(
            title="Stripe acquires AI routing startup OpenRouter in $500M deal",
            content="Fintech giant Stripe has reached an agreement to acquire AI model routing startup OpenRouter.",
            companies=["ET CIO"],
        )
        assert cat == NewsCategory.INTERNATIONAL

    def test_global_investment_bank_deal_classified_international(
        self, classifier: EventRegionClassifier
    ):
        """Test Goldman Sachs acquiring LCN Capital Partners is classified as INTERNATIONAL."""
        cat = classifier.classify(
            title="Goldman Sachs acquires real estate fund manager LCN Capital Partners",
            content="Goldman Sachs Asset Management announced the acquisition of LCN Capital Partners.",
            financial_figures=["$3 billion AUM"],
        )
        assert cat == NewsCategory.INTERNATIONAL

    def test_foreign_firm_investing_in_indian_asset_classified_india(
        self, classifier: EventRegionClassifier
    ):
        """Test global company investing in an Indian rupee asset is classified as INDIA."""
        cat = classifier.classify(
            title="Goldman Sachs Asset Management invests ₹1,200 crore in Indian logistics platform",
            content="Goldman Sachs has committed ₹1,200 crore to back a logistics developer in India.",
            financial_figures=["₹1,200 crore"],
        )
        assert cat == NewsCategory.INDIA

    def test_indian_regulatory_action_classified_india(
        self, classifier: EventRegionClassifier
    ):
        """Test RBI penalty is classified as INDIA."""
        cat = classifier.classify(
            title="RBI imposes ₹1.5 crore penalty on private sector bank for regulatory non-compliance",
            content="The Reserve Bank of India has levied a monetary penalty on the lender.",
            financial_figures=["₹1.5 crore"],
        )
        assert cat == NewsCategory.INDIA

    def test_global_central_bank_policy_classified_international(
        self, classifier: EventRegionClassifier
    ):
        """Test US Federal Reserve policy is classified as INTERNATIONAL."""
        cat = classifier.classify(
            title="Federal Reserve holds benchmark interest rate steady at 5.25%-5.50%",
            content="Fed Chair Jerome Powell said the central bank will wait for more confidence on inflation.",
            financial_figures=["5.25%-5.50%"],
        )
        assert cat == NewsCategory.INTERNATIONAL

    def test_sebi_market_regulation_classified_india(
        self, classifier: EventRegionClassifier
    ):
        """Test SEBI derivatives framework is classified as INDIA."""
        cat = classifier.classify(
            title="SEBI tightens index derivatives framework to curb retail speculation in F&O",
            content="Securities and Exchange Board of India announced measures on contract lot sizes.",
        )
        assert cat == NewsCategory.INDIA

    def test_mining_m_and_a_classified_international(
        self, classifier: EventRegionClassifier
    ):
        """Test Rio Tinto / Arcadium Lithium is classified as INTERNATIONAL."""
        cat = classifier.classify(
            title="Rio Tinto Agrees $6.7 Billion All-Cash Acquisition of Arcadium Lithium",
            content="Global mining powerhouse Rio Tinto will acquire Arcadium Lithium for $6.7 billion.",
            financial_figures=["$6.7 billion"],
        )
        assert cat == NewsCategory.INTERNATIONAL

    def test_classify_article_instance(self, classifier: EventRegionClassifier):
        """Test classifying an Article instance directly."""
        art = Article(
            title="Tata Motors plans ₹15,000 crore EV manufacturing hub in Tamil Nadu",
            url="https://economictimes.indiatimes.com/tata-ev-plant",
            source_name="The Economic Times",
            content_text="Tata Motors is set to expand electric vehicle production in southern India.",
            category=NewsCategory.INTERNATIONAL,  # intentionally flawed input category
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        cat = classifier.classify_article(art)
        assert cat == NewsCategory.INDIA

    def test_classify_event_instance(self, classifier: EventRegionClassifier):
        """Test classifying an Event instance directly."""
        event = Event(
            canonical_title="Apple reports record $94.9 billion revenue driven by iPhone 16 sales",
            description="Apple Inc announced quarterly financial results for Q4 fiscal 2024.",
            companies_involved=["Apple"],
            financial_figures=["$94.9 billion"],
            event_category=NewsCategory.INDIA,  # intentionally flawed input category
        )
        cat = classifier.classify_event(event)
        assert cat == NewsCategory.INTERNATIONAL

    def test_international_discovery_not_flipped_by_incidental_currency(self, classifier: EventRegionClassifier):
        cat = classifier.classify(
            title="Fund manager lists four of his best-value unloved stocks",
            content="The global portfolio manager discussed valuation and market conditions.",
            financial_figures=["₹500 crore"],
            discovery_region=NewsCategory.INTERNATIONAL,
        )
        assert cat == NewsCategory.INTERNATIONAL
