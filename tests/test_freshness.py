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


def _make_article(age_hours: float, *, date_verified: bool = True) -> Article:
    """Create a test article published `age_hours` hours ago."""
    now = datetime.now(timezone.utc)
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
    """Each age bucket — accept/reject correctness."""

    def test_24h_article_is_accepted(self, rule):
        art = _make_article(24)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 24h article to be accepted but got: {result.rejection_reason}"

    def test_36h_article_is_accepted(self, rule):
        art = _make_article(36)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 36h article to be accepted but got: {result.rejection_reason}"

    def test_48h_article_is_accepted(self, rule):
        art = _make_article(48)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 48h article to be accepted but got: {result.rejection_reason}"

    def test_60h_article_is_accepted(self, rule):
        art = _make_article(60)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 60h article to be accepted but got: {result.rejection_reason}"

    def test_72h_article_is_accepted(self, rule):
        # Use 71h which is safely inside the 72h window (avoids float precision edge)
        art = _make_article(71)
        result = rule.evaluate(art)
        assert result.is_accepted, f"Expected 71h article to be accepted but got: {result.rejection_reason}"

    def test_73h_article_is_rejected(self, rule):
        art = _make_article(73)
        result = rule.evaluate(art)
        assert not result.is_accepted, "Expected 73h article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()

    def test_100h_article_is_rejected(self, rule):
        art = _make_article(100)
        result = rule.evaluate(art)
        assert not result.is_accepted, "Expected 100h article to be REJECTED"
        assert "exceeds" in result.rejection_reason.lower()


class TestDateFilterRuleFreshnessScores:
    """Freshness score metadata must be attached correctly to accepted articles."""

    def test_0_24h_gets_score_1_0(self, rule):
        art = _make_article(12)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 1.0

    def test_24_48h_gets_score_0_8(self, rule):
        art = _make_article(36)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 0.8

    def test_48_72h_gets_score_0_5(self, rule):
        art = _make_article(60)
        rule.evaluate(art)
        assert art.metadata.get("freshness_score") == 0.5

    def test_freshness_bucket_label_attached(self, rule):
        for age, expected_bucket in [(12, "fresh_0_24h"), (36, "fresh_24_48h"), (60, "stale_48_72h")]:
            art = _make_article(age)
            rule.evaluate(art)
            assert art.metadata.get("freshness_bucket") == expected_bucket, (
                f"Age {age}h expected bucket '{expected_bucket}' but got '{art.metadata.get('freshness_bucket')}'"
            )

    def test_age_hours_stored_in_metadata(self, rule):
        art = _make_article(30)
        rule.evaluate(art)
        age = art.metadata.get("age_hours")
        assert age is not None
        assert 29 <= age <= 31, f"Expected age_hours ~30, got {age}"

    def test_rejected_article_has_no_freshness_score(self, rule):
        art = _make_article(80)
        rule.evaluate(art)
        # Rejected articles should not have a freshness_score set
        assert art.metadata.get("freshness_score") is None


class TestDateFilterRuleMondayExtension:
    """Monday briefings have a 96h lookback window instead of 72h."""

    def test_monday_80h_article_is_accepted(self, rule):
        # Force a Monday timestamp
        now = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)  # This is a Monday
        art = _make_article(80)
        result = rule.evaluate(art, now_utc=now)
        assert result.is_accepted, f"Expected Monday 80h article to be accepted: {result.rejection_reason}"

    def test_monday_96h_article_is_accepted(self, rule):
        now = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)
        art = _make_article(96)
        result = rule.evaluate(art, now_utc=now)
        assert result.is_accepted, f"Expected Monday 96h article to be accepted: {result.rejection_reason}"

    def test_monday_97h_article_is_rejected(self, rule):
        # Use a fixed Monday evaluation time and create the article relative to IT
        monday_eval = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)
        art = MagicMock()
        art.url = "https://example.com/monday-97h"
        art.title = "Monday 97h article"
        art.source_name = "Test Publisher"
        art.published_at = monday_eval - timedelta(hours=97)
        art.date_verified = True
        art.metadata = {}
        result = rule.evaluate(art, now_utc=monday_eval)
        assert not result.is_accepted, "Expected Monday 97h article to be REJECTED"

    def test_non_monday_49h_article_is_accepted(self, rule):
        # Tuesday — uses 72h default
        now = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)  # Tuesday
        art = _make_article(49)
        result = rule.evaluate(art, now_utc=now)
        assert result.is_accepted, f"Expected Tue 49h article to be accepted: {result.rejection_reason}"


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
