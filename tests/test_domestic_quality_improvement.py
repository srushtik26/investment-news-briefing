"""
Tests for Domestic News Selection Quality Improvements.

Covers:
1. Routine Supreme Court bail story is rejected/de-prioritized.
2. Routine High Court contempt story is rejected/de-prioritized.
3. "Court asks Centre to respond to plea" does not outrank major politics.
4. Landmark Supreme Court GST/tax ruling can qualify.
5. Major election result qualifies.
6. Cabinet economic policy qualifies.
7. India inflation/GDP shock qualifies.
8. Major nationwide strike/economic disruption qualifies.
9. Political reaction-only story is rejected.
10. Company quarterly earnings does NOT get routed into Domestic economy.
11. Final Domestic selection prefers politics/economy over routine courts.
12. Domestic final count remains exactly 5 when enough valid stories exist.
13. No more than one eligible judiciary story normally selected when enough stronger alternatives exist.
"""

from datetime import datetime, timezone
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import ScoredEvent, ScoreBreakdown
from app.ranking.sorter import select_diverse_domestic_candidates
from app.verification.domestic_trending import (
    DomesticTrendingEvaluator,
    DomesticTopic,
    classify_domestic_topic,
)


def _make_dummy_article(title: str, url: str, content: str = "") -> Article:
    return Article(
        id=f"art_{abs(hash(title))}",
        title=title,
        url=url,
        source_name="The Hindu",
        category=NewsCategory.DOMESTIC,
        content_text=content or f"Comprehensive national coverage regarding {title}.",
        published_at=datetime.now(timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_dummy_event(title: str, art: Article, tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE) -> Event:
    return Event(
        id=f"ev_{abs(hash(title))}",
        canonical_title=title,
        description=art.content_text,
        article_ids=[art.id],
        event_category=NewsCategory.DOMESTIC,
        verification_tier=tier,
        verification_confidence=90.0,
    )


def _make_dummy_scored_event(event: Event, score: float, rank: int = 1) -> ScoredEvent:
    breakdown = ScoreBreakdown(
        financial_magnitude=score,
        market_impact=score,
        investor_relevance=score,
        corporate_significance=score,
        source_quality=score,
        total_score=score,
    )
    return ScoredEvent(event=event, investment_score=score, score_breakdown=breakdown, rank=rank)


# 1. Routine Supreme Court bail story is rejected/de-prioritized
def test_routine_sc_bail_story_is_rejected():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Supreme Court hears bail plea of former state minister in corruption case",
        "https://thehindu.com/sc-bail-plea",
        "The Supreme Court bench on Monday took up the bail application of the former minister and adjourned hearing.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert not is_elig
    assert "REJECT_ROUTINE_COURT" in reason


# 2. Routine High Court contempt story is rejected/de-prioritized
def test_routine_hc_contempt_story_is_rejected():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "High Court issues contempt notice to retired DGP over SI reinstatement",
        "https://thehindu.com/hc-contempt-notice",
        "The High Court bench issued a contempt notice to the retired police chief regarding compliance with a service order.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert not is_elig
    assert "REJECT_ROUTINE_COURT" in reason


# 3. "Court asks Centre to respond to plea" does not outrank major politics
def test_court_asks_centre_to_respond_is_rejected_or_de_prioritized():
    evaluator = DomesticTrendingEvaluator()
    art_court = _make_dummy_article(
        "Supreme Court asks Centre to respond to plea challenging local notification within two weeks",
        "https://thehindu.com/sc-asks-centre",
        "A bench headed by the Chief Justice asked the central government to file an affidavit in response to a petition.",
    )
    ev_court = _make_dummy_event(art_court.title, art_court)
    is_elig, score, reason = evaluator.evaluate(ev_court, art_court)
    assert not is_elig
    assert "REJECT_ROUTINE_COURT" in reason


# 4. Landmark Supreme Court GST/tax ruling can qualify
def test_landmark_sc_gst_tax_ruling_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Supreme Court delivers landmark ruling on GST input tax credit for online gaming industry",
        "https://thehindu.com/sc-gst-landmark",
        "A five-judge Constitution Bench of the Supreme Court delivered a landmark ruling upholding nationwide GST provisions.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0


# 5. Major election result qualifies
def test_major_election_result_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Election Commission declares assembly election results as NDA crosses majority mark with 135 seats",
        "https://thehindu.com/election-results-nda",
        "The Election Commission of India finalized the vote tally for state assembly elections showing a decisive majority.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0
    assert classify_domestic_topic(art.title, art.content_text) == DomesticTopic.POLITICS_ELECTIONS


# 6. Cabinet economic policy qualifies
def test_cabinet_economic_policy_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Union Cabinet clears ₹75,000 crore national semiconductor package and export incentive scheme",
        "https://thehindu.com/cabinet-semiconductor-policy",
        "The Union Cabinet chaired by the Prime Minister approved a ₹75,000 crore incentive framework for domestic manufacturing.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0


# 7. India inflation/GDP shock qualifies
def test_india_inflation_gdp_shock_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "India retail inflation surges to 6.8% as food prices spike across major states",
        "https://thehindu.com/india-retail-inflation",
        "Official data released by the National Statistical Office showed consumer price index inflation rising sharply above RBI targets.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0
    assert classify_domestic_topic(art.title, art.content_text) == DomesticTopic.ECONOMY_NATIONAL


# 8. Major nationwide strike/economic disruption qualifies
def test_nationwide_strike_economic_disruption_qualifies():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Nationwide truckers strike disrupts essential goods supply across north and west India",
        "https://thehindu.com/truckers-strike-disruption",
        "A nationwide strike called by transport operators halted interstate freight movement and triggered supply disruptions.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert is_elig
    assert score >= 60.0
    assert classify_domestic_topic(art.title, art.content_text) == DomesticTopic.ECONOMY_NATIONAL


# 9. Political reaction-only story is rejected
def test_political_reaction_only_story_is_rejected():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "BJP slams Congress over comments on economic policy during press briefing",
        "https://thehindu.com/bjp-slams-congress",
        "BJP leaders hit out at the opposition party calling their recent statement misleading and baseless.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert not is_elig
    assert "REJECT_NOISE" in reason


# 10. Company quarterly earnings does NOT get routed into Domestic economy
def test_company_quarterly_earnings_rejected_by_domestic_evaluator():
    evaluator = DomesticTrendingEvaluator()
    art = _make_dummy_article(
        "Infosys Q1 net profit rises 8% YoY to ₹6,368 crore with raised revenue guidance",
        "https://thehindu.com/infosys-q1-results",
        "IT services major Infosys reported an 8% increase in quarterly net profit and raised its annual forecast.",
    )
    ev = _make_dummy_event(art.title, art)
    is_elig, score, reason = evaluator.evaluate(ev, art)
    assert not is_elig
    assert "REJECT_CATEGORY" in reason


# 11. Final Domestic selection prefers politics/economy over routine courts
def test_final_domestic_selection_prefers_politics_and_economy_over_routine_courts():
    articles_map = {}

    art_pol = _make_dummy_article("Election Commission announces dates for upcoming state assembly elections", "https://thehindu.com/p1")
    art_econ = _make_dummy_article("India GDP growth accelerates to 7.8% in Q1 led by manufacturing and services", "https://thehindu.com/e1")
    art_cab = _make_dummy_article("Union Cabinet approves nationwide highway expansion corridor project", "https://thehindu.com/c1")
    art_dis = _make_dummy_article("IMD issues red alert as severe cyclone makes landfall along coastal belt", "https://thehindu.com/d1")
    art_isro = _make_dummy_article("ISRO launches navigation satellite into designated geostationary orbit", "https://thehindu.com/s1")
    art_court = _make_dummy_article("High Court hearing continues on dispute regarding administrative procedure", "https://thehindu.com/ct1")

    for a in [art_pol, art_econ, art_cab, art_dis, art_isro, art_court]:
        articles_map[a.id] = a

    # Court story given a higher initial raw score (88) vs politics (82)
    ev_pol = _make_dummy_event(art_pol.title, art_pol)
    ev_econ = _make_dummy_event(art_econ.title, art_econ)
    ev_cab = _make_dummy_event(art_cab.title, art_cab)
    ev_dis = _make_dummy_event(art_dis.title, art_dis)
    ev_isro = _make_dummy_event(art_isro.title, art_isro)
    ev_court = _make_dummy_event(art_court.title, art_court)

    candidates = [
        _make_dummy_scored_event(ev_court, 88.0, 1),
        _make_dummy_scored_event(ev_pol, 82.0, 2),
        _make_dummy_scored_event(ev_econ, 82.0, 3),
        _make_dummy_scored_event(ev_cab, 82.0, 4),
        _make_dummy_scored_event(ev_dis, 80.0, 5),
        _make_dummy_scored_event(ev_isro, 75.0, 6),
    ]

    selected = select_diverse_domestic_candidates(
        domestic_candidates=candidates,
        articles_lookup=articles_map,
        target_count=5,
    )

    selected_titles = [s.event.canonical_title for s in selected]
    # Politics and economy must be selected
    assert art_pol.title in selected_titles
    assert art_econ.title in selected_titles
    assert art_cab.title in selected_titles
    # The routine court story should be ranked below the strong national stories
    assert selected[0].event.canonical_title != art_court.title


# 12. Domestic final count remains exactly 5 when enough valid stories exist
def test_domestic_final_count_remains_exactly_5():
    articles_map = {}
    candidates = []
    topics_list = [
        ("India inflation eases to 3.6% in July", "https://thehindu.com/ec1"),
        ("Union Cabinet clears new education infrastructure grant", "https://thehindu.com/gov1"),
        ("Election Commission issues model code of conduct", "https://thehindu.com/pol1"),
        ("Indian Navy commissions new guided missile destroyer", "https://thehindu.com/def1"),
        ("ISRO deploys next-generation weather radar satellite", "https://thehindu.com/sci1"),
        ("Disaster relief teams deployed following cloudburst in hills", "https://thehindu.com/dis1"),
    ]
    for idx, (title, url) in enumerate(topics_list, 1):
        art = _make_dummy_article(title, url)
        articles_map[art.id] = art
        ev = _make_dummy_event(title, art)
        candidates.append(_make_dummy_scored_event(ev, 80.0 - idx, idx))

    selected = select_diverse_domestic_candidates(
        domestic_candidates=candidates,
        articles_lookup=articles_map,
        target_count=5,
    )
    assert len(selected) == 5


# 13. No more than one eligible judiciary story normally selected when enough alternatives exist
def test_at_most_one_judiciary_story_selected_when_alternatives_exist():
    articles_map = {}
    candidates = []

    # 3 Landmark court stories
    court_stories = [
        ("Supreme Court Constitution Bench delivers landmark ruling on electoral bonds", "https://thehindu.com/sc-bonds"),
        ("Supreme Court landmark ruling upholds national reservation policy guidelines", "https://thehindu.com/sc-res"),
        ("Supreme Court landmark judgment resolves inter-state water dispute", "https://thehindu.com/sc-water"),
    ]
    for idx, (title, url) in enumerate(court_stories, 1):
        art = _make_dummy_article(title, url)
        articles_map[art.id] = art
        ev = _make_dummy_event(title, art)
        candidates.append(_make_dummy_scored_event(ev, 85.0 - idx, idx))

    # 5 Non-court national stories
    non_court_stories = [
        ("India inflation drops to multi-month low on food price stabilization", "https://thehindu.com/inf1"),
        ("Union Cabinet clears ₹50,000 crore national infrastructure connectivity plan", "https://thehindu.com/cab1"),
        ("Election Commission notifies polling dates for five state assemblies", "https://thehindu.com/ec1"),
        ("Indian Army inducts indigenous combat drone squadron along border", "https://thehindu.com/army1"),
        ("ISRO completes key flight qualification test for human spaceflight", "https://thehindu.com/isro1"),
    ]
    for idx, (title, url) in enumerate(non_court_stories, 4):
        art = _make_dummy_article(title, url)
        articles_map[art.id] = art
        ev = _make_dummy_event(title, art)
        candidates.append(_make_dummy_scored_event(ev, 82.0 - idx, idx))

    selected = select_diverse_domestic_candidates(
        domestic_candidates=candidates,
        articles_lookup=articles_map,
        target_count=5,
        max_court_stories=1,
    )

    assert len(selected) == 5
    court_selected = [s for s in selected if classify_domestic_topic(s.event.canonical_title, "") == DomesticTopic.COURT_JUDICIARY]
    assert len(court_selected) == 1
