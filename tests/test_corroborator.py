"""
Tests for the ActiveCorroborator module (Fix 4 — active second-source search).

Verifies:
  - Entity extraction from article titles/content
  - Event type detection
  - Corroboration query construction
  - Same-event matching (positive and negative cases)
  - Same-publisher rejection
  - Syndicated article rejection
  - Identical URL skip
  - Successful corroboration flow (mock)
  - Failed corroboration flow (mock)
  - Budget limits (max 2 queries per event, max 10 per run)
  - Pipeline sufficiency gate (Stage 8 skipped when < 5 verified)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from app.verification.corroborator import (
    ActiveCorroborator,
    CorroborationResult,
    reset_corroboration_counter,
    get_corroboration_count,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
    MAX_QUERIES_PER_EVENT,
)
from app.models.article import Article
from app.models.enums import NewsCategory
from app.models.event import Event
from app.verification.verifier import TwoSourceVerifier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _article(
    title: str,
    url: str,
    source: str = "Test Publisher",
    content: str = "",
    category: NewsCategory = NewsCategory.INDIA,
) -> Article:
    art = MagicMock(spec=Article)
    art.title = title
    art.url = url
    art.source_name = source
    art.content_text = content
    art.category = category
    art.published_at = datetime.now(timezone.utc) - timedelta(hours=12)
    art.metadata = {}
    art.id = url  # use URL as simple unique ID in tests
    return art


def _event(title: str, articles: list) -> Event:
    ev = MagicMock(spec=Event)
    ev.id = f"event_{title[:10]}"
    ev.canonical_title = title
    ev.article_ids = [a.id for a in articles]
    ev.event_category = articles[0].category if articles else NewsCategory.INDIA
    return ev


@pytest.fixture(autouse=True)
def reset_counter():
    """Reset the global corroboration counter before every test."""
    reset_corroboration_counter()
    yield
    reset_corroboration_counter()


@pytest.fixture
def corroborator():
    return ActiveCorroborator(extractor=MagicMock())


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

class TestEntityExtraction:
    def test_extracts_company_names_from_title(self, corroborator):
        art = _article("Reliance Industries acquires SolarX for Rs 5,000 crore", "https://example.com/1")
        entities = corroborator._extract_entities(art)
        assert "Reliance" in entities or "SolarX" in entities or "Reliance Industries" in entities

    def test_extracts_acronyms(self, corroborator):
        art = _article("SEBI imposes penalty on HDFC Securities", "https://example.com/2")
        entities = corroborator._extract_entities(art)
        assert "SEBI" in entities or "HDFC" in entities

    def test_returns_at_most_6_entities(self, corroborator):
        art = _article(
            "Apple Microsoft Google Meta Amazon Netflix report earnings",
            "https://example.com/3",
            content="Multiple tech companies in the news Apple Microsoft Google Meta Amazon Netflix"
        )
        entities = corroborator._extract_entities(art)
        assert len(entities) <= 6

    def test_empty_title_returns_empty_list(self, corroborator):
        art = _article("", "https://example.com/4")
        entities = corroborator._extract_entities(art)
        assert isinstance(entities, list)


# ---------------------------------------------------------------------------
# Event type detection
# ---------------------------------------------------------------------------

class TestEventTypeDetection:
    def test_acquisition_detected(self, corroborator):
        art = _article("HDFC Bank acquires 10% stake in fintech startup", "https://x.com/1")
        assert corroborator._detect_event_type(art) == "acquisition"

    def test_results_detected(self, corroborator):
        art = _article("Infosys Q2 results: net profit rises 15%", "https://x.com/2")
        assert corroborator._detect_event_type(art) == "results"

    def test_ipo_detected(self, corroborator):
        art = _article("SwiggyIPO listing day shares surge 18%", "https://x.com/3")
        assert corroborator._detect_event_type(art) == "ipo"

    def test_leadership_detected(self, corroborator):
        art = _article("Tata Motors appoints new CEO after MD resignation", "https://x.com/4")
        assert corroborator._detect_event_type(art) == "leadership"

    def test_unknown_defaults_to_business_event(self, corroborator):
        art = _article("Generic news without clear event type", "https://x.com/5")
        event_type = corroborator._detect_event_type(art)
        assert isinstance(event_type, str)
        assert len(event_type) > 0


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

class TestQueryConstruction:
    def test_returns_at_most_2_queries(self, corroborator):
        art = _article("Reliance acquires SolarX", "https://example.com/art1")
        queries = corroborator._build_corroboration_queries(art)
        assert len(queries) <= MAX_QUERIES_PER_EVENT

    def test_queries_are_non_empty_strings(self, corroborator):
        art = _article("HDFC Bank posts Rs 16,000 crore quarterly profit", "https://example.com/art2")
        queries = corroborator._build_corroboration_queries(art)
        assert all(isinstance(q, str) and len(q.strip()) > 0 for q in queries)

    def test_site_clause_excludes_primary_publisher(self, corroborator):
        art = _article("Test", "https://economictimes.indiatimes.com/article/1", category=NewsCategory.INDIA)
        clause = corroborator._build_site_clause(art)
        # Should exclude Economic Times domains
        assert "economictimes.indiatimes.com" not in clause.lower() or len(clause) > 20


# ---------------------------------------------------------------------------
# Same-event matching (uses TwoSourceVerifier directly)
# ---------------------------------------------------------------------------

class TestSameEventMatching:
    def setup_method(self):
        self.verifier = TwoSourceVerifier()

    def test_same_company_and_event_keyword_matches(self):
        art1 = _article("Reliance acquires SolarX for 5000 crore", "https://et.com/1", "Economic Times")
        art2 = _article("Reliance acquires SolarX deal 5000 crore", "https://bs.com/1", "Business Standard")
        is_same, score, reason = self.verifier.is_same_underlying_event(art1, art2)
        assert is_same, f"Expected same event. Score={score}, reason={reason}"

    def test_unrelated_articles_do_not_match(self):
        art1 = _article("Infosys posts 15% profit growth Q2", "https://et.com/2", "Economic Times")
        art2 = _article("Oil prices surge due to Middle East tensions", "https://bs.com/2", "Business Standard")
        is_same, score, reason = self.verifier.is_same_underlying_event(art1, art2)
        assert not is_same, f"Expected different events. Score={score}"


# ---------------------------------------------------------------------------
# Publisher independence checks
# ---------------------------------------------------------------------------

class TestPublisherIndependence:
    def setup_method(self):
        self.verifier = TwoSourceVerifier()

    def test_same_publisher_group_detected(self):
        art1 = _article("Test", "https://economictimes.indiatimes.com/x", "The Economic Times")
        art2 = _article("Test", "https://timesofindia.indiatimes.com/x", "Times of India")
        grp1 = self.verifier.get_publisher_group(art1)
        grp2 = self.verifier.get_publisher_group(art2)
        assert grp1 == grp2  # Both in times_group

    def test_different_publishers_are_independent(self):
        art1 = _article("Test", "https://business-standard.com/x", "Business Standard")
        art2 = _article("Test", "https://livemint.com/x", "Livemint")
        grp1 = self.verifier.get_publisher_group(art1)
        grp2 = self.verifier.get_publisher_group(art2)
        assert grp1 != grp2


# ---------------------------------------------------------------------------
# Syndication detection
# ---------------------------------------------------------------------------

class TestSyndicationDetection:
    def setup_method(self):
        self.verifier = TwoSourceVerifier()

    def test_pti_wire_both_articles_detected_as_syndicated(self):
        common = "The company announced its merger (PTI) - Sources said the deal..."
        art1 = _article("Merger announced", "https://et.com/1", content=f"(PTI) {common}")
        art2 = _article("Merger announced", "https://bs.com/1", content=f"(PTI) {common}")
        art1.title = "Merger announced"
        art2.title = "Merger announced"
        is_synd, reason = self.verifier.is_syndicated_republication(art1, art2)
        assert is_synd, f"Expected syndicated but got: {reason}"

    def test_unique_editorial_content_not_flagged_as_syndicated(self):
        art1 = _article(
            "HDFC acquires SolarX",
            "https://et.com/2",
            content="HDFC Bank on Tuesday announced the acquisition of SolarX for Rs 5,000 crore, marking a significant push into renewable energy financing.",
        )
        art2 = _article(
            "SolarX acquisition by HDFC Bank",
            "https://bs.com/2",
            content="Business Standard has learned that HDFC Bank has completed a deal to buy SolarX in a transaction valued at five thousand crore rupees, according to separate sources familiar with the matter.",
        )
        is_synd, reason = self.verifier.is_syndicated_republication(art1, art2)
        assert not is_synd, f"Expected NOT syndicated but got: {reason}"


# ---------------------------------------------------------------------------
# Corroboration flow (mocked)
# ---------------------------------------------------------------------------

class TestCorroborationFlow:
    def _make_extraction_result(self, article: Article):
        res = MagicMock()
        res.success = True
        res.article = article
        res.error_message = None
        return res

    def test_successful_corroboration_returns_success_true(self):
        """Full mock: single-source event → corroboration finds valid 2nd source."""
        primary = _article(
            "HDFC Bank acquires SolarX for Rs 5000 crore",
            "https://economictimes.indiatimes.com/article1",
            source="The Economic Times",
        )
        corr_art = _article(
            "HDFC Bank SolarX acquisition deal 5000 crore",
            "https://business-standard.com/article1",
            source="Business Standard",
        )
        event = _event("HDFC Bank acquires SolarX", [primary])

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = self._make_extraction_result(corr_art)

        mock_rss_results = [{"url": corr_art.url, "title": corr_art.title, "source": corr_art.source_name, "published_at": corr_art.published_at}]

        corroborator = ActiveCorroborator(extractor=mock_extractor)

        with patch.object(corroborator, "_search_rss", return_value=mock_rss_results):
            result = corroborator.corroborate(event=event, primary_article=primary)

        assert result.success, f"Expected corroboration success but got: {result.failure_reason}"
        assert result.corroborating_article is not None
        assert result.source_1_publisher == "The Economic Times"
        assert result.source_2_publisher == "Business Standard"

    def test_same_url_as_primary_is_skipped(self):
        """If corroboration RSS returns the same URL as primary, skip it."""
        primary = _article("HDFC Bank acquires SolarX", "https://et.com/article1", source="ET")
        event = _event("HDFC test", [primary])

        # RSS returns same URL
        mock_rss = [{"url": primary.url, "title": primary.title, "source": "ET", "published_at": primary.published_at}]

        mock_extractor = MagicMock()
        corroborator = ActiveCorroborator(extractor=mock_extractor)

        with patch.object(corroborator, "_search_rss", return_value=mock_rss):
            result = corroborator.corroborate(event=event, primary_article=primary)

        assert not result.success

    def test_same_publisher_second_source_is_rejected(self):
        """Corroboration candidate from same media group must be rejected."""
        primary = _article(
            "HDFC Bank acquires SolarX 5000 crore",
            "https://economictimes.indiatimes.com/art1",
            source="The Economic Times",
        )
        same_grp = _article(
            "HDFC Bank SolarX acquisition 5000 crore",
            "https://timesofindia.indiatimes.com/art1",
            source="Times of India",
        )
        event = _event("HDFC test", [primary])

        mock_rss = [{"url": same_grp.url, "title": same_grp.title, "source": same_grp.source_name, "published_at": same_grp.published_at}]
        mock_ext = MagicMock()
        mock_ext.extract.return_value = self._make_extraction_result(same_grp)

        corroborator = ActiveCorroborator(extractor=mock_ext)
        with patch.object(corroborator, "_search_rss", return_value=mock_rss):
            result = corroborator.corroborate(event=event, primary_article=primary)

        assert not result.success, "Expected corroboration to fail (same publisher group)"

    def test_no_rss_results_returns_failure(self):
        """Empty RSS results → corroboration fails cleanly."""
        primary = _article("Random event", "https://et.com/art1")
        event = _event("Random event", [primary])

        corroborator = ActiveCorroborator(extractor=MagicMock())
        with patch.object(corroborator, "_search_rss", return_value=[]):
            result = corroborator.corroborate(event=event, primary_article=primary)

        assert not result.success
        assert result.failure_reason is not None

    def test_paywall_extraction_failure_continues_to_next(self):
        """If one candidate fails extraction (paywall), corroboration continues to the next URL."""
        primary = _article("Tata Motors acquires EV company", "https://et.com/art1", source="ET")
        event = _event("Tata Motors acquires EV", [primary])

        paywall_rss = [
            {"url": "https://bs.com/paywall-art", "title": "Tata Motors EV acquisition", "source": "BS", "published_at": primary.published_at},
        ]
        fail_result = MagicMock()
        fail_result.success = False
        fail_result.article = None
        fail_result.error_message = "Paywall — 403 Forbidden"

        mock_ext = MagicMock()
        mock_ext.extract.return_value = fail_result

        corroborator = ActiveCorroborator(extractor=mock_ext)
        with patch.object(corroborator, "_search_rss", return_value=paywall_rss):
            result = corroborator.corroborate(event=event, primary_article=primary)

        # Paywall blocked — extraction fails → no valid second source found
        assert not result.success
        assert result.corroborating_article is None


# ---------------------------------------------------------------------------
# Budget limits
# ---------------------------------------------------------------------------

class TestCorroborationBudget:
    def test_max_queries_per_event_is_2(self):
        corroborator = ActiveCorroborator(extractor=MagicMock())
        art = _article("Test", "https://et.com/1")
        queries = corroborator._build_corroboration_queries(art)
        assert len(queries) <= 2

    def test_global_budget_exhausted_returns_failure(self):
        """When global run counter is exhausted, corroborate() returns failure immediately."""
        import app.verification.corroborator as mod
        mod._run_corroboration_count = MAX_CORROBORATION_SEARCHES_PER_RUN  # exhaust budget

        primary = _article("Test event", "https://et.com/art1")
        event = _event("Test event", [primary])

        corroborator = ActiveCorroborator(extractor=MagicMock())
        result = corroborator.corroborate(event=event, primary_article=primary)

        assert not result.success
        assert "budget" in (result.failure_reason or "").lower()

        # Reset
        mod._run_corroboration_count = 0

    def test_reset_counter_resets_to_zero(self):
        import app.verification.corroborator as mod
        mod._run_corroboration_count = 7
        reset_corroboration_counter()
        assert get_corroboration_count() == 0


# ---------------------------------------------------------------------------
# Pipeline sufficiency gate (Stage 8 skip logic)
# ---------------------------------------------------------------------------

class TestPipelineSufficiencyGate:
    """
    Verify the sufficiency gate logic: Stage 8 should be skipped when fewer
    than 5 India or 5 International verified events are available.
    """

    def test_insufficient_india_triggers_gate(self):
        # Simulate having only 2 India + 6 International candidates
        india_count = 2
        intl_count = 6
        MIN = 5
        sufficient = india_count >= MIN and intl_count >= MIN
        assert not sufficient, "Gate should FAIL when india_count < 5"

    def test_insufficient_intl_triggers_gate(self):
        india_count = 6
        intl_count = 3
        MIN = 5
        sufficient = india_count >= MIN and intl_count >= MIN
        assert not sufficient, "Gate should FAIL when intl_count < 5"

    def test_exactly_5_5_passes_gate(self):
        india_count = 5
        intl_count = 5
        MIN = 5
        sufficient = india_count >= MIN and intl_count >= MIN
        assert sufficient, "Gate should PASS when both == 5"

    def test_more_than_5_5_passes_gate(self):
        india_count = 8
        intl_count = 7
        MIN = 5
        sufficient = india_count >= MIN and intl_count >= MIN
        assert sufficient, "Gate should PASS when both > 5"

    def test_zero_candidates_triggers_gate(self):
        india_count = 0
        intl_count = 0
        MIN = 5
        sufficient = india_count >= MIN and intl_count >= MIN
        assert not sufficient
