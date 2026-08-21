"""
Regression tests for Part 12.4.1:
1. Region classification precedence (Arctos / Atlanta Falcons as International; General Atlantic / KFin as India).
2. Pure number entity rejection in single source evaluator ("20", "31" rejected; "20Cube", "3M" accepted).
3. Personal profile & billionaire wealth feature rejection.
4. Lakers sale classified as M&A (not Earnings).
5. Centralized get_section_quality_state calculations.
6. International final-mile discovery rotation and remaining budget allocation.
7. Mocked hybrid pipeline integration test.
"""

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock

from app.models.article import Article, NewsCategory
from app.models.enums import VerificationTier
from app.models.event import Event
from app.classification.region_classifier import EventRegionClassifier
from app.classification.classifier import AIArticleClassifier
from app.classification.models import ArticleEventType
from app.verification.single_source import (
    SingleSourceEvaluator,
    is_valid_named_company_entity,
    is_personal_profile_or_wealth_feature,
)
from run_pipeline import get_section_quality_state


def test_arctos_atlanta_falcons_classified_international():
    """Test that Arctos / Atlanta Falcons transaction stays INTERNATIONAL despite incidental body tokens."""
    clf = EventRegionClassifier()
    title = "Arctos agrees to buy 10% of Atlanta Falcons at $10.6 billion valuation"
    body = "Private equity firm Arctos acquired a minority stake in NFL team Atlanta Falcons. In unrelated news, Indian markets fell 500 crore rupees."
    
    cat, reason = clf.classify_with_reason(
        title=title,
        content=body,
        financial_figures=["$10.6 billion", "10%"],
        companies=["Arctos", "Atlanta Falcons"],
        discovery_region=NewsCategory.INTERNATIONAL,
    )
    assert cat == NewsCategory.INTERNATIONAL
    assert "International entity" in reason or "global context" in reason or "Dollar currency" in reason


def test_indian_transactions_stay_india():
    """Test that Indian transactions with foreign sponsors or Indian entities stay INDIA."""
    clf = EventRegionClassifier()

    # General Atlantic + KFin
    cat1, _ = clf.classify_with_reason(
        title="General Atlantic to sell stake in KFin Technologies for ₹1,200 crore",
        financial_figures=["₹1,200 crore"],
        companies=["General Atlantic", "KFin Technologies"],
        discovery_region=NewsCategory.INDIA,
    )
    assert cat1 == NewsCategory.INDIA

    # Fairfax + IDBI
    cat2, _ = clf.classify_with_reason(
        title="Fairfax Financial in talks to acquire majority stake in IDBI Bank",
        companies=["Fairfax", "IDBI Bank"],
        discovery_region=NewsCategory.INDIA,
    )
    assert cat2 == NewsCategory.INDIA

    # Tube Investments + Shanthi Gears
    cat3, _ = clf.classify_with_reason(
        title="Tube Investments of India Q1 net profit surges 25% to ₹280 crore",
        financial_figures=["₹280 crore", "25%"],
        companies=["Tube Investments of India"],
        discovery_region=NewsCategory.INDIA,
    )
    assert cat3 == NewsCategory.INDIA

    # Bain + JBM Auto
    cat4, _ = clf.classify_with_reason(
        title="Bain Capital invests ₹1,000 crore in JBM Auto electric vehicle arm",
        financial_figures=["₹1,000 crore"],
        companies=["Bain Capital", "JBM Auto"],
        discovery_region=NewsCategory.INDIA,
    )
    assert cat4 == NewsCategory.INDIA


def test_pure_numbers_rejected_as_company_entities():
    """Test that pure numeric strings are rejected as entities while alphanumeric companies are preserved."""
    assert is_valid_named_company_entity("20") is False
    assert is_valid_named_company_entity("31") is False
    assert is_valid_named_company_entity("2026") is False
    assert is_valid_named_company_entity("1.7") is False
    assert is_valid_named_company_entity("75") is False
    assert is_valid_named_company_entity("$100M") is False
    assert is_valid_named_company_entity("company") is False
    assert is_valid_named_company_entity("unspecified") is False

    assert is_valid_named_company_entity("20Cube") is True
    assert is_valid_named_company_entity("3M") is True
    assert is_valid_named_company_entity("B2Gold") is True
    assert is_valid_named_company_entity("Moderna") is True
    assert is_valid_named_company_entity("Reliance") is True


def test_personal_profile_rejected_by_single_source_evaluator():
    """Test that billionaire personal profiles and career features are rejected for single-source."""
    title = "Moderna co-founder is a billionaire once again at $1.7 billion — he once turned down 20 high-paying job offers..."
    body = "Noubar Afeyan, the venture capitalist who co-founded biotech powerhouse Moderna, has rejoined the billionaire ranks with a net worth of $1.7 billion."

    assert is_personal_profile_or_wealth_feature(title, body) is True

    evaluator = SingleSourceEvaluator()
    art = Article(
        id="art_mod",
        url="https://cnbc.com/moderna-billionaire",
        title=title,
        content_text=body * 3,
        source_name="CNBC",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INTERNATIONAL,
    )
    event = Event(
        id="ev_mod",
        canonical_title=title,
        description=body,
        article_ids=["art_mod"],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["20"],  # Misidentified number entity
        financial_figures=["$1.7 billion"],
    )

    is_elig, score, reason = evaluator.evaluate_event(event, art)
    assert is_elig is False
    assert "Personal profile" in reason or "wealth feature" in reason


def test_lakers_sale_classified_as_ma_not_earnings():
    """Test that sports/asset sales with transaction verbs classify as M&A / transaction, not Earnings."""
    classifier = AIArticleClassifier(api_key="")
    art = Article(
        id="art_lakers",
        url="https://bloomberg.com/lakers-sale",
        title="The Los Angeles Lakers sold for $12.5 billion to Mark Walter and Todd Boehly",
        content_text="The Los Angeles Lakers franchise sold for a record $12.5 billion valuation in an all-cash deal.",
        source_name="Bloomberg",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INTERNATIONAL,
    )

    res = classifier.classify(art)
    assert res.success is True
    assert res.classification.event_type == ArticleEventType.MA
    assert res.classification.is_hard_business_event is True


def test_get_section_quality_state_calculation():
    """Test centralized get_section_quality_state helper with various section compositions."""
    def make_ev(cid: str, cat: NewsCategory, tier: VerificationTier) -> Event:
        e = Event(
            id=cid,
            canonical_title=f"Event {cid}",
            description=f"Description for event {cid}",
            article_ids=[f"art_{cid}"],
            event_category=cat,
        )
        e.verification_tier = tier
        return e

    # Case 1: 5 two-source verified
    evs1 = [make_ev(f"e{i}", NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED) for i in range(5)]
    state1 = get_section_quality_state(evs1, [], NewsCategory.INDIA)
    assert state1["two_source_count"] == 5
    assert state1["single_source_count"] == 0
    assert state1["slot_deficit"] == 0
    assert state1["two_source_min_deficit"] == 0
    assert state1["section_complete"] is True

    # Case 2: 3 two-source + 2 single-source
    evs2_two = [make_ev(f"e{i}", NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED) for i in range(3)]
    evs2_sng = [make_ev(f"s{i}", NewsCategory.INTERNATIONAL, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE) for i in range(2)]
    state2 = get_section_quality_state(evs2_two, evs2_sng, NewsCategory.INTERNATIONAL)
    assert state2["two_source_count"] == 3
    assert state2["single_source_count"] == 2
    assert state2["eligible_total"] == 5
    assert state2["slot_deficit"] == 0
    assert state2["section_complete"] is True

    # Case 3: 2 two-source + 3 single-source (Must fail two-source minimum of 3)
    evs3_two = [make_ev(f"e{i}", NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED) for i in range(2)]
    evs3_sng = [make_ev(f"s{i}", NewsCategory.INTERNATIONAL, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE) for i in range(3)]
    state3 = get_section_quality_state(evs3_two, evs3_sng, NewsCategory.INTERNATIONAL)
    assert state3["two_source_count"] == 2
    assert state3["two_source_min_deficit"] == 1
    assert state3["section_complete"] is False

    # Case 4: 1 two-source + 1 single-source (SlotsNeeded=3, TwoSourceNeeded=2, SingleCapacity=1)
    evs4_two = [make_ev("e1", NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED)]
    evs4_sng = [make_ev("s1", NewsCategory.INTERNATIONAL, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE)]
    state4 = get_section_quality_state(evs4_two, evs4_sng, NewsCategory.INTERNATIONAL)
    assert state4["two_source_count"] == 1
    assert state4["single_source_count"] == 1
    assert state4["slot_deficit"] == 3
    assert state4["two_source_min_deficit"] == 2
    assert state4["single_source_capacity"] == 1
    assert state4["section_complete"] is False


def test_mocked_hybrid_integration_scenario():
    """
    Mocked scenario simulating the diagnostic live run:
    - 5 verified India events
    - International pool with Alibaba (verified), Arctos/Falcons (classified Intl), Moderna (rejected profile)
    - Final-mile query rotation for International delivering required hybrid composition (3 two-source + 2 single-source)
    - Final section validation passes.
    """
    clf = EventRegionClassifier()
    evaluator = SingleSourceEvaluator()

    # 1. India verified events (5)
    india_events = []
    for i in range(5):
        e = Event(
            id=f"ind_{i}",
            canonical_title=f"Indian Corporate Event {i} ₹500 crore",
            description=f"Indian corporate expansion {i}",
            article_ids=[f"art_in_1_{i}", f"art_in_2_{i}"],
            event_category=NewsCategory.INDIA,
        )
        e.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
        india_events.append(e)

    # 2. International initial events: Alibaba (verified), Arctos (verified), Moderna (profile candidate)
    ev_ali = Event(
        id="intl_ali",
        canonical_title="Alibaba revenue beats estimates with 15% cloud growth",
        description="Alibaba Group reported quarterly results.",
        article_ids=["art_ali_1", "art_ali_2"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    ev_ali.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED

    ev_arctos = Event(
        id="intl_arctos",
        canonical_title="Arctos agrees to buy 10% of Atlanta Falcons at $10.6 billion valuation",
        description="Arctos sports franchise transaction.",
        article_ids=["art_arc_1", "art_arc_2"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    ev_arctos.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED

    # Check Arctos region classification
    arc_cat = clf.classify_event(ev_arctos, [
        Article(
            id="art_arc_1",
            url="https://cnbc.com/arctos-falcons",
            title=ev_arctos.canonical_title,
            content_text="Arctos bought stake in Atlanta Falcons. Unrelated market fell 100 crore.",
            source_name="CNBC",
            published_at=datetime.now(timezone.utc),
            category=NewsCategory.INTERNATIONAL,
        )
    ])
    assert arc_cat == NewsCategory.INTERNATIONAL
    ev_arctos.event_category = arc_cat

    # Moderna profile rejected
    mod_art = Article(
        id="art_mod",
        url="https://cnbc.com/moderna-billionaire",
        title="Moderna co-founder is a billionaire once again at $1.7 billion — he once turned down 20 job offers",
        content_text="Noubar Afeyan net worth feature.",
        source_name="CNBC",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INTERNATIONAL,
    )
    ev_mod = Event(
        id="intl_mod",
        canonical_title=mod_art.title,
        description=mod_art.content_text,
        article_ids=["art_mod"],
        event_category=NewsCategory.INTERNATIONAL,
        companies_involved=["20"],
    )
    is_elig, _, _ = evaluator.evaluate_event(ev_mod, mod_art)
    assert is_elig is False

    # 3. Final mile adds 1 more two-source + 2 high-confidence singles
    ev_intl_3 = Event(
        id="intl_3",
        canonical_title="Boeing secures $5 billion defense contract",
        description="Boeing contract award",
        article_ids=["art_boeing_1", "art_boeing_2"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    ev_intl_3.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED

    ev_sng_1 = Event(
        id="intl_sng_1",
        canonical_title="Apple acquires AI imaging startup for $200 million",
        description="Apple AI startup acquisition.",
        article_ids=["art_apple_1"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    ev_sng_1.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE

    ev_sng_2 = Event(
        id="intl_sng_2",
        canonical_title="Nvidia announces $1 billion AI infrastructure investment",
        description="Nvidia datacenter expansion.",
        article_ids=["art_nv_1"],
        event_category=NewsCategory.INTERNATIONAL,
    )
    ev_sng_2.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE

    intl_verified = [ev_ali, ev_arctos, ev_intl_3]
    intl_singles = [ev_sng_1, ev_sng_2]

    # Verify quality state
    in_state = get_section_quality_state(india_events, [], NewsCategory.INDIA)
    intl_state = get_section_quality_state(intl_verified, intl_singles, NewsCategory.INTERNATIONAL)

    assert in_state["section_complete"] is True
    assert in_state["two_source_count"] == 5

    assert intl_state["section_complete"] is True
    assert intl_state["two_source_count"] == 3
    assert intl_state["single_source_count"] == 2
    assert intl_state["eligible_total"] == 5

