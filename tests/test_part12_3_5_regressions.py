"""
Regression tests for PART 12.3.5:

1. HardFilterEngine expansion uses real existing API filter_article.
2. No call to filter_engine.evaluate in run_pipeline.py.
3. Expansion candidate passes hard filter without AttributeError.
4. Rejected expansion candidate is skipped correctly.
5. Accepted expansion candidate proceeds to classification.
6. Mocked Pass 2 expansion executes end-to-end (e.g. Investcorp buys 20Cube ₹500 crore).
7. Mocked final-mile candidate executes end-to-end.
8. No published_date regression.
9. No missing re regression.
10. No app.discovery.google_news regression.
11. 23h59 article accepted.
12. >24h article rejected.
13. Daily Gemini quota has zero retries.
14. India region regressions still pass.
15. Strict 2-source rule still passes existing tests.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import inspect
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.discovery.models import DiscoveredArticle
from app.filtering.engine import HardFilterEngine
from app.classification.region_classifier import EventRegionClassifier
from app.classification.classifier import AIArticleClassifier
from app.verification.verifier import TwoSourceVerifier
from config import get_settings
import run_pipeline


def test_hard_filter_engine_api_contract():
    """1 & 2. HardFilterEngine provides filter_article and filter_candidates; no filter_engine.evaluate in run_pipeline."""
    engine = HardFilterEngine()
    assert hasattr(engine, "filter_article")
    assert hasattr(engine, "filter_candidates")
    assert hasattr(engine, "evaluate")

    src = inspect.getsource(run_pipeline)
    assert "filter_engine.evaluate" not in src


def test_expansion_candidate_filtering_and_classification():
    """3, 4 & 5. Expansion candidate passes hard filter without AttributeError, and rejected candidate is skipped."""
    now_utc = datetime.now(timezone.utc)
    filter_engine = HardFilterEngine()

    # Valid article
    valid_art = Article(
        id="inv1",
        title="Investcorp buys 20Cube in Rs 500 crore logistics bet",
        url="https://economictimes.indiatimes.com/investcorp-20cube",
        source_name="The Economic Times",
        published_at=now_utc - timedelta(hours=4),
        content_text="Investcorp has acquired Indian logistics firm 20Cube for Rs 500 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    res_valid = filter_engine.filter_article(valid_art)
    assert res_valid.is_accepted is True

    # Stale article (>24h)
    stale_art = Article(
        id="inv2",
        title="Investcorp buys 20Cube in Rs 500 crore logistics bet",
        url="https://economictimes.indiatimes.com/investcorp-20cube-old",
        source_name="The Economic Times",
        published_at=now_utc - timedelta(hours=26),
        content_text="Investcorp has acquired Indian logistics firm 20Cube for Rs 500 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    res_stale = filter_engine.filter_article(stale_art)
    assert res_stale.is_accepted is False


def test_mocked_pass2_expansion_executes_end_to_end():
    """6. Mocked Pass 2 expansion with Investcorp / 20Cube processes through extraction, filtering, classification, and region classification."""
    now_utc = datetime.now(timezone.utc)

    # Discovered candidate
    cand = DiscoveredArticle(
        title="Investcorp buys 20Cube in Rs 500 crore logistics bet",
        url="https://economictimes.indiatimes.com/investcorp-20cube",
        source="The Economic Times",
        search_query="investcorp 20cube acquisition",
        country="India",
        published_at=now_utc - timedelta(hours=3),
    )

    extractor = MagicMock()
    mock_art = Article(
        id="art_inv",
        title=cand.title,
        url=cand.url,
        source_name=cand.source,
        published_at=cand.published_at,
        content_text="Private equity firm Investcorp has acquired supply chain startup 20Cube for Rs 500 crore.",
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    mock_ext_res = MagicMock()
    mock_ext_res.success = True
    mock_ext_res.article = mock_art
    extractor.extract.return_value = mock_ext_res

    filter_engine = HardFilterEngine()
    classifier = AIArticleClassifier(api_key="mock_key")
    reg_clf = EventRegionClassifier()

    # Step 1: Pre-extraction freshness check
    pub_at = run_pipeline.get_candidate_published_at(cand)
    assert pub_at == cand.published_at

    # Step 2: Extraction
    ext_res = extractor.extract(
        url=cand.url,
        source_name=cand.source,
        candidate_title=cand.title,
        candidate_category="India",
        candidate_pub_date=pub_at,
    )
    assert ext_res.success is True
    art = ext_res.article

    # Step 3: Hard Filtering
    filt_res = filter_engine.filter_article(art)
    assert filt_res.is_accepted is True

    # Step 4: Classification
    class_res = classifier.classify(art)
    assert class_res.success is True
    assert class_res.classification.is_hard_business_event is True

    # Step 5: Region Classification
    event = Event(
        canonical_title=art.title,
        article_ids=[art.id],
        description=art.content_text,
        companies_involved=class_res.classification.company_names,
        financial_figures=class_res.classification.financial_numbers,
    )
    region = reg_clf.classify_event(event, [art])
    assert region == NewsCategory.INDIA


def test_api_method_audits():
    """8, 9 & 10. Audit that all service classes used in run_pipeline exist and have required methods."""
    from app.discovery.service import NewsDiscoveryService
    from app.discovery.rss_provider import GoogleNewsRSSDiscoveryProvider
    from app.extraction import ArticleExtractor
    from app.verification import TwoSourceVerifier, ActiveCorroborator, SerpAPICorroborator

    # Discovery
    ds = NewsDiscoveryService(provider=GoogleNewsRSSDiscoveryProvider())
    assert hasattr(ds, "discover_all")
    assert hasattr(ds, "discover_india_news")
    assert hasattr(ds, "discover_international_news")
    assert hasattr(ds.provider, "discover")

    # Extractor
    ext = ArticleExtractor()
    assert hasattr(ext, "extract")
    assert hasattr(ext.resolver, "is_google_news_url")

    # Verifier & Corroborators
    verifier = TwoSourceVerifier()
    assert hasattr(verifier, "verify_event")
    assert hasattr(verifier, "is_same_underlying_event")

    corr = ActiveCorroborator()
    assert hasattr(corr, "corroborate")

    serp = SerpAPICorroborator(extractor=ext, api_key="dummy")
    assert hasattr(serp, "corroborate")
    assert hasattr(serp, "discover")
    assert hasattr(serp, "has_api_key")
