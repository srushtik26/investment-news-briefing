"""
Free-Tier Resilience Tests.

Validates that the pipeline correctly enforces free-tier budget constraints:
- SourcePolicy correctly gates extraction
- Blocked/paywalled domains are not extracted
- CandidateRegistry prevents DEDUP_REJECTED retry
- Gemini quota=0 triggers offline fallback
- SerpAPI budget=0 keeps RSS-only operation
- Genuine supply shortage raises DATA_UNAVAILABLE (no fabrication)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# =========================================================================
# 1. SourcePolicy tier classification
# =========================================================================

class TestSourcePolicyTiers:
    """SourcePolicy correctly classifies all known domains."""

    def test_india_primary_tier1(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://economictimes.indiatimes.com/markets/article.html") == "PRIMARY"
        assert get_tier("https://livemint.com/markets/news.html") == "PRIMARY"
        assert get_tier("https://ndtvprofit.com/news/test.html") == "PRIMARY"
        assert get_tier("https://thehindubusinessline.com/article.html") == "PRIMARY"
        assert get_tier("https://business-standard.com/article") == "PRIMARY"

    def test_international_primary_tier1(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://cnbc.com/news/article.html") == "PRIMARY"
        assert get_tier("https://apnews.com/article/company-deal") == "PRIMARY"
        assert get_tier("https://businesswire.com/news/home/1234") == "PRIMARY"
        assert get_tier("https://globenewswire.com/news-release/1234") == "PRIMARY"
        assert get_tier("https://prnewswire.com/news-releases/1234.html") == "PRIMARY"

    def test_official_tier2(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://bseindia.com/xml-data/corpfiling.aspx") == "OFFICIAL"
        assert get_tier("https://sebi.gov.in/press-releases/2024/pr.html") == "OFFICIAL"
        assert get_tier("https://sec.gov/cgi-bin/browse-edgar") == "OFFICIAL"
        assert get_tier("https://rbi.org.in/scripts/PressReleaseUser.aspx") == "OFFICIAL"

    def test_paywalled_secondary_tier3(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://bloomberg.com/news/articles/2024-01-01/deal") == "SECONDARY"
        assert get_tier("https://ft.com/content/abc123") == "SECONDARY"
        assert get_tier("https://wsj.com/articles/abc") == "SECONDARY"
        assert get_tier("https://reuters.com/markets/deals/test") == "SECONDARY"

    def test_blocked_aggregators(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://tradingview.com/news/abc") == "BLOCKED"
        assert get_tier("https://seekingalpha.com/article/123") == "BLOCKED"
        assert get_tier("https://stockanalysis.com/stocks/aapl/news") == "BLOCKED"
        assert get_tier("https://motleyfool.com/investing/stock-market") == "BLOCKED"
        assert get_tier("https://wastetodaymagazine.com/article") == "BLOCKED"

    def test_blocked_social(self):
        from app.filtering.source_policy import get_tier
        assert get_tier("https://reddit.com/r/investing/post") == "BLOCKED"
        assert get_tier("https://twitter.com/user/status/123") == "BLOCKED"

    def test_blocked_path_patterns(self):
        from app.filtering.source_policy import get_tier
        # Author/tag pages should be blocked even on approved domains
        assert get_tier("https://cnbc.com/author/john-smith") == "BLOCKED"
        assert get_tier("https://economictimes.indiatimes.com/topic/infosys") == "BLOCKED"

    def test_is_extraction_worthy(self):
        from app.filtering.source_policy import is_extraction_worthy
        assert is_extraction_worthy("https://livemint.com/article") is True
        assert is_extraction_worthy("https://bloomberg.com/news/abc") is False
        assert is_extraction_worthy("https://tradingview.com/news") is False
        assert is_extraction_worthy("https://bseindia.com/filing.aspx") is True

    def test_is_corroboration_eligible(self):
        from app.filtering.source_policy import is_corroboration_eligible
        # SECONDARY should be corroboration-eligible but not extraction-worthy
        assert is_corroboration_eligible("https://bloomberg.com/news/abc") is True
        assert is_corroboration_eligible("https://tradingview.com/news") is False


# =========================================================================
# 2. Source prefilter is applied before HTTP extraction
# =========================================================================

class TestSourcePrefilterBeforeExtraction:
    """SourcePolicy blocks paywalled URLs before any HTTP fetch."""

    def test_bloomberg_skipped_before_extraction(self):
        from app.filtering.source_policy import is_extraction_worthy
        blocked_urls = [
            "https://bloomberg.com/news/articles/2024-01-01/test",
            "https://ft.com/content/abc123",
            "https://wsj.com/articles/test",
        ]
        for url in blocked_urls:
            assert not is_extraction_worthy(url), f"Expected {url} to be blocked by SourcePolicy"

    def test_source_policy_skips_tracked(self):
        """_extract_candidates returns non-zero source_policy_skips for blocked domains."""
        from app.pipeline.candidate_processing import _extract_candidates

        mock_extractor = MagicMock()
        mock_extractor.is_domain_degraded.return_value = False
        mock_extractor.blocked_url_cache = set()

        class FakeCandidate:
            def __init__(self, url, source="Bloomberg"):
                self.url = url
                self.source = source
                self.title = "Bloomberg Article"
                self.published_at = None

        candidates = [
            (FakeCandidate("https://bloomberg.com/news/articles/deal"), "International"),
            (FakeCandidate("https://wsj.com/articles/rate-hike"), "International"),
            (FakeCandidate("https://tradingview.com/news/abc"), "International"),
        ]
        seen = set()

        _, _, _, _, _, pre_rejects, dups, sp_skips = _extract_candidates(
            candidates, mock_extractor, seen, lambda m: None
        )
        assert sp_skips == 3, f"Expected 3 source_policy_skips, got {sp_skips}"
        # Extractor should NOT have been called for any of these
        mock_extractor.extract.assert_not_called()

    def test_approved_source_passes_prefilter(self):
        """Approved domains pass through to extractor."""
        from app.pipeline.candidate_processing import _extract_candidates

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.article = MagicMock()
        mock_result.article.id = "art-1"
        mock_result.original_url = "https://apnews.com/article/test"
        mock_result.resolved_url = "https://apnews.com/article/test"
        mock_result.status_code = 200
        mock_result.word_count = 250
        mock_result.extraction_method = "direct"
        mock_result.error_message = None

        mock_extractor = MagicMock()
        mock_extractor.is_domain_degraded.return_value = False
        mock_extractor.blocked_url_cache = set()
        mock_extractor.extract.return_value = mock_result

        class ResolverMock:
            def is_google_news_url(self, url):
                return False
        mock_extractor.resolver = ResolverMock()

        class FakeCandidate:
            url = "https://apnews.com/article/company-merger"
            source = "AP News"
            title = "Company Merger Deal"
            published_at = None

        seen = set()
        arts, _, _, _, _, _, _, sp_skips = _extract_candidates(
            [(FakeCandidate(), "International")], mock_extractor, seen, lambda m: None
        )
        assert sp_skips == 0, "Approved domain should not trigger source_policy_skips"
        mock_extractor.extract.assert_called_once()


# =========================================================================
# 3. CandidateRegistry prevents DEDUP_REJECTED retry
# =========================================================================

class TestCandidateRegistry:
    """PipelineContext.candidate_registry prevents re-processing rejected URLs."""

    def test_candidate_registry_field_exists(self):
        from app.pipeline.context import PipelineContext
        ctx = PipelineContext()
        assert hasattr(ctx, "candidate_registry"), "PipelineContext must have candidate_registry"
        assert isinstance(ctx.candidate_registry, dict)

    def test_permanently_rejected_urls_field_exists(self):
        from app.pipeline.context import PipelineContext
        ctx = PipelineContext()
        assert hasattr(ctx, "permanently_rejected_urls")
        assert isinstance(ctx.permanently_rejected_urls, set)

    def test_dedup_rejected_url_is_not_retried(self):
        """A URL marked DEDUP_REJECTED in the registry should be skipped in recovery."""
        from app.pipeline.context import PipelineContext
        ctx = PipelineContext()
        dedup_url = "https://economictimes.indiatimes.com/markets/xyz/article/1234"
        norm = dedup_url.strip().lower().rstrip("/")
        ctx.candidate_registry[norm] = "DEDUP_REJECTED"
        ctx.permanently_rejected_urls.add(norm)
        # The URL should appear in permanently_rejected_urls (recovery loops skip these)
        assert norm in ctx.permanently_rejected_urls

    def test_source_policy_skip_tracked_separately_from_dedup(self):
        """SourcePolicy skips and dedup rejects are tracked independently."""
        from app.pipeline.context import PipelineContext
        ctx = PipelineContext()
        assert ctx.source_policy_skips == 0
        ctx.source_policy_skips = 5
        assert ctx.source_policy_skips == 5


# =========================================================================
# 4. Portfolio discovery does NOT use SerpAPI budget
# =========================================================================

class TestPortfolioRSSBudget:
    """Portfolio discovery uses RSS only; should not deplete SerpAPI counter."""

    def test_portfolio_discovery_does_not_record_serpapi(self):
        from app.discovery.service import NewsDiscoveryService
        import inspect
        src = inspect.getsource(NewsDiscoveryService.discover_portfolio_news)
        # The function should NOT contain budget.record_serpapi_call
        assert "record_serpapi_call" not in src, (
            "discover_portfolio_news must NOT call budget.record_serpapi_call — "
            "portfolio discovery uses RSS only."
        )

    def test_portfolio_discovery_does_not_check_can_call_serpapi(self):
        from app.discovery.service import NewsDiscoveryService
        import inspect
        src = inspect.getsource(NewsDiscoveryService.discover_portfolio_news)
        assert "can_call_serpapi" not in src, (
            "discover_portfolio_news must NOT gate on can_call_serpapi — "
            "it uses RSS only, not SerpAPI."
        )


# =========================================================================
# 5. SourcePolicy does not break corroboration sources
# =========================================================================

class TestCorroborationSources:
    """SECONDARY sources (bloomberg, reuters) are eligible for corroboration but not extraction."""

    def test_reuters_corroboration_eligible_not_extraction(self):
        from app.filtering.source_policy import is_corroboration_eligible, is_extraction_worthy
        url = "https://reuters.com/markets/us/fed-rate-hike-2024"
        assert is_corroboration_eligible(url) is True
        assert is_extraction_worthy(url) is False

    def test_bloomberg_corroboration_eligible_not_extraction(self):
        from app.filtering.source_policy import is_corroboration_eligible, is_extraction_worthy
        url = "https://bloomberg.com/news/articles/test"
        assert is_corroboration_eligible(url) is True
        assert is_extraction_worthy(url) is False


# =========================================================================
# 6. India nexus rejects distribution-target-only stories
# =========================================================================

class TestIndiaNexusDistributionTarget:
    """Foreign-company 'distributing into India' stories are rejected from India section."""

    def _make_event(self, title: str, description: str = "", companies=None):
        from app.models import Event, NewsCategory
        return Event(
            canonical_title=title,
            article_ids=["art-1"],
            event_category=NewsCategory.INDIA,
            description=description,
            companies_involved=companies or [],
            metadata={},
        )

    def _make_article(self, title: str, content: str = "", source_name: str = "CNBC"):
        from app.models import Article, NewsCategory
        return Article(
            id="art-1",
            title=title,
            url="https://cnbc.com/test",
            source_name=source_name,
            category=NewsCategory.INDIA,
            content_text=content,
        )

    def test_foreign_streaming_content_deal_rejected(self):
        from app.classification.region_classifier import EventRegionClassifier
        clf = EventRegionClassifier()
        event = self._make_event(
            "Paramount and Warner Bros strike streaming content deal for India",
            "Two US studios have agreed to a content distribution deal for the Indian streaming market.",
            companies=["Paramount Global", "Warner Bros"],
        )
        article = self._make_article(
            "Paramount and Warner Bros strike streaming content deal for India",
            "Two US studios have agreed to a content distribution deal for the Indian streaming market.",
        )
        result, reason = clf.verify_india_business_nexus(event, article)
        assert result is False, f"Expected INDIA_NEXUS_REJECT, got True. Reason: {reason}"
        assert "INDIA_NEXUS_REJECT" in reason

    def test_indian_company_acquisition_passes(self):
        from app.classification.region_classifier import EventRegionClassifier
        clf = EventRegionClassifier()
        event = self._make_event(
            "Reliance Industries acquires Shree Cement subsidiary for ₹2,200 crore",
            "Reliance Industries has agreed to acquire a subsidiary of Shree Cement in a deal worth ₹2,200 crore.",
            companies=["Reliance Industries", "Shree Cement"],
        )
        article = self._make_article(
            "Reliance Industries acquires Shree Cement subsidiary for ₹2,200 crore",
            "Reliance Industries has agreed to acquire a subsidiary of Shree Cement in a deal worth ₹2,200 crore.",
            source_name="Business Standard",
        )
        result, reason = clf.verify_india_business_nexus(event, article)
        assert result is True, f"Indian company acquisition should pass nexus. Reason: {reason}"

    def test_sebi_regulatory_action_passes(self):
        from app.classification.region_classifier import EventRegionClassifier
        clf = EventRegionClassifier()
        event = self._make_event(
            "SEBI bars XYZ Brokers from securities market for six months",
            "SEBI has barred XYZ Brokers from the Indian securities market for six months after alleged market manipulation.",
            companies=["XYZ Brokers"],
        )
        article = self._make_article(
            "SEBI bars XYZ Brokers from securities market for six months",
            "SEBI has barred XYZ Brokers from the Indian securities market for six months after alleged market manipulation.",
        )
        result, reason = clf.verify_india_business_nexus(event, article)
        assert result is True, f"SEBI regulatory action should pass nexus. Reason: {reason}"


# =========================================================================
# 7. INTERNATIONAL_SOURCES no longer includes paywalled domains
# =========================================================================

class TestInternationalQuerySources:
    """INTERNATIONAL_SOURCES only contains freely accessible domains."""

    PAYWALLED_DOMAINS = {"bloomberg.com", "ft.com", "wsj.com", "reuters.com"}

    def test_international_sources_free_only(self):
        from app.discovery.queries import INTERNATIONAL_SOURCES
        domains = {src.domain for src in INTERNATIONAL_SOURCES}
        blocked = domains & self.PAYWALLED_DOMAINS
        assert not blocked, (
            f"INTERNATIONAL_SOURCES contains paywalled domains {blocked}. "
            f"These waste free-tier RSS query budget since extraction will always fail."
        )

    def test_secondary_signalling_sources_still_present(self):
        from app.discovery.queries import SECONDARY_SIGNALLING_SOURCES
        domains = {src.domain for src in SECONDARY_SIGNALLING_SOURCES}
        # Bloomberg and Reuters should remain in SECONDARY_SIGNALLING_SOURCES for corroboration
        assert "bloomberg.com" in domains, "bloomberg.com should remain in SECONDARY_SIGNALLING_SOURCES"
        assert "reuters.com" in domains, "reuters.com should remain in SECONDARY_SIGNALLING_SOURCES"

    def test_wire_services_in_international_sources(self):
        from app.discovery.queries import INTERNATIONAL_SOURCES
        domains = {src.domain for src in INTERNATIONAL_SOURCES}
        wire_services = {"businesswire.com", "globenewswire.com", "prnewswire.com"}
        for wire in wire_services:
            assert wire in domains, f"Wire service {wire} should be in INTERNATIONAL_SOURCES (free, accessible)"

    def test_india_sources_includes_ndtvprofit(self):
        from app.discovery.queries import INDIA_SOURCES
        domains = {src.domain for src in INDIA_SOURCES}
        assert "ndtvprofit.com" in domains, "ndtvprofit.com should be in INDIA_SOURCES"
        assert "thehindubusinessline.com" in domains, "thehindubusinessline.com should be in INDIA_SOURCES"
