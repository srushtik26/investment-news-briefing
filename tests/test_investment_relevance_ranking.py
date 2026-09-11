"""
Focused test suite for Investment Relevance Ranking.

Verifies:
1. JSW-Skoda style JV ranks above a routine quarterly result.
2. $1.6bn FPI selling ranks above minor company result.
3. satellite-spectrum regulatory decision ranks above generic stock story.
4. large capex story ranks above minor company announcement.
5. trusted source still matters as tie-breaker.
6. freshness still matters as tie-breaker.
7. materiality threshold unchanged.
8. section counts unchanged.
"""

from datetime import date, datetime, timezone
from typing import List, Optional
from unittest.mock import MagicMock
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.ranking import (
    CandidatePoolRanker,
    InvestmentRelevanceScorer,
    RankedCandidatePool,
    ScoreBreakdown,
    ScoredEvent,
)
from app.verification.materiality import INVESTMENT_MATERIALITY_THRESHOLD
from app.verification.single_source import SINGLE_SOURCE_MIN_CONFIDENCE
from app.verification.domestic_trending import DOMESTIC_EVALUATOR_MIN_SCORE
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection


def _make_art(
    aid: str,
    title: str,
    pub_dt: Optional[datetime] = None,
    cat: NewsCategory = NewsCategory.INDIA,
    body: str = "",
    source: str = "The Economic Times",
) -> Article:
    now = pub_dt or datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name=source,
        category=cat,
        content_text=body or f"Detailed reported business text for {title}.",
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
        article_ids=[art.id, f"{art.id}_2"] if tier == VerificationTier.TWO_SOURCE_VERIFIED else [art.id],
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


# 1. JSW-Skoda style JV ranks above a routine quarterly result
def test_1_jsw_skoda_jv_ranks_above_routine_quarterly_result():
    scorer = InvestmentRelevanceScorer()
    ranker = CandidatePoolRanker(scorer=scorer)

    art_jv = _make_art(
        "a_jv",
        "JSW Group and Skoda Auto Volkswagen form joint venture to develop electric vehicles and expand manufacturing in India",
        body="JSW Group signs definitive joint venture agreement with Skoda Auto Volkswagen with ₹15,000 crore investment.",
    )
    evt_jv = _make_evt("e_jv", art_jv, comp="JSW Group", figures=["₹15,000 crore"])

    art_routine = _make_art(
        "a_rot",
        "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80",
        body="Standalone financial results for the quarter ended June 30, 2026. Net profit Rs 1.2 crore.",
    )
    evt_rot = _make_evt("e_rot", art_routine, comp="Complete Sports and Management India Limited", figures=["Rs 1.2 crore"])

    scored_jv = scorer.score_event(evt_jv, source_count=2, is_multi_source_verified=True)
    scored_rot = scorer.score_event(evt_rot, source_count=2, is_multi_source_verified=True)

    assert scored_jv.investment_score > scored_rot.investment_score
    assert scored_jv.investment_score >= 80.0
    assert scored_rot.investment_score < 60.0
    assert scored_rot.score_breakdown.relevance_penalties > 0.0

    pool = ranker.rank_events([evt_rot, evt_jv])
    assert pool.india_candidates[0].event.id == "e_jv"
    assert pool.india_candidates[1].event.id == "e_rot"


# 2. $1.6bn FPI selling ranks above minor company result
def test_2_fpi_selling_ranks_above_minor_company_result():
    scorer = InvestmentRelevanceScorer()
    ranker = CandidatePoolRanker(scorer=scorer)

    art_fpi = _make_art(
        "a_fpi",
        "FPIs offload $1.6 billion in Indian equities as foreign capital flows reverse on global rate concerns",
        body="Foreign portfolio investors pulled out $1.6 billion from domestic shares in weekly foreign capital outflows.",
    )
    evt_fpi = _make_evt("e_fpi", art_fpi, comp="Foreign Portfolio Investors", figures=["$1.6 billion"])

    art_minor = _make_art(
        "a_minor",
        "ABC Garments reports Q1 net profit up 2% to Rs 6 crore",
        body="Quarterly results for the quarter ended June 30, 2026 with modest net profit growth.",
    )
    evt_minor = _make_evt("e_minor", art_minor, comp="ABC Garments", figures=["Rs 6 crore"])

    scored_fpi = scorer.score_event(evt_fpi, source_count=2, is_multi_source_verified=True)
    scored_minor = scorer.score_event(evt_minor, source_count=2, is_multi_source_verified=True)

    assert scored_fpi.investment_score > scored_minor.investment_score
    assert scored_fpi.investment_score >= 80.0
    assert scored_minor.score_breakdown.relevance_penalties > 0.0

    pool = ranker.rank_events([evt_minor, evt_fpi])
    assert pool.india_candidates[0].event.id == "e_fpi"
    assert pool.india_candidates[1].event.id == "e_minor"


# 3. Satellite-spectrum regulatory decision ranks above generic stock story
def test_3_satellite_spectrum_regulatory_decision_ranks_above_generic_stock_story():
    scorer = InvestmentRelevanceScorer()
    ranker = CandidatePoolRanker(scorer=scorer)

    art_dcc = _make_art(
        "a_dcc",
        "Digital Communications Commission clears administrative allocation of satellite broadband spectrum without auction",
        body="Telecom commission DCC clears regulatory policy guidelines on satellite spectrum allocation.",
    )
    evt_dcc = _make_evt("e_dcc", art_dcc, comp="Digital Communications Commission")

    art_stock = _make_art(
        "a_stock",
        "Stocks in focus: Top 5 stocks to watch today including Tata Motors and Reliance shares",
        body="Daily market wrap and list of stocks to watch in morning trade.",
    )
    evt_stock = _make_evt("e_stock", art_stock, comp="Tata Motors")

    scored_dcc = scorer.score_event(evt_dcc, source_count=2, is_multi_source_verified=True)
    scored_stock = scorer.score_event(evt_stock, source_count=2, is_multi_source_verified=True)

    assert scored_dcc.investment_score > scored_stock.investment_score
    assert scored_dcc.investment_score >= 80.0
    assert scored_stock.score_breakdown.relevance_penalties > 0.0

    pool = ranker.rank_events([evt_stock, evt_dcc])
    assert pool.india_candidates[0].event.id == "e_dcc"
    assert pool.india_candidates[1].event.id == "e_stock"


# 4. Large capex story ranks above minor company announcement
def test_4_large_capex_story_ranks_above_minor_company_announcement():
    scorer = InvestmentRelevanceScorer()
    ranker = CandidatePoolRanker(scorer=scorer)

    art_capex = _make_art(
        "a_capex",
        "Tata Steel announces ₹12,000 crore capex for green hydrogen and blast furnace capacity expansion",
        body="Tata Steel commits capital expenditure of ₹12,000 crore to build green steel capacity.",
    )
    evt_capex = _make_evt("e_capex", art_capex, comp="Tata Steel", figures=["₹12,000 crore"])

    art_minor = _make_art(
        "a_notice",
        "ABC Corp submits certificate under Regulation 74(5) of SEBI DP Regulations",
        body="Company compliance certificate submitted to stock exchange for quarterly secretarial compliance.",
    )
    evt_minor = _make_evt("e_notice", art_minor, comp="ABC Corp")

    scored_capex = scorer.score_event(evt_capex, source_count=2, is_multi_source_verified=True)
    scored_minor = scorer.score_event(evt_minor, source_count=2, is_multi_source_verified=True)

    assert scored_capex.investment_score > scored_minor.investment_score
    assert scored_capex.investment_score >= 80.0
    assert scored_minor.score_breakdown.relevance_penalties > 0.0

    pool = ranker.rank_events([evt_minor, evt_capex])
    assert pool.india_candidates[0].event.id == "e_capex"
    assert pool.india_candidates[1].event.id == "e_notice"


# 5. Trusted source still matters as tie-breaker
def test_5_trusted_source_matters_as_tie_breaker():
    scorer = InvestmentRelevanceScorer()
    ranker = CandidatePoolRanker(scorer=scorer)

    title = "Green Energy Corp commissions 500 MW solar plant with ₹2,500 crore capex"

    # Event A from Reuters (2 sources, multi-source verified, tier-1)
    art_a = _make_art("a_reuters", title, source="Reuters")
    evt_a = _make_evt("e_trusted", art_a, comp="Green Energy Corp", tier=VerificationTier.TWO_SOURCE_VERIFIED, figures=["₹2,500 crore"])

    # Event B from an obscure single source (1 source, uncorroborated)
    art_b = _make_art("a_unknown", title, source="Local Blog Wire")
    evt_b = _make_evt("e_untrusted", art_b, comp="Green Energy Corp", tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, figures=["₹2,500 crore"])

    scored_a = scorer.score_event(evt_a, source_count=2, is_multi_source_verified=True)
    scored_b = scorer.score_event(evt_b, source_count=1, is_multi_source_verified=False)

    # Trusted source gets higher source quality score
    assert scored_a.score_breakdown.source_quality > scored_b.score_breakdown.source_quality
    assert scored_a.investment_score >= scored_b.investment_score

    # Ranker orders trusted source ahead of uncorroborated source
    pool = ranker.rank_events([evt_b, evt_a])
    assert pool.india_candidates[0].event.id == "e_trusted"
    assert pool.india_candidates[1].event.id == "e_untrusted"


# 6. Freshness still matters as tie-breaker
def test_6_freshness_matters_as_tie_breaker():
    scorer = InvestmentRelevanceScorer()

    title = "Adani Power commissions 800 MW ultra-supercritical thermal unit in Jharkhand"
    art = _make_art("a_pow", title)
    evt = _make_evt("e_pow", art, comp="Adani Power", figures=["800 MW"])

    # Fresh article (published 2 hours ago: freshness_score = 1.0)
    scored_fresh = scorer.score_event(evt, source_count=2, is_multi_source_verified=True, freshness_score=1.0)

    # Older article (published 22 hours ago: freshness_score = 0.8)
    scored_older = scorer.score_event(evt, source_count=2, is_multi_source_verified=True, freshness_score=0.8)

    # Freshness provides calibrated tie-breaking advantage
    assert scored_fresh.investment_score > scored_older.investment_score
    assert scored_fresh.investment_score - scored_older.investment_score <= 5.0


# 7. Materiality threshold unchanged
def test_7_materiality_threshold_unchanged():
    assert INVESTMENT_MATERIALITY_THRESHOLD == 60.0
    assert SINGLE_SOURCE_MIN_CONFIDENCE == 80.0
    assert DOMESTIC_EVALUATOR_MIN_SCORE == 60.0


# 8. Section counts unchanged
def test_8_section_counts_unchanged():
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
    # Each entry provides enough national-significance text and action verbs for the evaluator.
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
            "inaugurated the project. The national highway will reduce travel time between Delhi and "
            "Mumbai significantly. NHAI will oversee the work.",
        "Finance Ministry issues revised guidelines for government procurement":
            "The Finance Ministry issued revised guidelines for government procurement that will apply to "
            "all central government ministries and departments. The guidelines, announced by the Union Cabinet, "
            "aim to boost Make-in-India and promote domestic manufacturing. Parliament will be informed "
            "of the new policy in the budget session.",
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
        eid = f"e_intl_{i}"
        art = Article(
            id=f"a_intl_{i}", url=url, title=headline, source_name=pub, category=NewsCategory.INTERNATIONAL,
            content_text=f"Reported international corporate business text for {headline}.", published_at=t_now,
            is_verified_url=True, date_verified=True, is_valid_date=True,
        )
        evt = _make_evt(eid, art, comp, figures=["2 billion dollar"])
        ctx.articles_lookup[art.id] = art
        accepted_stories.append({
            "event_id": eid, "headline": headline, "company_name": comp,
            "all_companies": [comp], "category": "international",
        })
        event_by_id[eid] = evt

    candidate_pool, domestic_pool, india_pool, intl_pool, sufficient, pipeline_status = run_ranking_and_selection(ctx, accepted_stories, event_by_id)
    assert len(candidate_pool.domestic_candidates) == 5
    assert len(candidate_pool.india_candidates) == 5
    assert len(candidate_pool.international_candidates) == 5
    assert sufficient is True
