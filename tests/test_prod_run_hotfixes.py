"""
Regression tests for production run fixes:
1. Preventing false India reclassification of global central bank stories (e.g. US Fed rate hike).
2. Rejecting bare regulatory filing forms (e.g. 'N-2/A') as generic headlines.
3. Domestic multi-horizon selection recovery when candidate count is deficient.
"""
from datetime import datetime, timezone, timedelta
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.classification.region_classifier import (
    EventRegionClassifier,
    verify_india_business_nexus,
)
from app.filtering.rules import is_generic_headline


def test_us_fed_rate_hike_not_reclassified_to_india():
    """Verify that a global central bank/rate hike headline with passing RBI mention is NOT reclassified to India."""
    title = "The backdrop in which the U.S. Fed raised interest rate"
    content = (
        "The Reserve Bank of India maintained its stance, but the global economy watched as "
        "the U.S. Federal Reserve raised benchmark interest rates to counter inflation pressures. "
        "Federal Reserve Chairman Jerome Powell indicated further policy tightening."
    )
    article = Article(
        id="art_fed_1",
        title=title,
        url="https://www.thehindu.com/business/Economy/the-backdrop-in-which-the-us-fed-raised-interest-rate/article123.ece",
        content_text=content,
        published_at=datetime.now(timezone.utc),
        source_name="The Hindu",
    )
    event = Event(
        canonical_title=title,
        article_ids=[article.id],
        description="US Federal Reserve raised interest rates to counter inflation",
        event_category=NewsCategory.INTERNATIONAL,
    )

    # 1. India business nexus check must REJECT
    is_nexus, nexus_reason = verify_india_business_nexus(event, article)
    assert not is_nexus, f"Expected nexus to be rejected, got reason: {nexus_reason}"
    assert "international central bank" in nexus_reason.lower() or "no strong india business signal" in nexus_reason.lower()

    # 2. Region classification must be INTERNATIONAL
    clf = EventRegionClassifier()
    detected_cat, cat_reason = clf.classify_with_reason(
        title=event.canonical_title,
        content=article.content_text[:500],
        companies=event.companies_involved,
        discovery_region=NewsCategory.INTERNATIONAL,
    )
    assert detected_cat == NewsCategory.INTERNATIONAL, f"Expected INTERNATIONAL, got {detected_cat}: {cat_reason}"


def test_bare_form_codes_rejected_as_generic_headlines():
    """Verify bare SEC/regulatory filing form codes are rejected as generic headlines."""
    bare_forms = ["N-2/A", "10-K", "8-K", "Form 10-Q", "SC 13D/A", "424B2", "Form 4", "DEF 14A"]
    for form in bare_forms:
        is_gen, reason = is_generic_headline(form)
        assert is_gen, f"Form code '{form}' should have been rejected as generic headline, got reason: {reason}"

    # Valid headlines mentioning form codes should still be accepted
    valid_titles = [
        "Tesla files 10-K report showing 15% revenue expansion",
        "Berkshire Hathaway submits Form 4 detailing Apple share sale",
        "Blackstone amends N-2/A filing for $2 billion credit fund",
    ]
    for valid_t in valid_titles:
        is_gen, reason = is_generic_headline(valid_t)
        assert not is_gen, f"Valid headline '{valid_t}' was incorrectly rejected: {reason}"


def test_domestic_multi_horizon_recovery():
    """Verify domestic selection recovers candidates beyond 24h when today/24h pool has fewer than 5 stories."""
    from unittest.mock import MagicMock
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
    from app.verification.domestic_trending import DomesticTrendingEvaluator

    now = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=now)
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    # Create 4 fresh domestic stories (1h old)
    dom_s = []
    all_arts = {}
    all_evs = {}
    for i in range(4):
        art = Article(
            id=f"dom_art_{i}",
            title=f"Prime Minister inaugurates national highway corridor phase {i} in Maharashtra",
            url=f"https://www.thehindu.com/news/national/highway-phase-{i}",
            content_text=f"The Prime Minister inaugurated a massive infrastructure project in Maharashtra with investment of Rs {1000 + i * 100} crore.",
            published_at=now - timedelta(hours=1),
            source_name="The Hindu",
        )
        ev = Event(
            canonical_title=art.title,
            article_ids=[art.id],
            description="Infrastructure inauguration by PM",
            event_category=NewsCategory.DOMESTIC,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        scored = ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=75.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=75.0, rationale="fresh",
            ),
            investment_score=75.0,
            rank=i + 1,
        )
        dom_s.append(scored)
        all_arts[art.id] = art
        all_evs[ev.id] = ev

    # Create 1 older domestic story (30h old) in memory that was deferred/older
    older_art = Article(
        id="dom_art_older",
        title="Supreme Court issues landmark ruling on environmental clearances for mining",
        url="https://indianexpress.com/article/india/supreme-court-mining-clearances",
        content_text="The Supreme Court of India delivered a comprehensive judgment on environmental norms and forest clearance regulations.",
        published_at=now - timedelta(hours=30),
        source_name="The Indian Express",
    )
    older_ev = Event(
        canonical_title=older_art.title,
        article_ids=[older_art.id],
        description="Supreme Court ruling on environment clearances",
        event_category=NewsCategory.DOMESTIC,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    all_arts[older_art.id] = older_art
    all_evs[older_ev.id] = older_ev

    ctx.articles_lookup = all_arts
    ctx.high_confidence_single_candidates = [older_ev]

    # Populate 5 India and 5 International
    ind_s = []
    intl_s = []
    for i in range(5):
        in_art = Article(
            id=f"in_art_{i}",
            title=f"Tata Motors Q2 net profit jumps {20 + i}% to Rs {3500 + i * 100} crore",
            url=f"https://www.livemint.com/market/tata-motors-q2-{i}",
            content_text="Tata Motors declared strong quarterly results beating street estimates.",
            published_at=now - timedelta(hours=2),
            source_name="Livemint",
        )
        in_ev = Event(
            canonical_title=in_art.title,
            article_ids=[in_art.id],
            description="Tata Motors Q2 financial results",
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        all_arts[in_art.id] = in_art
        all_evs[in_ev.id] = in_ev
        ind_s.append(ScoredEvent(
            event=in_ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=80.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=80.0, rationale="fresh",
            ),
            investment_score=80.0,
            rank=i + 1,
        ))

        intl_art = Article(
            id=f"intl_art_{i}",
            title=f"Microsoft acquires cloud AI cybersecurity startup for ${100 + i} million",
            url=f"https://www.cnbc.com/2026/09/25/microsoft-deal-{i}.html",
            content_text="Microsoft announced an agreement to acquire an AI security vendor.",
            published_at=now - timedelta(hours=2),
            source_name="CNBC",
        )
        intl_ev = Event(
            canonical_title=intl_art.title,
            article_ids=[intl_art.id],
            description="Microsoft corporate acquisition",
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        all_arts[intl_art.id] = intl_art
        all_evs[intl_ev.id] = intl_ev
        intl_s.append(ScoredEvent(
            event=intl_ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=80.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=80.0, rationale="fresh",
            ),
            investment_score=80.0,
            rank=i + 1,
        ))

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=dom_s,
        india_candidates=ind_s,
        international_candidates=intl_s,
    )
    ctx.ranker = mock_ranker
    accepted = [{"event_id": k} for k in all_evs]

    # Force 24h fallback status to verify recovery expands to 36h
    ctx.pipeline_status = "FALLBACK_SUCCESS_24H"

    _, dom_res, in_res, intl_res, sufficient, _ = run_ranking_and_selection(ctx, accepted, all_evs)
    assert len(dom_res) == 5, f"Expected 5 domestic candidates, got {len(dom_res)}"
    selected_titles = [s.event.canonical_title for s in dom_res]
    assert older_ev.canonical_title in selected_titles, "30h old domestic event was not recovered during horizon expansion"
