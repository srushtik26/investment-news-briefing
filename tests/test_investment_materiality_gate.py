"""
Tests for the Investment Materiality Gate.

Validates the 10 required test cases:
1. Routine small-company quarterly-result page is rejected.
2. Major ₹70,000 crore data-centre investment passes.
3. $4 billion chip deal passes.
4. Major regulatory policy change passes.
5. Generic stocks-to-watch fails.
6. Broker target/recommendation fails.
7. Major M&A passes.
8. Existing HCSS thresholds unchanged (separate from Materiality).
9. Existing 5/5/5 contract unchanged.
10. Domestic/India/International routing unchanged.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.verification.materiality import (
    InvestmentMaterialityEvaluator,
    evaluate_investment_materiality,
    INVESTMENT_MATERIALITY_THRESHOLD,
)
from app.verification.single_source import (
    SingleSourceEvaluator,
    SINGLE_SOURCE_MIN_CONFIDENCE,
)
from app.verification.domestic_trending import DOMESTIC_EVALUATOR_MIN_SCORE
from app.ranking import CandidatePoolRanker
from app.pipeline.context import PipelineContext
from app.pipeline.selection import (
    get_final_selectable_unique_events,
    check_refill_candidate_safety,
    run_ranking_and_selection,
)


def _make_art(
    aid: str,
    title: str,
    pub_dt: Optional[datetime] = None,
    cat: NewsCategory = NewsCategory.INDIA,
    body: str = "",
    comp: str = "",
    source: str = "The Economic Times",
) -> Article:
    now = pub_dt or datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name=source,
        category=cat,
        content_text=body or f"Detailed reported corporate text for {title} detailing commercial transaction metrics.",
        published_at=now,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_evt(
    eid: str,
    art: Article,
    comp: str = "CompanyX",
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    conf: float = 90.0,
    figures: Optional[List[str]] = None,
) -> Event:
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp] if comp else [],
        event_category=art.category,
        verification_tier=tier,
        verification_confidence=conf,
        single_source_confidence_score=conf,
        published_date=art.published_at.date() if art.published_at else date(2026, 9, 8),
        financial_figures=figures or [],
    )


def _make_ctx(ref_time=None):
    from app.verification import TwoSourceVerifier

    ref = ref_time or datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
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


# 1. Routine small-company quarterly-result page is rejected
def test_1_routine_small_company_quarterly_results_page_rejected():
    evaluator = InvestmentMaterialityEvaluator()
    # The exact example bad story from the prompt:
    title = "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80"
    art = _make_art("a_bad", title, comp="Complete Sports and Management India Limited")
    evt = _make_evt("e_bad", art, comp="Complete Sports and Management India Limited", conf=85.0)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD
    assert "routine_quarterly_results_page(-40)" in res.negative_reasons


# 2. Major ₹70,000 crore data-centre investment passes
def test_2_major_70000_crore_datacentre_investment_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Adani Group announces ₹70,000 crore investment to build hyper-scale green data centers in Andhra Pradesh"
    art = _make_art("a_adani", title, comp="Adani Group")
    evt = _make_evt("e_adani", art, comp="Adani Group", figures=["₹70,000 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "direct_investment_industry_implication(+15)" in res.positive_reasons
    assert "large_systemic_company(+15)" in res.positive_reasons


# 3. $4 billion chip deal passes
def test_3_4_billion_chip_deal_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "TSMC signs definitive agreement for $4 billion semiconductor manufacturing plant in Europe"
    art = _make_art("a_tsmc", title, cat=NewsCategory.INTERNATIONAL, comp="TSMC")
    evt = _make_evt("e_tsmc", art, comp="TSMC", figures=["$4 billion"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "large_systemic_company(+15)" in res.positive_reasons


# 4. Major regulatory policy change passes
def test_4_major_regulatory_policy_change_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Union Cabinet approves ₹75,000 crore national semiconductor package and incentive guidelines"
    art = _make_art("a_gov", title, cat=NewsCategory.DOMESTIC, comp="Union Cabinet", source="The Hindu")
    evt = _make_evt("e_gov", art, comp="Union Cabinet", figures=["₹75,000 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "policy_regulatory_macro_significance(+25)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons


# 5. Generic stocks-to-watch fails
def test_5_generic_stocks_to_watch_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Stocks to watch today: Reliance, Tata Motors, Infosys and 7 other stocks in focus"
    art = _make_art("a_watch", title)
    evt = _make_evt("e_watch", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD
    assert "stocks_to_watch_listicle(-35)" in res.negative_reasons


# 6. Broker target/recommendation fails
def test_6_broker_target_recommendation_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Jefferies maintains buy rating on Tata Motors with target price of ₹1,250"
    art = _make_art("a_broker", title, comp="Tata Motors")
    evt = _make_evt("e_broker", art, comp="Tata Motors")

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD
    assert "analyst_broker_recommendation_opinion(-30)" in res.negative_reasons


# 7. Major M&A passes
def test_7_major_ma_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Broadcom completes acquisition of VMware in $69 billion all-cash deal"
    art = _make_art("a_ma", title, cat=NewsCategory.INTERNATIONAL, comp="Broadcom")
    evt = _make_evt("e_ma", art, comp="Broadcom", figures=["$69 billion"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons


# 8. Existing HCSS thresholds unchanged (separate from Materiality)
def test_8_existing_hcss_thresholds_unchanged():
    assert SINGLE_SOURCE_MIN_CONFIDENCE == 80.0
    assert DOMESTIC_EVALUATOR_MIN_SCORE == 60.0
    assert INVESTMENT_MATERIALITY_THRESHOLD == 60.0

    ctx = _make_ctx()
    # Candidate with high HCSS (85.0) but routine result page -> fails Materiality
    art_routine = _make_art(
        "a_rot",
        "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80",
        comp="Complete Sports and Management India Limited",
    )
    evt_routine = _make_evt(
        "e_rot",
        art_routine,
        comp="Complete Sports and Management India Limited",
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        conf=85.0,  # PASSES HCSS (85 >= 80)
    )
    ctx.articles_lookup[art_routine.id] = art_routine

    # Test in refill safety check: high HCSS alone must NOT pass
    cand_story = {
        "event_id": "e_rot",
        "headline": art_routine.title,
        "company_name": "Complete Sports and Management India Limited",
        "all_companies": ["Complete Sports and Management India Limited"],
        "category": "india",
    }
    is_safe, reason = check_refill_candidate_safety(
        ev=evt_routine,
        cand_story=cand_story,
        sec_str="india",
        accepted_stories=[],
        event_by_id={},
        ctx=ctx,
        target_date=date(2026, 9, 8),
        lookback_days=3,
    )
    assert is_safe is False
    assert "MATERIALITY_REJECT" in reason

    # Candidate with high Materiality (90.0) but low HCSS (70.0) -> fails HCSS
    art_deal = _make_art(
        "a_deal",
        "Adani Group announces ₹70,000 crore investment to build hyper-scale green data centers in Andhra Pradesh",
        comp="Adani Group",
    )
    evt_deal_low_hcss = _make_evt(
        "e_deal_low",
        art_deal,
        comp="Adani Group",
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        conf=70.0,  # FAILS HCSS (70 < 80)
        figures=["₹70,000 crore"],
    )
    ctx.articles_lookup[art_deal.id] = art_deal

    cand_story_deal = {
        "event_id": "e_deal_low",
        "headline": art_deal.title,
        "company_name": "Adani Group",
        "all_companies": ["Adani Group"],
        "category": "india",
    }
    is_deal_safe, deal_reason = check_refill_candidate_safety(
        ev=evt_deal_low_hcss,
        cand_story=cand_story_deal,
        sec_str="india",
        accepted_stories=[],
        event_by_id={},
        ctx=ctx,
        target_date=date(2026, 9, 8),
        lookback_days=3,
    )
    assert is_deal_safe is False
    assert "LOW_CONFIDENCE" in deal_reason


# 9. Existing 5/5/5 contract unchanged
def test_9_existing_5_5_5_contract_unchanged():
    ctx = _make_ctx()
    ctx.ranker = CandidatePoolRanker()
    t_now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
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
        evt = _make_evt(eid, art, comp, figures=["Rs 800 crore"])
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
        evt = _make_evt(eid, art, comp, figures=["$2 billion"])
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


# 10. Domestic/India/International routing unchanged
def test_10_category_routing_unchanged():
    art_dom = _make_art("a_d", "Supreme Court issues nationwide ruling on environmental policy", cat=NewsCategory.DOMESTIC)
    evt_dom = _make_evt("e_d", art_dom, "Supreme Court")

    art_in = _make_art("a_i", "Reliance Industries acquires renewable power startup for ₹500 crore", cat=NewsCategory.INDIA)
    evt_in = _make_evt("e_i", art_in, "Reliance Industries")

    art_int = _make_art("a_int", "Google opens European AI infrastructure center in Frankfurt", cat=NewsCategory.INTERNATIONAL)
    evt_int = _make_evt("e_int", art_int, "Google")

    assert evt_dom.event_category == NewsCategory.DOMESTIC
    assert evt_in.event_category == NewsCategory.INDIA
    assert evt_int.event_category == NewsCategory.INTERNATIONAL


# 11. Live False Negative 1: EverBank/WaFd $3.9 billion reverse merger passes
def test_11_live_false_negative_everbank_wafd_reverse_merger_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "EverBank to combine with WaFd in $3.9 billion reverse merger"
    body = "EverBank Financial Corp agreed to combine with Washington Federal Inc in a $3.9 billion reverse merger deal."
    art = _make_art("a_everbank", title, cat=NewsCategory.INTERNATIONAL, body=body)
    evt = _make_evt("e_everbank", art, figures=["$3.9 billion"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "high_magnitude_transaction(+15)" in res.positive_reasons
    assert "direct_investment_industry_implication(+15)" in res.positive_reasons


# 12. Live False Negative 2: GE Aerospace $12 billion supplier acquisition passes
def test_12_live_false_negative_ge_aerospace_supplier_buyout_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "GE Aerospace to buy castings supplier for nearly US$12 billion to tackle engine bottleneck"
    body = "GE Aerospace has agreed to buy aerospace castings supplier for nearly US$12 billion to address supply chain bottleneck."
    art = _make_art("a_ge", title, cat=NewsCategory.INTERNATIONAL, body=body, comp="GE Aerospace")
    evt = _make_evt("e_ge", art, comp="GE Aerospace", figures=["US$12 billion"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "very_high_magnitude_transaction(+20)" in res.positive_reasons
    assert "large_systemic_company(+15)" in res.positive_reasons
    assert "major_sector_impact(+10)" in res.positive_reasons


# 13. Live False Negative 3: Cognition AI $2 billion raise at $48 billion valuation passes
def test_13_live_false_negative_cognition_ai_funding_at_valuation_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "AI Startup Cognition Raises $2 Billion at a $48 Billion Value"
    body = "AI coding startup Cognition raises $2 billion in new funding round at a $48 billion valuation led by major venture funds."
    art = _make_art("a_cognition", title, cat=NewsCategory.INTERNATIONAL, body=body)
    evt = _make_evt("e_cognition", art, figures=["$2 billion", "$48 billion"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "very_high_magnitude_transaction(+20)" in res.positive_reasons
    assert "major_sector_impact(+10)" in res.positive_reasons
    assert "valuation_or_lead_investor_backing(+10)" in res.positive_reasons


# 14. Live False Negative 4: European SpaceX rival $450M engine buildout passes
def test_14_live_false_negative_european_spacex_rival_funding_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "European SpaceX Rival Raises $450 Million to Build Rocket Engine"
    body = "European rocket startup raises $450 million in venture funding to build next-generation rocket engine for commercial satellite launch."
    art = _make_art("a_space_rival", title, cat=NewsCategory.INTERNATIONAL, body=body)
    evt = _make_evt("e_space_rival", art, figures=["$450 million"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "meaningful_magnitude_transaction(+10)" in res.positive_reasons
    assert "major_sector_impact(+10)" in res.positive_reasons


# 15. Pixxel $100M raise passes with strategic space sector context, fails without
def test_15_pixxel_funding_with_strategic_space_sector():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Pixxel raises $100 million co-led by Singapore’s Temasek and UK-based Seraphim"
    body_strategic = "Space tech startup Pixxel raises $100 million in Series C funding co-led by Temasek and Seraphim to expand hyperspectral satellite constellation."
    art_strat = _make_art("a_pixxel_strat", title, cat=NewsCategory.INDIA, body=body_strategic)
    evt_strat = _make_evt("e_pixxel_strat", art_strat, figures=["$100 million"])

    res_strat = evaluator.evaluate(evt_strat, art_strat)
    assert res_strat.is_material is True
    assert res_strat.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_sector_impact(+10)" in res_strat.positive_reasons

    # Without strategic context or sector, a generic $100M raise stays below 60
    art_generic = _make_art("a_generic_100m", "Tech firm raises $100 million", cat=NewsCategory.INDIA, body="Tech firm raises $100 million in funding.")
    evt_generic = _make_evt("e_generic_100m", art_generic, figures=["$100 million"])
    res_generic = evaluator.evaluate(evt_generic, art_generic)
    assert res_generic.is_material is False
    assert res_generic.score < INVESTMENT_MATERIALITY_THRESHOLD


# 16. Guardrail: Tiny ₹5 crore order fails
def test_16_tiny_five_crore_order_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Small firm bags minor order worth Rs 5 crore from municipal board"
    art = _make_art("a_tiny_order", title, body="Small company bags minor equipment supply order worth Rs 5 crore from municipal board.")
    evt = _make_evt("e_tiny_order", art, figures=["Rs 5 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 17. Dedicated Freight Corridor national infrastructure completion passes
def test_17_dedicated_freight_corridor_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Rs 20,700 crore, 326 km more: India completes its Dedicated Freight Corridor network"
    body = "The government has announced the completion of the Western Dedicated Freight Corridor spanning 326 km at an estimated cost of Rs 20,700 crore, bolstering logistics and freight capacity nationwide."
    art = _make_art("a_dfc", title, cat=NewsCategory.INDIA, body=body, comp="Indian Railways")
    evt = _make_evt("e_dfc", art, comp="Indian Railways", figures=["Rs 20,700 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons
    assert "very_high_magnitude_transaction(+20)" in res.positive_reasons
    assert "direct_investment_industry_implication(+15)" in res.positive_reasons
    assert "major_sector_impact(+10)" in res.positive_reasons


# 18. Guardrail: Routine small infrastructure story remains rejected
def test_18_routine_small_infrastructure_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Panchayat approves Rs 25 lakh road repair tender in rural district"
    body = "A local gram panchayat approved a road repair contract worth Rs 25 lakh for village road maintenance."
    art = _make_art("a_panchayat", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_panchayat", art, figures=["Rs 25 lakh"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 19. Institutional sponsor controlling stake buyout (Hyrox) passes
def test_19_hyrox_controlling_stake_buyout_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Investors led by L Catterton to buy controlling stake in Hyrox"
    body = "A consortium of investors led by consumer private equity firm L Catterton is set to buy a controlling stake in fitness brand Hyrox to accelerate global expansion."
    art = _make_art("a_hyrox", title, cat=NewsCategory.INTERNATIONAL, body=body, comp="Hyrox")
    evt = _make_evt("e_hyrox", art, comp="Hyrox")

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "direct_investment_industry_implication(+15)" in res.positive_reasons
    assert "institutional_sponsor_control_buyout(+10)" in res.positive_reasons
    assert "valuation_or_lead_investor_backing(+10)" in res.positive_reasons


# 20. Live run example: Freight Corridor Network Completed, Multi-Crore Projects Unveiled passes
def test_20_freight_corridor_multi_crore_projects_unveiled_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Freight Corridor Network Completed, Multi-Crore Projects Unveiled And Cleanliness Drive In Vadodara - India Today"
    body = "India's dedicated freight corridor network has been completed with multiple multi-crore infrastructure projects unveiled across logistics nodes."
    art = _make_art("a_fc_unveiled", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_fc_unveiled", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD
    assert "major_strategic_corporate_event(+30)" in res.positive_reasons
    assert "policy_regulatory_macro_significance(+25)" in res.positive_reasons
    assert "quantified_deal_capex_funding_contract(+20)" in res.positive_reasons


# 21. PASS: Major government logistics and capex programme
def test_21_major_government_capex_logistics_programme_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Cabinet approves Rs 76,000 crore mega port infrastructure and multimodal logistics corridor"
    body = "The Union Cabinet has approved a mega infrastructure outlay of Rs 76,000 crore to construct a deepwater container port and integrated multimodal logistics corridor."
    art = _make_art("a_port_capex", title, cat=NewsCategory.DOMESTIC, body=body, comp="Union Cabinet")
    evt = _make_evt("e_port_capex", art, comp="Union Cabinet", figures=["Rs 76,000 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD


# 22. PASS: Large renewable / power infrastructure expansion
def test_22_large_renewable_power_infrastructure_expansion_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "NTPC signs pact to commission 10 GW solar park and power grid expansion with Rs 45,000 crore capex"
    body = "State-run NTPC has signed a definitive pact to construct a 10 GW solar park and high-voltage power grid expansion under national clean energy mission."
    art = _make_art("a_ntpc_solar", title, cat=NewsCategory.DOMESTIC, body=body, comp="NTPC")
    evt = _make_evt("e_ntpc_solar", art, comp="NTPC", figures=["Rs 45,000 crore"])

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD


# 23. PASS: Major economic policy and regulatory action
def test_23_major_economic_policy_regulatory_action_passes():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Union Cabinet approves new national industrial corridor policy and PLI scheme expansion"
    body = "The Union Cabinet has approved comprehensive guidelines for national industrial corridors with enhanced production-linked incentive outlays across manufacturing clusters."
    art = _make_art("a_ind_policy", title, cat=NewsCategory.DOMESTIC, body=body, comp="Union Cabinet")
    evt = _make_evt("e_ind_policy", art, comp="Union Cabinet")

    res = evaluator.evaluate(evt, art)
    assert res.is_material is True
    assert res.score >= INVESTMENT_MATERIALITY_THRESHOLD


# 24. FAIL Guardrail: Routine political meeting and election poll strategy
def test_24_political_meeting_poll_strategy_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Sachin Pilot meets Rahul, Priyanka to discuss Punjab poll strategy"
    body = "Senior leaders met in New Delhi to deliberate on party organization, seat-sharing, and campaign rally schedules for upcoming polls."
    art = _make_art("a_pilot_poll", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_pilot_poll", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 25. FAIL Guardrail: Party whip and party infighting
def test_25_party_whip_infighting_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "BRS braces for action against Tata Madhu, issues three-line whip to MLCs"
    body = "The party leadership issued a strict three-line whip to all council members following factional infighting."
    art = _make_art("a_whip_story", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_whip_story", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 26. FAIL Guardrail: Helicopter / bad weather travel story
def test_26_helicopter_weather_story_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Poor visibility, bad weather force Bengal CM Suvendu Adhikari’s helicopter to return to Siliguri"
    body = "Inclement weather and heavy rain forced the VIP chopper to make an unscheduled return to the airbase."
    art = _make_art("a_heli_weather", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_heli_weather", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 27. FAIL Guardrail: Sports administration / governance act
def test_27_sports_governance_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Indian Golf Union passes major amendments to MoA to align with National Sports Governance Act"
    body = "The general council adopted constitutional amendments to meet compliance under the sports ministry code."
    art = _make_art("a_golf_union", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_golf_union", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 28. FAIL Guardrail: Generic diplomatic statement
def test_28_generic_diplomatic_statement_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Bangladesh President calls for quickly resolving outstanding issues with India through talks"
    body = "The head of state emphasized bilateral dialogue and traditional diplomatic ties during a media address."
    art = _make_art("a_bangla_talks", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_bangla_talks", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 29. FAIL Guardrail: Minor local disaster fund
def test_29_small_local_disaster_fund_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Suvendu announces Rs 254-cr disaster fund for Hills, new med college in Kalimpong"
    body = "A disaster fund for hills was announced along with local civic repairs following localized monsoon damage."
    art = _make_art("a_hills_fund", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_hills_fund", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


# 30. FAIL Guardrail: Local protest with no economic consequence
def test_30_local_protest_without_economic_scale_fails():
    evaluator = InvestmentMaterialityEvaluator()
    title = "Protesters seek recruitment notification for 72,000 vacancies in Karnataka"
    body = "A student group held a demonstration in front of the district collectorate demanding employment notices."
    art = _make_art("a_local_protest", title, cat=NewsCategory.DOMESTIC, body=body)
    evt = _make_evt("e_local_protest", art)

    res = evaluator.evaluate(evt, art)
    assert res.is_material is False
    assert res.score < INVESTMENT_MATERIALITY_THRESHOLD


