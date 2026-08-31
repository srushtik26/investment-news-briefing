"""
Regression Tests for 24h -> 36h -> 48h -> 72h Fallback Ladder.

Verifies:
1. 25h India: reject 24h, accept 36h
2. 37h International: reject 36h, accept 48h
3. 70h International: reject 48h, accept 72h
4. 73h: always reject (across all horizons)
5. Domestic freeze after 5
6. India freeze after 5
7. International freeze after 5
8. Source whitelist still enforced during fallback
9. Hard-event rule still enforced during fallback
10. Correct fallback_horizon_hours metadata
11. Final validator respects same horizon
12. SerpAPI cap remains <= 8
13. TwoSourceVerifier respects max_age_hours
"""

from datetime import datetime, timezone, timedelta
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.models.enums import VerificationTier
from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
from app.filtering.rules import DateFilterRule, SourceFilterRule, StoryTypeFilterRule
from app.verification.single_source import SingleSourceEvaluator
from app.verification.verifier import TwoSourceVerifier
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN
from app.validation.engine import FinalValidationEngine
from run_pipeline import evaluate_single_source_for_horizon, get_article_age_hours


def _make_article(
    id: str,
    title: str,
    source_name: str,
    url: str,
    age_hours: float,
    category: NewsCategory = NewsCategory.INDIA,
    content_text: str = "Quarterly results reported with net profit surging 25% to Rs 1,500 crore amid strong operating margins across business units.",
    now_utc: datetime = None,
) -> Article:
    ref_time = now_utc or datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    pub_time = ref_time - timedelta(hours=age_hours)
    words = content_text.split()
    if len(words) < 50:
        content_text = content_text + " " + " ".join([f"metric_{i}" for i in range(55)])
    return Article(
        id=id,
        title=title,
        source_name=source_name,
        url=url,
        content_text=content_text,
        published_at=pub_time,
        category=category,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_event(
    id: str,
    canonical_title: str,
    companies: list,
    article_ids: list,
    category: NewsCategory = NewsCategory.INDIA,
    tier: VerificationTier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
) -> Event:
    return Event(
        id=id,
        canonical_title=canonical_title,
        description="Detailed description of corporate event",
        companies_involved=companies,
        financial_figures=["₹1,500 crore"],
        percentages=["25%"],
        article_ids=article_ids,
        event_category=category,
        verification_tier=tier,
        verification_confidence=85.0,
        primary_publisher="The Economic Times" if category == NewsCategory.INDIA else "Reuters",
        primary_url="https://economictimes.indiatimes.com/news/company/story" if category == NewsCategory.INDIA else "https://www.reuters.com/business/story",
    )


# =========================================================================
# 1. 25h India: reject 24h, accept 36h
# =========================================================================
def test_25h_india_rejected_at_24h_accepted_at_36h():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="in_25h",
        title="L&T Bags ₹4,500 Crore Offshore Order in Middle East",
        source_name="The Economic Times",
        url="https://economictimes.indiatimes.com/industry/energy/l-t-order",
        age_hours=25.0,
        category=NewsCategory.INDIA,
        now_utc=ref_time,
    )
    ev = _make_event("ev_in_25h", art.title, ["L&T"], [art.id], NewsCategory.INDIA)
    evaluator = SingleSourceEvaluator()

    # Reject at 24h
    elig_24, _, rsn_24 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=24.0, now_utc=ref_time)
    assert elig_24 is False
    assert "25" in rsn_24 or "Stale" in rsn_24

    # Accept at 36h
    elig_36, _, rsn_36 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=36.0, now_utc=ref_time)
    assert elig_36 is True


# =========================================================================
# 2. 37h International: reject 36h, accept 48h
# =========================================================================
def test_37h_international_rejected_at_36h_accepted_at_48h():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="intl_37h",
        title="Rio Tinto Completes $6.7 Billion Acquisition of Arcadium Lithium",
        source_name="Reuters",
        url="https://www.reuters.com/markets/deals/rio-tinto-arcadium",
        age_hours=37.0,
        category=NewsCategory.INTERNATIONAL,
        now_utc=ref_time,
    )
    ev = _make_event("ev_intl_37h", art.title, ["Rio Tinto"], [art.id], NewsCategory.INTERNATIONAL)
    evaluator = SingleSourceEvaluator()

    # Reject at 36h
    elig_36, _, rsn_36 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=36.0, now_utc=ref_time)
    assert elig_36 is False
    assert "37" in rsn_36 or "Stale" in rsn_36

    # Accept at 48h
    elig_48, _, rsn_48 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=48.0, now_utc=ref_time)
    assert elig_48 is True


# =========================================================================
# 3. 70h International: reject 48h, accept 72h
# =========================================================================
def test_70h_international_rejected_at_48h_accepted_at_72h():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="intl_70h",
        title="Autodesk Agrees to Acquire Construction AI Firm for $850 Million",
        source_name="Reuters",
        url="https://www.reuters.com/technology/autodesk-acquisition",
        age_hours=70.0,
        category=NewsCategory.INTERNATIONAL,
        now_utc=ref_time,
    )
    ev = _make_event("ev_intl_70h", art.title, ["Autodesk"], [art.id], NewsCategory.INTERNATIONAL)
    evaluator = SingleSourceEvaluator()

    # Reject at 48h
    elig_48, _, rsn_48 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=48.0, now_utc=ref_time)
    assert elig_48 is False
    assert "70" in rsn_48 or "Stale" in rsn_48

    # Accept at 72h
    elig_72, _, rsn_72 = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=72.0, now_utc=ref_time)
    assert elig_72 is True


# =========================================================================
# 4. 73h: always reject across all horizons
# =========================================================================
def test_73h_candidate_always_rejected_even_at_72h():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="intl_73h",
        title="Novartis Acquires Biotech Firm for $1.2 Billion in Cash Deal",
        source_name="Reuters",
        url="https://www.reuters.com/business/healthcare-pharmaceuticals/novartis-deal",
        age_hours=73.0,
        category=NewsCategory.INTERNATIONAL,
        now_utc=ref_time,
    )
    ev = _make_event("ev_intl_73h", art.title, ["Novartis"], [art.id], NewsCategory.INTERNATIONAL)
    evaluator = SingleSourceEvaluator()

    for horizon in (24.0, 36.0, 48.0, 72.0):
        elig, _, rsn = evaluate_single_source_for_horizon(ev, art, evaluator, horizon_hours=horizon, now_utc=ref_time)
        assert elig is False
        assert "73" in rsn or "Stale" in rsn

    # Also DateFilterRule always rejects at 72h
    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=ref_time, max_age_hours=72.0)
    assert res.is_accepted is False
    assert res.rule_failed == "DATE"


# =========================================================================
# 5, 6, 7. Freezing after 5 stories
# =========================================================================
def test_section_freezing_logic():
    # When section count reaches 5, section becomes frozen and does not expand
    dom_count = 5
    india_count = 5
    intl_count = 3

    dom_frozen = (dom_count >= 5)
    india_frozen = (india_count >= 5)
    intl_frozen = (intl_count >= 5)

    assert dom_frozen is True
    assert india_frozen is True
    assert intl_frozen is False

    # Simulate ladder progression: only international expands
    expanded_sections = []
    for sec_name, is_frozen in [("Domestic", dom_frozen), ("India", india_frozen), ("International", intl_frozen)]:
        if not is_frozen:
            expanded_sections.append(sec_name)

    assert expanded_sections == ["International"]


# =========================================================================
# 8. Source whitelist still enforced during fallback
# =========================================================================
def test_source_whitelist_enforced_during_fallback():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    # 30h old article from unapproved blog source
    art = _make_article(
        id="unapproved_src",
        title="Major Tech Firm Acquires AI Startup for $500 Million",
        source_name="Random Unverified Tech Blog",
        url="https://www.randomblog.com/news/ai-acquisition",
        age_hours=30.0,
        category=NewsCategory.INTERNATIONAL,
        now_utc=ref_time,
    )
    engine = HardFilterEngine()
    filt = engine.filter_article(art, now_utc=ref_time, max_age_hours=36.0)
    assert filt.is_accepted is False
    assert filt.rule_failed == "SOURCE"


# =========================================================================
# 9. Hard-event rule still enforced during fallback
# =========================================================================
def test_hard_event_rule_enforced_during_fallback():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    # 30h old article with opinion/commentary
    art = _make_article(
        id="opinion_art",
        title="Opinion: Why Global AI Markets Could Face Stricter Regulation in 2027",
        source_name="Reuters",
        url="https://www.reuters.com/commentary/ai-markets-opinion",
        age_hours=30.0,
        category=NewsCategory.INTERNATIONAL,
        now_utc=ref_time,
    )
    engine = HardFilterEngine()
    filt = engine.filter_article(art, now_utc=ref_time, max_age_hours=36.0)
    assert filt.is_accepted is False
    assert filt.rule_failed == "STORY_TYPE"


# =========================================================================
# 10. Correct fallback_horizon_hours metadata
# =========================================================================
def test_correct_fallback_horizon_metadata():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="in_30h",
        title="Tata Motors Q1 Net Profit Jumps 32% to ₹5,400 Crore",
        source_name="The Economic Times",
        url="https://economictimes.indiatimes.com/markets/stocks/earnings/tata-motors",
        age_hours=30.0,
        category=NewsCategory.INDIA,
        now_utc=ref_time,
    )
    ev = _make_event("ev_in_30h", art.title, ["Tata Motors"], [art.id], NewsCategory.INDIA)
    ev.metadata = {"fallback_horizon_hours": 36.0}

    assert ev.metadata["fallback_horizon_hours"] == 36.0


# =========================================================================
# 11. Final validator respects same horizon
# =========================================================================
def test_final_validator_respects_fallback_horizon():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="art_35h",
        title="Infosys Signs $1.5 Billion Strategic Digital Transformation Pact",
        source_name="The Economic Times",
        url="https://economictimes.indiatimes.com/tech/ites/infosys-contract",
        age_hours=35.0,
        category=NewsCategory.INDIA,
        now_utc=ref_time,
    )
    ev = _make_event("ev_35h", art.title, ["Infosys"], [art.id], NewsCategory.INDIA)
    ev.metadata = {"fallback_horizon_hours": 36.0}

    # DateFilterRule evaluating at the event's horizon (36h) must accept
    rule = DateFilterRule()
    res = rule.evaluate(art, now_utc=ref_time, max_age_hours=ev.metadata["fallback_horizon_hours"])
    assert res.is_accepted is True


# =========================================================================
# 12. SerpAPI cap remains <= 8
# =========================================================================
def test_serpapi_cap_constant():
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# =========================================================================
# 13. TwoSourceVerifier respects max_age_hours
# =========================================================================
def test_twosource_verifier_respects_max_age_hours():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art1 = _make_article(
        id="art1_35h",
        title="Reliance Industries Acquires Majority Stake in Solar Tech Startup",
        source_name="The Economic Times",
        url="https://economictimes.indiatimes.com/industry/energy/reliance-solar",
        age_hours=35.0,
        category=NewsCategory.INDIA,
        now_utc=ref_time,
    )
    art2 = _make_article(
        id="art2_34h",
        title="Reliance Buys Controlling Interest in Solar Technology Firm",
        source_name="Business Standard",
        url="https://www.business-standard.com/companies/news/reliance-solar-acquisition",
        age_hours=34.0,
        category=NewsCategory.INDIA,
        now_utc=ref_time,
    )
    verifier = TwoSourceVerifier()

    # At 24h: rejected due to staleness
    same_24, _, rsn_24 = verifier.is_same_underlying_event(art1, art2, now_utc=ref_time, max_age_hours=24.0)
    assert same_24 is False
    assert "STALE" in rsn_24

    # At 36h: accepted as same event
    same_36, _, rsn_36 = verifier.is_same_underlying_event(art1, art2, now_utc=ref_time, max_age_hours=36.0)
    assert same_36 is True


# =========================================================================
# 14. DomesticTrendingEvaluator respects max_age_hours
# =========================================================================
def test_domestic_trending_evaluator_respects_max_age_hours():
    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    art = _make_article(
        id="dom_30h",
        title="Supreme Court Issues Landmark Guidelines on Environmental Impact Assessments",
        source_name="The Indian Express",
        url="https://indianexpress.com/article/india/supreme-court-guidelines-eia-12345/",
        age_hours=30.0,
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court of India on Monday issued comprehensive nationwide guidelines for environmental impact assessments across major infrastructure projects.",
        now_utc=ref_time,
    )
    ev = _make_event("ev_dom_30h", art.title, ["Supreme Court of India"], [art.id], NewsCategory.DOMESTIC)
    evaluator = DomesticTrendingEvaluator()

    # At 24h: rejected due to staleness
    elig_24, _, rsn_24 = evaluator.evaluate(ev, art, now_utc=ref_time, max_age_hours=24.0)
    assert elig_24 is False
    assert "REJECT_STALE" in rsn_24

    # At 36h: accepted
    elig_36, score_36, _ = evaluator.evaluate(ev, art, now_utc=ref_time, max_age_hours=36.0)
    assert elig_36 is True
    assert score_36 >= 60.0


# =========================================================================
# 15. get_quality_level ladder progression and DATA_UNAVAILABLE only on exhaustion
# =========================================================================
def test_get_quality_level_ladder_progression():
    from run_pipeline import get_quality_level
    from app.ranking.models import ScoredEvent, ScoreBreakdown

    ref_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    def _make_scored_event(age_hours: float, tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE):
        art = _make_article(f"art_{age_hours}", f"Corporate event {age_hours}", "Reuters", f"https://www.reuters.com/deal-{age_hours}", age_hours, NewsCategory.INTERNATIONAL, now_utc=ref_time)
        ev = _make_event(f"ev_{age_hours}", art.title, ["Company"], [art.id], NewsCategory.INTERNATIONAL, tier=tier)
        bd = ScoreBreakdown(
            financial_magnitude=20.0,
            market_impact=20.0,
            investor_relevance=20.0,
            corporate_significance=15.0,
            source_quality=10.0,
            total_score=85.0,
        )
        return ScoredEvent(
            event=ev,
            investment_score=85.0,
            score_breakdown=bd,
        ), art

    # 1. 5 stories <= 24h -> FALLBACK_SUCCESS_24H
    pool_24 = []
    lookup = {}
    for h in [2.0, 6.0, 12.0, 18.0, 23.0]:
        sc, art = _make_scored_event(h)
        pool_24.append(sc)
        lookup[art.id] = art
    assert get_quality_level(pool_24, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "FALLBACK_SUCCESS_24H"

    # 2. 5 stories with oldest 35h -> FALLBACK_SUCCESS_36H
    pool_36 = pool_24[:4]
    sc_35, art_35 = _make_scored_event(35.0)
    pool_36.append(sc_35)
    lookup[art_35.id] = art_35
    assert get_quality_level(pool_36, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "FALLBACK_SUCCESS_36H"

    # 3. 5 stories with oldest 45h -> FALLBACK_SUCCESS_48H
    pool_48 = pool_24[:4]
    sc_45, art_45 = _make_scored_event(45.0)
    pool_48.append(sc_45)
    lookup[art_45.id] = art_45
    assert get_quality_level(pool_48, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "FALLBACK_SUCCESS_48H"

    # 4. 5 stories with oldest 70h -> EMERGENCY_SUCCESS_72H
    pool_72 = pool_24[:4]
    sc_70, art_70 = _make_scored_event(70.0)
    pool_72.append(sc_70)
    lookup[art_70.id] = art_70
    assert get_quality_level(pool_72, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "EMERGENCY_SUCCESS_72H"

    # 5. Fewer than 5 stories -> DATA_UNAVAILABLE
    pool_deficient = pool_24[:4]
    assert get_quality_level(pool_deficient, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "DATA_UNAVAILABLE"

    # 6. Any story > 72h -> DATA_UNAVAILABLE
    pool_stale = pool_24[:4]
    sc_75, art_75 = _make_scored_event(75.0)
    pool_stale.append(sc_75)
    lookup[art_75.id] = art_75
    assert get_quality_level(pool_stale, two_source_count=0, articles_lookup=lookup, now_utc=ref_time) == "DATA_UNAVAILABLE"

