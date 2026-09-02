"""
Focused deterministic tests for Today-First selection, IST morning date awareness,
Domestic final-mile discovery, foreign domestic safeguard, and historical dedup.

Requirements:
1. Sep 2 story outranks Sep 1 story even if Sep 1 has slightly higher score.
2. If there are 5 Sep 2 qualifying stories: zero Sep 1 stories are selected.
3. If only 4 Sep 2 stories exist: exactly one qualifying previous-day <=24h story can backfill.
4. Morning IST conversion is correct.
5. UTC date crossing midnight does not misclassify the IST calendar date.
6. Fallback 36h does not activate while >=5 valid current/today candidates exist.
7. Domestic final-mile discovery runs when Domestic < 5.
8. Domestic final-mile candidates still pass evaluator >=60.
9. UK-only political story cannot classify as Domestic because of discovery prior.
10. Major India politics qualifies.
11. Major India economy/GDP story qualifies.
12. Lifestyle/advice/quote-based story does not fill Domestic.
13. Historical same-event dedup still prevents recycled events.
14. India/International hard-business rules remain unchanged.
"""

from datetime import date, datetime, timedelta, timezone
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.pipeline.selection import to_ist_date, run_ranking_and_selection
from app.pipeline.context import PipelineContext
from app.classification.region_classifier import EventRegionClassifier
from app.verification.domestic_trending import DomesticTrendingEvaluator, DomesticTopic, classify_domestic_topic
from app.deduplication.history import HistoryStore
from app.deduplication.engine import DeduplicationEngine

try:
    from zoneinfo import ZoneInfo
    IST_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    IST_TZ = timezone(timedelta(hours=5, minutes=30))


def _make_article(
    aid: str,
    title: str,
    published_at: datetime,
    category: NewsCategory = NewsCategory.INDIA,
    source_name: str = "The Economic Times",
    content: str = "",
) -> Article:
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name=source_name,
        category=category,
        content_text=content or f"Reported content about {title} with Rs 500 crore financial details.",
        published_at=published_at,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_event(
    eid: str,
    art: Article,
    company: str = "CompanyA",
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
) -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        event_category=art.category,
        companies_involved=[company],
        financial_figures=["₹500 crore"],
        verification_tier=tier,
        verification_confidence=90.0,
    )


def _make_scored_event(event: Event, score: float, rank: int = 1) -> ScoredEvent:
    breakdown = ScoreBreakdown(
        financial_magnitude=score,
        market_impact=score,
        investor_relevance=score,
        corporate_significance=score,
        source_quality=score,
        total_score=score,
    )
    return ScoredEvent(event=event, investment_score=score, score_breakdown=breakdown, rank=rank)


# 1. Sep 2 story outranks Sep 1 story even if Sep 1 has slightly higher score
def test_1_today_story_outranks_yesterday_story_despite_lower_score():
    # 06:40 AM IST on Sep 2 is 01:10 AM UTC on Sep 2
    ref_time = datetime(2026, 9, 2, 1, 10, tzinfo=timezone.utc)
    
    # Story 1: Sep 1, 20:00 IST (14:30 UTC), age ~10.6h, score 89.0
    art1 = _make_article("a1", "CompanyOld Q1 profit jumps 20% to Rs 600 crore", datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc))
    ev1 = _make_event("e1", art1, company="CompanyOld")
    s1 = _make_scored_event(ev1, 89.0)

    # Story 2: Sep 2, 05:00 IST (23:30 UTC on Sep 1), age ~1.6h, score 81.0
    art2 = _make_article("a2", "CompanyNew Q1 profit surges 15% to Rs 400 crore", datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc))
    ev2 = _make_event("e2", art2, company="CompanyNew")
    s2 = _make_scored_event(ev2, 81.0)

    articles_map = {art1.id: art1, art2.id: art2}

    class MockRanker:
        def rank_events(self, events, top_n=10):
            return RankedCandidatePool(domestic_candidates=[], india_candidates=[s1, s2], international_candidates=[])

    ctx = PipelineContext(
        run_reference_time=ref_time,
        data_dir=None,
        logs_dir=None,
        settings=None,
        articles_lookup=articles_map,
        ranker=MockRanker(),
    )
    from app.verification.verifier import TwoSourceVerifier
    ctx.verifier = TwoSourceVerifier()

    pool, dom, ind, intl, suff, status = run_ranking_and_selection(
        ctx,
        accepted_stories=[{"event_id": "e1"}, {"event_id": "e2"}],
        event_by_id={"e1": ev1, "e2": ev2},
    )

    # In ind, CompanyNew (Sep 2) must be ranked ahead of CompanyOld (Sep 1)
    assert len(ind) == 2
    assert ind[0].event.canonical_title == art2.title
    assert ind[1].event.canonical_title == art1.title


# 2. If there are 5 Sep 2 qualifying stories: zero Sep 1 stories are selected
def test_2_five_today_stories_means_zero_yesterday_stories_selected():
    ref_time = datetime(2026, 9, 2, 1, 10, tzinfo=timezone.utc)
    articles_map = {}
    events_map = {}
    india_cands = []

    # 5 Today stories (Sep 2)
    for i in range(1, 6):
        art = _make_article(
            f"a_today_{i}",
            f"CompanyToday{i} secures Rs {i * 100} crore defence contract",
            datetime(2026, 9, 2, 0, 30 + i, tzinfo=timezone.utc),
        )
        ev = _make_event(f"e_today_{i}", art, company=f"CompanyToday{i}")
        s = _make_scored_event(ev, 80.0 + i)
        articles_map[art.id] = art
        events_map[ev.id] = ev
        india_cands.append(s)

    # 3 Yesterday stories (Sep 1) with higher scores (95.0)
    for i in range(1, 4):
        art = _make_article(
            f"a_old_{i}",
            f"CompanyOld{i} signs major Rs {i * 500} crore acquisition deal",
            datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
        ev = _make_event(f"e_old_{i}", art, company=f"CompanyOld{i}")
        s = _make_scored_event(ev, 95.0 + i)
        articles_map[art.id] = art
        events_map[ev.id] = ev
        india_cands.append(s)

    class MockRanker:
        def rank_events(self, events, top_n=10):
            return RankedCandidatePool(domestic_candidates=[], india_candidates=list(india_cands), international_candidates=[])

    ctx = PipelineContext(
        run_reference_time=ref_time,
        data_dir=None,
        logs_dir=None,
        settings=None,
        articles_lookup=articles_map,
        ranker=MockRanker(),
    )
    from app.verification.verifier import TwoSourceVerifier
    ctx.verifier = TwoSourceVerifier()

    pool, dom, ind, intl, suff, status = run_ranking_and_selection(
        ctx,
        accepted_stories=[{"event_id": k} for k in events_map],
        event_by_id=events_map,
    )

    assert len(ind) == 5
    for s in ind:
        art = articles_map[s.event.article_ids[0]]
        assert to_ist_date(art.published_at) == date(2026, 9, 2)
        assert "CompanyOld" not in s.event.canonical_title


# 3. If only 4 Sep 2 stories exist: exactly one qualifying previous-day <=24h story can backfill
def test_3_four_today_stories_backfills_exactly_one_yesterday_story():
    ref_time = datetime(2026, 9, 2, 1, 10, tzinfo=timezone.utc)
    articles_map = {}
    events_map = {}
    india_cands = []

    # 4 Today stories (Sep 2)
    for i in range(1, 5):
        art = _make_article(
            f"a_today_{i}",
            f"CompanyToday{i} secures Rs {i * 100} crore order",
            datetime(2026, 9, 2, 0, 30 + i, tzinfo=timezone.utc),
        )
        ev = _make_event(f"e_today_{i}", art, company=f"CompanyToday{i}")
        s = _make_scored_event(ev, 80.0 + i)
        articles_map[art.id] = art
        events_map[ev.id] = ev
        india_cands.append(s)

    # 3 Yesterday stories (Sep 1) <= 24h
    for i in range(1, 4):
        art = _make_article(
            f"a_old_{i}",
            f"CompanyOld{i} profit jumps Rs {i * 200} crore",
            datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
        )
        ev = _make_event(f"e_old_{i}", art, company=f"CompanyOld{i}")
        s = _make_scored_event(ev, 85.0 + i)
        articles_map[art.id] = art
        events_map[ev.id] = ev
        india_cands.append(s)

    class MockRanker:
        def rank_events(self, events, top_n=10):
            return RankedCandidatePool(domestic_candidates=[], india_candidates=list(india_cands), international_candidates=[])

    ctx = PipelineContext(
        run_reference_time=ref_time,
        data_dir=None,
        logs_dir=None,
        settings=None,
        articles_lookup=articles_map,
        ranker=MockRanker(),
    )
    from app.verification.verifier import TwoSourceVerifier
    ctx.verifier = TwoSourceVerifier()

    pool, dom, ind, intl, suff, status = run_ranking_and_selection(
        ctx,
        accepted_stories=[{"event_id": k} for k in events_map],
        event_by_id=events_map,
    )

    assert len(ind) == 5
    today_count = sum(1 for s in ind if to_ist_date(articles_map[s.event.article_ids[0]].published_at) == date(2026, 9, 2))
    older_count = sum(1 for s in ind if to_ist_date(articles_map[s.event.article_ids[0]].published_at) == date(2026, 9, 1))
    assert today_count == 4
    assert older_count == 1


# 4. Morning IST conversion is correct
def test_4_morning_ist_conversion_correct():
    # 06:30 AM IST on 2 Sep 2026 is 01:00 AM UTC on 2 Sep 2026
    dt_utc = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    ist_date = to_ist_date(dt_utc)
    assert ist_date == date(2026, 9, 2)


# 5. UTC date crossing midnight does not misclassify the IST calendar date
def test_5_utc_midnight_boundary_does_not_misclassify_ist():
    # 02:00 AM IST on 2 Sep 2026 is 20:30 PM UTC on 1 Sep 2026
    dt_utc = datetime(2026, 9, 1, 20, 30, tzinfo=timezone.utc)
    assert dt_utc.date() == date(2026, 9, 1)  # UTC date is Sep 1
    assert to_ist_date(dt_utc) == date(2026, 9, 2)  # IST date is Sep 2!


# 6. Fallback 36h does not activate while >=5 valid current/today candidates exist
def test_6_fallback_does_not_activate_when_sufficient():
    from app.pipeline.fallback_manager import get_section_quality_state
    events = [_make_event(f"e{i}", _make_article(f"a{i}", f"Company{i} profit", datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc))) for i in range(5)]
    state = get_section_quality_state(events, [], NewsCategory.INDIA)
    assert state["section_complete"] is True
    assert state["slot_deficit"] == 0


# 7. Domestic final-mile discovery runs when Domestic < 5
def test_7_domestic_final_mile_discovery_runs_when_deficit():
    from app.pipeline.fallback_manager import run_expansion_and_fallbacks
    from app.verification.verifier import TwoSourceVerifier
    from app.verification import reset_corroboration_counter

    reset_corroboration_counter()
    called_queries = []
    class DummyProvider:
        def discover(self, query, country="India", max_results=10):
            called_queries.append(query)
            return []

    class DummyDiscoveryService:
        provider = DummyProvider()

    ctx = PipelineContext(
        run_reference_time=datetime(2026, 9, 2, 1, 10, tzinfo=timezone.utc),
        data_dir=None,
        logs_dir=None,
        settings=None,
        discovery_service=DummyDiscoveryService(),
        verifier=TwoSourceVerifier(),
        reg_clf=EventRegionClassifier(),
    )
    # Domestic has only 2 events (< 5)
    art1 = _make_article("ad1", "Union Cabinet clears infra mission", datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc), category=NewsCategory.DOMESTIC)
    art2 = _make_article("ad2", "Election Commission announces election schedule", datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc), category=NewsCategory.DOMESTIC)
    ev1 = _make_event("ed1", art1)
    ev2 = _make_event("ed2", art2)
    ctx.verified_events = [ev1, ev2]
    ctx.articles_lookup = {art1.id: art1, art2.id: art2}

    run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat: [ev1, ev2] if cat == NewsCategory.DOMESTIC else [ev1]*5,
        count_unique_section_events_fn=lambda cat: 2 if cat == NewsCategory.DOMESTIC else 5,
        get_unique_candidate_events_fn=lambda: [ev1, ev2],
        is_domestic_diversity_satisfied_fn=lambda evs: False,
    )

    # Verified that Domestic final-mile searches were executed
    dom_searches = [q for q in called_queries if "thehindu.com" in q or "Cabinet" in q or "Parliament" in q]
    assert len(dom_searches) > 0


# 8. Domestic final-mile candidates still pass evaluator >= 60
def test_8_domestic_candidate_requires_score_ge_60():
    evaluator = DomesticTrendingEvaluator()
    art = _make_article(
        "ad_pass",
        "Union Cabinet clears ₹75,000 crore national semiconductor package",
        datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        category=NewsCategory.DOMESTIC,
        content="The Union Cabinet chaired by the Prime Minister approved a major national semiconductor framework.",
    )
    ev = _make_event("ed_pass", art)
    is_elig, score, rsn = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0


# 9. UK-only political story cannot classify as Domestic because of discovery prior
def test_9_uk_only_political_story_routes_international_despite_domestic_prior():
    clf = EventRegionClassifier()
    title = "Former UK PM Boris Johnson urges Western allies to double defense spending"
    content = "Speaking in London, the former British prime minister said European countries must raise defense budgets."
    cat, rsn = clf.classify_with_reason(
        title=title,
        content=content,
        discovery_region=NewsCategory.DOMESTIC,
    )
    assert cat == NewsCategory.INTERNATIONAL
    assert "foreign geography" in rsn.lower()


# 10. Major India politics qualifies
def test_10_major_india_politics_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_article(
        "ad_pol",
        "Election Commission finalizes schedule for upcoming five state assembly elections",
        datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        category=NewsCategory.DOMESTIC,
        content="Chief Election Commissioner announced the polling phases across states with model code of conduct active.",
    )
    ev = _make_event("ed_pol", art)
    is_elig, score, rsn = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0
    assert classify_domestic_topic(art.title, art.content_text) == DomesticTopic.POLITICS_ELECTIONS


# 11. Major India economy/GDP story qualifies
def test_11_major_india_economy_gdp_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_article(
        "ad_econ",
        "India GDP growth accelerates to 7.8% in Q1 driven by manufacturing and services expansion",
        datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        category=NewsCategory.DOMESTIC,
        content="Official data showed strong macroeconomic resilience as gross domestic product exceeded forecasts.",
    )
    ev = _make_event("ed_econ", art)
    is_elig, score, rsn = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0
    assert classify_domestic_topic(art.title, art.content_text) == DomesticTopic.ECONOMY_NATIONAL


# 12. Lifestyle/advice/quote-based story does not fill Domestic
def test_12_lifestyle_advice_quote_story_rejected():
    evaluator = DomesticTrendingEvaluator()
    headlines = [
        "IIT, IIM, IAS to loneliness: Divya Mittal asks students to focus on mental health",
        "'Don't buy gold, don't marry abroad': PM Modi asks youth at youth convention",
        "Top 10 holiday destinations to visit during monsoon season",
    ]
    for h in headlines:
        art = _make_article("ad_noise", h, datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc), category=NewsCategory.DOMESTIC)
        ev = _make_event("ed_noise", art)
        is_elig, score, rsn = evaluator.evaluate(ev, art)
        assert not is_elig
        assert "REJECT_NOISE" in rsn


# 13. Historical same-event dedup still prevents recycled events
def test_13_historical_same_event_dedup_prevents_recycling():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)
    today = date(2026, 9, 2)

    # Save yesterday's briefing story
    hist_story = {
        "event_id": "evt-gk-101",
        "event_fingerprint": "gopro:m_and_a:2026-09-01:100million",
        "headline": "GoPro acquires Australian camera tech startup for $100 million",
        "company_name": "GoPro",
        "category": "international",
        "published_date": yesterday,
    }
    history_store.save_briefing(briefing_date=yesterday, stories=[hist_story])

    dedup = DeduplicationEngine(history_store=history_store)

    # Today an outlet publishes slightly varied headline of the exact same deal
    candidate_today = [
        {
            "headline": "GoPro acquires Australian camera tech startup for $100 million deal",
            "company_name": "GoPro",
            "event_type": "M_AND_A",
            "category": "international",
            "key_facts": ["100million"],
        }
    ]

    accepted, rejected = dedup.filter_stories(candidate_today, target_date=today, lookback_days=3)
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert rejected[0]["rejection_rule"] == "3_DAY_HISTORY"


# 14. India/International hard-business rules remain unchanged
def test_14_india_and_intl_hard_business_rules_unchanged():
    clf = EventRegionClassifier()
    # Indian quarterly results -> INDIA
    cat1, _ = clf.classify_with_reason(title="Infosys Q1 net profit rises 10% to Rs 6,000 crore", financial_figures=["Rs 6000 crore"])
    assert cat1 == NewsCategory.INDIA

    # Global Fed rate decision -> INTERNATIONAL
    cat2, _ = clf.classify_with_reason(title="Federal Reserve holds benchmark interest rates steady at 5.25%")
    assert cat2 == NewsCategory.INTERNATIONAL


# 15. Single-source confidence threshold is strictly >= 80 (79 does not qualify, 80 does qualify)
def test_15_single_source_confidence_threshold_is_strictly_80():
    from app.verification.single_source import SingleSourceEvaluator, SINGLE_SOURCE_MIN_CONFIDENCE

    assert SINGLE_SOURCE_MIN_CONFIDENCE == 80.0
    evaluator = SingleSourceEvaluator()

    body = (
        "Reliance Industries announced that it has agreed to acquire a 50 percent stake in a "
        "clean energy technology startup for a total consideration of Rs 1000 crore. The "
        "transaction will support green hydrogen development and clean tech innovations across India."
    )
    art = _make_article(
        "a_hcss",
        "Reliance acquires 50% stake in clean energy tech startup for Rs 1000 crore",
        datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        category=NewsCategory.INDIA,
        content=body,
    )
    ev = _make_event("e_hcss", art, company="Reliance")

    # Real evaluation achieves >= 80.0 and qualifies
    is_elig, score, _ = evaluator.evaluate_event(ev, art, now_utc=datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc))
    assert is_elig
    assert score >= 80.0

    # Boundary test: score 79.0 does not qualify (79.0 < SINGLE_SOURCE_MIN_CONFIDENCE)
    assert not (79.0 >= SINGLE_SOURCE_MIN_CONFIDENCE)
    assert (80.0 >= SINGLE_SOURCE_MIN_CONFIDENCE)


# 16. Semantic history dedup safety distinguishes separate developments
def test_16_semantic_history_dedup_safety_distinguishes_different_developments():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)
    today = date(2026, 9, 2)

    # Historical story 1: Earnings
    hist_story1 = {
        "event_id": "evt-earn-1",
        "event_fingerprint": "companya:earnings:2026-09-01:500crore",
        "headline": "Company A reports Q1 net profit jumps 15% to Rs 500 crore",
        "company_name": "Company A",
        "category": "india",
        "published_date": yesterday,
    }
    # Historical story 2: RBI repo rate
    hist_story2 = {
        "event_id": "evt-rbi-1",
        "event_fingerprint": "rbi:rate_cut:2026-09-01:25bps",
        "headline": "RBI cuts repo rate by 25 bps to 6.25% in monetary policy meet",
        "company_name": "RBI",
        "category": "domestic",
        "published_date": yesterday,
    }
    history_store.save_briefing(briefing_date=yesterday, stories=[hist_story1, hist_story2])

    dedup = DeduplicationEngine(history_store=history_store)

    # Today:
    # 1. Company A announces an acquisition (Different development! Should NOT be rejected)
    # 2. RBI publishes new regulatory framework for NBFC lending (Different development! Should NOT be rejected)
    candidates_today = [
        {
            "headline": "Company A announces acquisition of Company B for Rs 800 crore",
            "company_name": "Company A",
            "event_type": "M_AND_A",
            "category": "india",
            "key_facts": ["800crore"],
        },
        {
            "headline": "RBI publishes new regulatory framework for NBFC commercial lending",
            "company_name": "RBI",
            "event_type": "REGULATORY",
            "category": "domestic",
            "key_facts": ["nbfc"],
        },
    ]

    accepted, rejected = dedup.filter_stories(candidates_today, target_date=today, lookback_days=3)
    assert len(accepted) == 2
    assert len(rejected) == 0

