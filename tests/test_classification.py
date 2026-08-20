"""
Unit tests for AI Article Classification using mocked Gemini responses.
"""

from datetime import datetime, timezone
import json
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory
from app.classification import (
    AIArticleClassification,
    AIArticleClassifier,
    ArticleEventType,
    ClassificationResult,
)


@pytest.fixture
def sample_earnings_article() -> Article:
    """Fixture providing an earnings article."""
    return Article(
        title="HDFC Bank Q1 Net Profit Surges 18% YoY to ₹16,175 Crore on Strong NII",
        url="https://www.business-standard.com/hdfc-q1-results",
        source_name="Business Standard",
        published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        content_text=(
            "HDFC Bank reported an 18% year-on-year rise in net profit to ₹16,175 crore for the first quarter. "
            "Net interest income grew 26% while gross NPA improved to 1.33%. Nifty 50 traded flat following the announcement."
        ),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


@pytest.fixture
def sample_ma_article() -> Article:
    """Fixture providing an M&A article."""
    return Article(
        title="Rio Tinto Agrees $6.7 Billion Acquisition of Arcadium Lithium in All-Cash Deal",
        url="https://www.reuters.com/rio-tinto-arcadium-lithium",
        source_name="Reuters",
        published_at=datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc),
        content_text=(
            "Rio Tinto has reached a definitive agreement to acquire Arcadium Lithium for $6.7 billion in cash, "
            "securing premier lithium brine operations in Argentina."
        ),
        category=NewsCategory.INTERNATIONAL,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


class TestAIClassificationModels:
    """Tests for classification data schemas and validators."""

    def test_valid_classification_model(self):
        """Test valid instantiation and field values."""
        classification = AIArticleClassification(
            event_type=ArticleEventType.EARNINGS,
            company_names=["HDFC Bank"],
            financial_numbers=["₹16,175 crore"],
            percentages=["18%", "26%"],
            deal_value=None,
            market_indices=["Nifty 50"],
            commodity_prices=[],
            currency_values=[],
            key_outcome="HDFC Bank posted 18% YoY growth in Q1 net profit to ₹16,175 crore.",
            is_hard_business_event=True,
            has_specific_quantified_impact=True,
            is_investment_relevant=True,
        )

        assert classification.event_type == ArticleEventType.EARNINGS
        assert "HDFC Bank" in classification.company_names
        assert classification.deal_value is None
        assert classification.is_hard_business_event is True

    def test_deal_value_cleaning(self):
        """Test cleaning of placeholder deal values like N/A and none."""
        c1 = AIArticleClassification(
            event_type=ArticleEventType.MA,
            deal_value="N/A",
        )
        assert c1.deal_value is None

        c2 = AIArticleClassification(
            event_type=ArticleEventType.MA,
            deal_value="$6.7 billion",
        )
        assert c2.deal_value == "$6.7 billion"

    def test_event_type_enum_variations(self):
        """Test flexible parsing of event type strings."""
        assert ArticleEventType("M&A") == ArticleEventType.MA
        assert ArticleEventType("merger") == ArticleEventType.MA
        assert ArticleEventType("EARNINGS") == ArticleEventType.EARNINGS
        assert ArticleEventType("non_existent_type") == ArticleEventType.OTHER


class TestAIArticleClassifier:
    """Tests for AIArticleClassifier service with mocked responses."""

    def test_classify_earnings_success(self, sample_earnings_article: Article):
        """Test successful classification of earnings article via mock responder."""
        mock_response = json.dumps({
            "event_type": "EARNINGS",
            "company_names": ["HDFC Bank"],
            "financial_numbers": ["₹16,175 crore"],
            "percentages": ["18%", "26%"],
            "deal_value": None,
            "market_indices": ["Nifty 50"],
            "commodity_prices": [],
            "currency_values": [],
            "key_outcome": "HDFC Bank Q1 net profit grew 18% YoY to ₹16,175 crore on strong NII.",
            "is_hard_business_event": True,
            "has_specific_quantified_impact": True,
            "is_investment_relevant": True,
        })

        classifier = AIArticleClassifier(mock_responder=lambda art: mock_response)
        result: ClassificationResult = classifier.classify(sample_earnings_article)

        assert result.success is True
        assert result.classification is not None
        assert result.classification.event_type == ArticleEventType.EARNINGS
        assert result.classification.company_names == ["HDFC Bank"]
        assert result.classification.percentages == ["18%", "26%"]
        assert result.classification.is_hard_business_event is True
        assert result.attempts == 1

    def test_classify_ma_deal_value(self, sample_ma_article: Article):
        """Test extraction of M&A event with deal value."""
        mock_response = json.dumps({
            "event_type": "M&A",
            "company_names": ["Rio Tinto", "Arcadium Lithium"],
            "financial_numbers": ["$6.7 billion"],
            "percentages": [],
            "deal_value": "$6.7 billion",
            "market_indices": [],
            "commodity_prices": ["Lithium"],
            "currency_values": [],
            "key_outcome": "Rio Tinto agreed to acquire Arcadium Lithium for $6.7 billion in cash.",
            "is_hard_business_event": True,
            "has_specific_quantified_impact": True,
            "is_investment_relevant": True,
        })

        classifier = AIArticleClassifier(mock_responder=lambda art: mock_response)
        result = classifier.classify(sample_ma_article)

        assert result.success is True
        assert result.classification.event_type == ArticleEventType.MA
        assert result.classification.deal_value == "$6.7 billion"
        assert "Rio Tinto" in result.classification.company_names

    def test_retry_on_malformed_json_success(self, sample_earnings_article: Article):
        """Test that malformed JSON on attempt 1 retries and succeeds on attempt 2."""
        call_count = 0

        def mock_flaky_responder(art: Article) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "```json {invalid: malformed json... ```"
            return json.dumps({
                "event_type": "EARNINGS",
                "company_names": ["HDFC Bank"],
                "financial_numbers": ["₹16,175 crore"],
                "percentages": ["18%"],
                "deal_value": None,
                "market_indices": [],
                "commodity_prices": [],
                "currency_values": [],
                "key_outcome": "HDFC Bank net profit rose 18%.",
                "is_hard_business_event": True,
                "has_specific_quantified_impact": True,
                "is_investment_relevant": True,
            })

        classifier = AIArticleClassifier(mock_responder=mock_flaky_responder)
        result = classifier.classify(sample_earnings_article)

        assert result.success is True
        assert result.attempts == 2  # Proves retry occurred
        assert result.classification.event_type == ArticleEventType.EARNINGS

    def test_rejection_after_failed_retries(self, sample_earnings_article: Article):
        """Test that persistent malformed JSON results in failure after 2 attempts."""
        classifier = AIArticleClassifier(mock_responder=lambda art: "NOT VALID JSON AT ALL")
        result = classifier.classify(sample_earnings_article)

        assert result.success is False
        assert result.classification is None
        assert result.attempts == 2
        assert "Malformed output" in (result.error_message or "")

    def test_offline_heuristic_fallback(self, sample_earnings_article: Article):
        """Test fallback execution when no API key is supplied.

        Uses api_key="" (empty string) to prevent the classifier from falling
        back to any GEMINI_API_KEY that may be present in the environment / .env
        file. This guarantees the deterministic offline heuristic path is exercised.
        """
        classifier = AIArticleClassifier(api_key="", mock_responder=None)
        result = classifier.classify(sample_earnings_article)

        assert result.success is True
        assert result.classification is not None
        assert result.classification.event_type == ArticleEventType.EARNINGS
        assert result.classification.is_hard_business_event is True

    def test_gemini_429_rate_limit_fallback(self, sample_earnings_article: Article):
        """Test that 429 rate limit triggers offline fallback after retry."""
        def mock_429_responder(art: Article) -> str:
            raise Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")

        classifier = AIArticleClassifier(mock_responder=mock_429_responder)
        result = classifier.classify(sample_earnings_article)

        assert result.success is True
        assert result.attempts == 0  # 0 indicates offline fallback
        assert result.classification is not None
        assert result.classification.event_type == ArticleEventType.EARNINGS
        assert classifier._force_offline_mode is True

    def test_gemini_503_server_error_fallback(self, sample_earnings_article: Article):
        """Test that 503 server error triggers offline fallback after retry."""
        def mock_503_responder(art: Article) -> str:
            raise Exception("503 Service Unavailable: The model is currently overloaded")

        classifier = AIArticleClassifier(mock_responder=mock_503_responder)
        result = classifier.classify(sample_earnings_article)

        assert result.success is True
        assert result.attempts == 0  # 0 indicates offline fallback
        assert result.classification is not None
        assert classifier._force_offline_mode is True

    def test_remaining_candidates_classified_offline_after_rate_limit(
        self, sample_earnings_article: Article, sample_ma_article: Article
    ):
        """Test that remaining candidates are classified offline once rate limit triggers offline mode."""
        call_count = 0

        def mock_rate_limit_then_pass(art: Article) -> str:
            nonlocal call_count
            call_count += 1
            raise Exception("429 RESOURCE_EXHAUSTED")

        classifier = AIArticleClassifier(mock_responder=mock_rate_limit_then_pass)

        # Article 1 encounters 429 rate limit
        res1 = classifier.classify(sample_earnings_article)
        assert res1.success is True
        assert res1.attempts == 0
        assert classifier._force_offline_mode is True

        # Article 2 automatically uses offline classification without calling Gemini
        res2 = classifier.classify(sample_ma_article)
        assert res2.success is True
        assert res2.attempts == 0
        assert res2.classification is not None
        assert res2.classification.event_type == ArticleEventType.MA

    def test_gemini_call_cap_not_exceeded(self, sample_earnings_article: Article):
        """Test that Gemini max_articles call cap is strictly enforced."""
        valid_json = json.dumps({
            "event_type": "EARNINGS",
            "company_names": ["HDFC Bank"],
            "financial_numbers": ["₹16,175 crore"],
            "percentages": ["18%"],
            "deal_value": None,
            "market_indices": [],
            "commodity_prices": [],
            "currency_values": [],
            "key_outcome": "HDFC Bank net profit rose 18%.",
            "is_hard_business_event": True,
            "has_specific_quantified_impact": True,
            "is_investment_relevant": True,
        })

        # Set max_articles = 2
        classifier = AIArticleClassifier(max_articles=2, mock_responder=lambda art: valid_json)
        # Simulate live client presence so max_articles check runs
        classifier._client = object()

        res1 = classifier.classify(sample_earnings_article)
        res2 = classifier.classify(sample_earnings_article)
        assert classifier._live_call_count == 2
        assert res1.attempts == 1
        assert res2.attempts == 1

        # 3rd and 4th calls exceed max_articles cap, using offline fallback
        res3 = classifier.classify(sample_earnings_article)
        res4 = classifier.classify(sample_earnings_article)
        assert classifier._live_call_count == 2  # Proves cap wasn't exceeded
        assert res3.attempts == 0
        assert res4.attempts == 0

