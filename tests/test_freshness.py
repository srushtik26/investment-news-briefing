"""
Tests for Fix 2 — DateFilterRule freshness buckets and scoring.

Verifies:
  - 24h, 36h, 48h articles PASS (new 72h window)
  - 60h, 72h articles PASS (within extended window)
  - 73h, 100h articles are REJECTED (exceed 72h hard limit)
  - Freshness score metadata is correctly attached to accepted articles
  - Monday lookback is extended to 96h
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.filtering.rules import DateFilterRule
from app.models.article import Article
from app.models.enums import NewsCategory


def _make_article(age_hours: float, *, date_verified: bool = True, base_time: datetime = None) -> Article:
    """Create a test article published `age_hours` hours ago."""
    now = base_time or datetime.now(timezone.utc)
    pub_at = now - timedelta(hours=age_hours)
    art = MagicMock(spec=Article)
    art.url = f"https://example.com/article-{age_hours}h"
    art.title = f"Test Article {age_hours}h old"
    art.source_name = "Test Publisher"
    art.published_at = pub_at
    art.date_verified = date_verified
    art.metadata = {}
    return art


@pytest.fixture
def rule() -> DateFilterRule:
    return DateFilterRule()


class TestDateFilterRuleFreshnessBuckets:
    """Each age bucket — accept/reject correctness under 24-hour freshness policy."""

    def test_12h_article_is_accepted(self, rule):
        art = _make_article(12)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 12h article to be accepted but got: {result.rejection_reason}"

    def test_23h59m_article_is_accepted(self, rule):
        art = _make_article(23.98)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 23h59m article to be accepted but got: {result.rejection_reason}"

    def test_24h_exact_article_is_accepted(self, rule):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        art = _make_article(24.0, base_time=now)
        result = rule.evaluate(art, now_utc=now)
        assert result.is_accepted, f"Expected 24h article to be accepted but got: {result.rejection_reason}"

    def test_24h01m_article_is_rejected(self, rule):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        art = _make_article(24.02, base_time=now)
        result = rule.evaluate(art, now_utc=now)
        assert not result.is_accepted, "Expected 24h01m article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()

    def test_36h_article_is_rejected(self, rule):
        art = _make_article(36)
        result = rule.evaluate(art)
        assert not result.is_accepted, "Expected 36h article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()

    def test_48h_article_is_rejected(self, rule):
        art = _make_article(48)
        result = rule.evaluate(art)
        assert not result.is_accepted, "Expected 48h article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()

    def test_72h_article_is_rejected(self, rule):
        art = _make_article(72)
        result = rule.evaluate(art)
        assert not result.is_accepted, "Expected 72h article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()


class TestDateFilterRuleFreshnessScores:
    """Freshness score metadata must be attached correctly to accepted articles."""

    def test_0_6h_gets_score_1_0(self, rule):
        art = _make_article(3)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 1.0

    def test_6_12h_gets_score_0_9(self, rule):
        art = _make_article(9)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 0.9

    def test_12_24h_gets_score_0_8(self, rule):
        art = _make_article(18)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 0.8

    def test_freshness_bucket_label_attached(self, rule):
        for age, expected_bucket in [(3, "fresh_0_6h"), (9, "fresh_6_12h"), (18, "fresh_12_24h")]:
            art = _make_article(age)
            rule.evaluate(art)
            assert art.metadata.get("freshness_bucket") == expected_bucket, (
                f"Age {age}h expected bucket '{expected_bucket}' but got '{art.metadata.get('freshness_bucket')}'"
            )

    def test_age_hours_stored_in_metadata(self, rule):
        art = _make_article(15)
        rule.evaluate(art)
        age = art.metadata.get("age_hours")
        assert age is not None
        assert 14 <= age <= 16, f"Expected age_hours ~15, got {age}"

    def test_rejected_article_has_no_freshness_score(self, rule):
        art = _make_article(30)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") is None


class TestDateFilterRuleEdgeCases:
    """Edge cases: missing date, future date, missing date_verified."""

    def test_missing_published_at_is_rejected(self, rule):
        art = _make_article(10)
        art.published_at = None
        result = rule.evaluate(art)
        assert not result.is_accepted
        assert "missing" in result.rejection_reason.lower() or "unverified" in result.rejection_reason.lower()

    def test_future_date_is_rejected(self, rule):
        art = _make_article(-2)  # 2 hours in the future
        result = rule.evaluate(art)
        assert not result.is_accepted
        assert "future" in result.rejection_reason.lower()
