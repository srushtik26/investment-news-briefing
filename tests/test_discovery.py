"""
Unit tests for News Discovery Layer.
"""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.models.enums import NewsCategory
from app.discovery.models import DiscoveredArticle
from app.discovery.queries import (
    INDIA_SOURCES,
    INTERNATIONAL_SOURCES,
    SearchQueryBuilder,
)
from app.discovery.mock_provider import MockDiscoveryProvider, SAMPLE_MOCK_ARTICLES
from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
from app.discovery.service import NewsDiscoveryService


SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <link>https://news.google.com</link>
    <item>
      <title>Tata Motors Board Approves Demerger of CV and PV Businesses - The Economic Times</title>
      <link>https://economictimes.indiatimes.com/industry/auto/tata-motors-demerger-123.cms</link>
      <pubDate>Tue, 18 Aug 2026 08:30:00 GMT</pubDate>
      <description>&lt;a href="..."&gt;Read more&lt;/a&gt;Tata Motors board has approved splitting the company into two listed entities.</description>
      <source url="https://economictimes.indiatimes.com">The Economic Times</source>
    </item>
    <item>
      <title>Nvidia Reports Q2 Record Revenue of $30B - Reuters</title>
      <link>https://www.reuters.com/technology/nvidia-earnings-report-2026/</link>
      <pubDate>Tue, 18 Aug 2026 07:00:00 GMT</pubDate>
      <description>Nvidia announced record second-quarter sales driven by AI data center demand.</description>
      <source url="https://www.reuters.com">Reuters</source>
    </item>
  </channel>
</rss>
"""


class TestDiscoveredArticleModel:
    """Tests for DiscoveredArticle schema."""

    def test_valid_discovered_article(self):
        """Test valid instantiation and field normalization."""
        article = DiscoveredArticle(
            title="Reliance Retail Launches QIP",
            url="https://economictimes.indiatimes.com/article123",
            source="The Economic Times",
            snippet="Reliance Retail opens floor price for institutional placement.",
            published_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
            search_query="QIP floor price",
            country="in",  # Tests normalization to 'India'
            category_tag="qips",
        )

        assert article.country == "India"
        assert article.title == "Reliance Retail Launches QIP"
        assert article.source == "The Economic Times"
        assert article.category_tag == "qips"

    def test_invalid_url_rejected(self):
        """Test that invalid URLs fail validation."""
        with pytest.raises(ValidationError):
            DiscoveredArticle(
                title="Invalid URL Article",
                url="not-a-valid-url",
                source="Reuters",
                search_query="earnings",
                country="International",
            )

    def test_conversion_to_article_model(self):
        """Test to_article conversion method."""
        discovered = DiscoveredArticle(
            title="L&T Bags ₹4,200 Cr Order",
            url="https://business-standard.com/order-4200",
            source="Business Standard",
            snippet="L&T hydrocarbon business secured major offshore contract.",
            published_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
            search_query="acquires acquisition deal buyout",
            country="India",
        )

        article = discovered.to_article(content_text="Full extracted text body here.")
        assert article.title == discovered.title
        assert article.url == discovered.url
        assert article.category == NewsCategory.INDIA
        assert article.source_name == "Business Standard"
        assert article.is_verified_url is True
        assert article.is_valid_date is True
        assert "Full extracted text body here." in article.content_text


class TestSearchQueryBuilder:
    """Tests for SearchQueryBuilder and category definitions."""

    def test_target_sources_configuration(self):
        """Verify all requested Indian and International publications are configured.

        Architecture change: bloomberg, reuters, ft, wsj moved to SECONDARY_SIGNALLING_SOURCES
        (paywalled — used for corroboration only, not extraction). Wire services are PRIMARY.
        """
        india_domains = [s.domain for s in INDIA_SOURCES]
        assert "economictimes.indiatimes.com" in india_domains
        assert "business-standard.com" in india_domains
        assert "livemint.com" in india_domains
        assert "financialexpress.com" in india_domains
        assert "ndtvprofit.com" in india_domains
        assert "thehindubusinessline.com" in india_domains

        intl_domains = [s.domain for s in INTERNATIONAL_SOURCES]
        # Paywalled sources are NOT in INTERNATIONAL_SOURCES (moved to SECONDARY_SIGNALLING_SOURCES)
        assert "bloomberg.com" not in intl_domains
        assert "ft.com" not in intl_domains
        assert "wsj.com" not in intl_domains
        assert "reuters.com" not in intl_domains
        # Wire services and free publishers ARE primary
        assert "cnbc.com" in intl_domains
        assert "businesswire.com" in intl_domains
        assert "globenewswire.com" in intl_domains
        assert "prnewswire.com" in intl_domains
        # Paywalled sources remain in SECONDARY_SIGNALLING_SOURCES
        from app.discovery.queries import SECONDARY_SIGNALLING_SOURCES
        signal_domains = {s.domain for s in SECONDARY_SIGNALLING_SOURCES}
        assert "bloomberg.com" in signal_domains
        assert "reuters.com" in signal_domains
        assert "ft.com" in signal_domains
        assert "wsj.com" in signal_domains

    def test_international_sources_order(self):
        """Verify international sources only contains freely extractable publishers.

        Paywalled sources (bloomberg, ft, wsj, reuters) are now in SECONDARY_SIGNALLING_SOURCES.
        """
        intl_domains = [s.domain for s in INTERNATIONAL_SOURCES]
        expected_domains = {
            "cnbc.com",
            "apnews.com",
            "bbc.com",
            "marketwatch.com",
            "theguardian.com",
            "fortune.com",
            "businesswire.com",
            "globenewswire.com",
            "prnewswire.com",
        }
        actual_set = set(intl_domains)
        # All listed domains must be free/accessible
        for dom in intl_domains:
            assert dom in expected_domains, f"Unexpected domain {dom!r} in INTERNATIONAL_SOURCES"
        # No paywalled domain may appear
        paywalled = {"bloomberg.com", "ft.com", "wsj.com", "reuters.com"}
        assert not actual_set & paywalled, f"Paywalled domains in INTERNATIONAL_SOURCES: {actual_set & paywalled}"

    def test_query_generation_with_site_filters(self):
        """Test generated queries include site filters."""
        queries = SearchQueryBuilder.build_query_strings("India", category="mergers")
        assert len(queries) > 0
        first_query = queries[0]
        assert "site:business-standard.com" in first_query
        assert "site:economictimes.indiatimes.com" in first_query

    def test_query_generation_international(self):
        """Test international query generation uses wire services (not paywalled sources)."""
        queries = SearchQueryBuilder.build_query_strings("International", category="fed_decisions")
        assert len(queries) > 0
        first_query = queries[0]
        # Wire services should appear in site constraints
        assert "site:businesswire.com" in first_query or "site:cnbc.com" in first_query or "site:apnews.com" in first_query
        # Paywalled sources must NOT be in primary site constraints
        assert "site:wsj.com" not in first_query
        assert "site:bloomberg.com" not in first_query


class TestMockDiscoveryProvider:
    """Tests for MockDiscoveryProvider."""

    def test_india_discovery(self):
        """Test discovering India candidates."""
        provider = MockDiscoveryProvider()
        results = provider.discover(
            query="merger demerger",
            country="India",
            max_results=5,
        )
        assert len(results) > 0
        assert all(r.country == "India" for r in results)
        assert any("Tata Motors" in r.title for r in results)

    def test_international_discovery(self):
        """Test discovering International candidates."""
        provider = MockDiscoveryProvider()
        results = provider.discover(
            query="earnings revenue",
            country="International",
            max_results=5,
        )
        assert len(results) > 0
        assert all(r.country == "International" for r in results)
        assert any("Nvidia" in r.title for r in results)

    def test_category_tag_filtering(self):
        """Test filtering by specific hard business category tag."""
        provider = MockDiscoveryProvider()
        results = provider.discover(
            query="",
            country="India",
            max_results=5,
            category_tag="regulatory_actions",
        )
        assert len(results) >= 1
        assert results[0].category_tag == "regulatory_actions"
        assert "RBI" in results[0].title


class TestGoogleNewsRSSProvider:
    """Tests for Google News RSS XML parser."""

    def test_parse_rss_xml(self):
        """Test parsing structured RSS feed XML."""
        provider = GoogleNewsRSSDiscoveryProvider()
        results = provider.parse_rss_feed(
            xml_content=SAMPLE_RSS_XML,
            query="business news",
            country="India",
            max_results=10,
        )

        assert len(results) == 2
        first = results[0]
        assert first.title == "Tata Motors Board Approves Demerger of CV and PV Businesses"
        assert first.source == "The Economic Times"
        assert "economictimes.indiatimes.com" in first.url
        assert first.published_at is not None
        assert "Tata Motors board has approved" in (first.snippet or "")

        second = results[1]
        assert second.title == "Nvidia Reports Q2 Record Revenue of $30B"
        assert second.source == "Reuters"

    def test_parse_empty_or_malformed_xml(self):
        """Test handling of empty or broken XML gracefully."""
        provider = GoogleNewsRSSDiscoveryProvider()
        assert provider.parse_rss_feed("", "query", "India") == []
        assert provider.parse_rss_feed("<broken>xml", "query", "India") == []


class TestNewsDiscoveryService:
    """Tests for NewsDiscoveryService orchestration and deduplication."""

    def test_service_discover_india(self):
        """Test discovering India candidates via service."""
        service = NewsDiscoveryService(provider=MockDiscoveryProvider())
        candidates = service.discover_india_news(max_candidates=10)
        assert len(candidates) > 0
        assert all(c.country == "India" for c in candidates)

    def test_service_discover_international(self):
        """Test discovering International candidates via service."""
        service = NewsDiscoveryService(provider=MockDiscoveryProvider())
        candidates = service.discover_international_news(max_candidates=10)
        assert len(candidates) > 0
        assert all(c.country == "International" for c in candidates)

    def test_service_discover_all(self):
        """Test discover_all returning grouped dictionary."""
        service = NewsDiscoveryService(provider=MockDiscoveryProvider())
        results = service.discover_all(max_india=5, max_international=5)

        assert "india" in results
        assert "international" in results
        assert len(results["india"]) > 0
        assert len(results["international"]) > 0

    def test_url_deduplication(self):
        """Test deterministic URL deduplication across searches."""
        duplicate_article = SAMPLE_MOCK_ARTICLES[0].model_copy()
        custom_articles = [SAMPLE_MOCK_ARTICLES[0], duplicate_article, SAMPLE_MOCK_ARTICLES[1]]

        provider = MockDiscoveryProvider(custom_articles=custom_articles)
        service = NewsDiscoveryService(provider=provider)

        deduped = service._deduplicate_candidates(custom_articles)
        assert len(deduped) == 2
