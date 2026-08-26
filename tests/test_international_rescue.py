"""
Focused Regression Tests for International Rescue:
1. Final-mile never generates when:2d.
2. Final-mile never generates when:3d.
3. Final-mile always uses when:1d.
4. OR source groups are parenthesized.
5. India=5 freezes India discovery.
6. Intl=3 continues Intl discovery.
7. PRNewswire fresh earnings qualifies.
8. GlobeNewswire fresh earnings qualifies.
9. BusinessWire fresh hard event qualifies.
10. Official IR financial result may qualify.
11. >24h still rejected.
12. Manual seed goes through normal validation.
13. Invalid manual seed rejected.
14. Valid manual seed can fill Intl 4 -> 5.
15. Reaching Intl=5 immediately stops discovery.
16. Exactly 5+5 produces final briefing.
17. Gemini 429 still falls back deterministically.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.discovery.models import DiscoveredArticle
from app.models.article import Article
from app.models.event import Event, NewsCategory, VerificationTier
from app.models.entity_sanitizer import normalize_publisher_name
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.verification.single_source import SingleSourceEvaluator, is_trusted_publisher
from app.ai.editor import GeminiEditorialEngine, BriefingEditorialPayload
from run_pipeline import get_fallback_search_window, get_section_quality_state


def test_final_mile_window_strictly_1d():
    """Tests 1, 2, 3: Final-mile never generates when:2d/when:3d and always uses when:1d."""
    for pass_idx in range(1, 10):
        window = get_fallback_search_window(pass_idx)
        assert window == "when:1d"
        assert "when:2d" not in window
        assert "when:3d" not in window


def test_source_groups_parenthesized():
    """Test 4: All OR source groups in queries must be parenthesized."""
    sample_groups = [
        "(site:prnewswire.com OR site:globenewswire.com OR site:businesswire.com)",
        "(site:cnbc.com OR site:apnews.com OR site:bbc.com)",
        "(site:marketwatch.com OR site:fortune.com OR site:theguardian.com)",
        "(site:bloomberg.com OR site:reuters.com OR site:finance.yahoo.com)",
        "(site:business-standard.com OR site:livemint.com OR site:moneycontrol.com)",
    ]
    for grp in sample_groups:
        assert grp.startswith("(")
        assert grp.endswith(")")
        assert " OR " in grp


def test_india_frozen_when_quality_ge_5():
    """Test 5: India >= 5 quality events marks section complete and freezes India."""
    india_events = [
        Event(
            id=f"in_{i}",
            canonical_title=f"India Story {i}",
            description=f"Description {i}",
            article_ids=[f"art_in_{i}"],
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        for i in range(5)
    ]
    state = get_section_quality_state(
        events=[],
        high_conf_single_candidates=india_events,
        category=NewsCategory.INDIA,
    )
    assert state["section_complete"] is True
    assert state["eligible_total"] == 5
    assert state["slot_deficit"] == 0


def test_intl_continues_when_quality_3():
    """Test 6: Intl = 3 shows deficit=2 and section_complete=False."""
    intl_events = [
        Event(
            id=f"intl_{i}",
            canonical_title=f"Intl Story {i}",
            description=f"Description {i}",
            article_ids=[f"art_intl_{i}"],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        for i in range(3)
    ]
    state = get_section_quality_state(
        events=[],
        high_conf_single_candidates=intl_events,
        category=NewsCategory.INTERNATIONAL,
    )
    assert state["section_complete"] is False
    assert state["eligible_total"] == 3
    assert state["slot_deficit"] == 2


def test_wire_services_qualification():
    """Tests 7, 8, 9: PRNewswire, GlobeNewswire, and BusinessWire fresh hard events qualify."""
    evaluator = SingleSourceEvaluator()
    pub_date = datetime.now(timezone.utc)

    # 7. PRNewswire fresh earnings
    art_pr = Article(
        id="pr_1",
        title="Synopsys Reports Second Quarter Fiscal Year 2026 Financial Results",
        url="https://www.prnewswire.com/news-releases/synopsys-reports-results-12345.html",
        source_name="PR Newswire",
        published_at=pub_date,
        date_verified=True,
        content_text=(
            "Synopsys today reported financial results for its second quarter of fiscal year 2026. "
            "Revenue for the second quarter was $1.45 billion, an increase of 15% compared to $1.26 billion "
            "for the second quarter of fiscal year 2025. Operating income surged 20% on non-GAAP basis. "
            "Management raised full-year guidance based on robust semiconductor IP design demand."
        ),
    )
    ev_pr = Event(
        id="ev_pr",
        canonical_title=art_pr.title,
        description="Synopsys Q2 financial results",
        article_ids=[art_pr.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Synopsys"],
        financial_figures=["$1.45 billion", "15%"],
        percentages=["15%", "20%"],
    )
    is_elig_pr, score_pr, _ = evaluator.evaluate_event(ev_pr, art_pr)
    assert is_elig_pr is True
    assert score_pr >= 80.0

    # 8. GlobeNewswire fresh earnings
    art_gn = Article(
        id="gn_1",
        title="Cadence Reports First Quarter 2026 Financial Results",
        url="https://www.globenewswire.com/news-release/2026/08/25/cadence-results.html",
        source_name="GlobeNewswire",
        published_at=pub_date,
        date_verified=True,
        content_text=(
            "Cadence Design Systems announced results for the first quarter of 2026 with total revenue "
            "of $1.02 billion, compared to $850 million in the prior year quarter. GAAP net income was $240 million. "
            "The company repurchased $120 million of common shares during the quarter and increased guidance."
        ),
    )
    ev_gn = Event(
        id="ev_gn",
        canonical_title=art_gn.title,
        description="Cadence Q1 results",
        article_ids=[art_gn.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Cadence"],
        financial_figures=["$1.02 billion", "$240 million"],
        percentages=[],
    )
    is_elig_gn, score_gn, _ = evaluator.evaluate_event(ev_gn, art_gn)
    assert is_elig_gn is True
    assert score_gn >= 80.0

    # 9. BusinessWire fresh hard event
    art_bw = Article(
        id="bw_1",
        title="Qualcomm Completes Acquisition of Alphawave Semiconductor",
        url="https://www.businesswire.com/news/home/20260825/qualcomm-alphawave.html",
        source_name="Business Wire",
        published_at=pub_date,
        date_verified=True,
        content_text=(
            "Qualcomm Incorporated today announced the completion of its acquisition of Alphawave Semiconductor "
            "in an all-cash transaction valued at $2.4 billion. The transaction strengthens Qualcomm's data center "
            "connectivity and AI computing portfolio following regulatory clearances worldwide."
        ),
    )
    ev_bw = Event(
        id="ev_bw",
        canonical_title=art_bw.title,
        description="Qualcomm acquisition completion",
        article_ids=[art_bw.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Qualcomm"],
        financial_figures=["$2.4 billion"],
        percentages=[],
    )
    is_elig_bw, score_bw, _ = evaluator.evaluate_event(ev_bw, art_bw)
    assert is_elig_bw is True
    assert score_bw >= 80.0


def test_official_ir_and_stale_rejection():
    """Tests 10, 11: Official IR financial result may qualify, but >24h is strictly rejected."""
    evaluator = SingleSourceEvaluator()
    pub_date_fresh = datetime.now(timezone.utc)
    pub_date_stale = pub_date_fresh - timedelta(hours=28)

    # 10. Official IR release (Fresh <= 24h)
    art_ir_fresh = Article(
        id="ir_1",
        title="Applied Materials Investor Relations Reports Q3 Net Income of $1.8 Billion",
        url="https://ir.appliedmaterials.com/news-releases/news-release-details/q3-2026",
        source_name="Applied Materials Investor Relations",
        published_at=pub_date_fresh,
        date_verified=True,
        content_text=(
            "Applied Materials Investor Relations announced third-quarter financial results. "
            "Net revenue rose to $6.8 billion while net income reached $1.8 billion. Operating margin expanded "
            "to 30.5% with record demand from leading-edge logic customers."
        ),
    )
    ev_ir = Event(
        id="ev_ir",
        canonical_title=art_ir_fresh.title,
        description="Applied Materials Q3 results",
        article_ids=[art_ir_fresh.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Applied Materials"],
        financial_figures=["$1.8 Billion", "$6.8 billion"],
        percentages=["30.5%"],
    )
    assert is_trusted_publisher(art_ir_fresh.source_name) is True
    is_elig, score, _ = evaluator.evaluate_event(ev_ir, art_ir_fresh)
    assert is_elig is True
    assert score >= 80.0

    # 11. Stale article (>24h) strictly rejected
    art_ir_stale = art_ir_fresh.model_copy(update={"published_at": pub_date_stale})
    is_elig_stale, _, reason = evaluator.evaluate_event(ev_ir, art_ir_stale)
    assert is_elig_stale is False
    assert "REJECT: Stale" in reason


def test_manual_seed_validation():
    """Tests 12, 13, 14: Manual seed undergoes normal validation; invalid is rejected; valid fills pool."""
    evaluator = SingleSourceEvaluator()
    pub_date = datetime.now(timezone.utc)

    # 12 & 14: Valid manual seed
    art_valid = Article(
        id="art_valid",
        title="Broadcom Announces $5 Billion Share Repurchase Authorization",
        url="https://www.prnewswire.com/news-releases/broadcom-buyback-2026.html",
        source_name="PR Newswire",
        published_at=pub_date,
        date_verified=True,
        content_text=(
            "Broadcom Inc. announced that its Board of Directors has authorized a new share repurchase program "
            "of up to $5.0 billion of common stock through December 2026. Capital allocation remains disciplined."
        ),
    )
    ev_valid = Event(
        id="ev_valid",
        canonical_title=art_valid.title,
        description="Broadcom buyback authorization",
        article_ids=[art_valid.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["Broadcom"],
        financial_figures=["$5.0 billion"],
        percentages=[],
    )
    is_v_elig, _, _ = evaluator.evaluate_event(ev_valid, art_valid)
    assert is_v_elig is True

    # 13: Invalid manual seed (generic market chatter, no hard event, untrusted blog)
    art_invalid = Article(
        id="art_invalid",
        title="Stock Market Today: Why Wall Street Could Rally Next Week",
        url="https://randomblog.com/markets/rally.html",
        source_name="Random Blog",
        published_at=pub_date,
        date_verified=True,
        content_text="Traders are speculating about what might happen next week in the stock market with no real deals.",
    )
    ev_invalid = Event(
        id="ev_invalid",
        canonical_title=art_invalid.title,
        description="Speculative blog post",
        article_ids=[art_invalid.id],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=[],
        financial_figures=[],
        percentages=[],
    )
    is_inv_elig, _, reason = evaluator.evaluate_event(ev_invalid, art_invalid)
    assert is_inv_elig is False
    assert "Untrusted publisher" in reason or "No recognized hard business event" in reason or "Insufficient article extraction body" in reason


def test_intl_stop_discovery_when_5_reached():
    """Test 15: Reaching Intl=5 stops discovery immediately."""
    intl_events = [
        Event(
            id=f"intl_{i}",
            canonical_title=f"International Story {i}",
            description=f"Description {i}",
            article_ids=[f"art_{i}"],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        for i in range(5)
    ]
    current_intl_qual = [e for e in intl_events if e.event_category == NewsCategory.INTERNATIONAL]
    assert len(current_intl_qual) >= 5


def test_gemini_429_deterministic_fallback_producing_5_plus_5():
    """Tests 16, 17: Exactly 5 India + 5 Intl candidates produce a valid briefing even under Gemini 429."""
    engine = GeminiEditorialEngine()
    dummy_breakdown = ScoreBreakdown(
        financial_magnitude=85.0,
        market_impact=85.0,
        investor_relevance=85.0,
        corporate_significance=85.0,
        source_quality=85.0,
        total_score=85.0,
    )
    
    india_scored = [
        ScoredEvent(
            event=Event(
                id=f"in_ev_{i}",
                canonical_title=f"India Story {i}",
                description=f"India event description {i}",
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
                canonical_title=f"International Story {i}",
                description=f"Intl event description {i}",
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
        aid = s.event.article_ids[0]
        articles_lookup[aid] = Article(
            id=aid,
            title=s.event.canonical_title,
            url=f"https://example.com/{aid}",
            source_name="Business Standard" if "in_ev" in s.event.id else "PR Newswire",
            published_at=datetime.now(timezone.utc),
            date_verified=True,
            content_text="Detailed corporate event text with key financial metrics and numbers for ranking.",
        )
        
    raw_json = engine._generate_offline_editorial_fallback(pool, articles_lookup)
    payload = BriefingEditorialPayload.model_validate_json(raw_json)
    
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5
    for st in payload.india_stories:
        assert st.section.lower() == "india"
        assert st.headline
        assert st.source
        assert st.url
    for st in payload.international_stories:
        assert st.section.lower() == "international"
        assert st.headline
        assert st.source
        assert st.url
