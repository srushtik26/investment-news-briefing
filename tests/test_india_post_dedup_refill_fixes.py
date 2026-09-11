"""
Tests for India 4/5 Post-Dedup Refill Fixes.

Validates the 10 required invariants:
1. India starts at 4/5 after Stage 6
2. First two reserve candidates are known history duplicates
3. Refill skips those duplicates permanently for the run
4. Refill then considers a new eligible India candidate
5. New candidate fills India to 5/5
6. Different companies with same event type are not duplicates
7. Same company same event remains duplicate
8. Thresholds unchanged (Domestic >= 60, India/Intl HCSS >= 80)
9. Exact 5/5/5 contract unchanged
10. Domestic and International behavior unchanged
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.deduplication.history import HistoryStore
from app.deduplication.engine import DeduplicationEngine
from app.verification import TwoSourceVerifier
from app.verification.single_source import SingleSourceEvaluator, SINGLE_SOURCE_MIN_CONFIDENCE
from app.verification.domestic_trending import DOMESTIC_EVALUATOR_MIN_SCORE
from app.pipeline.context import PipelineContext
from app.pipeline.selection import (
    run_deduplication,
    run_post_dedup_refill,
    run_ranking_and_selection,
    check_refill_candidate_safety,
    is_refill_candidate_safe,
    to_ist_date,
)


def _make_art(aid: str, title: str, pub_dt: datetime, cat: NewsCategory, body: str = "", comp: str = "") -> Article:
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name="The Hindu" if cat == NewsCategory.DOMESTIC else "The Economic Times",
        category=cat,
        content_text=body or f"Reported article body for {title} discussing corporate business developments.",
        published_at=pub_dt,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_evt(
    eid: str,
    art: Article,
    comp: str = "OrgX",
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    conf: float = 90.0,
) -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp],
        event_category=art.category,
        verification_tier=tier,
        verification_confidence=conf,
        single_source_confidence_score=conf,
        published_date=art.published_at.date(),
    )


def _make_ctx(ref_time=None):
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


# 1 to 5. India starts at 4/5 after Stage 6; first two reserves are history duplicates;
# refill skips them permanently; evaluates new eligible India candidate; fills to 5/5
def test_1_to_5_india_refill_skips_history_duplicates_and_fills_to_5():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)

    # Pre-populate history with the two known history duplicates:
    # 1. Skyways Air Services quarterly results
    # 2. Complete Sports and Management India quarterly results
    hist_1 = {
        "event_id": "hist_skyways",
        "event_fingerprint": "skyways:earnings:2026-09-01",
        "headline": "Skyways Air Services quarterly net profit rises 18% to Rs 45 crore",
        "company_name": "Skyways Air Services",
        "category": "india",
        "published_date": yesterday,
    }
    hist_2 = {
        "event_id": "hist_csmi",
        "event_fingerprint": "completesports:earnings:2026-09-01",
        "headline": "Complete Sports and Management India quarterly results show revenue growth of 22%",
        "company_name": "Complete Sports and Management India",
        "category": "india",
        "published_date": yesterday,
    }
    history_store.save_briefing(yesterday, [hist_1, hist_2])

    ctx = _make_ctx(ref_time=datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc))
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    # Accepted 4 stories for India
    accepted_stories = [
        {
            "event_id": "e_in_1",
            "headline": "Tata Motors commercial vehicle business reports record monthly sales",
            "company_name": "Tata Motors",
            "all_companies": ["Tata Motors"],
            "event_type": "BUSINESS_EXPANSION",
            "category": "india",
        },
        {
            "event_id": "e_in_2",
            "headline": "Infosys signs 5-year cloud migration deal with European financial group",
            "company_name": "Infosys",
            "all_companies": ["Infosys"],
            "event_type": "CONTRACT_WIN",
            "category": "india",
        },
        {
            "event_id": "e_in_3",
            "headline": "Reliance Retail acquires minority stake in quick-commerce supply firm",
            "company_name": "Reliance Retail",
            "all_companies": ["Reliance Retail"],
            "event_type": "ACQUISITION",
            "category": "india",
        },
        {
            "event_id": "e_in_4",
            "headline": "Adani Ports signs concession agreement for deepwater container terminal",
            "company_name": "Adani Ports",
            "all_companies": ["Adani Ports"],
            "event_type": "INFRASTRUCTURE",
            "category": "india",
        },
    ]

    event_by_id = {}
    for s in accepted_stories:
        art = _make_art(f"a_{s['event_id']}", s["headline"], t_now, NewsCategory.INDIA)
        evt = _make_evt(s["event_id"], art, s["company_name"])
        ctx.articles_lookup[art.id] = art
        event_by_id[s["event_id"]] = evt

    # In memory, we have:
    # 1. Skyways Air Services quarterly results (known history duplicate)
    # 2. Complete Sports and Management India quarterly results (known history duplicate)
    # 3. Shiprocket acquisition (new eligible candidate!)
    art_skyways = _make_art("a_skyways", hist_1["headline"], t_now, NewsCategory.INDIA)
    evt_skyways = _make_evt("e_skyways", art_skyways, "Skyways Air Services")

    art_csmi = _make_art("a_csmi", hist_2["headline"], t_now, NewsCategory.INDIA)
    evt_csmi = _make_evt("e_csmi", art_csmi, "Complete Sports and Management India")

    art_shiprocket = _make_art(
        "a_shiprocket",
        "Shiprocket acquires logistics software platform for Rs 850 crore in all-cash transaction",
        t_now,
        NewsCategory.INDIA,
        body="Shiprocket acquires logistics software platform for Rs 850 crore in all-cash transaction to expand automated warehouse network and supply chain infrastructure across India.",
    )
    evt_shiprocket = _make_evt("e_shiprocket", art_shiprocket, "Shiprocket", conf=88.0)

    for a, e in [(art_skyways, evt_skyways), (art_csmi, evt_csmi), (art_shiprocket, evt_shiprocket)]:
        ctx.articles_lookup[a.id] = a
        ctx.verified_events.append(e)

    # Simulate that Stage 6 ran and marked Skyways and CSMI as rejected
    ctx.stage6_rejected_stories = [
        {"event_id": "e_skyways", "category": "india", "rejection_rule": "3_DAY_HISTORY"},
        {"event_id": "e_csmi", "category": "india", "rejection_rule": "3_DAY_HISTORY"},
    ]

    # Verify India is at 4/5
    assert len([s for s in accepted_stories if s.get("category") == "india"]) == 4

    # Run post-dedup refill
    refilled_stories, refilled_event_by_id = run_post_dedup_refill(ctx, accepted_stories, event_by_id)

    # 1. India must now be 5/5
    final_india = [s for s in refilled_stories if s.get("category") == "india"]
    assert len(final_india) == 5

    # 2 & 3. Refill permanently skipped the duplicates
    assert not any("Skyways" in s.get("headline", "") for s in final_india)
    assert not any("Complete Sports" in s.get("headline", "") for s in final_india)

    # 4 & 5. Refill considered and accepted the eligible candidate Shiprocket
    assert any("Shiprocket" in s.get("headline", "") for s in final_india)
    assert "e_shiprocket" in refilled_event_by_id


# 6. Different companies with same event type are not duplicates
def test_6_different_companies_same_event_type_are_not_duplicates():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)

    # Save a contract win for ABC Infrastructure
    hist_a = {
        "event_id": "hist_a",
        "event_fingerprint": "abcinfrastructure:contract:2026-09-01:4200crore",
        "headline": "ABC Infrastructure wins Rs 4,200 crore highway EPC contract from NHAI",
        "company_name": "ABC Infrastructure",
        "category": "india",
        "published_date": yesterday,
    }
    history_store.save_briefing(yesterday, [hist_a])

    ctx = _make_ctx(ref_time=datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc))
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    # XYZ Manufacturing also wins a contract with similar event type
    art_b = _make_art(
        "a_b",
        "XYZ Manufacturing wins Rs 3,500 crore industrial equipment contract from BHEL",
        t_now,
        NewsCategory.INDIA,
        body="XYZ Manufacturing wins Rs 3,500 crore industrial equipment contract from BHEL to supply advanced power infrastructure components.",
    )
    evt_b = _make_evt("e_b", art_b, "XYZ Manufacturing", conf=85.0)
    ctx.articles_lookup[art_b.id] = art_b

    cand_story_b = {
        "event_id": "e_b",
        "headline": art_b.title,
        "company_name": "XYZ Manufacturing",
        "all_companies": ["XYZ Manufacturing"],
        "event_type": "CONTRACT_WIN",
        "category": "india",
    }

    accepted_stories = []
    event_by_id = {}

    is_safe, reason = check_refill_candidate_safety(
        ev=evt_b,
        cand_story=cand_story_b,
        sec_str="india",
        accepted_stories=accepted_stories,
        event_by_id=event_by_id,
        ctx=ctx,
        target_date=date(2026, 9, 2),
        lookback_days=3,
    )

    # Different company with same event type MUST be safe (NOT duplicate)
    assert is_safe is True
    assert reason == "OK"


# 7. Same company same event remains duplicate
def test_7_same_company_same_event_remains_duplicate():
    history_store = HistoryStore(db_path=":memory:")
    yesterday = date(2026, 9, 1)

    hist_a = {
        "event_id": "hist_a",
        "event_fingerprint": "tata:earnings:2026-09-01",
        "headline": "Tata Motors Q1 consolidated net profit rises 74% to Rs 5,566 crore",
        "company_name": "Tata Motors",
        "category": "india",
        "published_date": yesterday,
    }
    history_store.save_briefing(yesterday, [hist_a])

    ctx = _make_ctx(ref_time=datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc))
    ctx.history_store = history_store
    ctx.dedup_engine = DeduplicationEngine(history_store=history_store)
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    art_repeat = _make_art("a_repeat", "Tata Motors Q1 consolidated net profit rises 74% to Rs 5,566 crore", t_now, NewsCategory.INDIA)
    evt_repeat = _make_evt("e_repeat", art_repeat, "Tata Motors", conf=95.0)
    ctx.articles_lookup[art_repeat.id] = art_repeat

    cand_story_repeat = {
        "event_id": "e_repeat",
        "headline": art_repeat.title,
        "company_name": "Tata Motors",
        "all_companies": ["Tata Motors"],
        "event_type": "EARNINGS",
        "category": "india",
    }

    accepted_stories = []
    event_by_id = {}

    is_safe, reason = check_refill_candidate_safety(
        ev=evt_repeat,
        cand_story=cand_story_repeat,
        sec_str="india",
        accepted_stories=accepted_stories,
        event_by_id=event_by_id,
        ctx=ctx,
        target_date=date(2026, 9, 2),
        lookback_days=3,
    )

    # Same company same event MUST be rejected
    assert is_safe is False
    assert "3_DAY_HISTORY" in reason or "SAME_UNDERLYING_EVENT" in reason


# 8. Thresholds unchanged (Domestic >= 60, India/Intl HCSS >= 80)
def test_8_thresholds_unchanged():
    assert DOMESTIC_EVALUATOR_MIN_SCORE == 60.0
    assert SINGLE_SOURCE_MIN_CONFIDENCE == 80.0

    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    # A single-source India candidate with HCSS = 75 (< 80) must be rejected
    art_low = _make_art("a_low", "Startup Z raises Rs 20 crore in seed round", t_now, NewsCategory.INDIA)
    evt_low = _make_evt(
        "e_low",
        art_low,
        "Startup Z",
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        conf=75.0,
    )
    ctx.articles_lookup[art_low.id] = art_low

    cand_story_low = {
        "event_id": "e_low",
        "headline": art_low.title,
        "company_name": "Startup Z",
        "all_companies": ["Startup Z"],
        "category": "india",
    }

    is_safe, reason = check_refill_candidate_safety(
        ev=evt_low,
        cand_story=cand_story_low,
        sec_str="india",
        accepted_stories=[],
        event_by_id={},
        ctx=ctx,
        target_date=date(2026, 9, 2),
        lookback_days=3,
    )
    assert is_safe is False
    assert "LOW_CONFIDENCE" in reason

    # Domestic candidate with score = 65 (>= 60) must pass quality gate
    art_dom = _make_art("a_dom", "Government issues new guidelines for renewable energy integration", t_now, NewsCategory.DOMESTIC)
    evt_dom = _make_evt(
        "e_dom",
        art_dom,
        "Ministry of Power",
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        conf=65.0,
    )
    ctx.articles_lookup[art_dom.id] = art_dom

    cand_story_dom = {
        "event_id": "e_dom",
        "headline": art_dom.title,
        "company_name": "Ministry of Power",
        "all_companies": ["Ministry of Power"],
        "category": "domestic",
    }

    is_dom_safe, dom_reason = check_refill_candidate_safety(
        ev=evt_dom,
        cand_story=cand_story_dom,
        sec_str="domestic",
        accepted_stories=[],
        event_by_id={},
        ctx=ctx,
        target_date=date(2026, 9, 2),
        lookback_days=3,
    )
    assert is_dom_safe is True
    assert dom_reason == "OK"


# 9. Exact 5/5/5 unchanged
def test_9_exact_5_5_5_unchanged():
    from app.ranking import CandidatePoolRanker
    ctx = _make_ctx()
    ctx.ranker = CandidatePoolRanker()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    accepted_stories = []
    event_by_id = {}

    dom_data = [
        ("Union Cabinet approves semiconductor incentive scheme", "The Hindu", "https://thehindu.com/1"),
        ("Parliament passes National Disaster Management Amendment Bill", "The Indian Express", "https://indianexpress.com/2"),
        ("ISRO successfully completes reusable launch vehicle test", "Hindustan Times", "https://hindustantimes.com/3"),
        ("National Highway Authority awards 4-lane expressway contracts", "Livemint", "https://livemint.com/4"),
        ("Finance Ministry issues revised guidelines for government procurement", "Business Standard", "https://business-standard.com/5"),
    ]
    india_data = [
        ("Tata Motors acquires electric vehicle battery maker for Rs 800 crore", "Tata Motors", "Livemint", "https://livemint.com/in1"),
        ("Infosys wins 500 million dollar IT modernization deal", "Infosys", "The Economic Times", "https://economictimes.com/in2"),
        ("Reliance Retail expands warehouse network with Rs 1200 crore capex", "Reliance Retail", "Business Standard", "https://business-standard.com/in3"),
        ("Adani Ports signs 30-year concession pact for deepwater berth", "Adani Ports", "Financial Express", "https://financialexpress.com/in4"),
        ("Bharti Airtel rolls out enterprise AI services across 10 cities", "Bharti Airtel", "The Hindu BusinessLine", "https://thehindubusinessline.com/in5"),
    ]
    intl_data = [
        ("Microsoft acquires cyber security company in 2 billion dollar cash transaction", "Microsoft", "Reuters", "https://reuters.com/int1"),
        ("Apple partners with semiconductor foundry for next generation processors", "Apple", "Bloomberg", "https://bloomberg.com/int2"),
        ("Google opens new hyperscale cloud data center campus in Europe", "Google", "CNBC", "https://cnbc.com/int3"),
        ("Amazon Web Services invests 5 billion dollars to expand sovereign cloud", "Amazon", "Financial Times", "https://ft.com/int4"),
        ("Nvidia announces new automotive autonomous driving computing platform", "Nvidia", "Wall Street Journal", "https://wsj.com/int5"),
    ]

    # Content bodies that satisfy DomesticTrendingEvaluator threshold (score >= 60).
    dom_content_bodies = {
        "Union Cabinet approves semiconductor incentive scheme":
            "The Union Cabinet chaired by Prime Minister Narendra Modi approved a major semiconductor "
            "incentive scheme worth Rs 50,000 crore. The scheme will attract global chipmakers to India and "
            "boost domestic electronics manufacturing. Parliament is expected to take note of the approval.",
        "Parliament passes National Disaster Management Amendment Bill":
            "Parliament today passed the National Disaster Management Amendment Bill after extensive debate "
            "in both Lok Sabha and Rajya Sabha. The bill passed with a large majority and grants the National "
            "Disaster Management Authority enhanced powers to direct states and union territories during disasters. "
            "Prime Minister Narendra Modi called it a landmark legislation.",
        "ISRO successfully completes reusable launch vehicle test":
            "ISRO successfully completed the reusable launch vehicle landing experiment at its Chitradurga "
            "test facility in Karnataka. The satellite launch vehicle demonstrated autonomous landing capability "
            "for the first time, marking a milestone for India's space programme. The Gaganyaan mission "
            "programme will benefit from this technology.",
        "National Highway Authority awards 4-lane expressway contracts":
            "The National Highway Authority of India awarded contracts for the construction of a 4-lane "
            "expressway worth Rs 12,000 crore connecting three states. Prime Minister Narendra Modi "
            "inaugurated the project. The national highway will reduce travel time significantly. NHAI will oversee.",
        "Finance Ministry issues revised guidelines for government procurement":
            "The Finance Ministry issued revised guidelines for government procurement that will apply to "
            "all central government ministries and departments. The guidelines, announced by the Union Cabinet, "
            "aim to boost Make-in-India and promote domestic manufacturing. Parliament will be informed.",
    }
    for i, (headline, pub, url) in enumerate(dom_data, 1):
        eid = f"e_dom_{i}"
        body = dom_content_bodies.get(headline, f"Reported national policy text for {headline}. "
               "The Union Cabinet and Parliament approved this measure. Prime Minister Narendra Modi "
               "announced it as a landmark decision for India.")
        art = Article(
            id=f"a_dom_{i}", url=url, title=headline, source_name=pub, category=NewsCategory.DOMESTIC,
            content_text=body, published_at=t_now,
            is_verified_url=True, date_verified=True, is_valid_date=True,
        )
        evt = _make_evt(eid, art, "NationalGovt")
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid, "headline": headline, "company_name": "NationalGovt",
            "all_companies": ["NationalGovt"], "category": "domestic",
        })
        event_by_id[eid] = evt

    for i, (headline, comp, pub, url) in enumerate(india_data, 1):
        eid = f"e_in_{i}"
        art = Article(
            id=f"a_in_{i}", url=url, title=headline, source_name=pub, category=NewsCategory.INDIA,
            content_text=f"Reported corporate business text for {headline}.", published_at=t_now,
            is_verified_url=True, date_verified=True, is_valid_date=True,
        )
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid, "headline": headline, "company_name": comp,
            "all_companies": [comp], "category": "india",
        })
        event_by_id[eid] = evt

    for i, (headline, comp, pub, url) in enumerate(intl_data, 1):
        eid = f"e_int_{i}"
        art = Article(
            id=f"a_int_{i}", url=url, title=headline, source_name=pub, category=NewsCategory.INTERNATIONAL,
            content_text=f"Reported global corporate text for {headline}.", published_at=t_now,
            is_verified_url=True, date_verified=True, is_valid_date=True,
        )
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid, "headline": headline, "company_name": comp,
            "all_companies": [comp], "category": "international",
        })
        event_by_id[eid] = evt

    pool, dom, ind, intl, suff, status = run_ranking_and_selection(ctx, accepted_stories, event_by_id)
    assert len(dom) == 5
    assert len(ind) == 5
    assert len(intl) == 5
    assert len(dom) + len(ind) + len(intl) == 15
    assert suff is True



# 10. Domestic and International behavior unchanged
def test_10_domestic_and_international_refill_behavior_unchanged():
    ctx = _make_ctx()
    t_now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    # 4 Domestic, 5 India, 5 International
    accepted_stories = []
    event_by_id = {}

    # 4 Domestic
    for i in range(1, 5):
        eid = f"e_dom_{i}"
        headline = f"Union Cabinet policy reform step {i}"
        comp = f"Govt{i}"
        art = _make_art(f"a_dom_{i}", headline, t_now, NewsCategory.DOMESTIC)
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid,
            "headline": headline,
            "company_name": comp,
            "all_companies": [comp],
            "category": "domestic",
        })
        event_by_id[eid] = evt

    # 5 India
    for i in range(1, 6):
        eid = f"e_in_{i}"
        headline = f"Indian corporate expansion event {i}"
        comp = f"IndCorp{i}"
        art = _make_art(f"a_in_{i}", headline, t_now, NewsCategory.INDIA)
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid,
            "headline": headline,
            "company_name": comp,
            "all_companies": [comp],
            "category": "india",
        })
        event_by_id[eid] = evt

    # 5 International
    for i in range(1, 6):
        eid = f"e_int_{i}"
        headline = f"Global multinational tech event {i}"
        comp = f"GlobalCorp{i}"
        art = _make_art(f"a_int_{i}", headline, t_now, NewsCategory.INTERNATIONAL)
        evt = _make_evt(eid, art, comp)
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid,
            "headline": headline,
            "company_name": comp,
            "all_companies": [comp],
            "category": "international",
        })
        event_by_id[eid] = evt

    # Extra reserve Domestic candidate in memory
    art_dom_5 = _make_art("a_dom_5", "Finance Ministry announces updated sovereign bond calendar", t_now, NewsCategory.DOMESTIC)
    evt_dom_5 = _make_evt("e_dom_5", art_dom_5, "Finance Ministry", conf=75.0)
    ctx.articles_lookup[art_dom_5.id] = art_dom_5
    ctx.verified_events.append(evt_dom_5)

    refilled_stories, _ = run_post_dedup_refill(ctx, accepted_stories, event_by_id)

    dom_final = [s for s in refilled_stories if s.get("category") == "domestic"]
    in_final = [s for s in refilled_stories if s.get("category") == "india"]
    int_final = [s for s in refilled_stories if s.get("category") == "international"]

    assert len(dom_final) == 5
    assert len(in_final) == 5
    assert len(int_final) == 5
    assert any("Finance Ministry" in s.get("headline", "") for s in dom_final)
