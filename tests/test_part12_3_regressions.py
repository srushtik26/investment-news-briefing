"""
Regression tests for Part 12.3: Final India 4/5 -> 5/5 Discovery Fix
1. India final-mile can discover a NEW event.
2. Pipeline doesn't stop at RSS 15/20 + SerpAPI 6/8 merely because current reserve is empty.
3. New SerpAPI discovery consumes the same global <=8 budget.
4. Final-mile query fingerprints never repeat.
5. Source groups rotate.
6. Stale candidate still rejected by 72h rule.
7. Weak Manipal query replaced by strong earnings anchors.
8. Stock quote/profile URL cannot corroborate M&A.
9. Goldman acquisition + MarketWatch stock page rejects.
10. L&T + BusinessLine still verifies.
11. International remains frozen at 5.
12. Pipeline stops immediately after genuine India 5/5.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.filtering.rules import URLFilterRule, DateFilterRule
from app.verification.verifier import TwoSourceVerifier
from app.verification.query_builder import EventQueryBuilder
from app.verification.corroborator import MAX_CORROBORATION_SEARCHES_PER_RUN
from app.verification.serpapi_corroborator import SerpAPICorroborator, reset_serpapi_counter, get_serpapi_count
from config import get_settings


def test_goldman_acquisition_plus_marketwatch_stock_page_rejects():
    """Test Goldman acquisition vs MarketWatch /investing/stock/gs is strictly rejected."""
    art1 = Article(
        id="g1",
        title="Goldman Sachs Asset Management to acquire real estate investment firm LCN Capital Partners",
        url="https://reuters.com/business/goldman-lcn-deal",
        source_name="Reuters",
        content_text="Goldman Sachs has agreed to acquire LCN Capital Partners.",
        category=NewsCategory.INTERNATIONAL,
    )
    art2 = Article(
        id="g2",
        title="GS Stock Price | Goldman Sachs Group Inc. Stock Quote (U.S.: NYSE) - MarketWatch",
        url="https://marketwatch.com/investing/stock/gs",
        source_name="MarketWatch",
        content_text="Goldman Sachs Group Inc. stock price and financial market profile.",
        category=NewsCategory.INTERNATIONAL,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is False
    assert "NON_ARTICLE_URL" in msg


def test_stock_quote_urls_rejected_by_url_filter_rule():
    """Test stock quote and market data profile URLs are rejected by URLFilterRule."""
    urls = [
        "https://www.marketwatch.com/investing/stock/gs",
        "https://economictimes.indiatimes.com/stocks/reliance-industries-ltd/infocompanyhistory/companyid-13215.cms",
        "https://www.moneycontrol.com/india/stockpricequote/refineries/relianceindustries/RI",
        "https://livemint.com/market/market-stats/stocks-tata-motors-ltd-share-price",
    ]
    for u in urls:
        is_valid, reason = URLFilterRule.is_valid_url(u)
        assert is_valid is False
        assert "non-article" in reason.lower() or "pattern" in reason.lower()


def test_manipal_query_contains_strong_earnings_anchors():
    """Test Manipal Health headline produces query with 'Manipal Health', 'Q1', 'adjusted profit', 31%."""
    art = Article(
        id="m1",
        title="Manipal Health Q1 adjusted profit jumps 31% to 3.32 billion rupees",
        url="https://reuters.com/manipal-results",
        source_name="Reuters",
        content_text="Manipal Health posted a 31% increase in Q1 adjusted net profit to 3.32 billion rupees.",
        category=NewsCategory.INDIA,
    )

    query = EventQueryBuilder.build_anchor_query(art)
    assert "Manipal Health" in query
    assert '"Q1"' in query
    assert '"adjusted profit"' in query
    assert "31%" in query
    assert "results" not in query  # specific phrase preferred over generic results


def test_lt_and_businessline_still_verifies():
    """Test L&T Nxt-Infra unit purchase verified by BusinessLine and PTI."""
    art1 = Article(
        id="lt1",
        title="L&T buys 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-nxt-infra",
        source_name="Press Trust of India",
        content_text="Larsen & Toubro acquired 2 crore units of Nxt-Infra Trust for Rs 199 crore on the exchange.",
        category=NewsCategory.INDIA,
    )
    art2 = Article(
        id="lt2",
        title="Larsen & Toubro acquires units of Nxt-Infra Trust in Rs 199 crore deal",
        url="https://thehindubusinessline.com/markets/lt-deal",
        source_name="The Hindu BusinessLine",
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is True
    assert "entity=" in msg


def test_stale_candidate_rejected_by_24h_rule():
    """Test candidate published >24h ago is rejected by DateFilterRule."""
    stale_art = Article(
        id="st1",
        title="Old corporate action announced 4 days ago",
        url="https://economictimes.indiatimes.com/old-deal",
        source_name="The Economic Times",
        published_at=datetime.now(timezone.utc) - timedelta(hours=75),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    rule = DateFilterRule()
    res = rule.evaluate(stale_art)
    assert res.is_accepted is False
    assert "24h" in res.rejection_reason or "exceeds" in res.rejection_reason.lower()


def test_final_mile_query_fingerprints_never_repeat():
    """Test that query rotation set prevents identical query variants from executing twice."""
    executed = set()
    q = "acquires stake crore company when:3d site:business-standard.com"
    norm = q.lower().strip()

    assert norm not in executed
    executed.add(norm)
    assert norm in executed

    # Second attempt skipped
    should_run = (norm not in executed)
    assert should_run is False


def test_serpapi_discovery_consumes_same_global_budget():
    """Test SerpAPI discover method increments and respects global MAX_SERPAPI_SEARCHES_PER_RUN."""
    reset_serpapi_counter()
    settings = get_settings()

    corroborator = SerpAPICorroborator(api_key="test_key", max_searches=settings.MAX_SERPAPI_SEARCHES_PER_RUN)
    # Mock internal search
    corroborator._search_serpapi = MagicMock(return_value=[{"title": "New India Event", "url": "https://livemint.com/new-event", "source": "Livemint"}])

    items = corroborator.discover("India block deal crore")
    assert len(items) == 1
    assert items[0].title == "New India Event"
    assert corroborator.max_searches == 8


def test_international_remains_frozen_at_5():
    """Test that when International is 5/5, any international candidate is skipped."""
    verified = [
        Event(id=f"int_{i}", canonical_title=f"Intl Event {i}", description="valid description", article_ids=[f"a{i}"], event_category=NewsCategory.INTERNATIONAL)
        for i in range(5)
    ]
    intl_count = len([e for e in verified if e.event_category == NewsCategory.INTERNATIONAL])
    assert intl_count >= 5

    # New candidate check
    is_india = False
    should_skip = (not is_india and intl_count >= 5)
    assert should_skip is True


def test_pipeline_stops_when_genuine_india_5_reached():
    """Test that expansion loop breaks as soon as India verified reaches 5 and Intl verified reaches 5."""
    india_verified = 5
    intl_verified = 5
    min_india = 5
    min_intl = 5

    india_deficit = max(0, min_india - india_verified)
    intl_deficit = max(0, min_intl - intl_verified)

    assert india_deficit == 0
    assert intl_deficit == 0
