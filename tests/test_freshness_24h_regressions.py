"""
Regression tests for 24-Hour Freshness Policy:
1. 23h59m article accepted
2. exactly 24h article accepted
3. 24h01m article rejected
4. 48h article rejected
5. Google News final-mile query contains when:1d
6. primary <=24h + secondary <=24h verifies
7. primary <=24h + secondary >24h rejects
8. dedup still checks previous 3 days
9. no 72h hard-coded freshness remains in pipeline logic
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.filtering.rules import DateFilterRule
from app.verification.verifier import TwoSourceVerifier
from app.deduplication.engine import DeduplicationEngine
from app.deduplication.history import HistoryStore
from config import get_settings


def test_23h59m_article_accepted():
    """Test article published 23h 59m ago is accepted under 24h policy."""
    now_utc = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    pub_at = now_utc - timedelta(hours=23, minutes=59)

    art = Article(
        id="art_23h59m",
        title="Fresh Breaking Deal Announced Recently",
        url="https://livemint.com/fresh-deal",
        source_name="Livemint",
        published_at=pub_at,
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=now_utc)
    assert res.is_accepted is True


def test_exactly_24h_article_accepted():
    """Test article published exactly 24h ago is accepted."""
    now_utc = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    pub_at = now_utc - timedelta(hours=24)

    art = Article(
        id="art_24h",
        title="Corporate Action exactly 24 hours ago",
        url="https://livemint.com/exact-24h",
        source_name="Livemint",
        published_at=pub_at,
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=now_utc)
    assert res.is_accepted is True


def test_24h01m_article_rejected():
    """Test article published 24h 01m ago is rejected."""
    now_utc = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    pub_at = now_utc - timedelta(hours=24, minutes=1)

    art = Article(
        id="art_24h01m",
        title="Slightly Stale Corporate Action",
        url="https://livemint.com/stale-24h1m",
        source_name="Livemint",
        published_at=pub_at,
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=now_utc)
    assert res.is_accepted is False
    assert "exceeds" in res.rejection_reason.lower()


def test_48h_article_rejected():
    """Test article published 48h ago is rejected."""
    now_utc = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    pub_at = now_utc - timedelta(hours=48)

    art = Article(
        id="art_48h",
        title="Two Days Old Story",
        url="https://livemint.com/old-48h",
        source_name="Livemint",
        published_at=pub_at,
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=now_utc)
    assert res.is_accepted is False
    assert "exceeds" in res.rejection_reason.lower()


def test_google_news_final_mile_query_contains_when_1d():
    """Test that all final-mile query templates contain 'when:1d' and none contain 'when:3d'."""
    import inspect
    import run_pipeline

    source_code = inspect.getsource(run_pipeline)
    assert "when:1d" in source_code
    assert "when:3d" not in source_code


def test_primary_24h_and_secondary_24h_verifies():
    """Test that when both primary and secondary sources are <=24h old, verification succeeds."""
    now_utc = datetime.now(timezone.utc)

    art1 = Article(
        id="p1",
        title="L&T buys 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-nxt-infra",
        source_name="Press Trust of India",
        published_at=now_utc - timedelta(hours=5),
        content_text="Larsen & Toubro acquired 2 crore units of Nxt-Infra Trust for Rs 199 crore on the exchange.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art2 = Article(
        id="p2",
        title="Larsen & Toubro acquires units of Nxt-Infra Trust in Rs 199 crore deal",
        url="https://thehindubusinessline.com/markets/lt-deal",
        source_name="The Hindu BusinessLine",
        published_at=now_utc - timedelta(hours=10),
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is True
    assert "entity=" in msg


def test_primary_24h_and_secondary_gt24h_rejects():
    """Test that when primary is <=24h but secondary is >24h old (e.g. 36h), verification rejects."""
    now_utc = datetime.now(timezone.utc)

    art1 = Article(
        id="p1",
        title="L&T buys 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-nxt-infra",
        source_name="Press Trust of India",
        published_at=now_utc - timedelta(hours=5),
        content_text="Larsen & Toubro acquired 2 crore units of Nxt-Infra Trust for Rs 199 crore on the exchange.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art2 = Article(
        id="p2",
        title="Larsen & Toubro acquires units of Nxt-Infra Trust in Rs 199 crore deal",
        url="https://thehindubusinessline.com/markets/lt-deal",
        source_name="The Hindu BusinessLine",
        published_at=now_utc - timedelta(hours=36),
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is False
    assert "STALE_SECOND_SOURCE" in msg or "STALE" in msg


def test_dedup_still_checks_previous_3_days():
    """Test deduplication engine still enforces a 3-day historical lookback window."""
    settings = get_settings()
    assert settings.DEDUP_LOOKBACK_DAYS == 3
    assert settings.STORY_FRESHNESS_HOURS == 24.0

    today = date(2026, 8, 21)
    two_days_ago = today - timedelta(days=2)

    mock_store = MagicMock(spec=HistoryStore)
    mock_store.get_recent_fingerprints.return_value = {
        "hdfc_bank:earnings:2026-08-21:16175cr_18pct"
    }

    engine = DeduplicationEngine(history_store=mock_store)
    candidates = [
        {
            "headline": "HDFC Bank Q1 Profit Jumps 18%",
            "company_name": "HDFC Bank",
            "event_type": "EARNINGS",
            "category": "india",
            "key_facts": ["16175cr", "18pct"],
        }
    ]

    accepted, rejected = engine.filter_stories(
        candidate_stories=candidates,
        target_date=today,
        lookback_days=settings.DEDUP_LOOKBACK_DAYS,
    )

    assert len(accepted) == 0
    assert len(rejected) == 1
    assert rejected[0]["rejection_rule"] == "3_DAY_HISTORY"


def test_no_72h_hardcoded_freshness_in_pipeline_logic():
    """Test that DateFilterRule and settings use 24h freshness."""
    rule = DateFilterRule()
    assert rule.DEFAULT_LOOKBACK_HOURS == 24.0
    assert rule.max_age_hours == 24.0
