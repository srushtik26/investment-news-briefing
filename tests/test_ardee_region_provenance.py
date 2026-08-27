"""
Regression Test for Ardee Region Provenance & India 5/5 Briefing Generation.
1. Ardee Industries standalone net profit rises 5.96% is classified as EARNINGS, hard_event=True, INDIA.
2. Discovery region INDIA is preserved across clustering and reclassification.
3. 5 India (Groww, Honasa, Horizon Industrial Parks, Welspun, Ardee Industries) + 5 International pass Stage 9 and generate final briefing.
"""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.models.article import Article
from app.models.event import Event, NewsCategory, VerificationTier
from app.classification.classifier import AIArticleClassifier
from app.classification.models import ArticleEventType
from app.classification.region_classifier import EventRegionClassifier
from app.deduplication.clusterer import EventClusterer
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.verification.single_source import SingleSourceEvaluator
from app.validation.engine import FinalValidationEngine
from app.validation.models import ValidationStatus
from app.ai.editor import GeminiEditorialEngine, BriefingEditorialPayload


def test_ardee_classification_region_and_quality():
    """Test Ardee Industries standalone net profit rises 5.96% classifies as EARNINGS, hard_event=True, INDIA, and qualifies."""
    classifier = AIArticleClassifier(api_key="")
    reg_clf = EventRegionClassifier()
    evaluator = SingleSourceEvaluator()
    pub_date = datetime.now(timezone.utc)

    art = Article(
        id="ardee_1",
        title="Ardee Industries standalone net profit rises 5.96% to Rs 4.2 crore in Q1",
        url="https://www.business-standard.com/companies/news/ardee-industries-standalone-net-profit-rises-5-96-in-q1-12345.html",
        source_name="Business Standard",
        published_at=pub_date,
        date_verified=True,
        category=NewsCategory.INDIA,
        content_text=(
            "Ardee Industries reported standalone net profit of Rs 4.2 crore for the first quarter ended June 2026, "
            "registering an increase of 5.96% as compared to Rs 3.96 crore in the corresponding period of the previous fiscal. "
            "Total standalone revenue from operations grew 8.5% year-on-year to Rs 48.5 crore."
        ),
    )

    # 1. Classification
    class_res = classifier.classify(art)
    assert class_res.success is True
    assert class_res.classification is not None
    assert class_res.classification.event_type == ArticleEventType.EARNINGS
    assert class_res.classification.is_hard_business_event is True

    # 2. Region Classification with discovery_region=INDIA
    region, reason = reg_clf.classify_with_reason(
        title=art.title,
        content=art.content_text,
        financial_figures=["Rs 4.2 crore", "5.96%"],
        companies=["Ardee Industries"],
        discovery_region=NewsCategory.INDIA,
    )
    assert region == NewsCategory.INDIA

    # 3. Clustering preserves discovery region
    clusterer = EventClusterer()
    clustered_events = clusterer.cluster_articles_into_events([art])
    assert len(clustered_events) == 1
    assert clustered_events[0].event_category == NewsCategory.INDIA

    # 4. Single Source Quality Evaluation
    ev = clustered_events[0]
    ev.article_ids = [art.id]
    ev.companies_involved = ["Ardee Industries"]
    ev.financial_figures = ["Rs 4.2 crore", "5.96%"]
    is_elig, score, _ = evaluator.evaluate_event(ev, art)
    assert is_elig is True
    assert score >= 80.0

    # 5. classify_event retains INDIA upon re-evaluation
    reclassified_region = reg_clf.classify_event(ev, [art])
    assert reclassified_region == NewsCategory.INDIA


def test_mocked_pool_groww_honasa_horizon_welspun_ardee_produces_briefing():
    """Test exact live scenario: 5 India (Groww, Honasa, Horizon, Welspun, Ardee) + 5 Intl generates final briefing."""
    engine = GeminiEditorialEngine()
    dummy_breakdown = ScoreBreakdown(
        financial_magnitude=85.0,
        market_impact=85.0,
        investor_relevance=85.0,
        corporate_significance=85.0,
        source_quality=85.0,
        total_score=85.0,
    )

    india_stories_data = [
        ("Groww Mutual Fund Launches New Nifty Total Market Index Fund", "Groww", "Rs 500 Crore"),
        ("Honasa calls off proposed Fluence Pharma acquisition citing revised focus", "Honasa Consumer", "Rs 150 Crore"),
        ("Horizon Industrial Parks acquires 100-acre logistics park for Rs 700 crore", "Horizon Industrial Parks", "Rs 700 crore"),
        ("Welspun Corp promoter group, CEO to sell up to Rs 1,417 crore stake via block deal", "Welspun Corp", "Rs 1,417 crore"),
        ("Ardee Industries standalone net profit rises 5.96% to Rs 4.2 crore in Q1", "Ardee Industries", "Rs 4.2 crore"),
    ]

    intl_stories_data = [
        ("Nvidia Reports Q2 Revenue Surge of 122% to $30 Billion", "Nvidia", "$30 Billion"),
        ("Broadcom Announces $5 Billion Share Repurchase Authorization", "Broadcom", "$5 Billion"),
        ("Target Reports Q2 Net Profit of $1.2 Billion on Retail Traffic Rebound", "Target", "$1.2 Billion"),
        ("Synopsys Reports Second Quarter Revenue Growth of 15% to $1.45 Billion", "Synopsys", "$1.45 Billion"),
        ("Qualcomm Completes Acquisition of Alphawave Semiconductor for $2.4 Billion", "Qualcomm", "$2.4 Billion"),
    ]

    india_scored = []
    intl_scored = []
    articles_lookup = {}

    for idx, (title, comp, fig) in enumerate(india_stories_data):
        aid = f"art_in_{idx}"
        ev = Event(
            id=f"in_ev_{idx}",
            canonical_title=title,
            description=f"Description for {title}",
            article_ids=[aid],
            event_category=NewsCategory.INDIA,
            companies_involved=[comp],
            financial_figures=[fig],
            percentages=[],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        scored = ScoredEvent(
            event=ev,
            score_breakdown=dummy_breakdown,
            investment_score=95.0 - idx,
            rank=idx + 1,
        )
        india_scored.append(scored)
        articles_lookup[aid] = Article(
            id=aid,
            title=title,
            url=f"https://www.business-standard.com/article/{aid}",
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc),
            date_verified=True,
            category=NewsCategory.INDIA,
            content_text=(
                f"{comp} reported major corporate transactions and operational updates today involving {fig}. "
                "The corporate board approved all necessary regulatory filings and documentation, "
                "ensuring full compliance with securities regulations and supporting long-term shareholder value creation."
            ),
        )

    for idx, (title, comp, fig) in enumerate(intl_stories_data):
        aid = f"art_intl_{idx}"
        ev = Event(
            id=f"intl_ev_{idx}",
            canonical_title=title,
            description=f"Description for {title}",
            article_ids=[aid],
            event_category=NewsCategory.INTERNATIONAL,
            companies_involved=[comp],
            financial_figures=[fig],
            percentages=[],
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        scored = ScoredEvent(
            event=ev,
            score_breakdown=dummy_breakdown,
            investment_score=95.0 - idx,
            rank=idx + 1,
        )
        intl_scored.append(scored)
        articles_lookup[aid] = Article(
            id=aid,
            title=title,
            url=f"https://www.reuters.com/business/{aid}",
            source_name="Reuters",
            published_at=datetime.now(timezone.utc),
            date_verified=True,
            category=NewsCategory.INTERNATIONAL,
            content_text=(
                f"{comp} announced significant global business developments and financial figures today valued at {fig}. "
                "Management confirmed the strategic transaction enhances market footprint and operating cash flow metrics across key international regions."
            ),
        )

    dom_stories_data = [
        "Supreme Court Constitution Bench rules on national tribunal appointments",
        "Cabinet approves Rs 10000 crore national semiconductor mission package",
        "Election Commission announces schedule for assembly elections",
        "ISRO successfully launches next generation navigation satellite",
        "Parliament passes landmark national digital data protection bill",
    ]
    dom_scored = []
    for idx, title in enumerate(dom_stories_data):
        aid = f"art_dom_{idx}"
        ev = Event(
            id=f"dom_ev_{idx}",
            canonical_title=title,
            description=f"Description for {title}",
            article_ids=[aid],
            event_category=NewsCategory.DOMESTIC,
            companies_involved=[],
            financial_figures=[],
            percentages=[],
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            primary_publisher="The Hindu",
            primary_url=f"https://thehindu.com/dom-{idx}",
            secondary_publisher="Indian Express",
            secondary_url=f"https://indianexpress.com/dom-{idx}",
        )
        scored = ScoredEvent(
            event=ev,
            score_breakdown=dummy_breakdown,
            investment_score=95.0 - idx,
            rank=idx + 1,
        )
        dom_scored.append(scored)
        articles_lookup[aid] = Article(
            id=aid,
            title=title,
            url=f"https://thehindu.com/dom-{idx}",
            source_name="The Hindu",
            published_at=datetime.now(timezone.utc),
            date_verified=True,
            category=NewsCategory.DOMESTIC,
            content_text=f"Government national policy {title} announcement covering broad judicial and constitutional developments across the country.",
        )

    pool = RankedCandidatePool(
        domestic_candidates=dom_scored,
        india_candidates=india_scored,
        international_candidates=intl_scored,
    )

    raw_json = engine._generate_offline_editorial_fallback(pool, articles_lookup)
    payload = BriefingEditorialPayload.model_validate_json(raw_json)

    val_engine = FinalValidationEngine()
    events_lookup = {s.event.id: s.event for s in dom_scored + india_scored + intl_scored}

    report = val_engine.validate_briefing(
        payload=payload,
        events_lookup=events_lookup,
        articles_lookup=articles_lookup,
        target_date=date.today(),
        strict_5_per_section=True,
        quality_ladder_mode=True,
    )

    assert report.is_valid is True
    assert report.status == ValidationStatus.PASSED
    assert len(payload.domestic_stories) == 5
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5
