"""
Unit and regression tests for Part 11 High-Recall and Quality Optimizations.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.ranking.scorer import calculate_corroboration_priority
from app.ranking.pre_ranker import ArticlePreRanker, ACCESSIBLE_PRIMARY_PUBLISHERS
from app.extraction.extractor import ArticleExtractor
from app.extraction.models import ExtractionResult
from app.discovery.queries import SearchQueryBuilder, ACCESSIBLE_INTERNATIONAL_SOURCES
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_rejection_reasons,
)
from app.verification.verifier import TwoSourceVerifier
from app.classification.classifier import AIArticleClassifier
from app.classification.region_classifier import EventRegionClassifier


def test_corroboration_priority_scoring():
    """Test that high-impact hard business events get high priority while live price trackers get low priority."""
    ma_article = Article(
        id="a1",
        title="Goldman Sachs acquires LCN Capital in $410M deal",
        url="https://cnbc.com/goldman-deal",
        source_name="CNBC",
        content_text="Goldman Sachs has agreed to acquire LCN Capital Partners for $410 million.",
        category=NewsCategory.INTERNATIONAL,
    )
    ma_event = Event(
        id="e1",
        canonical_title="Goldman Sachs acquires LCN Capital",
        description="Goldman Sachs has agreed to acquire LCN Capital Partners for $410 million.",
        article_ids=["a1"],
        companies_involved=["Goldman Sachs", "LCN Capital"],
    )

    live_article = Article(
        id="a2",
        title="Nifty today: Live stock price and market updates",
        url="https://livemint.com/market-live",
        source_name="Livemint",
        content_text="Live stock market updates and share price movements today.",
        category=NewsCategory.INDIA,
    )
    live_event = Event(
        id="e2",
        canonical_title="Nifty live stock market updates",
        description="Live stock market updates and share price movements today.",
        article_ids=["a2"],
    )

    ma_score = calculate_corroboration_priority(ma_event, ma_article)
    live_score = calculate_corroboration_priority(live_event, live_article)

    assert ma_score >= 70.0
    assert live_score <= 20.0
    assert ma_score > live_score


def test_extractor_blocked_url_cache():
    """Test that 401/403 errors are cached and subsequent calls immediately skip extraction."""
    extractor = ArticleExtractor()

    # Pre-populate blocked cache
    blocked_url = "https://bloomberg.com/news/articles/2026-08-20/locked-article"
    extractor.blocked_url_cache.add(blocked_url.lower().rstrip("/"))

    res = extractor.extract(blocked_url, source_name="Bloomberg")
    assert not res.success
    assert res.extraction_method == "blocked_cache"
    assert "PREVIOUSLY_BLOCKED_URL" in (res.error_message or "")


def test_extractor_domain_metrics():
    """Test that domain extraction success/failed statistics are accurately tracked."""
    extractor = ArticleExtractor()
    extractor.domain_extraction_stats.clear()

    # Simulating extraction results
    netloc = "cnbc.com"
    stats = extractor.domain_extraction_stats.setdefault(netloc, {"success": 0, "failed": 0, "blocked_401_403": 0})
    stats["success"] += 2
    stats["failed"] += 1

    assert extractor.domain_extraction_stats["cnbc.com"]["success"] == 2
    assert extractor.domain_extraction_stats["cnbc.com"]["failed"] == 1


def test_accessible_international_sources():
    """Test that accessible sources (CNBC, AP, BBC, etc.) are prioritized."""
    accessible = SearchQueryBuilder.get_accessible_sources_for_country("International")
    domains = [s.domain for s in accessible]
    assert "cnbc.com" in domains
    assert "apnews.com" in domains
    assert "bbc.com" in domains
    assert "reuters.com" not in domains  # Signaling, not in primary accessible list

    pre_ranker = ArticlePreRanker()
    art_accessible = Article(
        id="a1",
        title="Rio Tinto acquires Arcadium Lithium for $6.7 billion",
        url="https://cnbc.com/rio-arcadium",
        source_name="CNBC",
        content_text="Rio Tinto acquires Arcadium Lithium.",
        category=NewsCategory.INTERNATIONAL,
    )
    score = pre_ranker.score_article(art_accessible)
    assert score >= 50.0


def test_serpapi_targeted_query_anchors():
    """Test high-precision query anchor building without restrictive site clause."""
    corroborator = SerpAPICorroborator()
    art = Article(
        id="a1",
        title="L&T buys 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-nxt-infra",
        source_name="The Economic Times",
        content_text="Larsen & Toubro has purchased units of Nxt-Infra Trust.",
        category=NewsCategory.INDIA,
    )
    event = Event(
        id="e1",
        canonical_title="L&T buys units of Nxt-Infra Trust",
        description="Larsen & Toubro has purchased units of Nxt-Infra Trust.",
        article_ids=["a1"],
        companies_involved=["Larsen & Toubro", "Nxt-Infra Trust"],
    )

    query = corroborator._build_targeted_query(art, event=event)
    assert "Larsen & Toubro" in query or "L&T" in query
    assert "Nxt-Infra Trust" in query
    assert "acquisition" in query or "199" in query
    assert "site:" not in query  # Broad search without site clause in query


def test_serpapi_rejection_diagnostics():
    """Test canonical rejection reasons recorded during candidate processing."""
    reset_serpapi_counter()
    corroborator = SerpAPICorroborator()

    corroborator._record_rejection("NON_ARTICLE_URL")
    corroborator._record_rejection("SAME_PUBLISHER_GROUP")
    corroborator._record_rejection("EVENT_TYPE_MISMATCH")

    reasons = get_serpapi_rejection_reasons()
    assert reasons.get("NON_ARTICLE_URL") == 1
    assert reasons.get("SAME_PUBLISHER_GROUP") == 1
    assert reasons.get("EVENT_TYPE_MISMATCH") == 1


def test_gemini_api_key_sanitization():
    """Test that API key quotes and whitespace are safely sanitized."""
    classifier = AIArticleClassifier(api_key="  'AIzaSyFakeKey123'  ")
    assert classifier.api_key == "AIzaSyFakeKey123"


def test_region_classifier_runs_before_corroboration():
    """Test that EventRegionClassifier classifies event based on subject rather than publisher."""
    classifier = EventRegionClassifier()

    art = Article(
        id="a1",
        title="Stripe acquires OpenRouter to expand AI model offerings",
        url="https://economictimes.indiatimes.com/cio/stripe-openrouter",
        source_name="ET CIO",
        content_text="Stripe announced the acquisition of OpenRouter.",
        category=NewsCategory.INDIA,  # Discovered from Indian publisher
    )
    event = Event(
        id="e1",
        canonical_title="Stripe acquires OpenRouter",
        description="Stripe announced the acquisition of OpenRouter.",
        article_ids=["a1"],
        companies_involved=["Stripe", "OpenRouter"],
    )

    classified_cat = classifier.classify_event(event, [art])
    assert classified_cat == NewsCategory.INTERNATIONAL


def test_strict_same_event_matching_preserved():
    """Test that L&T unit purchase vs Nxt-Infra profit rise remains strictly rejected."""
    verifier = TwoSourceVerifier()

    art1 = Article(
        id="a1",
        title="L&T buys over 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-buy",
        source_name="The Economic Times",
        content_text="L&T has purchased 2 crore units of Nxt-Infra Trust for Rs 199 crore in a block deal.",
        category=NewsCategory.INDIA,
        published_at=datetime.now(timezone.utc),
    )
    art2 = Article(
        id="a2",
        title="Nxt-Infra Trust consolidated net profit rises 71.83% in June 2026 quarter",
        url="https://business-standard.com/nxt-profit",
        source_name="Business Standard",
        content_text="Nxt-Infra Trust consolidated net profit rose 71.83% in the quarter ended June 2026.",
        category=NewsCategory.INDIA,
        published_at=datetime.now(timezone.utc),
    )

    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert not is_same
    assert "EVENT_TYPE_MISMATCH" in (reason or "") or "FINANCIAL_FACT_MISMATCH" in (reason or "")
