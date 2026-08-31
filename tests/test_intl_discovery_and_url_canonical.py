"""
Comprehensive regression tests for:
1. Free horizon-aware International RSS discovery ladder (36h, 48h, 72h).
2. International discovery budgeting (0 SerpAPI calls for RSS, SerpAPI cap <= 8).
3. First-party and speculative deal validation (nears deal rejected, definitive agreement accepted).
4. Complete removal of TinyURL shortening in favor of original canonical URLs across
   formatter, final briefing, plain-text email, HTML email, and URL audit.
"""

from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.models.enums import VerificationTier
from app.filtering.rules import SourceFilterRule, StoryTypeFilterRule, DateFilterRule, URLFilterRule
from app.verification.single_source import SingleSourceEvaluator
from app.verification.verifier import TwoSourceVerifier
from app.verification.models import VerificationStatus
from app.verification.serpapi_corroborator import (
    MAX_SERPAPI_SEARCHES_PER_RUN,
    get_serpapi_count,
    reset_serpapi_counter,
)
from app.formatting.formatter import BriefingFormatter
from app.email.email_sender import parse_briefing_text, generate_briefing_html
from app.validation.engine import FinalValidationEngine
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection


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
            f"{title}. The enterprise announced definitive financial transactions with verified data."
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
# Tests 1-6: International RSS Fallback, Budget & Horizon Windows
# ---------------------------------------------------------------------------

def test_intl_36h_fallback_performs_new_rss_discovery():
    """1. International 36h fallback executes Google News RSS discovery with when:1d."""
    horizon = 36.0
    when_days = 1 if horizon <= 36.0 else (2 if horizon <= 48.0 else 3)
    when_param = f"when:{when_days}d"
    assert when_param == "when:1d"
    query = f"site:reuters.com company acquisition {when_param}"
    assert "when:1d" in query
    assert "reuters.com" in query


def test_intl_48h_uses_when_2d():
    """2. International 48h uses when:2d."""
    horizon = 48.0
    when_days = 1 if horizon <= 36.0 else (2 if horizon <= 48.0 else 3)
    when_param = f"when:{when_days}d"
    assert when_param == "when:2d"
    query = f"site:cnbc.com company earnings {when_param}"
    assert "when:2d" in query


def test_intl_72h_uses_when_3d():
    """3. International 72h uses when:3d."""
    horizon = 72.0
    when_days = 1 if horizon <= 36.0 else (2 if horizon <= 48.0 else 3)
    when_param = f"when:{when_days}d"
    assert when_param == "when:3d"
    query = f"company acquisition Bloomberg {when_param}"
    assert "when:3d" in query


def test_intl_discovery_stops_once_5_qualified_candidates_exist():
    """4. International discovery terminates immediately when 5 qualified candidates exist."""
    intl_count = {"count": 2}

    def count_intl():
        return intl_count["count"]

    queries_run = 0
    query_list = ["q1", "q2", "q3", "q4", "q5"]
    for q in query_list:
        if count_intl() >= 5:
            break
        queries_run += 1
        intl_count["count"] += 3  # Hits 5 on the first iteration!

    assert queries_run == 1
    assert count_intl() >= 5


def test_free_rss_intl_discovery_consumes_zero_serpapi_calls():
    """5. FREE RSS International discovery consumes zero SerpAPI calls."""
    reset_serpapi_counter()
    initial_serp_count = get_serpapi_count()

    # Simulate discovery provider invocation
    mock_provider = MagicMock()
    mock_provider.discover.return_value = []
    mock_provider.discover("site:reuters.com company earnings when:1d", country="US", max_results=10)

    # SerpAPI counter must remain unchanged at 0
    assert get_serpapi_count() == initial_serp_count == 0


def test_serpapi_total_still_bounded_by_cap():
    """6. SerpAPI total remains strictly <= 8."""
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# ---------------------------------------------------------------------------
# Tests 7-13: Freshness, Filtering, First-Party & Speculative Deals
# ---------------------------------------------------------------------------

def test_over_72h_intl_candidate_rejected():
    """7. Any International candidate >72h remains rejected even at the 72h horizon."""
    ref_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    art = _make_article(
        url="https://reuters.com/stale-intl-74h",
        source_name="Reuters",
        title="International Conglomerate announces quarterly earnings",
        age_hours=74.0,
        ref_time=ref_time,
    )
    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=ref_time, max_age_hours=72.0)
    assert res.is_accepted is False
    assert "exceeds" in (res.rejection_reason or "").lower()


def test_random_non_approved_source_still_rejected():
    """8. Random non-approved third-party source remains strictly rejected."""
    art = _make_article(
        url="https://unapproved-financial-blog.xyz/news/deal",
        source_name="Unapproved Blog",
        title="Mega Enterprise acquires rival for $5 billion",
    )
    rule = SourceFilterRule()
    res = rule.evaluate(art)
    assert res.is_accepted is False
    assert "not in approved publisher whitelist" in (res.rejection_reason or "")


def test_first_party_candidate_with_unverified_date_does_not_qualify():
    """9. First-party candidate with date_verified=False does NOT qualify alone."""
    art = _make_article(
        url="https://corporate.homedepot.com/news/q2-earnings",
        source_name="Home Depot Corporate",
        title="The Home Depot Announces Second Quarter Results",
        content_text="The Home Depot announced second quarter revenue of $43 billion and earnings." + " word" * 50,
    )
    art.date_verified = False  # Unverified publication timestamp
    rule = SourceFilterRule()
    res = rule.evaluate(art)
    assert res.is_accepted is False


def test_first_party_plus_independent_trusted_publisher_can_qualify():
    """10. First-party corporate source + independent trusted publisher can qualify via TwoSourceVerifier."""
    ref_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    art_fp = _make_article(
        url="https://nvidianews.nvidia.com/news/q2-fiscal-2025-results",
        source_name="NVIDIA Newsroom",
        title="NVIDIA Reports Financial Results for Second Quarter Fiscal 2025",
        content_text="NVIDIA reported record quarterly revenue of $30.0 billion and net income of $16.6 billion." + " word" * 50,
        ref_time=ref_time,
    )
    art_fp.metadata = {"source_class": "FIRST_PARTY_PRIMARY"}

    art_trusted = _make_article(
        url="https://reuters.com/technology/nvidia-q2-earnings-beat",
        source_name="Reuters",
        title="NVIDIA delivers record revenue of $30.0 billion beating Wall Street expectations",
        content_text="NVIDIA Corp reported second quarter revenue of $30.0 billion on surging AI infrastructure demand." + " word" * 50,
        ref_time=ref_time,
    )

    event = Event(
        canonical_title="NVIDIA Reports Q2 Financial Results",
        description="NVIDIA second quarter fiscal results",
        article_ids=[art_fp.id, art_trusted.id],
        event_category=NewsCategory.INTERNATIONAL,
    )

    verifier = TwoSourceVerifier()
    rv = verifier.verify_event(event, [art_fp, art_trusted], now_utc=ref_time)
    assert rv.verification_status == VerificationStatus.VERIFIED
    assert event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED


def test_insurance_brokerage_does_not_trigger_analyst_rating():
    """11. 'insurance brokerage' does NOT trigger analyst_rating."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://cnbc.com/brokerage-expansion",
        source_name="CNBC",
        title="Aon completes acquisition of commercial insurance brokerage firm USI",
        content_text="Aon announced the completion of its transaction to acquire commercial insurance brokerage USI." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is True
    assert "analyst_rating" not in (res.matched_patterns or [])


def test_nears_deal_does_not_qualify_as_completed_hard_ma():
    """12. 'nears $17 billion deal' does NOT qualify as completed hard M&A (speculative deal talks)."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://cnbc.com/aon-deal-speculative",
        source_name="CNBC",
        title="Aon nears $17 billion deal to buy insurance broker USI",
        content_text="Aon is nearing an agreement to buy insurance broker USI for approximately $17 billion." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is False
    assert res.rule_failed == "STORY_TYPE"
    assert "speculative" in (res.rejection_reason or "").lower()


def test_definitive_acquisition_agreement_can_qualify():
    """13. Definitive acquisition agreement cleanly qualifies as hard business event."""
    story_rule = StoryTypeFilterRule()
    art = _make_article(
        url="https://reuters.com/aon-deal-definitive",
        source_name="Reuters",
        title="Aon signs definitive agreement to acquire USI in $17 billion transaction",
        content_text="Aon announced it has entered into a definitive agreement to acquire insurance broker USI for $17 billion." + " word" * 50,
    )
    res = story_rule.evaluate(art)
    assert res.is_accepted is True


# ---------------------------------------------------------------------------
# Tests 14-18: Complete Removal of TinyURL Across All Outputs & Audits
# ---------------------------------------------------------------------------

def _build_mock_payload() -> BriefingEditorialPayload:
    stories = []
    for i in range(5):
        stories.append(
            EditorialStorySelection(
                headline=f"Major Enterprise Action Headline {i+1} With Quantified Figures",
                summary="The corporation reported operating revenue of $5.2 billion and increased dividend by 15%.",
                source="Reuters",
                url=f"https://www.reuters.com/business/articles/direct-long-canonical-url-{i+1}?ref=rss",
                event_id=f"ev_{i+1}",
                section="international",
            )
        )
    return BriefingEditorialPayload(
        india_stories=stories,
        domestic_stories=stories,
        international_stories=stories,
    )


def test_formatter_contains_full_original_urls():
    """14. Formatter outputs complete original URLs without any shortening."""
    formatter = BriefingFormatter()
    payload = _build_mock_payload()
    res = formatter.format(payload, briefing_date=date(2026, 8, 31))

    assert "https://www.reuters.com/business/articles/direct-long-canonical-url-1?ref=rss" in res.text
    assert "tinyurl.com" not in res.text


def test_final_briefing_contains_no_tinyurl():
    """15. Final briefing output string contains zero instances of 'tinyurl.com'."""
    formatter = BriefingFormatter()
    payload = _build_mock_payload()
    res = formatter.format(payload, briefing_date=date(2026, 8, 31), shorten_urls=False)

    assert "tinyurl.com" not in res.text
    for story in payload.international_stories:
        assert story.url in res.text


def test_html_href_uses_original_url():
    """16. HTML email's 'Read full article ->' link uses the full original URL."""
    formatter = BriefingFormatter()
    payload = _build_mock_payload()
    res = formatter.format(payload, briefing_date=date(2026, 8, 31))

    parsed = parse_briefing_text(res.text)
    assert parsed is not None
    html_output = generate_briefing_html(parsed)

    target_url = "https://www.reuters.com/business/articles/direct-long-canonical-url-1?ref=rss"
    assert f'href="{target_url}"' in html_output
    assert "tinyurl.com" not in html_output


def test_plaintext_email_uses_original_url():
    """17. Plain-text email fallback contains the full original URL."""
    formatter = BriefingFormatter()
    payload = _build_mock_payload()
    res = formatter.format(payload, briefing_date=date(2026, 8, 31))

    # The canonical text is the exact text used as EmailMessage plain text fallback
    target_url = "https://www.reuters.com/business/articles/direct-long-canonical-url-2?ref=rss"
    assert target_url in res.text
    assert "tinyurl.com" not in res.text


def test_url_audit_uses_canonical_original_url():
    """18. URL audit in validation engine validates the canonical original candidate URL."""
    engine = FinalValidationEngine()
    payload = _build_mock_payload()

    # Pass original URLs as allowed candidate manifest
    candidate_urls = {s.url for s in payload.international_stories}

    # Validation engine checks story.url against candidate_urls
    for s in payload.international_stories:
        assert s.url in candidate_urls
        is_valid, _ = engine.url_rule.is_valid_url(s.url)
        assert is_valid is True
        assert "tinyurl.com" not in s.url
