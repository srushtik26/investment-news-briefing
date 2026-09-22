"""
Regression tests for production/local-run failures discovered on 2026-09-22:
1. NSE IPO classification (routes to INDIA despite mentioning Nasdaq/Wall St)
2. India count preservation (5 post-dedup, 2 fail Stage 7 -> refilled to 5)
3. Portfolio materiality cache (retains score >= 60, not reset to 0)
4. Portfolio discovery path (12 discovered, 2 India-routed, both material -> materiality_pass=2)
5. Foreign leakage (foreign company + foreign event -> INTERNATIONAL)
6. India final region audit (India subject mistakenly in International -> moved/corrected)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.classification.region_classifier import (
    EventRegionClassifier,
    verify_india_business_nexus,
)
from app.ranking.watchlist import get_portfolio_company_role
from app.pipeline.story_context import StoryContext, build_story_context
from app.verification.materiality import evaluate_investment_materiality
from app.ranking.sorter import CandidatePoolRanker
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.pipeline.selection import run_ranking_and_selection
from app.pipeline.context import PipelineContext


_now = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)


def _art(aid, title, body="", cat=NewsCategory.INDIA, source="Business Standard", comp=""):
    if not body:
        if cat == NewsCategory.INTERNATIONAL:
            body = f"Article body about {title}. International policy action involving global markets."
        elif cat == NewsCategory.DOMESTIC:
            body = f"Article body about {title}. Central public governance issue in New Delhi."
        else:
            body = f"Article body about {title}. Includes ₹1,500 crore corporate investment by {comp or 'conglomerate'}."
    return Article(
        id=aid,
        url=f"https://example.com/{aid}",
        title=title,
        source_name=source,
        category=cat,
        content_text=body,
        published_at=_now,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _evt(eid, art, comp="Tata Motors", cat=NewsCategory.INDIA, tier=VerificationTier.TWO_SOURCE_VERIFIED, conf=90.0, figures=None):
    if figures is None:
        figures = ["₹1,500 crore"] if cat == NewsCategory.INDIA else []
    return Event(
        id=eid,
        canonical_title=art.title,
        description=art.content_text,
        article_ids=[art.id],
        companies_involved=[comp],
        event_category=cat,
        verification_tier=tier,
        verification_confidence=conf,
        single_source_confidence_score=conf,
        published_date=art.published_at.date(),
        financial_figures=figures,
    )


# ---------------------------------------------------------------------------
# Test 1: NSE IPO classification
# ---------------------------------------------------------------------------
def test_nse_ipo_classification_routes_to_india():
    classifier = EventRegionClassifier()
    headline = "Investors rush into India’s National Stock Exchange IPO at valuation multiple above Nasdaq"
    body = "India's premier bourse National Stock Exchange (NSE) has filed for an initial public offering with SEBI."

    cat, reason = classifier.classify_with_reason(f"{headline} {body}")
    assert cat == NewsCategory.INDIA, f"Expected INDIA, got {cat} (reason: {reason})"

    # Also test BSE and Indian capital markets infrastructure
    cat2, _ = classifier.classify_with_reason("BSE sets record derivative trading volume as retail investors participate")
    assert cat2 == NewsCategory.INDIA

    # Foreign Nasdaq news remains INTERNATIONAL
    cat3, _ = classifier.classify_with_reason("Nasdaq composite drops 1.5% as tech megacaps sell off on Wall Street")
    assert cat3 == NewsCategory.INTERNATIONAL


# ---------------------------------------------------------------------------
# Test 2: India count preservation (5 post-dedup, 2 fail Stage 7 -> refilled to 5)
# ---------------------------------------------------------------------------
def test_india_count_preservation_with_reserves():
    ctx = PipelineContext(run_reference_time=_now)
    ctx.ranker = CandidatePoolRanker()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0)

    # 5 initial post-dedup India candidates
    # 3 will pass Stage 7, 2 will fail (e.g. noise or missing nexus)
    art_pass1 = _art("a1", "Reliance Industries acquires German battery maker for €500M", "Reliance acquires firm.", comp="Reliance Industries")
    art_pass2 = _art("a2", "Tata Motors signs ₹2,000 crore EV manufacturing pact in Tamil Nadu", "Tata Motors plant.", comp="Tata Motors")
    art_pass3 = _art("a3", "Infosys signs $1 billion cloud transformation deal with European bank", "Infosys contract.", comp="Infosys")

    # Failing stories: e.g. purely foreign or routine politics
    art_fail1 = _art("a4", "Bad weather forces flight delays at Delhi airport", "Weather delay.", comp="Delhi Airport")
    art_fail2 = _art("a5", "Stocks to watch today: Top buzzing stock picks for Tuesday trading", "Listicle.", comp="Market")

    # 2 valid reserves available in reserve pool
    art_res1 = _art("ar1", "L&T wins ₹3,500 crore order for high-speed rail project", "L&T order.", comp="L&T")
    art_res2 = _art("ar2", "Mahindra & Mahindra invests ₹1,200 crore to scale tractor capacity", "M&M capex.", comp="M&M")

    ev_pass1 = _evt("e1", art_pass1, comp="Reliance Industries")
    ev_pass2 = _evt("e2", art_pass2, comp="Tata Motors")
    ev_pass3 = _evt("e3", art_pass3, comp="Infosys")
    ev_fail1 = _evt("e4", art_fail1, comp="Delhi Airport")
    ev_fail2 = _evt("e5", art_fail2, comp="Market")
    ev_res1 = _evt("er1", art_res1, comp="L&T")
    ev_res2 = _evt("er2", art_res2, comp="M&M")

    # Add Domestic (5) and International (5) to satisfy other sections
    dom_evs = []
    for i in range(5):
        a = _art(f"d{i}", f"Supreme Court ruling on inter-state water dispute {i}", cat=NewsCategory.DOMESTIC)
        e = _evt(f"de{i}", a, cat=NewsCategory.DOMESTIC)
        dom_evs.append(e)

    intl_evs = []
    for i in range(5):
        a = _art(f"intl{i}", f"European Central Bank cuts interest rates by 25 bps {i}", cat=NewsCategory.INTERNATIONAL)
        e = _evt(f"ie{i}", a, cat=NewsCategory.INTERNATIONAL)
        intl_evs.append(e)

    for a in [art_pass1, art_pass2, art_pass3, art_fail1, art_fail2, art_res1, art_res2] + [e.canonical_title for e in dom_evs] + [e.canonical_title for e in intl_evs]:
        pass

    all_arts = [art_pass1, art_pass2, art_pass3, art_fail1, art_fail2, art_res1, art_res2]
    for e in dom_evs + intl_evs:
        all_arts.append(_art(e.article_ids[0], e.canonical_title, cat=e.event_category))
    ctx.articles_lookup = {a.id: a for a in all_arts}

    accepted_stories = (
        [{"event_id": f"de{i}", "headline": dom_evs[i].canonical_title, "category": "domestic"} for i in range(5)]
        + [{"event_id": "e1", "headline": ev_pass1.canonical_title, "category": "india"},
           {"event_id": "e2", "headline": ev_pass2.canonical_title, "category": "india"},
           {"event_id": "e3", "headline": ev_pass3.canonical_title, "category": "india"},
           {"event_id": "e4", "headline": ev_fail1.canonical_title, "category": "india"},
           {"event_id": "e5", "headline": ev_fail2.canonical_title, "category": "india"}]
        + [{"event_id": f"ie{i}", "headline": intl_evs[i].canonical_title, "category": "international"} for i in range(5)]
    )

    event_by_id = {e.id: e for e in dom_evs + [ev_pass1, ev_pass2, ev_pass3, ev_fail1, ev_fail2, ev_res1, ev_res2] + intl_evs}

    ctx.india_reserve_pool = [ev_res1, ev_res2]
    ctx.verified_events = list(event_by_id.values())

    cand_pool, dom_pool, india_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx, accepted_stories, event_by_id
    )

    assert len(india_pool) == 5, f"Expected 5 India candidates after recovery, got {len(india_pool)}"
    assert sufficient is True


# ---------------------------------------------------------------------------
# Test 3: Portfolio materiality cache
# ---------------------------------------------------------------------------
def test_portfolio_materiality_cache_retains_score():
    art = _art("a_pf", "Infosys wins $1.5 billion cloud enterprise modernization deal", "Large deal.")
    ev = _evt("e_pf", art, comp="Infosys")

    sc = build_story_context(ev, art)
    assert sc.materiality_score >= 60.0, f"Expected >= 60.0, got {sc.materiality_score}"
    assert ev.metadata.get("investment_materiality_score") == sc.materiality_score

    # Re-reading should not reset to 0
    sc2 = build_story_context(ev, art)
    assert sc2.materiality_score == sc.materiality_score
    assert ev.metadata.get("investment_materiality_score") >= 60.0


# ---------------------------------------------------------------------------
# Test 4: Portfolio discovery path (materiality_pass matches material count)
# ---------------------------------------------------------------------------
def test_portfolio_funnel_materiality_pass():
    ctx = PipelineContext(run_reference_time=_now)
    ctx.ranker = CandidatePoolRanker()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0)

    # 2 material portfolio events
    art_pf1 = _art("apf1", "Infosys signs $2 billion AI computing partnership with global bank", comp="Infosys")
    art_pf2 = _art("apf2", "State Bank of India raises ₹10,000 crore via tier-2 infrastructure bonds", comp="State Bank of India")

    ev_pf1 = _evt("epf1", art_pf1, comp="Infosys")
    ev_pf2 = _evt("epf2", art_pf2, comp="State Bank of India")

    # 3 other India events
    art_in3 = _art("ain3", "Tata Motors inaugurates ₹5,000 crore commercial vehicle facility", comp="Tata Motors")
    art_in4 = _art("ain4", "L&T wins ₹4,200 crore offshore energy contract", comp="L&T")
    art_in5 = _art("ain5", "Adani Ports commissions new container terminal", comp="Adani Ports")

    ev_in3 = _evt("ein3", art_in3, comp="Tata Motors")
    ev_in4 = _evt("ein4", art_in4, comp="L&T")
    ev_in5 = _evt("ein5", art_in5, comp="Adani Ports")

    dom_evs = [_evt(f"d_{i}", _art(f"da_{i}", f"Cabinet approves national highway expansion phase {i}", cat=NewsCategory.DOMESTIC), cat=NewsCategory.DOMESTIC) for i in range(5)]
    intl_evs = [_evt(f"int_{i}", _art(f"inta_{i}", f"Federal Reserve holds benchmark rate steady at 5.25% {i}", cat=NewsCategory.INTERNATIONAL), cat=NewsCategory.INTERNATIONAL) for i in range(5)]

    all_evs = [ev_pf1, ev_pf2, ev_in3, ev_in4, ev_in5] + dom_evs + intl_evs
    all_arts = [art_pf1, art_pf2, art_in3, art_in4, art_in5] + [_art(e.article_ids[0], e.canonical_title, cat=e.event_category) for e in dom_evs + intl_evs]
    ctx.articles_lookup = {a.id: a for a in all_arts}

    accepted_stories = (
        [{"event_id": e.id, "headline": e.canonical_title, "category": "domestic"} for e in dom_evs]
        + [{"event_id": e.id, "headline": e.canonical_title, "category": "india"} for e in [ev_pf1, ev_pf2, ev_in3, ev_in4, ev_in5]]
        + [{"event_id": e.id, "headline": e.canonical_title, "category": "international"} for e in intl_evs]
    )

    logs = []
    ctx.log_exec = lambda m: logs.append(m)
    event_by_id = {e.id: e for e in all_evs}
    ctx.verified_events = all_evs

    cand_pool, dom_pool, india_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx, accepted_stories, event_by_id
    )

    # Check that execution log contains PORTFOLIO_FUNNEL with materiality_pass >= 2
    full_log_str = "\n".join(logs)
    assert "materiality_pass=" in full_log_str
    # Extract materiality_pass
    val = None
    for line in logs:
        if "materiality_pass=" in line:
            val = int(line.split("materiality_pass=")[1].split()[0])
            break
    assert val is not None and val >= 2, f"Expected materiality_pass >= 2, got {val}"


# ---------------------------------------------------------------------------
# Test 5: Foreign leakage rejection
# ---------------------------------------------------------------------------
def test_foreign_leakage_routes_to_international():
    classifier = EventRegionClassifier()
    headline = "Toyota and Panasonic expand electric vehicle battery joint venture in Japan"
    body = "Toyota Motor Corporation and Panasonic Holdings will invest ¥100 billion to expand lithium-ion production."

    cat, reason = classifier.classify_with_reason(f"{headline} {body}")
    assert cat == NewsCategory.INTERNATIONAL, f"Expected INTERNATIONAL, got {cat}"

    # Also verify nexus check rejects it for India
    art = _art("a_for", headline, body, cat=NewsCategory.INTERNATIONAL)
    ev = _evt("e_for", art, comp="Toyota", cat=NewsCategory.INTERNATIONAL)
    is_nexus, nex_reason = verify_india_business_nexus(ev, art)
    assert is_nexus is False, f"Expected is_nexus=False for foreign company event, got {is_nexus}"


# ---------------------------------------------------------------------------
# Test 6: India final region audit corrects misclassified International story
# ---------------------------------------------------------------------------
def test_india_final_region_audit_relocates_story():
    ctx = PipelineContext(run_reference_time=_now)
    ctx.ranker = CandidatePoolRanker()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0)

    # NSE IPO story mistakenly categorized as INTERNATIONAL in candidate pool
    art_nse = _art(
        "art_nse",
        "Investors rush into India’s National Stock Exchange IPO at valuation multiple above Nasdaq",
        body="The National Stock Exchange has filed for an IPO valuing the exchange at over ₹1.5 lakh crore ($18 billion) with global investor bids flooding in.",
        cat=NewsCategory.INTERNATIONAL,
    )
    ev_nse = _evt("ev_nse", art_nse, comp="National Stock Exchange", cat=NewsCategory.INTERNATIONAL, figures=["₹1.5 lakh crore", "$18 billion"])

    # 4 India stories
    in_evs = [
        _evt(f"in_{i}", _art(f"art_in_{i}", f"Indian conglomerate wins ₹{i+1},000 crore contract {i}"), comp=f"Comp{i}")
        for i in range(4)
    ]

    # 5 International stories (including NSE IPO) + 1 reserve
    intl_evs = [ev_nse] + [
        _evt(f"intl_{i}", _art(f"art_intl_{i}", f"US Federal Reserve cuts benchmark interest rate by 25 bps following FOMC meeting {i}", body=f"The Federal Reserve lowered borrowing costs by a quarter percentage point to 4.75% across global capital markets {i}.", cat=NewsCategory.INTERNATIONAL), comp="Federal Reserve", cat=NewsCategory.INTERNATIONAL)
        for i in range(5)
    ]

    # 5 Domestic stories
    dom_evs = [
        _evt(f"dom_{i}", _art(f"art_dom_{i}", f"Union Cabinet approves ₹25,000 crore national infrastructure outlay Phase {i}", cat=NewsCategory.DOMESTIC), comp="Union Cabinet", cat=NewsCategory.DOMESTIC)
        for i in range(5)
    ]

    all_evs = in_evs + intl_evs + dom_evs
    all_arts = [_art(e.article_ids[0], e.canonical_title, cat=e.event_category) for e in all_evs]
    ctx.articles_lookup = {a.id: a for a in all_arts}

    accepted_stories = (
        [{"event_id": e.id, "headline": e.canonical_title, "category": "domestic"} for e in dom_evs]
        + [{"event_id": e.id, "headline": e.canonical_title, "category": "india"} for e in in_evs]
        + [{"event_id": e.id, "headline": e.canonical_title, "category": "international"} for e in intl_evs]
    )
    event_by_id = {e.id: e for e in all_evs}
    ctx.verified_events = all_evs

    cand_pool, dom_pool, india_pool, intl_pool, sufficient, status = run_ranking_and_selection(
        ctx, accepted_stories, event_by_id
    )

    # ev_nse should have been moved from International to India
    india_ids = [s.event.id for s in india_pool]
    intl_ids = [s.event.id for s in intl_pool]

    assert "ev_nse" in india_ids, f"NSE IPO was not relocated to India pool: {india_ids}"
    assert "ev_nse" not in intl_ids, f"NSE IPO remained in International pool: {intl_ids}"
    assert len(india_pool) == 5
    assert len(intl_pool) == 5
