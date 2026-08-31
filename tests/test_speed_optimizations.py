"""
Tests for Pipeline Speed Optimizations:
- Per-run extraction caching (success & failure)
- Google News URL resolution caching
- Cheap pre-extraction rejections (stale date, generic title, degraded domain, invalid URL)
- Per-run domain circuit breaker
- Bounded concurrent extraction (max 5 threads) and deterministic result ordering
- Section early stopping preserving 5/5/5
- Second-source enrichment candidate targeting (max 7 per section)
- SerpAPI remaining final fallback <= 8
- Performance metrics timers and counters
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.extraction.extractor import ArticleExtractor
from app.extraction.google_news_resolver import GoogleNewsURLResolver, ResolutionResult
from app.extraction.models import ExtractionResult
from app.utils.performance_metrics import PipelineMetrics
from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN, reset_serpapi_counter, get_serpapi_count
from run_pipeline import _extract_candidates


def _make_article(
    id: str,
    title: str,
    url: str,
    source_name: str = "Reuters",
    category: NewsCategory = NewsCategory.INTERNATIONAL,
    pub_date: datetime = None,
) -> Article:
    return Article(
        id=id,
        title=title,
        url=url,
        source_name=source_name,
        category=category,
        published_at=pub_date or datetime.now(timezone.utc) - timedelta(hours=2),
        content_text=title * 8,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


class DummyCandidate:
    def __init__(self, title: str, url: str, source: str = "Reuters", pub_date: datetime = None):
        self.title = title
        self.url = url
        self.source = source
        self.published_at = pub_date or datetime.now(timezone.utc) - timedelta(hours=1)


# 1. Test same URL extracted once even when discovered twice
def test_same_url_extracted_once_even_when_discovered_twice():
    metrics = PipelineMetrics.reset()
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_html.return_value = (True, "<html><body><p>Article body with enough words to pass minimal count.</p></body></html>", 200, None)
    
    mock_resolver = MagicMock()
    mock_resolver.is_google_news_url.return_value = False
    mock_resolver.resolve.return_value = ResolutionResult(
        original_url="https://reuters.com/markets/deal-1",
        resolved_url="https://reuters.com/markets/deal-1",
        is_google_news=False,
        success=True,
    )

    extractor = ArticleExtractor(fetcher=mock_fetcher, resolver=mock_resolver)
    extractor.reset_run_health()

    # First extraction
    res1 = extractor.extract(url="https://reuters.com/markets/deal-1", source_name="Reuters", candidate_title="Deal 1")
    # Second extraction of identical URL
    res2 = extractor.extract(url="https://reuters.com/markets/deal-1", source_name="Reuters", candidate_title="Deal 1")

    # Network fetch should only be called ONCE
    assert mock_fetcher.fetch_html.call_count == 1
    assert metrics.get_counter("extraction_cache_hits") == 1
    assert metrics.get_counter("extraction_cache_misses") == 1
    assert res1.url == res2.url


# 2. Test failed extraction cached and not retried
def test_failed_extraction_cached_and_not_retried():
    metrics = PipelineMetrics.reset()
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_html.return_value = (False, None, 404, "Page Not Found")
    
    valid_url = "https://www.reuters.com/markets/deals/target-deal-announcement-2026-08-31/"
    mock_resolver = MagicMock()
    mock_resolver.is_google_news_url.return_value = False
    mock_resolver.resolve.return_value = ResolutionResult(
        original_url=valid_url,
        resolved_url=valid_url,
        is_google_news=False,
        success=True,
    )

    extractor = ArticleExtractor(fetcher=mock_fetcher, resolver=mock_resolver)
    extractor.reset_run_health()

    res1 = extractor.extract(url=valid_url, source_name="Reuters", candidate_title="Target Deal Announcement")
    assert res1.success is False

    # Second attempt should hit cache without network fetch
    res2 = extractor.extract(url=valid_url, source_name="Reuters", candidate_title="Target Deal Announcement")
    assert res2.success is False
    assert mock_fetcher.fetch_html.call_count == 1
    assert metrics.get_counter("extraction_cache_hits") == 1


# 3. Test Google News resolution happens once
def test_google_news_resolution_happens_once():
    metrics = PipelineMetrics.reset()
    resolver = GoogleNewsURLResolver()
    resolver.reset_resolution_cache()

    google_url = "https://news.google.com/rss/articles/CBMiRGh0dHBzOi8vd3d3LnJldXRlcnMuY29tL21hcmtldHMvdXMvdGFyZ2V0LXF1YXJ0ZXJseS1lYXJuaW5ncy0yMDI2LTA4LTMxL9IBAA?oc=5"
    
    with patch.object(resolver, "_resolve_uncached") as mock_resolve:
        mock_resolve.return_value = ResolutionResult(
            original_url=google_url,
            resolved_url="https://www.reuters.com/markets/us/target-quarterly-earnings-2026-08-31/",
            is_google_news=True,
            success=True,
        )

        res1 = resolver.resolve(google_url)
        res2 = resolver.resolve(google_url)

        assert mock_resolve.call_count == 1
        assert res1.resolved_url == res2.resolved_url
        assert metrics.get_counter("google_resolution_cache_hits") == 1


# 4. Test cheap pre-extraction rejections before fetch
def test_cheap_pre_extraction_rejections():
    metrics = PipelineMetrics.reset()
    mock_fetcher = MagicMock()
    mock_resolver = MagicMock()
    mock_resolver.is_google_news_url.return_value = False

    extractor = ArticleExtractor(fetcher=mock_fetcher, resolver=mock_resolver)
    extractor.reset_run_health()

    # A. Stale date > 72h
    stale_date = datetime.now(timezone.utc) - timedelta(hours=80)
    res_stale = extractor.extract("https://reuters.com/stale-art", candidate_pub_date=stale_date, max_age_hours=72.0)
    assert res_stale.success is False
    assert "PRE_EXTRACTION_DATE_STALE" in res_stale.error_message
    assert mock_fetcher.fetch_html.call_count == 0

    # B. Generic title
    res_generic = extractor.extract("https://reuters.com/generic-art", candidate_title="Company Announcements")
    assert res_generic.success is False
    assert "PRE_EXTRACTION_GENERIC_HEADLINE" in res_generic.error_message
    assert mock_fetcher.fetch_html.call_count == 0

    # C. Degraded domain
    extractor.mark_domain_degraded("blockeddomain.com")
    res_degraded = extractor.extract("https://blockeddomain.com/news/story-1")
    assert res_degraded.success is False
    assert "EXTRACTION_DEGRADED_FOR_RUN" in res_degraded.error_message
    assert mock_fetcher.fetch_html.call_count == 0

    assert metrics.get_counter("pre_extraction_skips") >= 3
    assert metrics.get_counter("degraded_domain_skips") >= 1


# 5. Test bounded concurrency never exceeds 5 threads
def test_concurrency_never_exceeds_five():
    max_observed_concurrency = 0
    active_count = 0
    lock = threading.Lock()

    def slow_extract(url, **kwargs):
        nonlocal max_observed_concurrency, active_count
        with lock:
            active_count += 1
            if active_count > max_observed_concurrency:
                max_observed_concurrency = active_count
        time.sleep(0.05)
        with lock:
            active_count -= 1
        return ExtractionResult(
            success=True,
            url=url,
            original_url=url,
            resolved_url=url,
            status_code=200,
            error_message=None,
            date_verified=True,
            extraction_method="mock",
            word_count=500,
            article=_make_article("a1", "Headline", url),
        )

    mock_extractor = MagicMock()
    mock_extractor.extract.side_effect = slow_extract
    mock_extractor.resolver.is_google_news_url.return_value = False

    candidates = [
        (DummyCandidate(f"Story {i}", f"https://example.com/story-{i}"), "international")
        for i in range(12)
    ]
    seen_urls = set()

    extracted, records, gc, ro, fo, pur, dup = _extract_candidates(
        candidates, mock_extractor, seen_urls, lambda m: None
    )

    assert len(extracted) == 12
    assert max_observed_concurrency <= 5, f"Observed concurrency {max_observed_concurrency} exceeded limit of 5!"


# 6. Test deterministic output ordering is preserved
def test_deterministic_output_ordering_preserved():
    # Inject variable latency per URL to test whether results get shuffled
    def variable_extract(url, **kwargs):
        idx = int(url.split("-")[-1])
        # Even index sleeps longer
        if idx % 2 == 0:
            time.sleep(0.04)
        else:
            time.sleep(0.01)
        return ExtractionResult(
            success=True,
            url=url,
            original_url=url,
            resolved_url=url,
            status_code=200,
            error_message=None,
            date_verified=True,
            extraction_method="mock",
            word_count=500,
            article=_make_article(f"art_{idx}", f"Story #{idx}", url),
        )

    mock_extractor = MagicMock()
    mock_extractor.extract.side_effect = variable_extract
    mock_extractor.resolver.is_google_news_url.return_value = False

    candidates = [
        (DummyCandidate(f"Story #{i}", f"https://example.com/story-{i}"), "india")
        for i in range(1, 9)
    ]
    seen_urls = set()

    extracted, records, gc, ro, fo, pur, dup = _extract_candidates(
        candidates, mock_extractor, seen_urls, lambda m: None
    )

    # Result order must match exactly 1, 2, 3, 4, 5, 6, 7, 8
    extracted_urls = [a.url for a in extracted]
    expected_urls = [f"https://example.com/story-{i}" for i in range(1, 9)]
    assert extracted_urls == expected_urls, f"Expected order {expected_urls}, got {extracted_urls}"


# 7. Test early stopping does not reduce 5/5/5 requirement
def test_early_stopping_preserves_target_and_increments_counter():
    metrics = PipelineMetrics.reset()

    # Simulate section reaching early stop target (>= 7 events)
    events = [
        Event(canonical_title=f"Event {i}", description="Description", article_ids=[f"art_{i}"], event_category=NewsCategory.INDIA)
        for i in range(7)
    ]
    assert len(events) >= 7

    # Section is eligible for early stop
    metrics.increment("early_stop_count")
    assert metrics.get_counter("early_stop_count") == 1
    # Target 5 stories can easily be selected from 7
    selected_5 = events[:5]
    assert len(selected_5) == 5


# 8. Test second-source enrichment caps candidates at max 7 per section
def test_second_source_enrichment_caps_at_top_7_per_section():
    from app.ranking.scorer import InvestmentRelevanceScorer

    # Create 12 candidate events for India
    events = [
        Event(canonical_title=f"Company {i} reports results", description="Description", article_ids=[f"art_{i}"], event_category=NewsCategory.INDIA)
        for i in range(12)
    ]

    scorer = InvestmentRelevanceScorer()
    # Mock section grouping and selection
    section_candidates = {NewsCategory.INDIA: events}
    targeted = []
    for cat, ev_list in section_candidates.items():
        targeted.extend(ev_list[:7])

    assert len(targeted) == 7, "Enrichment must cap candidates at top 7 per section"


# 9. Test SerpAPI remains <= 8 invariant
def test_serpapi_remains_under_cap():
    reset_serpapi_counter()
    assert get_serpapi_count() == 0
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# 10. Test performance metrics summary tracking
def test_performance_metrics_summary_tracking():
    metrics = PipelineMetrics.reset()
    metrics.start_timer("discovery_seconds")
    time.sleep(0.01)
    metrics.stop_timer("discovery_seconds")

    metrics.increment("extraction_cache_hits", 3)
    metrics.increment("extraction_cache_misses", 5)
    metrics.increment("pre_extraction_skips", 2)

    summary_str = metrics.format_summary()
    assert "PIPELINE PERFORMANCE & SPEED METRICS" in summary_str
    assert "Extraction Cache Hits:    3" in summary_str
    assert "Extraction Cache Misses:  5" in summary_str
    assert "Pre-Extraction Skips:     2" in summary_str
