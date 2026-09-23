"""
Production Hardening Regression and Parity Audit Tests.

Verifies:
1. Dashboard sync strictly enforces the 15-story contract (5 Domestic, 5 India, 5 International)
   and rejects legacy 10-story or partial briefings.
2. Domain matching is canonical and safe against lookalikes (fake-reuters.com, notmoneycontrol.com).
3. Source policy parity: unknown domains return UNKNOWN and are not extraction worthy.
4. Commercial PR distributor corroboration rules (PR distributors cannot corroborate company
   releases or each other for two-source independence).
5. Centralized invariants in config.py.
6. Unified numeric normalization delegation in run_daily_15.py.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.filtering.rules import SourceFilterRule, DomesticSourceFilterRule
from app.filtering.source_policy import get_tier, is_extraction_worthy, is_corroboration_eligible, _domain_in_set, SourcePolicy
from app.verification.verifier import TwoSourceVerifier, PRESS_RELEASE_DISTRIBUTOR_DOMAINS
from run_dashboard_sync import _build_briefing, parse_briefing_text, DashboardStory
import config
from run_daily_15 import _canonical_numbers


# =========================================================================
# 1. Dashboard Contract Verification (15 Stories Strictly Required)
# =========================================================================

def _make_dashboard_story(idx: int, section: str) -> DashboardStory:
    return DashboardStory(
        section=section,
        position=idx,
        headline=f"Headline #{idx} in {section}",
        summary=f"Summary of event #{idx} in {section} with facts and figures.",
        source="Reuters",
        url=f"https://www.reuters.com/business/story-{section}-{idx}",
    )


def test_dashboard_sync_rejects_10_story_briefing():
    """10-story briefing (India=5, Domestic=0, International=5) must be rejected."""
    stories = (
        [_make_dashboard_story(i, "india") for i in range(1, 6)]
        + [_make_dashboard_story(i, "international") for i in range(1, 6)]
    )
    briefing = _build_briefing("Briefing Text", date(2026, 9, 23), stories)
    assert briefing is None, "10-story briefing should be rejected by production sync"


def test_dashboard_sync_accepts_15_story_briefing():
    """15-story briefing (Domestic=5, India=5, International=5) must be accepted."""
    stories = (
        [_make_dashboard_story(i, "domestic") for i in range(1, 6)]
        + [_make_dashboard_story(i, "india") for i in range(1, 6)]
        + [_make_dashboard_story(i, "international") for i in range(1, 6)]
    )
    briefing = _build_briefing("Briefing Text", date(2026, 9, 23), stories)
    assert briefing is not None
    assert briefing.story_count == 15
    assert len(briefing.domestic_stories) == 5
    assert len(briefing.india_stories) == 5
    assert len(briefing.international_stories) == 5


def test_dashboard_sync_rejects_asymmetric_counts():
    """Any briefing missing even a single story from any section must be rejected."""
    # 4 Domestic, 5 India, 5 International
    s1 = (
        [_make_dashboard_story(i, "domestic") for i in range(1, 5)]
        + [_make_dashboard_story(i, "india") for i in range(1, 6)]
        + [_make_dashboard_story(i, "international") for i in range(1, 6)]
    )
    assert _build_briefing("Text", date(2026, 9, 23), s1) is None

    # 5 Domestic, 4 India, 5 International
    s2 = (
        [_make_dashboard_story(i, "domestic") for i in range(1, 6)]
        + [_make_dashboard_story(i, "india") for i in range(1, 5)]
        + [_make_dashboard_story(i, "international") for i in range(1, 6)]
    )
    assert _build_briefing("Text", date(2026, 9, 23), s2) is None

    # 5 Domestic, 5 India, 4 International
    s3 = (
        [_make_dashboard_story(i, "domestic") for i in range(1, 6)]
        + [_make_dashboard_story(i, "india") for i in range(1, 6)]
        + [_make_dashboard_story(i, "international") for i in range(1, 5)]
    )
    assert _build_briefing("Text", date(2026, 9, 23), s3) is None


def test_parse_briefing_text_requires_domestic_section():
    """parse_briefing_text must reject text lacking TOP 5 DOMESTIC HEADLINES."""
    text_without_domestic = """
    INVESTMENT COMMITTEE BRIEFING - 23 September 2026

    TOP 5 INDIA BUSINESS HEADLINES
    1. India Headline One
    Summary here.
    Source: Reuters (https://reuters.com/1)

    2. India Headline Two
    Summary here.
    Source: Reuters (https://reuters.com/2)

    3. India Headline Three
    Summary here.
    Source: Reuters (https://reuters.com/3)

    4. India Headline Four
    Summary here.
    Source: Reuters (https://reuters.com/4)

    5. India Headline Five
    Summary here.
    Source: Reuters (https://reuters.com/5)

    TOP 5 INTERNATIONAL BUSINESS HEADLINES
    1. Intl Headline One
    Summary here.
    Source: Reuters (https://reuters.com/i1)

    2. Intl Headline Two
    Summary here.
    Source: Reuters (https://reuters.com/i2)

    3. Intl Headline Three
    Summary here.
    Source: Reuters (https://reuters.com/i3)

    4. Intl Headline Four
    Summary here.
    Source: Reuters (https://reuters.com/i4)

    5. Intl Headline Five
    Summary here.
    Source: Reuters (https://reuters.com/i5)
    """
    assert parse_briefing_text(text_without_domestic, default_date=date(2026, 9, 23)) is None


# =========================================================================
# 2. Domain Matching and Lookalike Protection
# =========================================================================

def test_domain_in_set_subdomains_and_lookalikes():
    """Ensure exact and subdomains match, while lookalikes are strictly rejected."""
    approved = frozenset({"reuters.com", "moneycontrol.com", "economictimes.indiatimes.com"})

    # Valid exact and subdomains
    assert _domain_in_set("reuters.com", approved) is True
    assert _domain_in_set("www.reuters.com", approved) is True
    assert _domain_in_set("markets.reuters.com", approved) is True
    assert _domain_in_set("moneycontrol.com", approved) is True
    assert _domain_in_set("m.moneycontrol.com", approved) is True

    # Lookalikes and attacks
    assert _domain_in_set("fake-reuters.com", approved) is False
    assert _domain_in_set("notmoneycontrol.com", approved) is False
    assert _domain_in_set("reuters.com.attacker.example", approved) is False
    assert _domain_in_set("economictimes.indiatimes.com.phishing.org", approved) is False


def test_source_filter_rule_rejects_lookalike_domain_even_with_spoofed_name():
    """SourceFilterRule must reject fake-reuters.com even when source_name='Reuters'."""
    art = Article(
        id="art-spoofed-1",
        title="Major Acquisition Deal Announced",
        url="https://fake-reuters.com/markets/deal",
        source_name="Reuters",
        published_at=datetime.now(timezone.utc),
        content_text="A major corporate transaction was disclosed today." + " word" * 50,
        date_verified=True,
    )
    rule = SourceFilterRule()
    res = rule.evaluate(art)
    assert res.is_accepted is False
    assert "not in approved publisher whitelist" in res.rejection_reason


def test_domestic_source_filter_rule_rejects_lookalike_domain():
    """DomesticSourceFilterRule must reject lookalikes even with valid domestic source name."""
    art = Article(
        id="art-spoofed-2",
        title="National Policy Update Disclosed",
        url="https://notndtv.com/india-news/policy",
        source_name="NDTV",
        published_at=datetime.now(timezone.utc),
        content_text="Government announced a nationwide policy framework." + " word" * 50,
        date_verified=True,
    )
    rule = DomesticSourceFilterRule()
    res = rule.evaluate(art)
    assert res.is_accepted is False
    assert "not in the trusted domestic publisher registry" in res.rejection_reason


# =========================================================================
# 3. Source Policy Parity (Unknown Domains)
# =========================================================================

def test_source_policy_unknown_domain_handling():
    """Unlisted domains return UNKNOWN and are not extraction worthy."""
    url = "https://unapproved-crypto-blog.xyz/news/story-123"
    assert get_tier(url) == "UNKNOWN"
    assert is_extraction_worthy(url) is False
    assert is_corroboration_eligible(url) is False


# =========================================================================
# 4. Commercial PR Distributor Corroboration Rules
# =========================================================================

def test_pr_distributors_cannot_corroborate_first_party_company():
    """First-party company release + PRNewswire cannot qualify as two independent sources."""
    art_company = Article(
        id="art_fp_1",
        title="Apple Reports Third Quarter Financial Results",
        url="https://www.apple.com/newsroom/2026/09/apple-reports-q3-results/",
        source_name="Apple Newsroom",
        published_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        content_text="Apple today announced financial results for its fiscal 2026 third quarter ended June 27, 2026. The Company posted quarterly revenue of $85.8 billion.",
        category=NewsCategory.INTERNATIONAL,
        date_verified=True,
    )
    art_company.metadata = {"source_class": "FIRST_PARTY_PRIMARY"}

    art_pr = Article(
        id="art_pr_1",
        title="Apple Reports Third Quarter Results On PRNewswire",
        url="https://www.prnewswire.com/news-releases/apple-q3-results-2026.html",
        source_name="PR Newswire",
        published_at=datetime(2026, 9, 20, 10, 5, tzinfo=timezone.utc),
        content_text="Apple today announced financial results for its fiscal 2026 third quarter ended June 27, 2026. Revenue reached $85.8 billion.",
        category=NewsCategory.INTERNATIONAL,
        date_verified=True,
    )

    ev = Event(
        id="ev_apple_q3",
        canonical_title="Apple Reports Third Quarter Results",
        description="Apple reports fiscal third quarter results with revenue.",
        event_date=date(2026, 9, 20),
        category=NewsCategory.INTERNATIONAL,
        article_ids=[art_company.id, art_pr.id],
    )

    ref_time = datetime(2026, 9, 20, 11, 0, tzinfo=timezone.utc)
    verifier = TwoSourceVerifier()
    res = verifier.verify_event(ev, [art_company, art_pr], now_utc=ref_time)
    assert res.is_independent is False
    assert "Commercial PR distributors cannot corroborate first-party corporate announcements" in (res.matching_details or "")


def test_pr_distributors_cannot_corroborate_each_other():
    """Two commercial PR distributors (e.g. PRNewswire + GlobeNewswire) cannot corroborate each other."""
    art_pr1 = Article(
        id="art_pr_1",
        title="MegaCorp Signs Definitive Merger Agreement for $4 Billion",
        url="https://www.prnewswire.com/news-releases/megacorp-merger-deal.html",
        source_name="PR Newswire",
        published_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        content_text="MegaCorp entered into a definitive merger agreement to acquire TargetCorp for $4 billion in cash.",
        category=NewsCategory.INTERNATIONAL,
        date_verified=True,
    )

    art_pr2 = Article(
        id="art_pr_2",
        title="TargetCorp Announces Merger Agreement with MegaCorp",
        url="https://www.globenewswire.com/news-release/2026/09/targetcorp-merger.html",
        source_name="GlobeNewswire",
        published_at=datetime(2026, 9, 20, 10, 2, tzinfo=timezone.utc),
        content_text="TargetCorp entered into a definitive merger agreement to be acquired by MegaCorp for $4 billion in cash.",
        category=NewsCategory.INTERNATIONAL,
        date_verified=True,
    )

    ev = Event(
        id="ev_merger",
        canonical_title="MegaCorp to Acquire TargetCorp for $4 Billion",
        description="MegaCorp agrees to acquire TargetCorp for cash.",
        event_date=date(2026, 9, 20),
        category=NewsCategory.INTERNATIONAL,
        article_ids=[art_pr1.id, art_pr2.id],
    )

    ref_time = datetime(2026, 9, 20, 11, 0, tzinfo=timezone.utc)
    verifier = TwoSourceVerifier()
    res = verifier.verify_event(ev, [art_pr1, art_pr2], now_utc=ref_time)
    assert res.is_independent is False
    assert "Commercial PR distributors cannot corroborate each other" in (res.matching_details or "")


# =========================================================================
# 5. Centralized Invariants and Unified Numeric Normalization
# =========================================================================

def test_centralized_invariants_and_targets():
    """Ensure canonical business invariants match requirements."""
    assert config.DOMESTIC_TARGET == 5
    assert config.INDIA_TARGET == 5
    assert config.INTERNATIONAL_TARGET == 5
    assert config.TOTAL_STORY_TARGET == 15
    assert config.INDIA_MATERIALITY_THRESHOLD == 60.0
    assert config.DEDUP_LOOKBACK_DAYS == 3
    assert config.FRESHNESS_LADDER_HOURS == (24.0, 36.0, 48.0, 72.0)


def test_canonical_number_normalization_shared():
    """Ensure run_daily_15._canonical_numbers handles Indian and Western numbering consistently."""
    n1 = _canonical_numbers("Company net profit surged 5,57,700% in Q1")
    n2 = _canonical_numbers("Company net profit surged 557,700% in Q1")
    assert "557700" in n1
    assert n1 == n2
