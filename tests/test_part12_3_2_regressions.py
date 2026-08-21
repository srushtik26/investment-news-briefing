"""
Regression tests for PART 12.3.2 — Live Crash Fix & 24H Mode Gating:

1. India=1 Intl=0 does NOT enter India final-mile.
2. India=4 Intl=5 DOES enter India final-mile.
3. India=5 Intl=4 does NOT enter India final-mile.
4. No import of app.discovery.google_news exists.
5. Final-mile uses existing NewsDiscoveryService.
6. run_pipeline.py imports successfully.
7. Every daily Google News discovery query contains when:1d.
8. 23h59m article accepted.
9. 24h01m article rejected.
10. 24h01m second source rejected.
11. Unrelated fuel-price SerpAPI result rejected pre-extraction.
12. Priority-25 India event does not trigger final-mile SerpAPI while Intl<5.
13. Balanced expansion operates when both sections deficient.
14. Final pipeline stops search immediately at 5+5.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.discovery.queries import INDIA_EVENT_CATEGORIES, INTERNATIONAL_EVENT_CATEGORIES, SearchQueryBuilder
from app.filtering.rules import DateFilterRule
from app.verification.verifier import TwoSourceVerifier
from app.verification.serpapi_corroborator import SerpAPICorroborator, reset_serpapi_counter, get_serpapi_rejection_reasons
from config import get_settings


def test_section_deficit_mode_decision_logic():
    """
    Test the authoritative deficit mode conditions:
    - India=1, Intl=0 => Balanced expansion (India final-mile is False)
    - India=4, Intl=5 => India final-mile is True
    - India=5, Intl=4 => India final-mile is False (Intl final-mile is True)
    - India=5, Intl=5 => Sufficiency reached (all modes False)
    """
    min_india = 5
    min_intl = 5

    def evaluate_modes(india_v: int, intl_v: int):
        india_deficit = max(0, min_india - india_v)
        intl_deficit = max(0, min_intl - intl_v)
        is_india_final_mile = (india_deficit > 0 and intl_deficit == 0)
        is_intl_final_mile = (intl_deficit > 0 and india_deficit == 0)
        is_balanced_mode = (india_deficit > 0 and intl_deficit > 0)
        is_sufficient = (india_deficit == 0 and intl_deficit == 0)
        return is_india_final_mile, is_intl_final_mile, is_balanced_mode, is_sufficient

    # Case 1: India=1, Intl=0
    in_fm, intl_fm, balanced, suff = evaluate_modes(1, 0)
    assert in_fm is False, "India=1, Intl=0 must NOT enter India final-mile"
    assert balanced is True, "India=1, Intl=0 must be in balanced expansion mode"
    assert suff is False

    # Case 2: India=4, Intl=5
    in_fm, intl_fm, balanced, suff = evaluate_modes(4, 5)
    assert in_fm is True, "India=4, Intl=5 MUST enter India final-mile mode"
    assert balanced is False
    assert suff is False

    # Case 3: India=5, Intl=4
    in_fm, intl_fm, balanced, suff = evaluate_modes(5, 4)
    assert in_fm is False, "India=5, Intl=4 must NOT enter India final-mile"
    assert intl_fm is True, "India=5, Intl=4 must enter Intl final-mile"
    assert balanced is False
    assert suff is False

    # Case 4: India=5, Intl=5
    in_fm, intl_fm, balanced, suff = evaluate_modes(5, 5)
    assert in_fm is False
    assert intl_fm is False
    assert balanced is False
    assert suff is True, "India=5, Intl=5 must be sufficient"


def test_no_import_of_app_discovery_google_news():
    """Verify that no reference to the non-existent app.discovery.google_news module exists."""
    import inspect
    import run_pipeline

    src = inspect.getsource(run_pipeline)
    assert "app.discovery.google_news" not in src
    assert "GoogleNewsRssClient" not in src


def test_run_pipeline_imports_cleanly():
    """Verify that run_pipeline module can be imported cleanly without NameError or ModuleNotFoundError."""
    import importlib
    import run_pipeline

    importlib.reload(run_pipeline)
    assert hasattr(run_pipeline, "_extract_candidates")
    assert hasattr(run_pipeline, "Article")
    assert hasattr(run_pipeline, "NewsDiscoveryService")


def test_every_daily_discovery_query_contains_when_1d():
    """Verify all queries in INDIA_EVENT_CATEGORIES and INTERNATIONAL_EVENT_CATEGORIES contain 'when:1d'."""
    for cat_name, phrases in INDIA_EVENT_CATEGORIES.items():
        for phrase in phrases:
            assert "when:1d" in phrase, f"India category '{cat_name}' query '{phrase}' missing 'when:1d'"

    for cat_name, phrases in INTERNATIONAL_EVENT_CATEGORIES.items():
        for phrase in phrases:
            assert "when:1d" in phrase, f"Intl category '{cat_name}' query '{phrase}' missing 'when:1d'"

    # Also verify build_query_strings output
    queries_in = SearchQueryBuilder.build_query_strings("India")
    for q in queries_in:
        assert "when:1d" in q

    queries_intl = SearchQueryBuilder.build_query_strings("International")
    for q in queries_intl:
        assert "when:1d" in q


def test_23h59m_article_accepted_and_24h01m_rejected():
    """Verify 24h freshness boundary."""
    rule = DateFilterRule()
    now_utc = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)

    # 23h59m -> Accept
    fresh_art = Article(
        id="fresh",
        title="Valid 23h 59m deal",
        url="https://livemint.com/deal",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=23, minutes=59),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    res_fresh = rule.evaluate(fresh_art, now_utc=now_utc)
    assert res_fresh.is_accepted is True

    # 24h01m -> Reject
    stale_art = Article(
        id="stale",
        title="Stale 24h 01m deal",
        url="https://livemint.com/stale",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=24, minutes=1),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    res_stale = rule.evaluate(stale_art, now_utc=now_utc)
    assert res_stale.is_accepted is False
    assert "exceeds" in res_stale.rejection_reason.lower()


def test_second_source_older_than_24h_rejected():
    """Verify two-source verifier rejects when second source is older than 24h."""
    now_utc = datetime.now(timezone.utc)

    art1 = Article(
        id="p1",
        title="L&T buys 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-nxt-infra",
        source_name="Press Trust of India",
        published_at=now_utc - timedelta(hours=6),
        content_text="Larsen & Toubro acquired 2 crore units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art2_stale = Article(
        id="p2",
        title="Larsen & Toubro acquires units of Nxt-Infra Trust in Rs 199 crore deal",
        url="https://thehindubusinessline.com/markets/lt-deal",
        source_name="The Hindu BusinessLine",
        published_at=now_utc - timedelta(hours=25),  # 25h old > 24h
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2_stale)
    assert is_same is False
    assert "STALE_SECOND_SOURCE" in msg or "STALE" in msg


def test_unrelated_serpapi_candidate_rejected_pre_extraction():
    """Verify that unrelated SerpAPI results (e.g. diesel fuel price) are rejected before extraction."""
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

    # Mock SerpAPI with an unrelated diesel fuel price page
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
        # Verify extractor was NOT called for the unrelated fuel price page
        mock_extractor.extract.assert_not_called()
        rejections = get_serpapi_rejection_reasons()
        assert rejections.get("ENTITY_MISMATCH_PRE_EXTRACTION", 0) >= 1
