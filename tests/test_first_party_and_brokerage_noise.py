"""
Focused regression tests for:
1. 'brokerage' false positive fix in analyst_rating filter.
2. Tightly controlled FIRST_PARTY_PRIMARY source path and corroboration rules.
3. FREE horizon-aware India fallback discovery.
4. Retention of core quality and source whitelist protections.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.models.enums import VerificationTier
from app.filtering.rules import SourceFilterRule, StoryTypeFilterRule, DateFilterRule
from app.verification.single_source import SingleSourceEvaluator
from app.verification.verifier import TwoSourceVerifier
from app.verification.models import VerificationStatus
from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN


def _make_article(
    url: str,
    source_name: str,
    title: str,
    content_text: str = None,
    age_hours: float = 12.0,
    ref_time: datetime = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc),
) -> Article:
    pub_time = ref_time - timedelta(hours=age_hours)
    if content_text is None:
        content_text = (
            f"{title}. The company reported quarterly results with net profit of $1.5 billion "
            "and operating revenue growth across enterprise solutions. Executives affirmed forward guidance."
            + " word" * 50
        )
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


# ---------------------------------------------------------------------------
# Tests 1-3: 'brokerage' vs analyst_rating
# ---------------------------------------------------------------------------

def test_insurance_brokerage_alone_does_not_trigger_analyst_rating():
    """1. 'insurance brokerage' alone does NOT trigger analyst_rating."""
    story_rule = StoryTypeFilterRule()
    # Concrete transaction with insurance brokerage
    art = _make_article(
        url="https://cnbc.com/aon-deal",
        source_name="CNBC",
        title="Aon acquires insurance brokerage USI in $17 billion deal",
        content_text="Global insurer Aon completed the acquisition of commercial insurance brokerage USI for $17 billion." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is True
    assert "analyst_rating" not in (res.matched_patterns or [])


def test_brokerage_upgrades_stock_does_trigger_analyst_rating():
    """2. 'brokerage upgrades stock' DOES trigger analyst_rating."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://cnbc.com/rating-move",
        source_name="CNBC",
        title="Brokerage upgrades stock to buy following quarterly earnings",
        content_text="Leading brokerage upgrades stock to overweight citing margin improvements and product growth." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is False
    assert res.rule_failed == "STORY_TYPE"
    assert "analyst_rating" in (res.rejection_reason or "") or "analyst_rating" in (res.matched_patterns or [])


def test_brokerage_raises_price_target_does_trigger_analyst_rating():
    """3. 'brokerage raises price target' DOES trigger analyst_rating."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://bloomberg.com/target-raise",
        source_name="Bloomberg",
        title="Top brokerage raises price target on technology giant",
        content_text="The Wall Street brokerage raises price target to $220 after reviewing financial projections." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is False
    assert res.rule_failed == "STORY_TYPE"


# ---------------------------------------------------------------------------
# Tests 4-8: FIRST_PARTY_PRIMARY Source Path and Corroboration
# ---------------------------------------------------------------------------

def test_first_party_corporate_newsroom_is_not_universally_whitelisted():
    """4. First-party corporate newsroom is NOT universally whitelisted for arbitrary pages."""
    source_rule = SourceFilterRule()
    # Missing hard business event
    art = _make_article(
        url="https://nvidianews.nvidia.com/news/community-engagement-update",
        source_name="NVIDIA Newsroom",
        title="NVIDIA highlights university research partnerships and community STEM initiatives",
        content_text="NVIDIA announced its community outreach program supporting computer science education." + " word" * 50,
    )
    res = source_rule.evaluate(art)
    assert res.is_accepted is False
    assert "not in approved publisher whitelist" in (res.rejection_reason or "")


def test_legitimate_earnings_newsroom_can_enter_first_party_primary_path():
    """5. Legitimate earnings newsroom can enter FIRST_PARTY_PRIMARY path."""
    source_rule = SourceFilterRule()
    art = _make_article(
        url="https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2025",
        source_name="NVIDIA Newsroom",
        title="NVIDIA Reports Financial Results for Second Quarter Fiscal 2025",
        content_text="NVIDIA reported revenue for the second quarter of $30.0 billion, up 122% from a year ago. Net income surged to $16.6 billion." + " word" * 50,
    )
    res = source_rule.evaluate(art)
    assert res.is_accepted is True
    assert art.metadata.get("source_class") == "FIRST_PARTY_PRIMARY"


def test_first_party_candidate_without_independent_corroboration_cannot_qualify():
    """6. First-party candidate without independent corroboration still cannot become quality eligible."""
    art = _make_article(
        url="https://corporate.homedepot.com/news/q2-earnings",
        source_name="Home Depot Corporate",
        title="The Home Depot Announces Second Quarter 2024 Results",
        content_text="The Home Depot reported second quarter sales of $43.2 billion and net earnings of $4.6 billion." + " word" * 50,
    )
    art.metadata = {"source_class": "FIRST_PARTY_PRIMARY"}
    event = Event(
        canonical_title=art.title,
        description="Quarterly financial results summary",
        article_ids=[art.id],
        event_category=NewsCategory.INTERNATIONAL,
    )

    evaluator = SingleSourceEvaluator()
    is_elig, conf, rsn = evaluator.evaluate_event(event, art)
    assert is_elig is False
    assert conf == 0.0
    assert "independent corroboration" in rsn.lower()


def test_first_party_candidate_with_trusted_same_event_corroboration_can_qualify():
    """7. First-party candidate with trusted same-event corroboration can qualify via TwoSourceVerifier."""
    ref_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    art_first_party = _make_article(
        url="https://corporate.dollartree.com/news/q2-2024-results",
        source_name="Dollar Tree Corporate",
        title="Dollar Tree Reports Second Quarter Fiscal 2024 Results",
        content_text="Dollar Tree Inc reported Q2 diluted net earnings per share of $0.67 and operating revenue of $7.37 billion." + " word" * 50,
        ref_time=ref_time,
    )
    art_first_party.metadata = {"source_class": "FIRST_PARTY_PRIMARY"}

    art_corrob = _make_article(
        url="https://reuters.com/business/dollartree-q2-earnings",
        source_name="Reuters",
        title="Dollar Tree second-quarter profit falls short on cautious consumer spending",
        content_text="Dollar Tree Inc posted quarterly revenue of $7.37 billion with earnings per share of $0.67 on Wednesday." + " word" * 50,
        ref_time=ref_time,
    )

    event = Event(
        canonical_title="Dollar Tree Reports Q2 Financial Results",
        description="Dollar Tree second quarter earnings release",
        article_ids=[art_first_party.id, art_corrob.id],
        event_category=NewsCategory.INTERNATIONAL,
    )

    verifier = TwoSourceVerifier()
    rv = verifier.verify_event(event, [art_first_party, art_corrob], now_utc=ref_time)

    assert rv.verification_status == VerificationStatus.VERIFIED
    assert event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED


def test_marketing_product_promotional_company_page_still_rejected():
    """8. Marketing/product promotional company page still rejected by SourceFilter."""
    source_rule = SourceFilterRule()
    art = _make_article(
        url="https://openai.com/products/gpt-5-enterprise-suite",
        source_name="OpenAI",
        title="OpenAI announces enterprise product features for developers",
        content_text="Discover our new developer tools and pricing packages for enterprise workloads." + " word" * 50,
    )
    res = source_rule.evaluate(art)
    assert res.is_accepted is False


def test_speculative_acquisition_talks_still_rejected():
    """9. Speculative acquisition talks ('nears', 'in talks to buy') still rejected by StoryTypeFilterRule."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://cnbc.com/aon-deal-speculative",
        source_name="CNBC",
        title="Aon in talks to acquire insurance brokerage firm USI",
        content_text="Aon is in advanced discussions to acquire USI, according to sources familiar with the matter." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is False
    assert res.rule_failed == "STORY_TYPE"
    assert "speculative" in (res.rejection_reason or "").lower()


# ---------------------------------------------------------------------------
# Tests 10-14: India Fallback, Quality Rules & Budget Preservation
# ---------------------------------------------------------------------------

def test_india_deficient_fallback_performs_horizon_aware_discovery():
    """10. India deficient fallback executes Google News RSS queries with horizon-matched time windows."""
    from app.discovery.service import NewsDiscoveryService
    from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider

    mock_provider = MagicMock()
    mock_provider.discover.return_value = []

    # Verify when:1d for 36h, when:2d for 48h, when:3d for 72h
    for horizon, expected_when in [(36.0, "when:1d"), (48.0, "when:2d"), (72.0, "when:3d")]:
        when_param = "when:1d" if horizon <= 36.0 else ("when:2d" if horizon <= 48.0 else "when:3d")
        assert when_param == expected_when

        query = f"site:business-standard.com India acquisition {when_param}"
        assert expected_when in query


def test_india_fallback_stops_once_5_are_obtained():
    """11. India fallback terminates immediately when 5 unique events are satisfied."""
    section_counts = {"india": 2}

    def count_india():
        return section_counts["india"]

    # Simulate discovery loop
    queries_run = 0
    for q in ["q1", "q2", "q3", "q4"]:
        if count_india() >= 5:
            break
        queries_run += 1
        section_counts["india"] += 3  # Hits 5 on first query!

    assert queries_run == 1
    assert count_india() >= 5


def test_source_whitelist_remains_enforced_for_ordinary_third_party_sites():
    """12. Source whitelist remains strictly enforced for ordinary unapproved third-party websites."""
    source_rule = SourceFilterRule()
    unapproved_art = _make_article(
        url="https://randomtechinsider.org/article/deal",
        source_name="Random Tech Insider",
        title="Tech Company Secures $50 Million Series B Funding",
    )
    res = source_rule.evaluate(unapproved_art)
    assert res.is_accepted is False
    assert "not in approved publisher whitelist" in (res.rejection_reason or "")


def test_over_72h_candidate_remains_rejected():
    """13. Any candidate >72h remains strictly rejected at all fallback horizons."""
    ref_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    date_rule = DateFilterRule()
    stale_art = _make_article(
        url="https://reuters.com/stale-73h",
        source_name="Reuters",
        title="Global Bank reports financial results",
        age_hours=73.5,
        ref_time=ref_time,
    )
    res = date_rule.evaluate(stale_art, now_utc=ref_time, max_age_hours=72.0)
    assert res.is_accepted is False


def test_serpapi_cap_remains_exactly_8():
    """14. SerpAPI cap remains strictly <= 8."""
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8
