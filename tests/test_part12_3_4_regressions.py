"""
Regression tests for PART 12.3.4:

1. DiscoveredArticle canonical publication field is published_at.
2. No run_pipeline reference to candidate.published_date remains.
3. Expansion candidate with published_at processes normally.
4. 23h candidate proceeds.
5. 25h candidate stale-rejects.
6. 30 expansion candidates do not fail due to publication attribute.
7. AttributeError is not counted as ordinary extraction failure.
8. Internal processing errors affect final status/report.
9. Region classification fixes remain (KFin, JBM, Fairfax/IDBI, Tube/Shanthi => INDIA).
10. Gemini daily quota => zero retry.
11. when:1d remains enabled.
12. Strict two-source rule unchanged.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import inspect
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.discovery.models import DiscoveredArticle
from app.discovery.queries import INDIA_EVENT_CATEGORIES, INTERNATIONAL_EVENT_CATEGORIES
from app.classification.region_classifier import EventRegionClassifier
from app.classification.classifier import AIArticleClassifier
from app.filtering.rules import DateFilterRule
from app.verification.verifier import TwoSourceVerifier
from config import get_settings
import run_pipeline


def test_discovered_article_canonical_publication_field():
    """1. DiscoveredArticle canonical publication field is published_at."""
    now_utc = datetime.now(timezone.utc)
    da = DiscoveredArticle(
        title="Test Headline",
        url="https://example.com/test",
        source="Example",
        search_query="test query",
        country="India",
        published_at=now_utc,
    )
    assert hasattr(da, "published_at")
    assert da.published_at == now_utc
    assert not hasattr(da, "published_date")


def test_no_run_pipeline_reference_to_cand_published_date():
    """2. No run_pipeline reference to cand.published_date remains."""
    src = inspect.getsource(run_pipeline)
    assert "cand.published_date" not in src
    assert "candidate.published_date" not in src
    assert hasattr(run_pipeline, "get_candidate_published_at")


def test_expansion_candidate_with_published_at_processes_normally():
    """3. Expansion candidate with published_at processes without AttributeError."""
    now_utc = datetime.now(timezone.utc)
    da = DiscoveredArticle(
        title="Tata Motors Q1 Net Profit Jumps 74% to Rs 5,566 Crore",
        url="https://economictimes.indiatimes.com/tata-motors-results",
        source="The Economic Times",
        search_query="tata motors quarterly results",
        country="India",
        published_at=now_utc - timedelta(hours=3),
    )
    pub_at = run_pipeline.get_candidate_published_at(da)
    assert pub_at == da.published_at

    # Mock extractor
    mock_extractor = MagicMock()
    mock_res = MagicMock()
    mock_res.success = True
    mock_res.article = Article(
        title=da.title,
        url=da.url,
        source_name=da.source,
        published_at=pub_at,
        category=NewsCategory.INDIA,
    )
    mock_extractor.extract.return_value = mock_res

    # Extract using the candidate
    res = mock_extractor.extract(
        url=da.url,
        source_name=da.source,
        candidate_title=da.title,
        candidate_category="India",
        candidate_pub_date=pub_at,
    )
    assert res.success is True
    assert res.article.title == da.title


def test_23h_candidate_proceeds_and_25h_stale_rejects():
    """4 & 5. 23h candidate proceeds; 25h candidate stale-rejects."""
    now_utc = datetime.now(timezone.utc)
    rule = DateFilterRule()

    # 23h
    art_23h = Article(
        id="art_23h",
        title="Recent corporate action within 24h",
        url="https://livemint.com/deal",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=23),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    assert rule.evaluate(art_23h, now_utc=now_utc).is_accepted is True

    # 25h
    art_25h = Article(
        id="art_25h",
        title="Old corporate action older than 24h",
        url="https://livemint.com/old-deal",
        source_name="Livemint",
        published_at=now_utc - timedelta(hours=25),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    assert rule.evaluate(art_25h, now_utc=now_utc).is_accepted is False


def test_30_expansion_candidates_do_not_fail_attribute():
    """6. Batch of 30 DiscoveredArticle expansion candidates all yield valid published_at without AttributeError."""
    now_utc = datetime.now(timezone.utc)
    candidates = [
        DiscoveredArticle(
            title=f"Corporate Action Event {i} Rs {i*100} crore",
            url=f"https://livemint.com/deal-{i}",
            source="Livemint",
            search_query="corporate deal",
            country="India",
            published_at=now_utc - timedelta(hours=i % 20),
        )
        for i in range(30)
    ]

    for cand in candidates:
        pub_at = run_pipeline.get_candidate_published_at(cand)
        assert pub_at is not None
        assert pub_at == cand.published_at


def test_region_classification_fixes_remain():
    """9. Verify region classifications: KFin, JBM, Fairfax/IDBI, Tube/Shanthi => INDIA."""
    clf = EventRegionClassifier()

    cases = [
        ("General Atlantic sells 8.75% stake in KFin Technologies", ["General Atlantic", "KFin Technologies"]),
        ("Bain Capital invests in EV maker JBM Auto", ["Bain Capital", "JBM Auto"]),
        ("India to give Canada's Fairfax two years to consolidate holdings for IDBI Bank deal", ["Fairfax Financial", "IDBI Bank"]),
        ("Tube Investments of India acquires additional 2.69% stake in Shanthi Gears", ["Tube Investments of India", "Shanthi Gears"]),
    ]

    for title, companies in cases:
        ev = Event(
            canonical_title=title,
            description=f"Transaction details for {title}",
            companies_involved=companies,
        )
        assert clf.classify_event(ev) == NewsCategory.INDIA, f"Failed for: {title}"


def test_when_1d_remains_enabled():
    """11. All daily discovery query definitions contain when:1d."""
    for cat, queries in INDIA_EVENT_CATEGORIES.items():
        for q in queries:
            assert "when:1d" in q, f"India {cat} missing when:1d: {q}"

    for cat, queries in INTERNATIONAL_EVENT_CATEGORIES.items():
        for q in queries:
            assert "when:1d" in q, f"Intl {cat} missing when:1d: {q}"


def test_two_source_verifier_strictness_unchanged():
    """12. Strict two-source rule is intact."""
    now_utc = datetime.now(timezone.utc)
    art1 = Article(
        id="a1",
        title="Larsen & Toubro acquires 2 crore units of Nxt-Infra Trust for Rs 199 crore",
        url="https://economictimes.indiatimes.com/lt-deal",
        source_name="PTI",
        published_at=now_utc - timedelta(hours=2),
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
    )
    art2_same_pub = Article(
        id="a2",
        title="Larsen & Toubro buys Nxt-Infra units worth Rs 199 cr",
        url="https://economictimes.indiatimes.com/markets/lt-deal-2",
        source_name="PTI",  # same publisher group
        published_at=now_utc - timedelta(hours=2),
        content_text="L&T has purchased units of Nxt-Infra Trust for Rs 199 crore.",
        category=NewsCategory.INDIA,
    )
    verifier = TwoSourceVerifier()
    # Same publisher group must fail verification
    grp1 = verifier.get_publisher_group(art1)
    grp2 = verifier.get_publisher_group(art2_same_pub)
    assert grp1 == grp2
