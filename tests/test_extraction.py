"""
Unit tests for Article Extraction Layer with saved HTML fixtures.
"""

from datetime import datetime, timezone
import pytest

from app.extraction.extractor import ArticleExtractor
from app.extraction.html_parser import HTMLArticleParser
from app.extraction.http_client import ArticleFetcher
from app.extraction.models import ExtractionResult
from app.models.enums import NewsCategory


# --- HTML FIXTURES ---

FIXTURE_JSON_LD_ARTICLE = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Tata Motors Board Approves Demerger - The Economic Times</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Tata Motors Board Approves Demerger of CV and PV Businesses",
        "datePublished": "2026-08-18T08:30:00+05:30",
        "dateModified": "2026-08-18T09:00:00+05:30",
        "author": {
            "@type": "Person",
            "name": "Nandini Sengupta"
        },
        "publisher": {
            "@type": "Organization",
            "name": "The Economic Times"
        },
        "description": "Tata Motors has approved splitting into two listed entities."
    }
    </script>
</head>
<body>
    <header><nav>Navigation Menu</nav></header>
    <article class="article-body">
        <p>Tata Motors on Tuesday approved the demerger of its commercial vehicles and passenger vehicles into two separate listed entities.</p>
        <p>The decision was taken at a board meeting held earlier today. The demerger will be implemented through an NCLT scheme of arrangement.</p>
        <p>All shareholders of Tata Motors will continue to have identical shareholding in both the listed entities following the completion of the restructuring.</p>
        <p>The company noted that the move will empower each business unit to pursue independent growth strategies and unlock substantial shareholder value.</p>
    </article>
    <footer>Copyright 2026. All rights reserved.</footer>
</body>
</html>
"""

FIXTURE_OPENGRAPH_META_ARTICLE = """<!DOCTYPE html>
<html>
<head>
    <meta property="og:title" content="Nvidia Q2 Revenue Surges 122% to $30 Billion on AI Demand" />
    <meta property="og:description" content="Nvidia reported second-quarter earnings that beat consensus estimates on strong data center revenue." />
    <meta property="article:published_time" content="2026-08-18T07:00:00Z" />
    <meta property="article:author" content="Stephen Nellis" />
    <meta property="og:site_name" content="Reuters" />
</head>
<body>
    <div class="story-content">
        <p>Nvidia reported second-quarter revenue of $30.04 billion on Wednesday, exceeding Wall Street projections driven by relentless demand for AI accelerators.</p>
        <p>The company's data center division posted revenue of $26.3 billion, representing a 154% increase compared to the corresponding period last fiscal year.</p>
        <p>Net income jumped to $16.6 billion, or 67 cents per share, compared with $6.18 billion in the year-ago quarter.</p>
        <p>Chief Executive Jensen Huang affirmed that demand for upcoming Blackwell architecture chips remains exceptionally strong across cloud service providers.</p>
    </div>
</body>
</html>
"""

FIXTURE_TIME_TAG_ARTICLE = """<!DOCTYPE html>
<html>
<head>
    <title>HDFC Bank Reports Robust First Quarter Growth</title>
</head>
<body>
    <h1>HDFC Bank Net Profit Rises 18% in Q1</h1>
    <div class="byline">By Saloni Shukla</div>
    <time datetime="2026-08-18T09:15:00Z">August 18, 2026</time>
    <main>
        <p>HDFC Bank reported an 18 percent year-on-year increase in its net profit for the quarter ended June 30, supported by steady core net interest income growth.</p>
        <p>The country's largest private sector lender stated that asset quality remained stable with gross non-performing assets standing at 1.33 percent.</p>
        <p>Total advances grew 14.8 percent over the previous year, while retail deposits continued to show healthy traction across domestic branches.</p>
        <p>Management indicated that credit costs are expected to stay well within guided ranges over the remainder of the fiscal year.</p>
    </main>
</body>
</html>
"""

FIXTURE_NO_DATE_ARTICLE = """<!DOCTYPE html>
<html>
<head>
    <title>L&T Secures Major Engineering Contract</title>
</head>
<body>
    <h1>Larsen & Toubro Hydrocarbon Bags ₹4,200 Crore Order</h1>
    <article>
        <p>Larsen and Toubro's hydrocarbon business has secured a significant offshore engineering and procurement contract valued at over ₹4,200 crore.</p>
        <p>The scope of work comprises engineering, procurement, construction, and installation of offshore gas processing platforms and subsea pipelines.</p>
        <p>The company stated that this project further strengthens its execution credentials across the international energy infrastructure corridor.</p>
        <p>Execution is slated to take place over a 36-month timeline with phased commissioning milestones.</p>
    </article>
</body>
</html>
"""

FIXTURE_NON_ARTICLE_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>404 Page Not Found - Business Daily</title>
</head>
<body>
    <header><nav><a href="/">Home</a> | <a href="/markets">Markets</a></nav></header>
    <h1>Error 404: The requested page could not be located</h1>
    <p>Please check the URL and try again.</p>
    <footer>Help & Support</footer>
</body>
</html>
"""


class TestHTMLArticleParser:
    """Tests for HTMLArticleParser extraction logic."""

    def test_json_ld_extraction(self):
        """Test extraction from JSON-LD schema markup."""
        parser = HTMLArticleParser()
        result = parser.parse(
            html=FIXTURE_JSON_LD_ARTICLE,
            url="https://economictimes.indiatimes.com/tata-demerger",
        )

        assert result.is_article is True
        assert result.title == "Tata Motors Board Approves Demerger of CV and PV Businesses"
        assert result.author == "Nandini Sengupta"
        assert result.source_name == "The Economic Times"
        assert result.date_verified is True
        assert result.published_at is not None
        assert result.published_at.year == 2026
        assert result.published_at.month == 8
        assert "commercial vehicles and passenger vehicles" in result.content_text
        assert result.word_count > 40

    def test_opengraph_extraction(self):
        """Test extraction from OpenGraph and meta tags."""
        parser = HTMLArticleParser()
        result = parser.parse(
            html=FIXTURE_OPENGRAPH_META_ARTICLE,
            url="https://www.reuters.com/nvidia-q2",
        )

        assert result.is_article is True
        assert "Nvidia Q2 Revenue Surges 122%" in result.title
        assert result.author == "Stephen Nellis"
        assert result.source_name == "Reuters"
        assert result.date_verified is True
        assert result.published_at == datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc)
        assert "$30.04 billion" in result.content_text

    def test_time_tag_extraction(self):
        """Test extraction using HTML5 time tag and byline."""
        parser = HTMLArticleParser()
        result = parser.parse(
            html=FIXTURE_TIME_TAG_ARTICLE,
            url="https://www.livemint.com/hdfc-bank-results",
            fallback_source="Livemint",
        )

        assert result.is_article is True
        assert "HDFC Bank" in result.title
        assert result.author == "Saloni Shukla"
        assert result.date_verified is True
        assert result.published_at == datetime(2026, 8, 18, 9, 15, tzinfo=timezone.utc)
        assert result.source_name == "Livemint"

    def test_no_date_article_sets_date_verified_false(self):
        """Test that missing publication date marks date_verified=False without guessing."""
        parser = HTMLArticleParser()
        result = parser.parse(
            html=FIXTURE_NO_DATE_ARTICLE,
            url="https://www.business-standard.com/lt-order",
            fallback_source="Business Standard",
        )

        assert result.is_article is True
        assert result.published_at is None
        assert result.date_verified is False  # Never guessed!
        assert "Larsen & Toubro" in result.title

    def test_non_article_rejected(self):
        """Test that 404 or minimal navigation pages are rejected as non-articles."""
        parser = HTMLArticleParser()
        result = parser.parse(
            html=FIXTURE_NON_ARTICLE_PAGE,
            url="https://www.example.com/not-found",
        )

        assert result.is_article is False
        assert result.word_count < 35


class TestArticleExtractor:
    """Tests for ArticleExtractor coordinator service."""

    def test_extract_from_html_success(self):
        """Test complete extraction returning valid Article model."""
        extractor = ArticleExtractor()
        url = "https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger/108192301.cms"

        result: ExtractionResult = extractor.extract_from_html(
            html=FIXTURE_JSON_LD_ARTICLE,
            url=url,
            candidate_category="India",
        )

        assert result.success is True
        assert result.article is not None
        assert result.url == url  # Exact URL preserved
        assert result.article.url == url
        assert result.article.category == NewsCategory.INDIA
        assert result.article.author == "Nandini Sengupta"
        assert result.article.source_name == "The Economic Times"
        assert result.date_verified is True
        assert result.article.is_verified_url is True
        assert result.article.is_valid_date is True

    def test_extract_from_html_non_article_failure(self):
        """Test that non-article page returns extraction failure result."""
        extractor = ArticleExtractor()
        url = "https://example.com/404-page"

        result: ExtractionResult = extractor.extract_from_html(
            html=FIXTURE_NON_ARTICLE_PAGE,
            url=url,
        )

        assert result.success is False
        assert result.article is None
        assert result.url == url
        assert "could not be confirmed as a valid article" in (result.error_message or "")


class TestArticleFetcher:
    """Tests for ArticleFetcher client and retry handling."""

    def test_invalid_scheme_rejected(self):
        """Test non-HTTP URL schemes are immediately rejected."""
        fetcher = ArticleFetcher()
        success, html, status, err = fetcher.fetch_html("ftp://invalid.com/file.html")
        assert success is False
        assert "Invalid URL scheme" in (err or "")

    def test_fetch_with_mocked_network_error(self, monkeypatch):
        """Test fetcher retry loop on network exception."""
        import httpx

        def mock_get(*args, **kwargs):
            raise httpx.ConnectError("Simulated connection failure")

        monkeypatch.setattr(httpx.Client, "get", mock_get)

        fetcher = ArticleFetcher(max_retries=2, backoff_factor=0.01)
        success, html, status, err = fetcher.fetch_html("https://example.com/test-article")

        assert success is False
        assert html is None
        assert "Network connection error" in (err or "")
