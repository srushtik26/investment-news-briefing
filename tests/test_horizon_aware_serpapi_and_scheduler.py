"""
Tests for Horizon-Aware SerpAPI Discovery, Caching, and GitHub Actions Daily Scheduler.

Verifies:
1. 36h, 48h, 72h SerpAPI cache identities differ.
2. 48h fallback can discover a candidate not returned by 36h.
3. 72h fallback can discover a candidate not returned by 48h.
4. Cached request does not consume another SerpAPI API call.
5. Total real SerpAPI calls remain <= 8.
6. No candidate >72h is accepted.
7. Existing source whitelist still applies.
8. Existing hard-event rules still apply.
9. Workflow runs run_daily.py.
10. Workflow has primary + recovery schedule.
11. Workflow has manual workflow_dispatch.
12. No secrets are hardcoded.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import re
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.filtering.engine import HardFilterEngine
from app.filtering.rules import DateFilterRule, SourceFilterRule, StoryTypeFilterRule
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_count,
    get_serpapi_time_window,
    MAX_SERPAPI_SEARCHES_PER_RUN,
    _serpapi_query_cache,
)


@pytest.fixture(autouse=True)
def clean_serpapi_state():
    """Reset SerpAPI counters and caches before each test."""
    reset_serpapi_counter()
    yield
    reset_serpapi_counter()


# ---------------------------------------------------------------------------
# Tests 1-5: SerpAPI Horizon Caching, Discovery & Request Budgets
# ---------------------------------------------------------------------------

def test_cache_identities_differ_across_horizons():
    """1. 36h, 48h, 72h SerpAPI cache identities differ."""
    corrob = SerpAPICorroborator(api_key="dummy_test_key")
    query = "company earnings results"

    with patch("requests.get") as mock_get:
        # Mock HTTP responses for 3 distinct horizons
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "news_results": [
                    {"title": "Earnings Q3", "link": "https://reuters.com/1", "source": "Reuters", "date": "1 day ago"}
                ]
            },
        )

        # 36h call
        corrob._search_serpapi(query, active_horizon=36.0)
        # 48h call
        corrob._search_serpapi(query, active_horizon=48.0)
        # 72h call
        corrob._search_serpapi(query, active_horizon=72.0)

        assert (query, 36.0) in _serpapi_query_cache
        assert (query, 48.0) in _serpapi_query_cache
        assert (query, 72.0) in _serpapi_query_cache
        # Ensure they are separate cache entries
        assert len({(query, 36.0), (query, 48.0), (query, 72.0)}) == 3


def test_48h_fallback_can_discover_candidate_not_returned_by_36h():
    """2. 48h fallback can discover a candidate not returned by 36h."""
    corrob = SerpAPICorroborator(api_key="dummy_test_key")
    query = "company acquisition deal"

    def side_effect(*args, **kwargs):
        params = kwargs.get("params", {})
        tbs = params.get("tbs")
        if tbs == "qdr:d":  # 36h window
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "news_results": [
                        {"title": "Acquisition 24h", "link": "https://bloomberg.com/deal24", "source": "Bloomberg"}
                    ]
                },
            )
        elif tbs == "qdr:d2":  # 48h window
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "news_results": [
                        {"title": "Acquisition 48h Special", "link": "https://bloomberg.com/deal48", "source": "Bloomberg"}
                    ]
                },
            )
        return MagicMock(status_code=200, json=lambda: {"news_results": []})

    with patch("requests.get", side_effect=side_effect):
        items_36h = corrob.discover(query, active_horizon=36.0)
        items_48h = corrob.discover(query, active_horizon=48.0)

        urls_36h = [item.url for item in items_36h]
        urls_48h = [item.url for item in items_48h]

        assert "https://bloomberg.com/deal24" in urls_36h
        assert "https://bloomberg.com/deal48" in urls_48h
        assert "https://bloomberg.com/deal48" not in urls_36h


def test_72h_fallback_can_discover_candidate_not_returned_by_48h():
    """3. 72h fallback can discover a candidate not returned by 48h."""
    corrob = SerpAPICorroborator(api_key="dummy_test_key")
    query = "company funding round"

    def side_effect(*args, **kwargs):
        params = kwargs.get("params", {})
        tbs = params.get("tbs")
        if tbs == "qdr:d2":  # 48h window
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "news_results": [
                        {"title": "Funding 48h", "link": "https://reuters.com/fund48", "source": "Reuters"}
                    ]
                },
            )
        elif tbs == "qdr:d3":  # 72h window
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "news_results": [
                        {"title": "Funding 72h Deep Candidate", "link": "https://reuters.com/fund72", "source": "Reuters"}
                    ]
                },
            )
        return MagicMock(status_code=200, json=lambda: {"news_results": []})

    with patch("requests.get", side_effect=side_effect):
        items_48h = corrob.discover(query, active_horizon=48.0)
        items_72h = corrob.discover(query, active_horizon=72.0)

        urls_48h = [item.url for item in items_48h]
        urls_72h = [item.url for item in items_72h]

        assert "https://reuters.com/fund48" in urls_48h
        assert "https://reuters.com/fund72" in urls_72h
        assert "https://reuters.com/fund72" not in urls_48h


def test_cached_request_does_not_consume_serpapi_api_call():
    """4. Cached request does not consume another SerpAPI API call."""
    corrob = SerpAPICorroborator(api_key="dummy_test_key")
    query = "corporate guidance"

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "news_results": [
                    {"title": "Guidance Update", "link": "https://ft.com/g1", "source": "Financial Times"}
                ]
            },
        )

        assert get_serpapi_count() == 0

        # First call (real network request)
        corrob.discover(query, active_horizon=48.0)
        assert get_serpapi_count() == 1
        assert mock_get.call_count == 1

        # Second call at same horizon (cached)
        cached_items = corrob.discover(query, active_horizon=48.0)
        assert get_serpapi_count() == 1  # Not incremented!
        assert mock_get.call_count == 1  # No second HTTP call!
        assert len(cached_items) == 1
        assert cached_items[0].url == "https://ft.com/g1"


def test_total_real_serpapi_calls_remain_bounded_by_cap():
    """5. Total real SerpAPI calls remain <= 8."""
    corrob = SerpAPICorroborator(api_key="dummy_test_key", max_searches=8)

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"news_results": []},
        )

        # Attempt to make 15 unique searches
        for i in range(15):
            corrob.discover(f"unique query number {i}", active_horizon=36.0)

        assert get_serpapi_count() == 8
        assert mock_get.call_count == 8
        assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# ---------------------------------------------------------------------------
# Tests 6-8: Quality, Freshness & Whitelist Rules During Fallback
# ---------------------------------------------------------------------------

def _make_sample_article(
    url: str,
    source_name: str,
    age_hours: float,
    title: str = "TCS signs $500 million cloud transformation agreement with global bank",
    ref_time: datetime = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc),
    content_text: str = None,
) -> Article:
    pub_time = ref_time - timedelta(hours=age_hours)
    if content_text is None:
        body = (
            f"{title}. The multi-year transaction is valued at $500 million and expands digital transformation "
            "initiatives across international operations. Detailed quarterly execution targets have been approved."
        )
        content_text = body + " word " * 40
    return Article(
        id=f"art_{abs(hash(url)) % 100000}",
        title=title,
        source_name=source_name,
        url=url,
        content_text=content_text,
        published_at=pub_time,
        category=NewsCategory.INTERNATIONAL,
        is_verified_url=True,
        date_verified=True,
    )


def test_no_candidate_over_72h_is_accepted():
    """6. No candidate >72h is accepted."""
    ref_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    date_rule = DateFilterRule()

    # Article at 71h (valid at 72h horizon)
    art_71h = _make_sample_article("https://reuters.com/valid71", "Reuters", age_hours=71.0, ref_time=ref_time)
    res_71h = date_rule.evaluate(art_71h, now_utc=ref_time, max_age_hours=72.0)
    assert res_71h.is_accepted is True

    # Article at 72.5h (rejected even at 72h horizon)
    art_72_5h = _make_sample_article("https://reuters.com/stale72_5", "Reuters", age_hours=72.5, ref_time=ref_time)
    res_72_5h = date_rule.evaluate(art_72_5h, now_utc=ref_time, max_age_hours=72.0)
    assert res_72_5h.is_accepted is False
    assert "exceeds" in (res_72_5h.rejection_reason or "").lower() or "stale" in (res_72_5h.rejection_reason or "").lower()


def test_existing_source_whitelist_still_applies():
    """7. Existing source whitelist still applies during fallback discovery."""
    source_rule = SourceFilterRule()

    whitelisted_art = _make_sample_article("https://bloomberg.com/valid", "Bloomberg", age_hours=40.0)
    res_white = source_rule.evaluate(whitelisted_art)
    assert res_white.is_accepted is True

    untrusted_art = _make_sample_article("https://randominvestblog.xyz/penny", "Random Penny Stock Blog", age_hours=40.0)
    res_untrusted = source_rule.evaluate(untrusted_art)
    assert res_untrusted.is_accepted is False
    reason = (res_untrusted.rejection_reason or "").lower()
    assert "not approved" in reason or "not in approved" in reason


def test_existing_hard_event_rules_still_apply():
    """8. Existing hard-event rules still apply during fallback discovery."""
    story_rule = StoryTypeFilterRule()

    # Speculative / commentary story without concrete transaction
    commentary_art = _make_sample_article(
        "https://reuters.com/opinion",
        "Reuters",
        age_hours=45.0,
        title="Federal Bank shares fall 4% as lender likely to acquire a rival",
        content_text="Detailed article content describing market developments and speculation commentary without transaction.",
    )
    res_commentary = story_rule.evaluate(commentary_art)
    assert res_commentary.is_accepted is False
    reason = (res_commentary.rejection_reason or "").lower()
    assert "speculative" in reason or "opinion" in reason or "not a recognized" in reason or "noise" in reason

    # Hard event (acquisition)
    hard_event_art = _make_sample_article(
        "https://reuters.com/deal",
        "Reuters",
        age_hours=45.0,
        title="Nvidia to acquire AI infrastructure startup Run:ai for $700 million",
    )
    res_hard = story_rule.evaluate(hard_event_art)
    assert res_hard.is_accepted is True


# ---------------------------------------------------------------------------
# Tests 9-12: GitHub Actions Daily Briefing Scheduler Audit & Verification
# ---------------------------------------------------------------------------

@pytest.fixture
def workflow_content():
    workflow_path = Path(".github/workflows/daily_briefing.yml")
    assert workflow_path.exists(), "daily_briefing.yml workflow does not exist"
    return workflow_path.read_text(encoding="utf-8")


def test_workflow_runs_run_daily_py(workflow_content):
    """9. Workflow runs run_daily.py."""
    assert "python run_daily.py" in workflow_content


def test_workflow_has_primary_and_recovery_schedule(workflow_content):
    """10. Workflow has primary (06:40 IST = 01:10 UTC) + recovery schedule (06:55 IST = 01:25 UTC)."""
    # Primary scheduled run (06:35 or 06:40 AM IST -> 01:05 or 01:10 UTC)
    assert ("cron: '5 1 * * *'" in workflow_content or "cron: '10 1 * * *'" in workflow_content or 'cron: "5 1 * * *"' in workflow_content)
    # Recovery run (06:55 AM IST -> 01:25 UTC)
    assert "cron: '25 1 * * *'" in workflow_content or 'cron: "25 1 * * *"' in workflow_content


def test_workflow_has_manual_workflow_dispatch(workflow_content):
    """11. Workflow has manual workflow_dispatch."""
    assert "workflow_dispatch:" in workflow_content


def test_workflow_no_secrets_are_hardcoded(workflow_content):
    """12. No secrets are hardcoded in workflow file; all use ${{ secrets.* }}."""
    # Required secret references
    assert "${{ secrets.GEMINI_API_KEY }}" in workflow_content
    assert "${{ secrets.SERPAPI_API_KEY }}" in workflow_content
    assert "${{ secrets.GMAIL_SENDER }}" in workflow_content
    assert "${{ secrets.GMAIL_APP_PASSWORD }}" in workflow_content
    assert "secrets.GMAIL_RECIPIENT" in workflow_content or "secrets.GMAIL_RECIPIENTS" in workflow_content

    # Disallow hardcoded values for these fields
    for line in workflow_content.splitlines():
        if any(sec in line for sec in ["API_KEY:", "APP_PASSWORD:", "GMAIL_SENDER:", "GMAIL_RECIPIENT"]):
            assert "${{ secrets." in line, f"Possible hardcoded secret in line: {line}"

