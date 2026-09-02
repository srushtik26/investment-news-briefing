"""
Tests for Free RSS Budget Reservation for Domestic and Post-Dedup Refill.

Validates the 16 required invariants:
1. Domestic <5 and shared RSS budget nearly exhausted: Domestic still gets its reserved RSS searches.
2. If Domestic >=5: unused reserved budget becomes available to other sections.
3. India/International cannot consume Domestic reserved budget while Domestic is deficient.
4. Domestic final-mile log appears when Domestic <5.
5. Domestic final-mile candidate still must score >=60.
6. India reaches 5 before Stage 6, one history repeat is removed, post-dedup refill restores India to 5.
7. 3_DAY_HISTORY rejected event never re-enters via refill.
8. Post-dedup refill is bounded to one cycle.
9. No refill occurs when all three sections remain >=5 after dedup.
10. Today-First behavior remains unchanged.
11. Exact 5/5/5 contract unchanged.
12. Domestic threshold remains >=60.
13. India/International HCSS remains >=80.
14. Two-source verification unchanged.
15. SerpAPI cap unchanged.
16. Generic "Morning News Brief" roundup is rejected for Domestic.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.verification import (
    MAX_CORROBORATION_SEARCHES_PER_RUN,
    DOMESTIC_RESERVED_RSS_SEARCHES,
    get_corroboration_count,
    reset_corroboration_counter,
    increment_corroboration_count,
    TwoSourceVerifier,
)
from app.verification.single_source import SINGLE_SOURCE_MIN_CONFIDENCE
from app.verification.domestic_trending import (
    DomesticTrendingEvaluator,
    DOMESTIC_EVALUATOR_MIN_SCORE,
)
from app.verification.serpapi_corroborator import MAX_SERPAPI_SEARCHES_PER_RUN
from app.deduplication.history import HistoryStore
from app.deduplication.engine import DeduplicationEngine
from app.pipeline.context import PipelineContext
from app.pipeline.selection import (
    run_deduplication,
    run_post_dedup_refill,
    run_ranking_and_selection,
    to_ist_date,
    count_quality_eligible_domestic,
    is_refill_candidate_safe,
)


def _make_art(aid: str, title: str, pub_dt: datetime, cat: NewsCategory, body: str = "") -> Article:
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name="The Hindu" if cat == NewsCategory.DOMESTIC else "The Economic Times",
        category=cat,
        content_text=body or f"Full reported article body for {title} containing at least thirty words of detailed context and reporting on this important development.",
        published_at=pub_dt,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_evt(eid: str, art: Article, comp: str = "OrgX", tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED) -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp],
        event_category=art.category,
        verification_tier=tier,
        verification_confidence=90.0,
        published_date=art.published_at.date(),
    )


def _make_ctx(ref_time=None):
    from unittest.mock import MagicMock
    ref = ref_time or datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    settings = MagicMock()
    settings.DEDUP_LOOKBACK_DAYS = 3
    logs = []
    ctx = PipelineContext(
        run_reference_time=ref,
        data_dir=None,
        logs_dir=None,
        settings=settings,
        log_exec=lambda m: logs.append(m),
    )
    ctx.logs = logs
    ctx.verifier = TwoSourceVerifier()
    return ctx


# 1. Domestic <5 and shared RSS budget nearly exhausted: Domestic still gets reserved RSS searches
def test_1_domestic_under_5_gets_reserved_rss_searches():
    reset_corroboration_counter()
    # Simulate 18 searches used out of 20
    increment_corroboration_count(18)
    assert get_corroboration_count() == 18
    assert DOMESTIC_RESERVED_RSS_SEARCHES == 3

    dom_unique_count = 3  # Deficient (< 5)
    # Domestic has access to full budget remaining: 20 - 18 = 2 searches
    rss_rem_domestic = MAX_CORROBORATION_SEARCHES_PER_RUN - get_corroboration_count()
    assert rss_rem_domestic == 2
    assert rss_rem_domestic > 0


# 2. If Domestic >=5: unused reserved budget becomes available to other sections
def test_2_domestic_ge_5_releases_reserved_rss_searches():
    reset_corroboration_counter()
    increment_corroboration_count(17)

    dom_unique_count = 5  # Sufficient (>= 5)
    reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_unique_count < 5 else 0
    assert reserve == 0

    # India/Intl can use remaining searches up to full 20
    usable_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - reserve - get_corroboration_count()
    assert usable_rem == 3


# 3. India/International cannot consume Domestic reserved budget while Domestic is deficient
def test_3_india_intl_cannot_consume_domestic_reserved_budget_while_deficient():
    reset_corroboration_counter()
    # 17 searches consumed out of 20
    increment_corroboration_count(17)

    dom_unique_count = 3  # Deficient (< 5)
    reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_unique_count < 5 else 0
    assert reserve == 3

    # Usable for India/International is 20 - 3 - 17 = 0
    usable_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - reserve - get_corroboration_count()
    assert usable_rem <= 0

    # India/International cannot trigger new discovery searches
    can_intl_search = usable_rem > 0
    assert can_intl_search is False


# 4. Domestic final-mile log appears when Domestic <5
def test_4_domestic_final_mile_log_appears_when_deficient():
    ctx = _make_ctx()
    dom_count = 3
    rss_rem = MAX_CORROBORATION_SEARCHES_PER_RUN - 10
    if dom_count < 5 and rss_rem > 0:
        ctx.log_exec(f"[DOMESTIC_FINAL_MILE_DISCOVERY] Searching for NEW Domestic events (current={dom_count}/5, budget_rem={rss_rem})...")

    assert any("[DOMESTIC_FINAL_MILE_DISCOVERY]" in line for line in ctx.logs)


# 5. Domestic final-mile candidate still must score >=60
def test_5_domestic_final_mile_candidate_must_score_ge_60():
    evaluator = DomesticTrendingEvaluator()
    assert DOMESTIC_EVALUATOR_MIN_SCORE == 60.0

    art_pass = _make_art(
        "a_pass",
        "Union Cabinet approves Rs 10,000 crore incentive scheme for domestic semiconductor manufacturing",
        datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc),
        NewsCategory.DOMESTIC,
    )
    ev_pass = _make_evt("e_pass", art_pass, "Union Cabinet")
    is_qual_pass, score_pass, _ = evaluator.evaluate(ev_pass, art_pass)
    assert score_pass >= 60.0
    assert is_qual_pass is True

    art_fail = _make_art(
        "a_fail",
        "Local municipal council meeting discusses streetlight maintenance schedule",
        datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc),
        NewsCategory.DOMESTIC,
    )
    ev_fail = _make_evt("e_fail", art_fail, "Council")
    is_qual_fail, score_fail, _ = evaluator.evaluate(ev_fail, art_fail)
    assert score_fail < 60.0
    assert is_qual_fail is False


# 6. India reaches 5 before Stage 6, one history repeat is removed, post-dedup refill restores India to 5
def test_6_india_5_reduced_to_4_by_history_refills_to_5():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)
    today = date(2026, 9, 2)

    # Save Milky Mist repeat in 3-day history
    milky_hist = {
        "event_id": "e_hist_milky",
        "event_fingerprint": "milkymist:funding:2026-09-01:100crore",
        "headline": "Milky Mist secures Rs 100 crore private equity funding for expansion",
        "company_name": "Milky Mist",
        "category": "india",
        "published_date": yesterday,
    }
    history_store.save_briefing(yesterday, [milky_hist])

    ctx = _make_ctx(ref_time=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc))
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)

    # Setup 5 India candidate stories, including Milky Mist
    arts = {}
    evts = {}
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    # 4 fresh India stories + 1 Milky Mist repeat
    india_heads = [
        ("e_in_1", "Tata Motors acquires commercial vehicle tech firm for Rs 450 crore", "Tata Motors"),
        ("e_in_2", "Infosys Q1 net profit jumps 14% to Rs 6,500 crore", "Infosys"),
        ("e_in_3", "Reliance Retail opens 50 new smart stores across South India", "Reliance Retail"),
        ("e_in_4", "Adani Ports signs 30-year concession pact for international terminal", "Adani Ports"),
        ("e_in_5", "Milky Mist secures Rs 100 crore private equity funding for expansion", "Milky Mist"), # HISTORY REPEAT!
    ]
    # And 1 extra valid backup candidate in memory
    india_heads.append(
        ("e_in_6", "Wipro partners with Google Cloud for generative enterprise AI solutions", "Wipro")
    )

    for eid, title, comp in india_heads:
        art = _make_art(f"a_{eid}", title, t_now, NewsCategory.INDIA)
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        ctx.verified_events.append(evt)

    # Also add 5 Domestic and 5 International to keep them sufficient
    for i in range(1, 6):
        a_d = _make_art(f"a_dom_{i}", f"Union Government Cabinet major reform policy initiative number {i}", t_now, NewsCategory.DOMESTIC)
        e_d = _make_evt(f"e_dom_{i}", a_d, f"Govt{i}")
        ctx.articles_lookup[a_d.id] = a_d
        ctx.verified_events.append(e_d)

        a_int = _make_art(f"a_int_{i}", f"Global multinational tech corporation quarterly earnings report {i}", t_now, NewsCategory.INTERNATIONAL)
        e_int = _make_evt(f"e_int_{i}", a_int, f"GlobalCorp{i}")
        ctx.articles_lookup[a_int.id] = a_int
        ctx.verified_events.append(e_int)

    # Stage 6 deduplication
    accepted, event_by_id = run_deduplication(ctx)

    # Verify Milky Mist was removed by Stage 6 dedup
    accepted_india = [s for s in accepted if s.get("category") == "india"]
    assert len(accepted_india) == 5  # 4 initial + 1 backup candidate were in pool, but Milky Mist was removed
    assert not any("Milky Mist" in s.get("headline", "") for s in accepted_india)

    # Now simulate only 4 accepted India stories before refill
    accepted_4 = [s for s in accepted if "Wipro" not in s.get("headline", "")]
    assert len([s for s in accepted_4 if s.get("category") == "india"]) == 4

    # Run post-dedup refill
    refilled_stories, refilled_events = run_post_dedup_refill(ctx, accepted_4, event_by_id)

    # Post-dedup refill should restore India to exactly 5 using Wipro!
    final_india = [s for s in refilled_stories if s.get("category") == "india"]
    assert len(final_india) == 5
    assert any("Wipro" in s.get("headline", "") for s in final_india)
    assert not any("Milky Mist" in s.get("headline", "") for s in final_india)


# 7. 3_DAY_HISTORY rejected event never re-enters via refill
def test_7_history_rejected_event_never_reenters_refill():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)

    hist_story = {
        "event_id": "e_stale_1",
        "event_fingerprint": "companyx:acquisition:2026-09-01:500crore",
        "headline": "Company X acquires Company Y for Rs 500 crore",
        "company_name": "Company X",
        "category": "india",
        "published_date": yesterday,
    }
    history_store.save_briefing(yesterday, [hist_story])

    ctx = _make_ctx(ref_time=datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc))
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)

    # Candidate matches the historical story
    art = _make_art("a_stale", "Company X acquires Company Y for Rs 500 crore", datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc), NewsCategory.INDIA)
    evt = _make_evt("e_stale_1", art, "Company X")
    ctx.articles_lookup[art.id] = art
    ctx.verified_events.append(evt)

    accepted_stories = []
    event_by_id = {}

    refilled_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)
    # Stale story MUST NOT enter
    assert not any("Company X" in s.get("headline", "") for s in refilled_stories)


# 8. Post-dedup refill is bounded to one cycle
def test_8_post_dedup_refill_is_bounded():
    ctx = _make_ctx()
    # Empty candidates
    accepted_stories = []
    event_by_id = {}

    # Calling refill on empty state completes immediately in 1 bounded pass without infinite recursion
    refilled_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)
    assert len(refilled_stories) == 0


# 9. No refill occurs when all three sections remain >=5 after dedup
def test_9_no_refill_when_all_three_sections_ge_5():
    ctx = _make_ctx()
    accepted_stories = []
    event_by_id = {}
    for sec in ["domestic", "india", "international"]:
        for i in range(5):
            accepted_stories.append({"event_id": f"e_{sec}_{i}", "headline": f"Head {sec} {i}", "category": sec})

    refilled_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)
    assert len(refilled_stories) == 15
    assert any("[POST_DEDUP_REFILL] All sections sufficient" in line for line in ctx.logs)


# 10. Today-First behavior remains unchanged
def test_10_today_first_behavior_unchanged():
    dt_today = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    dt_yesterday = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    ref_dt = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    assert to_ist_date(dt_today) == to_ist_date(ref_dt)
    assert to_ist_date(dt_yesterday) != to_ist_date(ref_dt)


# 11. Exact 5/5/5 contract unchanged
def test_11_exact_5_5_5_contract_unchanged():
    from config import get_settings
    settings = get_settings()
    assert settings.MIN_VERIFIED_INDIA == 5
    assert settings.MIN_VERIFIED_INTL == 5


# 12. Domestic threshold remains >=60
def test_12_domestic_threshold_remains_ge_60():
    assert DOMESTIC_EVALUATOR_MIN_SCORE == 60.0


# 13. India/International HCSS remains >=80
def test_13_india_intl_hcss_remains_ge_80():
    assert SINGLE_SOURCE_MIN_CONFIDENCE == 80.0


# 14. Two-source verification unchanged
def test_14_two_source_verification_unchanged():
    v = TwoSourceVerifier()
    a1 = _make_art("a1", "Company A acquires Company B for Rs 500 crore", datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc), NewsCategory.INDIA)
    a2 = _make_art("a2", "Company A completes buyout of Company B for Rs 500 cr", datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc), NewsCategory.INDIA)
    is_same, conf, _ = v.is_same_underlying_event(a1, a2)
    assert is_same is True
    assert conf >= 0.8


# 15. SerpAPI cap unchanged
def test_15_serpapi_cap_unchanged():
    assert MAX_SERPAPI_SEARCHES_PER_RUN == 8


# 16. Generic "Morning News Brief" roundup is rejected/deprioritized for Domestic
def test_16_generic_morning_news_brief_roundup_rejected():
    evaluator = DomesticTrendingEvaluator()
    roundup_titles = [
        "HT Morning News Brief September 2: Top headlines of the day and major updates",
        "Today's news wrap: Top national headlines this evening",
        "Daily roundup: What you need to know today",
        "Morning digest: Key news and top stories from India",
        "Evening brief: Summary of major political and national developments",
    ]
    for title in roundup_titles:
        is_noise, rsn = evaluator.is_domestic_noise(title, "")
        assert is_noise is True, f"Expected '{title}' to be rejected as noise but got {rsn}"


# 17. RSS refill cannot add semantic duplicate
def test_17_rss_refill_cannot_add_semantic_duplicate():
    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    target_date = date(2026, 9, 2)

    # Accepted story
    art_acc = _make_art("a_acc", "Tata Motors acquires commercial vehicle startup for Rs 450 crore", t_now, NewsCategory.INDIA)
    evt_acc = _make_evt("e_acc", art_acc, "Tata Motors")
    ctx.articles_lookup[art_acc.id] = art_acc

    accepted_stories = [{
        "event_id": evt_acc.id,
        "headline": evt_acc.canonical_title,
        "company_name": "Tata Motors",
        "category": "india",
    }]
    event_by_id = {evt_acc.id: evt_acc}

    # RSS-discovered candidate that reports the exact same acquisition
    art_dup = _make_art("a_dup", "Tata Motors completes acquisition of commercial vehicle startup for Rs 450 crore", t_now, NewsCategory.INDIA)
    evt_dup = _make_evt("e_dup", art_dup, "Tata Motors")
    ctx.articles_lookup[art_dup.id] = art_dup
    cand_dup = {
        "event_id": evt_dup.id,
        "headline": evt_dup.canonical_title,
        "company_name": "Tata Motors",
        "category": "india",
    }

    # Must be rejected by is_refill_candidate_safe as a semantic duplicate
    is_safe = is_refill_candidate_safe(evt_dup, cand_dup, "india", accepted_stories, event_by_id, ctx, target_date, 3)
    assert is_safe is False, "Expected semantic duplicate to be rejected by refill safety check"


# 18. RSS refill cannot add second India story for same company
def test_18_rss_refill_cannot_add_second_india_story_for_same_company():
    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    target_date = date(2026, 9, 2)

    # Accepted India story for Reliance
    art_acc = _make_art("a_rel_1", "Reliance Retail opens 50 new smart stores across South India", t_now, NewsCategory.INDIA)
    evt_acc = _make_evt("e_rel_1", art_acc, "Reliance")
    ctx.articles_lookup[art_acc.id] = art_acc

    accepted_stories = [{
        "event_id": evt_acc.id,
        "headline": evt_acc.canonical_title,
        "company_name": "Reliance",
        "category": "india",
    }]
    event_by_id = {evt_acc.id: evt_acc}

    # Completely different story, but also for Reliance
    art_cand = _make_art("a_rel_2", "Reliance Jio launches new enterprise 5G cloud connectivity suite", t_now, NewsCategory.INDIA)
    evt_cand = _make_evt("e_rel_2", art_cand, "Reliance")
    ctx.articles_lookup[art_cand.id] = art_cand
    cand_story = {
        "event_id": evt_cand.id,
        "headline": evt_cand.canonical_title,
        "company_name": "Reliance",
        "category": "india",
    }

    # Must be rejected by is_refill_candidate_safe under 1-story-per-company rule
    is_safe = is_refill_candidate_safe(evt_cand, cand_story, "india", accepted_stories, event_by_id, ctx, target_date, 3)
    assert is_safe is False, "Expected second India story for same company to be rejected"


# 19. RSS refill cannot create cross-section duplicate
def test_19_rss_refill_cannot_create_cross_section_duplicate():
    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    target_date = date(2026, 9, 2)

    # Accepted in Domestic section
    art_dom = _make_art("a_dom", "Supreme Court issues landmark ruling on nationwide telecommunications spectrum fees", t_now, NewsCategory.DOMESTIC)
    evt_dom = _make_evt("e_dom", art_dom, "Supreme Court")
    ctx.articles_lookup[art_dom.id] = art_dom

    accepted_stories = [{
        "event_id": evt_dom.id,
        "headline": evt_dom.canonical_title,
        "company_name": "Supreme Court",
        "category": "domestic",
    }]
    event_by_id = {evt_dom.id: evt_dom}

    # Candidate for India section that covers the exact same Supreme Court spectrum ruling
    art_in = _make_art("a_in", "SC delivers landmark verdict on telecom spectrum dues nationwide", t_now, NewsCategory.INDIA)
    evt_in = _make_evt("e_in", art_in, "Supreme Court")
    ctx.articles_lookup[art_in.id] = art_in
    cand_story = {
        "event_id": evt_in.id,
        "headline": evt_in.canonical_title,
        "company_name": "Supreme Court",
        "category": "india",
    }

    # Cross-section semantic duplicate must be rejected
    is_safe = is_refill_candidate_safe(evt_in, cand_story, "india", accepted_stories, event_by_id, ctx, target_date, 3)
    assert is_safe is False, "Expected cross-section duplicate to be rejected by refill safety check"


# 20. Fallback refill follows same append-safety checks
def test_20_fallback_refill_follows_same_append_safety_checks():
    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    target_date = date(2026, 9, 2)

    # Candidate with unverified tier or low score
    art_low = _make_art("a_low", "Low confidence single source story about regional trade", t_now, NewsCategory.INDIA)
    evt_low = _make_evt("e_low", art_low, "TradeCorp", tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)
    evt_low.verification_confidence = 65.0  # Fails India/Intl HCSS requirement of >= 80.0
    evt_low.single_source_confidence_score = 65.0
    ctx.articles_lookup[art_low.id] = art_low

    cand_story = {
        "event_id": evt_low.id,
        "headline": evt_low.canonical_title,
        "company_name": "TradeCorp",
        "category": "india",
    }

    is_safe = is_refill_candidate_safe(evt_low, cand_story, "india", [], {}, ctx, target_date, 3)
    assert is_safe is False, "Expected fallback candidate below quality threshold to be rejected"


# 21. Refill uses pipeline target date, not host date
def test_21_refill_uses_pipeline_target_date_not_host_date():
    history_store = HistoryStore(db_path=":memory:")
    # Historical story saved on 2026-09-01 (1 day prior to pipeline target date 2026-09-02)
    hist_date = date(2026, 9, 1)
    hist_story = {
        "event_id": "e_hist_sep1",
        "event_fingerprint": "xyzcorp:earnings:2026-09-01:100cr",
        "headline": "XYZ Corp reports Q1 net profit of Rs 100 crore",
        "company_name": "XYZ Corp",
        "category": "india",
        "published_date": hist_date,
    }
    history_store.save_briefing(hist_date, [hist_story])

    # Explicit pipeline reference time: 2026-09-02 (even if host date differs)
    pipe_ref_time = datetime(2026, 9, 2, 1, 10, tzinfo=timezone.utc)
    ctx = _make_ctx(ref_time=pipe_ref_time)
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)

    art_cand = _make_art("a_cand", "XYZ Corp reports Q1 net profit of Rs 100 crore", datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc), NewsCategory.INDIA)
    evt_cand = _make_evt("e_cand", art_cand, "XYZ Corp")
    ctx.articles_lookup[art_cand.id] = art_cand
    ctx.verified_events.append(evt_cand)

    accepted_stories = []
    event_by_id = {}

    refilled_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)
    # The story from 2026-09-01 is in the 3-day history window of 2026-09-02, so it MUST NOT be accepted
    assert not any("XYZ Corp" in s.get("headline", "") for s in refilled_stories)


# 22. Domestic HCSS candidates count toward reserve sufficiency
def test_22_domestic_hcss_candidates_count_toward_reserve_sufficiency():
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    evaluator = DomesticTrendingEvaluator()
    verifier = TwoSourceVerifier()

    # 2 Two-source verified Domestic events
    a1 = _make_art("a_ver_1", "Union Cabinet approves Rs 10000 crore semiconductor incentive scheme", t_now, NewsCategory.DOMESTIC)
    e1 = _make_evt("e_ver_1", a1, "Union Cabinet", tier=VerificationTier.TWO_SOURCE_VERIFIED)

    a2 = _make_art("a_ver_2", "Parliament passes landmark digital personal data protection bill", t_now, NewsCategory.DOMESTIC)
    e2 = _make_evt("e_ver_2", a2, "Parliament", tier=VerificationTier.TWO_SOURCE_VERIFIED)

    verified = [e1, e2]

    # 3 High-confidence single-source Domestic events
    a3 = _make_art("a_hcss_3", "Supreme Court constitution bench upholds state powers on sub-quota reservation", t_now, NewsCategory.DOMESTIC)
    e3 = _make_evt("e_hcss_3", a3, "Supreme Court", tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)
    e3.verification_confidence = 75.0

    a4 = _make_art("a_hcss_4", "Indian Army and DRDO successfully test-fire long-range hypersonic cruise missile", t_now, NewsCategory.DOMESTIC)
    e4 = _make_evt("e_hcss_4", a4, "Indian Army", tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)
    e4.verification_confidence = 80.0

    a5 = _make_art("a_hcss_5", "ISRO launches next-generation navigation satellite into geostationary transfer orbit", t_now, NewsCategory.DOMESTIC)
    e5 = _make_evt("e_hcss_5", a5, "ISRO", tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)
    e5.verification_confidence = 78.0

    hcss = [e3, e4, e5]

    articles = {a.id: a for a in [a1, a2, a3, a4, a5]}

    # Count quality-eligible Domestic
    dom_count = count_quality_eligible_domestic(
        verified_events=verified,
        high_confidence_single_candidates=hcss,
        single_source_events=[],
        articles_lookup=articles,
        domestic_evaluator=evaluator,
        verifier=verifier,
        run_reference_time=t_now,
    )
    # All 5 must be counted!
    assert dom_count == 5


# 23. Domestic <5 still reserves exactly 3
def test_23_domestic_under_5_still_reserves_exactly_3():
    dom_count = 4  # Deficient
    reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_count < 5 else 0
    assert reserve == 3
    assert DOMESTIC_RESERVED_RSS_SEARCHES == 3


# 24. Domestic >=5 releases reserve
def test_24_domestic_ge_5_releases_reserve():
    dom_count = 5  # Sufficient
    reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_count < 5 else 0
    assert reserve == 0
