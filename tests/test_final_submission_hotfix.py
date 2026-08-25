"""
Regression tests for Final Submission Hotfix:
1. pub_at undefined error in _extract_candidates fixed.
2. 5 India + 5 International quality single-source candidates satisfy sufficiency.
3. Obvious headline entity extraction fallback handles apostrophes, hyphens, and quarterly results.
4. SingleSourceEvaluator accepts canonical publishers without active corroboration.
5. Offline/429 editorial fallback generates exactly 5 India + 5 International stories.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.models.article import Article
from app.discovery.models import DiscoveredArticle
from app.models.event import Event, NewsCategory, VerificationTier
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.models.entity_sanitizer import normalize_publisher_name, sanitize_company_entities
from app.verification.query_builder import EventQueryBuilder
from app.verification.single_source import SingleSourceEvaluator
from app.ai.editor import GeminiEditorialEngine, BriefingEditorialPayload
from run_pipeline import _extract_candidates, get_section_quality_state


def test_extract_candidates_pub_at_defined():
    """Verify that _extract_candidates passes published_at to extractor without NameError."""
    pub_date = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
    cand = DiscoveredArticle(
        title="Airtel Payments Bank Q1 net profit jumps 82%",
        url="https://example.com/airtel-q1",
        source="Business Standard",
        country="India",
        published_at=pub_date,
        search_query="Airtel Payments Bank Q1",
    )
    
    mock_extractor = MagicMock()
    mock_res = MagicMock()
    mock_res.original_url = cand.url
    mock_res.resolved_url = cand.url
    mock_res.status_code = 200
    mock_res.word_count = 500
    mock_res.extraction_method = "direct"
    mock_res.success = True
    mock_res.error_message = None
    mock_res.article = Article(
        id="art_1",
        title=cand.title,
        url=cand.url,
        source_name=cand.source,
        published_at=pub_date,
        content_text="Airtel Payments Bank reported an 82% jump in net profit.",
    )
    mock_extractor.extract.return_value = mock_res
    mock_extractor.resolver.is_google_news_url.return_value = False
    
    seen = set()
    logs = []
    
    extracted, records, gc, ro, fo, pur, dup = _extract_candidates(
        [(cand, "india")],
        mock_extractor,
        seen,
        lambda msg: logs.append(msg),
    )
    
    assert len(extracted) == 1
    assert extracted[0].title == cand.title
    assert mock_extractor.extract.call_count == 1
    # Ensure candidate_pub_date is passed
    kwargs = mock_extractor.extract.call_args.kwargs
    assert kwargs["candidate_pub_date"] == pub_date


def test_get_section_quality_state_sufficiency():
    """5 single-source quality candidates must pass sufficiency without requiring 3 two-source."""
    singles = [
        Event(
            id=f"sing_{i}",
            canonical_title=f"India Story {i}",
            description=f"Description {i}",
            article_ids=[f"art_{i}"],
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        for i in range(5)
    ]
    
    state = get_section_quality_state(
        events=[],
        high_conf_single_candidates=singles,
        category=NewsCategory.INDIA,
    )
    
    assert state["two_source_count"] == 0
    assert state["single_source_count"] == 5
    assert state["eligible_total"] == 5
    assert state["slot_deficit"] == 0
    assert state["section_complete"] is True


def test_headline_entity_extraction():
    """Verify regex and query builder extract companies from common headline patterns."""
    h1 = "Airtel Payments Bank Q1 net profit jumps 82%"
    art1 = Article(id="1", title=h1, url="https://example.com/1", source_name="Economic Times", content_text="Body")
    ents1 = EventQueryBuilder.extract_entities(art1)
    assert any("airtel payments bank" in e.lower() for e in ents1)

    h2 = "Dick's Sporting Goods stock falls 15% as retailer misses expectations"
    art2 = Article(id="2", title=h2, url="https://example.com/2", source_name="Reuters", content_text="Body")
    ents2 = EventQueryBuilder.extract_entities(art2)
    assert any("dick's sporting goods" in e.lower() or "dick" in e.lower() for e in ents2)

    h3 = "Horizon Industrial Parks Ltd Quarterly Results"
    art3 = Article(id="3", title=h3, url="https://example.com/3", source_name="Business Standard", content_text="Body")
    ents3 = EventQueryBuilder.extract_entities(art3)
    assert any("horizon industrial parks" in e.lower() for e in ents3)


def test_publisher_normalization_and_single_source_eval():
    """Verify publisher normalization and single source evaluation with canonical publisher."""
    evaluator = SingleSourceEvaluator()
    pub_date = datetime.now(timezone.utc)
    
    # Test publisher normalization
    assert normalize_publisher_name("@bsindia") == "Business Standard"
    assert normalize_publisher_name("Moneycontrol.com") == "Moneycontrol"
    assert normalize_publisher_name("The Economic Times") == "Economic Times"
    assert normalize_publisher_name("The Wall Street Journal") == "Wall Street Journal"
    
    art = Article(
        id="art_bs",
        title="Tata Motors Q1 Net Profit Jumps 74% to Rs 5,566 Crore",
        url="https://www.business-standard.com/article/companies/tata-motors-1234.html",
        source_name=normalize_publisher_name("@bsindia"),
        published_at=pub_date,
        date_verified=True,
        content_text=(
            "Tata Motors reported a 74% increase in net profit for the first quarter of the financial year, "
            "reaching Rs 5,566 crore amid strong global and domestic demand across passenger and commercial vehicles. "
            "Total consolidated revenue from operations surged to Rs 1,02,236 crore during the quarter under review, "
            "compared to Rs 82,221 crore in the corresponding period of the previous fiscal year. "
            "Operating margins expanded significantly driven by premium vehicle demand and favorable product mix. "
            "The management reaffirmed its target to become net auto debt-free within the ongoing financial year."
        ),
    )
    event = Event(
        id="ev_tata",
        canonical_title=art.title,
        description="Tata Motors Q1 results description",
        article_ids=[art.id],
        event_category=NewsCategory.INDIA,
        companies_involved=["Tata Motors"],
        financial_figures=["Rs 5,566 crore", "Rs 1,02,236 crore"],
        percentages=["74%"],
    )
    
    is_elig, score, reason = evaluator.evaluate_event(event, art)
    assert is_elig is True
    assert score >= 80.0
    assert "trusted_pub" in reason


def test_gemini_editorial_offline_fallback():
    """Ensure GeminiEditorialEngine fallback generates exactly 5 India and 5 International stories."""
    engine = GeminiEditorialEngine()
    
    dummy_breakdown = ScoreBreakdown(
        financial_magnitude=80.0,
        market_impact=80.0,
        investor_relevance=80.0,
        corporate_significance=80.0,
        source_quality=80.0,
        total_score=80.0,
    )
    india_scored = [
        ScoredEvent(
            event=Event(
                id=f"in_ev_{i}",
                canonical_title=f"India Corporate Deal Event {i}",
                description=f"India deal description {i}",
                article_ids=[f"art_in_{i}"],
                event_category=NewsCategory.INDIA,
                companies_involved=[f"Company IN {i}"],
                verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
            ),
            score_breakdown=dummy_breakdown,
            investment_score=90.0 - i,
            rank=i + 1,
        )
        for i in range(5)
    ]
    intl_scored = [
        ScoredEvent(
            event=Event(
                id=f"intl_ev_{i}",
                canonical_title=f"International Corporate Deal Event {i}",
                description=f"Intl deal description {i}",
                article_ids=[f"art_intl_{i}"],
                event_category=NewsCategory.INTERNATIONAL,
                companies_involved=[f"Company INTL {i}"],
                verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
            ),
            score_breakdown=dummy_breakdown,
            investment_score=90.0 - i,
            rank=i + 1,
        )
        for i in range(5)
    ]
    
    pool = RankedCandidatePool(
        india_candidates=india_scored,
        international_candidates=intl_scored,
    )
    
    articles_lookup = {}
    for s in india_scored + intl_scored:
        ev = s.event
        aid = ev.article_ids[0]
        articles_lookup[aid] = Article(
            id=aid,
            title=ev.canonical_title,
            url=f"https://example.com/{aid}",
            source_name="Business Standard" if "in_ev" in ev.id else "Reuters",
            published_at=datetime.now(timezone.utc),
            content_text="Detailed article content with sufficient length for testing editorial engine selection.",
        )
    
    raw_json = engine._generate_offline_editorial_fallback(pool, articles_lookup)
    assert isinstance(raw_json, str)
    payload = BriefingEditorialPayload.model_validate_json(raw_json)
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5
