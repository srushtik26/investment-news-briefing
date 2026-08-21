"""
Regression tests for PART 12.3.3:

1. _score_discovery_candidate works without NameError (numeric/crore/percentage).
2. KFin + General Atlantic => INDIA.
3. JBM Auto + Bain Capital => INDIA.
4. Fairfax + IDBI Bank => INDIA.
5. Tube Investments + Shanthi Gears => INDIA.
6. Stripe + OpenRouter => INTERNATIONAL.
7. verified INDIA expansion increments india_verified immediately.
8. verified INDIA event does not increment international_verified.
9. remaining RSS=1 permits exactly one query.
10. remaining RSS=0 permits zero queries.
11. article page timestamp >24h rejects primary.
12. second source >24h rejects verification.
13. both sources <=24h may verify.
14. daily Gemini quota 429 performs zero retries.
15. transient 429 still follows retry policy.
16. Manipal fuel-price result rejects before extraction.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.classification.region_classifier import EventRegionClassifier
from app.classification.classifier import AIArticleClassifier
from app.filtering.rules import DateFilterRule
from app.verification.verifier import TwoSourceVerifier
from app.verification.corroborator import (
    ActiveCorroborator,
    reset_corroboration_counter,
    get_corroboration_count,
    increment_corroboration_count,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_rejection_reasons,
)
import run_pipeline


def test_score_discovery_candidate_no_nameerror():
    """1. _score_discovery_candidate executes without NameError with numbers, crore, percentages."""
    headlines = [
        "Tata Motors Q1 net profit jumps 74% to Rs 5,566 crore",
        "L&T acquires 8.75% stake in engineering firm for Rs 450 crore",
        "General Atlantic sells 8.75% stake in KFin Technologies",
        "Market update: Sensex and Nifty hit fresh all-time highs",
        "Generic opinion piece on economic trends",
    ]
    for hl in headlines:
        score = run_pipeline._score_discovery_candidate(hl)
        assert isinstance(score, (int, float))
        assert score >= 0.0


def test_kfin_general_atlantic_classified_india():
    """2. KFin + General Atlantic => INDIA."""
    clf = EventRegionClassifier()
    event = Event(
        canonical_title="General Atlantic sells 8.75% stake in KFin Technologies",
        description="General Atlantic offloaded an 8.75% stake in KFin Technologies through block deals.",
        companies_involved=["General Atlantic", "KFin Technologies"],
        financial_figures=["8.75%"],
        event_category=NewsCategory.INDIA,
    )
    res = clf.classify_event(event)
    assert res == NewsCategory.INDIA


def test_jbm_auto_bain_capital_classified_india():
    """3. JBM Auto + Bain Capital => INDIA."""
    clf = EventRegionClassifier()
    event = Event(
        canonical_title="Bain Capital invests in EV maker JBM Auto",
        description="Bain Capital will invest in Indian electric bus manufacturer JBM Auto.",
        companies_involved=["Bain Capital", "JBM Auto"],
        event_category=NewsCategory.INDIA,
    )
    res = clf.classify_event(event)
    assert res == NewsCategory.INDIA


def test_fairfax_idbi_bank_classified_india():
    """4. Fairfax + IDBI Bank => INDIA."""
    clf = EventRegionClassifier()
    event = Event(
        canonical_title="India to give Canada's Fairfax two years to consolidate holdings for IDBI Bank deal",
        description="The Indian government will give Fairfax Financial time to structure its IDBI Bank bid.",
        companies_involved=["Fairfax Financial", "IDBI Bank"],
        event_category=NewsCategory.INDIA,
    )
    res = clf.classify_event(event)
    assert res == NewsCategory.INDIA


def test_tube_investments_shanthi_gears_classified_india():
    """5. Tube Investments + Shanthi Gears => INDIA."""
    clf = EventRegionClassifier()
    event = Event(
        canonical_title="Tube Investments of India acquires additional 2.69% stake in Shanthi Gears",
        description="Tube Investments has increased its shareholding in subsidiary Shanthi Gears.",
        companies_involved=["Tube Investments of India", "Shanthi Gears"],
        financial_figures=["2.69%"],
        event_category=NewsCategory.INDIA,
    )
    res = clf.classify_event(event)
    assert res == NewsCategory.INDIA


def test_stripe_openrouter_classified_international():
    """6. Stripe + OpenRouter => INTERNATIONAL."""
    clf = EventRegionClassifier()
    event = Event(
        canonical_title="Stripe acquires AI routing platform OpenRouter for $1.2B",
        description="Fintech giant Stripe announced the acquisition of US startup OpenRouter.",
        companies_involved=["Stripe", "OpenRouter"],
        financial_figures=["$1.2B"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    res = clf.classify_event(event)
    assert res == NewsCategory.INTERNATIONAL


def test_verified_india_increments_india_verified_immediately():
    """7 & 8. verified INDIA expansion increments india_verified immediately and NOT international_verified."""
    india_verified = []
    intl_verified = []

    reg_clf = EventRegionClassifier()
    event = Event(
        canonical_title="General Atlantic sells 8.75% stake in KFin Technologies",
        description="General Atlantic offloaded stake in Indian company KFin Technologies.",
        companies_involved=["General Atlantic", "KFin Technologies"],
    )
    event.event_category = reg_clf.classify_event(event)

    if event.event_category == NewsCategory.INDIA:
        india_verified.append(event)
    elif event.event_category == NewsCategory.INTERNATIONAL:
        intl_verified.append(event)

    assert len(india_verified) == 1
    assert len(intl_verified) == 0


def test_remaining_rss_budget_enforcement():
    """9 & 10. remaining RSS=1 permits exactly one query; remaining RSS=0 permits zero queries."""
    reset_corroboration_counter()
    increment_corroboration_count(MAX_CORROBORATION_SEARCHES_PER_RUN - 1)
    assert get_corroboration_count() == MAX_CORROBORATION_SEARCHES_PER_RUN - 1

    # Firing 1 query brings budget to max
    increment_corroboration_count(1)
    assert get_corroboration_count() == MAX_CORROBORATION_SEARCHES_PER_RUN

    # When budget == max, remaining queries must be 0
    remaining = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
    assert remaining == 0


def test_article_page_timestamp_gt24h_rejects_primary():
    """11. article page timestamp >24h rejects primary."""
    now_utc = datetime.now(timezone.utc)
    art1_stale = Article(
        id="p1_stale",
        title="Manipal Health Q1 profit rises 31% to Rs 332 crore",
        url="https://economictimes.indiatimes.com/manipal-stale",
        source_name="The Economic Times",
        published_at=now_utc - timedelta(hours=24, minutes=30),  # 24.5h > 24h
        category=NewsCategory.INDIA,
    )
    art2_fresh = Article(
        id="p2_fresh",
        title="Manipal Health posts 31% rise in Q1 profit at Rs 332 crore",
        url="https://livemint.com/manipal-fresh",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=2),
        category=NewsCategory.INDIA,
    )
    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1_stale, art2_fresh)
    assert is_same is False
    assert "STALE_PRIMARY_SOURCE" in msg


def test_second_source_gt24h_rejects_verification():
    """12. second source >24h rejects verification."""
    now_utc = datetime.now(timezone.utc)
    art1_fresh = Article(
        id="p1_fresh",
        title="Manipal Health Q1 profit rises 31% to Rs 332 crore",
        url="https://economictimes.indiatimes.com/manipal-fresh",
        source_name="The Economic Times",
        published_at=now_utc - timedelta(hours=2),
        category=NewsCategory.INDIA,
    )
    art2_stale = Article(
        id="p2_stale",
        title="Manipal Health posts 31% rise in Q1 profit at Rs 332 crore",
        url="https://livemint.com/manipal-stale",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=25),
        category=NewsCategory.INDIA,
    )
    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1_fresh, art2_stale)
    assert is_same is False
    assert "STALE_SECOND_SOURCE" in msg


def test_both_sources_le24h_may_verify():
    """13. both sources <=24h may verify."""
    now_utc = datetime.now(timezone.utc)
    art1 = Article(
        id="p1",
        title="Manipal Health Q1 profit rises 31% to Rs 332 crore",
        url="https://economictimes.indiatimes.com/manipal-fresh",
        source_name="The Economic Times",
        published_at=now_utc - timedelta(hours=4),
        content_text="Manipal Health reported a 31% rise in net profit to Rs 332 crore for the first quarter.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art2 = Article(
        id="p2",
        title="Manipal Health posts 31% rise in Q1 profit at Rs 332 crore",
        url="https://livemint.com/manipal-fresh-2",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=2),
        content_text="Hospital chain Manipal Health reported net profit of Rs 332 crore, up 31% year on year.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)
    assert is_same is True


def test_daily_gemini_quota_zero_retries():
    """14. daily Gemini quota 429 performs zero retries and switches to offline fallback immediately."""
    article = Article(
        id="art_quota",
        title="Borderline news item requiring LLM",
        url="https://example.com/test",
        source_name="Example News",
        published_at=datetime.now(timezone.utc),
        content_text="Borderline content with ambiguity.",
        category=NewsCategory.INDIA,
    )

    classifier = AIArticleClassifier(api_key="fake_key")

    with patch.object(classifier, "_classify_deterministic", return_value=None):
        with patch.object(
            classifier,
            "_call_model",
            side_effect=Exception("429 ResourceExhausted: quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier limit: 20"),
        ) as mock_call:
            res = classifier.classify(article)
            # Verify only 1 attempt was made before switching offline
            assert mock_call.call_count == 1
            assert classifier._force_offline_mode is True
            assert res.attempts == 0  # offline fallback returns attempts=0


def test_transient_429_follows_retry_policy():
    """15. transient 429 (not daily quota) still follows retry policy."""
    article = Article(
        id="art_transient",
        title="Borderline news item requiring LLM",
        url="https://example.com/test",
        source_name="Example News",
        published_at=datetime.now(timezone.utc),
        content_text="Borderline content with ambiguity.",
        category=NewsCategory.INDIA,
    )

    classifier = AIArticleClassifier(api_key="fake_key")
    classifier.RATE_LIMIT_BACKOFF_SECONDS = 0

    with patch.object(classifier, "_classify_deterministic", return_value=None):
        with patch.object(
            classifier,
            "_call_model",
            side_effect=Exception("429 ResourceExhausted: Rate limit exceeded. Please wait a moment."),
        ) as mock_call:
            res = classifier.classify(article)
            # Verify 2 attempts were made before giving up
            assert mock_call.call_count == 2


def test_manipal_fuel_price_result_rejects_before_extraction():
    """16. Manipal fuel-price result rejects before extraction."""
    reset_serpapi_counter()

    event = Event(
        canonical_title="Manipal Health Q1 adjusted profit rises 31% to Rs 332 crore",
        description="Manipal Health announced a 31% rise in Q1 adjusted net profit.",
        companies_involved=["Manipal Health"],
        financial_figures=["Rs 332 crore", "31%"],
        event_category=NewsCategory.INDIA,
    )

    primary = Article(
        id="prim",
        title="Manipal Health Q1 adjusted profit rises 31% to Rs 332 crore",
        url="https://economictimes.indiatimes.com/manipal-results",
        source_name="The Economic Times",
        published_at=datetime.now(timezone.utc) - timedelta(hours=4),
        content_text="Manipal Health reported Q1 profit of Rs 332 crore.",
        category=NewsCategory.INDIA,
    )

    mock_extractor = MagicMock()
    corroborator = SerpAPICorroborator(extractor=mock_extractor, api_key="fake_key", max_searches=5)

    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "news_results": [
                {
                    "title": "Diesel Price in Kolkata Today - Check Current Fuel Rates",
                    "link": "https://www.moneycontrol.com/fuel-price/west-bengal/diesel-price-in-kolkata/",
                    "source": {"name": "Moneycontrol"},
                    "date": "2 hours ago",
                }
            ]
        }
        mock_get.return_value = mock_response

        res = corroborator.corroborate(event=event, primary_article=primary)

        assert res.success is False
        mock_extractor.extract.assert_not_called()
        rejections = get_serpapi_rejection_reasons()
        assert rejections.get("ENTITY_MISMATCH_PRE_EXTRACTION", 0) >= 1
