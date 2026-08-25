"""
Unit tests for Google News URL Resolver, Extraction Fallback, and Zero Story Editorial Protection.
"""

from unittest.mock import MagicMock, patch
import pytest

from app.extraction.google_news_resolver import GoogleNewsURLResolver, ResolutionResult
from app.extraction.extractor import ArticleExtractor
from app.extraction.models import ExtractionResult
from app.models.article import Article
from app.ranking.models import RankedCandidatePool
from app.ai import GeminiEditorialEngine


class TestGoogleNewsURLResolver:
    """Tests for GoogleNewsURLResolver."""

    def test_is_google_news_url(self):
        resolver = GoogleNewsURLResolver()
        assert resolver.is_google_news_url("https://news.google.com/rss/articles/CBMi123") is True
        assert resolver.is_google_news_url("https://news.google.com/articles/CBMi123") is True
        assert resolver.is_google_news_url("https://news.google.com/read/CBMi123") is True
        assert resolver.is_google_news_url("https://www.reuters.com/business/finance") is False
        assert resolver.is_google_news_url("https://www.google.com") is False

    def test_non_google_url_remains_unchanged(self):
        resolver = GoogleNewsURLResolver()
        url = "https://www.business-standard.com/article/finance/tatamotors-q1"
        res = resolver.resolve(url)
        assert res.success is True
        assert res.is_google_news is False
        assert res.resolved_url == url
        assert res.resolution_method == "passthrough"

    def test_publisher_url_validator(self):
        assert GoogleNewsURLResolver._is_valid_publisher_url(
            "https://www.gstatic.com/gnews/logo/google_news_192.png"
        ) is False
        assert GoogleNewsURLResolver._is_valid_publisher_url(
            "https://www.reuters.com/business/test-company-deal"
        ) is True

    @patch("googlenewsdecoder.gnewsdecoder")
    @patch("httpx.Client.get")
    def test_html_fallback_skips_gstatic_image(self, mock_get, mock_gnewsdecoder):
        mock_gnewsdecoder.return_value = {
            "status": True,
            "decoded_url": "https://www.gstatic.com/gnews/logo/google_news_192.png",
        }
        mock_response = MagicMock()
        mock_response.url = "https://news.google.com/rss/articles/CBMi123"
        mock_response.text = (
            '<a href="https://www.gstatic.com/gnews/logo/google_news_192.png">'
            '<a href="https://www.reuters.com/business/test-company-deal">'
        )
        mock_get.return_value = mock_response

        resolver = GoogleNewsURLResolver()
        res = resolver.resolve("https://news.google.com/rss/articles/CBMi123")

        assert res.success is True
        assert res.resolved_url == "https://www.reuters.com/business/test-company-deal"
        assert "gstatic.com" not in res.resolved_url
        assert res.resolution_method == "html_link_regex"

    @patch("googlenewsdecoder.gnewsdecoder")
    def test_resolve_google_news_url_success(self, mock_gnewsdecoder):
        mock_gnewsdecoder.return_value = {
            "status": True,
            "decoded_url": "https://www.reuters.com/business/tata-motors-results"
        }
        resolver = GoogleNewsURLResolver()
        google_url = "https://news.google.com/rss/articles/CBMi123"
        res = resolver.resolve(google_url)

        assert res.success is True
        assert res.is_google_news is True
        assert res.resolved_url == "https://www.reuters.com/business/tata-motors-results"
        assert res.resolution_method == "gnewsdecoder"

    @patch("googlenewsdecoder.gnewsdecoder")
    @patch("httpx.Client.get")
    def test_resolve_google_news_url_failure(self, mock_get, mock_gnewsdecoder):
        mock_gnewsdecoder.side_effect = Exception("Decoding error")
        mock_resp = MagicMock()
        mock_resp.url = "https://news.google.com/rss/articles/CBMi123"
        mock_resp.text = "<html><title>Google News</title></html>"
        mock_get.return_value = mock_resp

        resolver = GoogleNewsURLResolver()
        google_url = "https://news.google.com/rss/articles/CBMi123"
        res = resolver.resolve(google_url)

        assert res.success is False
        assert res.is_google_news is True
        assert "resolution failed" in (res.failure_reason or "").lower()


class TestExtractorResolutionAndFallback:
    """Tests for ArticleExtractor resolution integration and fallback."""

    def test_google_news_landing_page_rejection(self):
        extractor = ArticleExtractor()
        landing_html = "<html><head><title>Google News</title></head><body>Google News summary text</body></html>"
        res = extractor.extract_from_html(
            html=landing_html,
            url="https://news.google.com/rss/articles/CBMi123",
            candidate_title="Tata Motors Q1",
        )
        assert res.success is False
        assert "Google News landing page" in (res.error_message or "")

    def test_successful_publisher_article_extraction(self):
        extractor = ArticleExtractor()
        valid_html = """
        <html>
        <head>
            <title>Tata Motors Q1 Net Profit Surges 80% to Rs 775 Crore | Reuters</title>
            <meta name="author" content="Jane Doe" />
            <meta property="article:published_time" content="2026-08-18T10:00:00Z" />
        </head>
        <body>
            <article>
                <h1>Tata Motors Q1 Net Profit Surges 80% to Rs 775 Crore</h1>
                <p>Tata Motors reported a robust 80% jump in Q1 net profit reaching Rs 775 crore driven by strong commercial vehicle demand.</p>
                <p>Revenue grew 9% year-on-year to Rs 105,000 crore while EBITDA margins expanded 120 basis points across international markets.</p>
                <p>Management expects continued momentum in electric vehicle adoption and fleet orders for the remainder of the fiscal year.</p>
            </article>
        </body>
        </html>
        """
        res = extractor.extract_from_html(
            html=valid_html,
            url="https://www.reuters.com/business/tata-motors",
            candidate_category="India",
        )
        assert res.success is True
        assert res.article is not None
        assert "Tata Motors" in res.article.title
        assert res.word_count > 35
        assert res.extraction_method == "primary"

    def test_primary_extraction_failure_fallback_success(self):
        extractor = ArticleExtractor()
        # HTML that fails JSON-LD / container parsing but has paragraph tags
        html_with_paragraphs = """
        <html>
        <head><title>Reliance Industries Acquires Renewable Energy Firm</title></head>
        <body>
            <div>
                <p>Reliance Industries has finalized the strategic acquisition of a major renewable energy platform for $1.2 billion in cash.</p>
                <p>The transaction will add 2.5 gigawatts of operating solar assets across key industrial corridors in western India.</p>
                <p>The deal strengthens Reliance Clean Energy division towards its net zero emission targets by 2035 according to regulatory filings.</p>
            </div>
        </body>
        </html>
        """
        res = extractor.extract_from_html(
            html=html_with_paragraphs,
            url="https://www.business-standard.com/article/reliance-deal",
            candidate_title="Reliance Acquires Clean Energy Firm",
        )
        assert res.success is True
        assert res.article is not None
        assert res.word_count >= 25
        assert res.extraction_method in ("primary", "fallback")

    def test_extraction_both_fail(self):
        extractor = ArticleExtractor()
        empty_html = "<html><body><div>Short text</div></body></html>"
        res = extractor.extract_from_html(
            html=empty_html,
            url="https://www.example.com/short-page",
            candidate_title="Short Page",
        )
        assert res.success is False
        assert "could not be confirmed" in (res.error_message or "")


class TestZeroStoriesEditorialProtection:
    """Tests for Fix 5: Gemini Editorial is NOT called when zero candidate stories exist."""

    def test_zero_stories_skips_gemini_call(self):
        engine = GeminiEditorialEngine(api_key="")
        empty_pool = RankedCandidatePool(india_candidates=[], international_candidates=[])
        articles_map = {}

        with patch.object(engine, "_call_model") as mock_call:
            result = engine.select_and_synthesize_briefing(empty_pool, articles_map)
            assert result.success is False
            assert "NO_STORIES_AVAILABLE" in (result.error_message or "")
            assert result.attempts == 0
            # Confirm _call_model was NEVER invoked!
            mock_call.assert_not_called()
