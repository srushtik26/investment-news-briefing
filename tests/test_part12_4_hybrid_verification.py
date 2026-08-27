"""
Unit and Regression Tests for Part 12.4:
Hybrid Verification Model + Reliable 5 India / 5 International Story Completion.
"""

from datetime import datetime, timezone, timedelta, date
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.models.enums import VerificationTier
from app.verification.single_source import (
    SingleSourceEvaluator,
    is_multi_event_roundup,
    TRUSTED_INDIA_PUBLISHERS,
    TRUSTED_INTL_PUBLISHERS,
)
from app.verification.query_builder import EventQueryBuilder
from app.verification.verifier import TwoSourceVerifier
from app.ranking.sorter import CandidatePoolRanker
from app.ranking.models import ScoredEvent, ScoreBreakdown
from app.validation.engine import FinalValidationEngine, ValidationStatus
from app.ai.editor import BriefingEditorialPayload, EditorialStorySelection
from app.formatting.formatter import BriefingFormatter


def _create_mock_article(
    id: str,
    title: str,
    source_name: str,
    url: str,
    content_text: str = "Detailed business report discussing financial performance, contracts, and market trends.",
    age_hours: float = 4.0,
    category: NewsCategory = NewsCategory.INDIA,
) -> Article:
    pub_time = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    # Ensure content has > 50 words for valid extraction body
    if len(content_text.split()) < 50:
        content_text = content_text + " " + " ".join([f"extra_word_{i}" for i in range(60)])
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


def _create_mock_event(
    id: str,
    canonical_title: str,
    companies: list,
    article_ids: list,
    category: NewsCategory = NewsCategory.INDIA,
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    figures: list = None,
) -> Event:
    return Event(
        id=id,
        canonical_title=canonical_title,
        description="detailed description of the corporate event",
        companies_involved=companies,
        financial_figures=figures or ["₹500 crore"],
        article_ids=article_ids,
        event_category=category,
        verification_tier=tier,
        verification_confidence=90.0 if tier == VerificationTier.TWO_SOURCE_VERIFIED else 85.0,
    )



def _make_mock_domestic_fixtures():
    dom_stories = []
    dom_events = {}
    dom_articles = {}
    dom_titles = [
        "Supreme Court Constitution Bench rules on national tribunal appointments",
        "Cabinet approves Rs 10000 crore national semiconductor mission package",
        "Election Commission announces schedule for assembly elections",
        "ISRO successfully launches next generation navigation satellite",
        "Parliament passes landmark national digital data protection bill",
    ]
    for i in range(1, 6):
        aid1 = f"art_dom_1_{i}"
        aid2 = f"art_dom_2_{i}"
        title = dom_titles[i - 1]
        art1 = _create_mock_article(aid1, title, "The Hindu", f"https://thehindu.com/dom-{i}", category=NewsCategory.DOMESTIC)
        art2 = _create_mock_article(aid2, title, "The Indian Express", f"https://indianexpress.com/dom-{i}", category=NewsCategory.DOMESTIC)
        dom_articles[aid1] = art1
        dom_articles[aid2] = art2
        ev = _create_mock_event(f"ev_dom_{i}", title, [], [aid1, aid2], category=NewsCategory.DOMESTIC, tier=VerificationTier.TWO_SOURCE_VERIFIED)
        ev.primary_publisher = "The Hindu"
        ev.primary_url = art1.url
        ev.secondary_publisher = "The Indian Express"
        ev.secondary_url = art2.url
        dom_events[ev.id] = ev
        dom_stories.append(EditorialStorySelection(section="domestic", event_id=ev.id, headline=title, source=art1.source_name, url=art1.url))
    return dom_stories, dom_events, dom_articles


# =========================================================================
# 1. Section Composition Validation (Min 3 Two-Source, Max 2 Single-Source)
# =========================================================================

def test_composition_5_verified_0_single_valid():
    """5 verified + 0 single = VALID"""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    for i in range(5):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True


def test_composition_3_verified_2_singles_valid():
    """3 verified + 2 high-confidence single = VALID"""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    # India: 3 two-source + 2 single
    for i in range(3):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(3, 5):
        h = f"India Corp {i} Net Profit Jumps 25% to ₹1200 crore"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "Livemint", f"https://livemint.com/story-{i}")
        articles_map[art1.id] = art1
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id], NewsCategory.INDIA, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, ["₹1200 crore", "25%"])
        ev.primary_publisher = art1.source_name
        ev.primary_url = art1.url
        ev.single_source_confidence_score = 90.0
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    # Intl: 5 two-source
    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True


def test_composition_2_verified_3_singles_fails():
    """2 verified + 3 singles = INVALID (Check 8 fails)"""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    # India: 2 two-source + 3 single
    for i in range(2):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(2, 5):
        h = f"India Corp {i} Net Profit Jumps 25% to ₹1200 crore"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "Livemint", f"https://livemint.com/story-{i}")
        articles_map[art1.id] = art1
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id], NewsCategory.INDIA, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, ["₹1200 crore", "25%"])
        ev.primary_publisher = art1.source_name
        ev.primary_url = art1.url
        ev.single_source_confidence_score = 90.0
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    # Intl: 5 two-source
    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    assert "minimum 3 required" in report.failure_reason


# =========================================================================
# 2. SingleSourceEvaluator Tests
# =========================================================================

def test_single_source_trusted_india_publishers():
    """Test single source evaluator passes trusted India publishers."""
    evaluator = SingleSourceEvaluator()
    art = _create_mock_article(
        "a1",
        "Investcorp buys 20Cube in ₹500 crore logistics bet",
        "The Economic Times",
        "https://economictimes.indiatimes.com/investcorp-20cube",
        age_hours=2.0,
    )
    ev = _create_mock_event("e1", art.title, ["Investcorp", "20Cube"], [art.id], NewsCategory.INDIA, VerificationTier.UNVERIFIED, ["₹500 crore"])
    is_eligible, score, reason = evaluator.evaluate_event(ev, art)
    assert is_eligible is True
    assert score >= 80.0
    assert "Score=" in reason


def test_single_source_trusted_intl_publishers():
    """Test single source evaluator passes trusted International publishers."""
    evaluator = SingleSourceEvaluator()
    art = _create_mock_article(
        "a1",
        "Alphabet acquires cybersecurity startup Wiz for $23 billion",
        "Reuters",
        "https://reuters.com/alphabet-wiz",
        age_hours=3.0,
        category=NewsCategory.INTERNATIONAL,
    )
    ev = _create_mock_event("e1", art.title, ["Alphabet", "Wiz"], [art.id], NewsCategory.INTERNATIONAL, VerificationTier.UNVERIFIED, ["$23 billion"])
    is_eligible, score, reason = evaluator.evaluate_event(ev, art)
    assert is_eligible is True
    assert score >= 80.0


def test_international_high_confidence_single_survives_section_state():
    event = _create_mock_event(
        "intl_single",
        "Alphabet acquires cybersecurity startup Wiz for $23 billion",
        ["Alphabet", "Wiz"],
        ["intl_article"],
        category=NewsCategory.INTERNATIONAL,
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        figures=["$23 billion"],
    )
    event.single_source_confidence_score = 90.0

    from run_pipeline import get_section_quality_state

    state = get_section_quality_state([], [event], NewsCategory.INTERNATIONAL)

    assert state["single_source_count"] == 1
    assert state["eligible_total"] == 1


def test_expansion_style_international_single_is_promoted_after_failed_searches():
    evaluator = SingleSourceEvaluator()
    article = _create_mock_article(
        "nvent_article",
        "NVent Electric Bolsters Data-Center Offerings With Up to $2.3 Billion Acquisition",
        "MarketWatch",
        "https://www.marketwatch.com/story/nvent-electric-acquisition",
        content_text="NVent Electric announced an acquisition to expand its data-center offerings. "
        "The transaction is valued at up to $2.3 billion and is expected to strengthen "
        "the company portfolio, product capabilities, and customer reach across major markets.",
        age_hours=2.0,
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "nvent_event",
        article.title,
        ["NVent Electric"],
        [article.id],
        category=NewsCategory.INTERNATIONAL,
        tier=VerificationTier.UNVERIFIED,
        figures=["$2.3 billion"],
    )

    is_eligible, score, reason = evaluator.evaluate_event(event, article)
    assert is_eligible is True

    event.verification_tier = VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
    event.verification_confidence = score
    event.single_source_confidence_score = score
    event.verification_reason = reason
    from run_pipeline import get_section_quality_state
    state = get_section_quality_state([], [event], NewsCategory.INTERNATIONAL)
    assert state["single_source_count"] == 1


def test_rejected_expansion_single_is_not_promoted():
    evaluator = SingleSourceEvaluator()
    article = _create_mock_article(
        "rejected_article",
        "Market commentary on stocks to buy",
        "MarketWatch",
        "https://www.marketwatch.com/story/commentary",
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "rejected_event",
        article.title,
        ["Example Corp"],
        [article.id],
        category=NewsCategory.INTERNATIONAL,
        tier=VerificationTier.UNVERIFIED,
    )

    is_eligible, _, _ = evaluator.evaluate_event(event, article)
    assert is_eligible is False
    assert event.verification_tier == VerificationTier.UNVERIFIED


def test_international_final_mile_prioritizes_qualified_singles():
    from run_pipeline import prioritize_intl_final_mile_candidates

    qualified = []
    for event_id, title, tier in [
        ("xpeng", "Xpeng reports results with $6.3 billion valuation", VerificationTier.TWO_SOURCE_VERIFIED),
        ("shein", "Shein targets $27 billion Hong Kong IPO", VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE),
        ("nvent", "NVent Electric announces $2.3 billion acquisition", VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE),
        ("bessent", "Bessent imposes $500 billion penalty on major company", VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE),
        ("lakers", "Los Angeles Lakers worth every bit of $12.5 billion", VerificationTier.UNVERIFIED),
        ("commentary", "Market commentary on stocks to buy", VerificationTier.UNVERIFIED),
    ]:
        if event_id == "xpeng":
            continue
        article = _create_mock_article(event_id, title, "MarketWatch", f"https://www.marketwatch.com/{event_id}", category=NewsCategory.INTERNATIONAL)
        event = _create_mock_event(event_id, title, [event_id.title()], [article.id], NewsCategory.INTERNATIONAL, tier, ["$27 billion"])
        qualified.append((event, article))

    events = [event for event, _ in qualified]
    articles = {article.id: article for _, article in qualified}
    ordered = prioritize_intl_final_mile_candidates(events, articles, SingleSourceEvaluator())
    ordered_ids = [event.id for _, event, _ in ordered]

    qualified_ids = {"shein", "nvent", "bessent"}
    assert set(ordered_ids[:3]) == qualified_ids
    assert all(ordered_ids.index(event_id) < ordered_ids.index("lakers") for event_id in qualified_ids)
    assert all(ordered_ids.index(event_id) < ordered_ids.index("commentary") for event_id in qualified_ids)


def test_serpapi_reserves_two_attempts_for_international_upgrades():
    from run_pipeline import get_general_serpapi_limit, get_intl_serpapi_reservation

    intl_state = {"two_source_count": 1}
    assert get_intl_serpapi_reservation(intl_state, 8) == 2
    assert get_general_serpapi_limit(intl_state, 8) == 6


def test_serpapi_reservation_releases_at_international_floor():
    from run_pipeline import get_general_serpapi_limit, get_intl_serpapi_reservation

    intl_state = {"two_source_count": 3}
    assert get_intl_serpapi_reservation(intl_state, 8) == 0
    assert get_general_serpapi_limit(intl_state, 8) == 8


def test_solved_india_cannot_consume_internationally_reserved_attempts():
    from run_pipeline import get_general_serpapi_limit

    india_state = {"two_source_count": 3}
    intl_state = {"two_source_count": 1}
    assert india_state["two_source_count"] >= 3
    assert get_general_serpapi_limit(intl_state, 8) == 6


def test_deficient_india_retains_full_serpapi_limit_when_intl_is_solved():
    from run_pipeline import get_general_serpapi_limit

    india_state = {"two_source_count": 2}
    intl_state = {"two_source_count": 3}
    assert india_state["two_source_count"] < 3
    assert get_general_serpapi_limit(intl_state, 8) == 8


def test_quality_ladder_strict_and_trusted_single_levels():
    from types import SimpleNamespace
    from run_pipeline import get_quality_level

    def scored_event(event):
        return SimpleNamespace(event=event)

    strict_events = []
    articles = {}
    for index in range(5):
        article = _create_mock_article(
            f"strict_{index}",
            f"India Corp {index} acquires rival for $10 billion",
            "Reuters",
            f"https://reuters.com/strict-{index}",
        )
        event = _create_mock_event(
            f"strict_event_{index}", article.title, [f"India Corp {index}"], [article.id],
            NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED,
        )
        strict_events.append(scored_event(event))
        articles[article.id] = article
    assert get_quality_level(strict_events, 5, articles) == "STRICT_SUCCESS"

    single_events = []
    single_articles = {}
    for index in range(5):
        article = _create_mock_article(
            f"single_{index}",
            f"Global Corp {index} acquires rival for $10 billion",
            "Reuters",
            f"https://reuters.com/single-{index}",
            category=NewsCategory.INTERNATIONAL,
        )
        event = _create_mock_event(
            f"single_event_{index}", article.title, [f"Global Corp {index}"], [article.id],
            NewsCategory.INTERNATIONAL, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        single_events.append(scored_event(event))
        single_articles[article.id] = article
    assert get_quality_level(single_events, 0, single_articles) == "FALLBACK_SUCCESS_24H"


def test_quality_ladder_horizon_levels_are_bounded():
    from types import SimpleNamespace
    from run_pipeline import get_quality_level

    article = _create_mock_article(
        "fallback_36", "Global Corp acquires rival for $10 billion", "Reuters",
        "https://reuters.com/fallback-36", age_hours=30.0,
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "fallback_event", article.title, ["Global Corp"], [article.id],
        NewsCategory.INTERNATIONAL, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        ["$10 billion"],
    )
    scored = [SimpleNamespace(event=event)] * 5
    articles = {article.id: article}
    assert get_quality_level(scored, 0, articles) == "FALLBACK_SUCCESS_36H"

    article.published_at = datetime.now(timezone.utc) - timedelta(hours=43)
    assert get_quality_level(scored, 0, articles) == "FALLBACK_SUCCESS_48H"
    article.published_at = datetime.now(timezone.utc) - timedelta(hours=60)
    assert get_quality_level(scored, 0, articles) == "EMERGENCY_SUCCESS_72H"
    article.published_at = datetime.now(timezone.utc) - timedelta(hours=73)
    assert get_quality_level(scored, 0, articles) == "DATA_UNAVAILABLE"


def test_quality_ladder_garbage_is_rejected_at_all_horizons():
    from run_pipeline import evaluate_single_source_for_horizon

    article = _create_mock_article(
        "gold_garbage", "A massive trade just happened in gold. The options market is buzzing",
        "CNBC", "https://www.cnbc.com/gold-garbage", category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "gold_garbage_event", article.title, [], [article.id],
        NewsCategory.INTERNATIONAL, VerificationTier.UNVERIFIED, ["$2 billion"],
    )
    evaluator = SingleSourceEvaluator()
    for horizon in (24.0, 36.0, 48.0, 72.0):
        eligible, _, _ = evaluate_single_source_for_horizon(event, article, evaluator, horizon)
        assert eligible is False


def test_serpapi_duplicate_attempt_key_is_stable_for_same_event():
    from run_pipeline import get_serpapi_event_key

    event = _create_mock_event(
        "nvent_attempt",
        "NVent Electric announces $2.3 billion acquisition",
        ["NVent Electric"],
        ["article"],
        NewsCategory.INTERNATIONAL,
        VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
    )
    equivalent = event.model_copy(deep=True)

    assert get_serpapi_event_key(event) == get_serpapi_event_key(equivalent)


def test_reservation_releases_for_international_discovery_after_attempted_upgrade():
    from run_pipeline import get_general_serpapi_limit, has_unattempted_intl_upgrade

    article = _create_mock_article(
        "attempted_article",
        "NVent Electric announces $2.3 billion acquisition",
        "MarketWatch",
        "https://marketwatch.com/nvent",
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "attempted_event",
        article.title,
        ["NVent Electric"],
        [article.id],
        NewsCategory.INTERNATIONAL,
        VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        ["$2.3 billion"],
    )
    from run_pipeline import get_serpapi_event_key
    attempted = {get_serpapi_event_key(event)}
    assert has_unattempted_intl_upgrade([event], {article.id: article}, SingleSourceEvaluator(), attempted) is False
    assert get_general_serpapi_limit({"two_source_count": 1}, 8, upgrade_available=False) == 8


def test_international_final_mile_can_continue_with_empty_reserve_pool_and_budget():
    from run_pipeline import should_stop_expansion

    assert should_stop_expansion([], {"two_source_count": 1}, True, 6, 8, False) is False
    assert should_stop_expansion([], {"two_source_count": 1}, True, 8, 8, False) is True


def test_unattempted_qualified_upgrade_remains_before_discovery_release():
    from run_pipeline import get_general_serpapi_limit, has_unattempted_intl_upgrade

    article = _create_mock_article(
        "qualified_article",
        "Shein targets $27 billion Hong Kong IPO",
        "Reuters",
        "https://reuters.com/shein-ipo",
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "qualified_event",
        article.title,
        ["Shein"],
        [article.id],
        NewsCategory.INTERNATIONAL,
        VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        ["$27 billion"],
    )
    assert has_unattempted_intl_upgrade([event], {article.id: article}, SingleSourceEvaluator(), set()) is True
    assert get_general_serpapi_limit({"two_source_count": 1}, 8, upgrade_available=True) == 6


def test_qualified_international_single_upgrade_is_not_double_counted():
    primary = _create_mock_article(
        "upgrade_primary",
        "Alphabet acquires cybersecurity startup Wiz for $23 billion",
        "Reuters",
        "https://reuters.com/alphabet-wiz",
        category=NewsCategory.INTERNATIONAL,
    )
    secondary = _create_mock_article(
        "upgrade_secondary",
        "Alphabet buys Wiz for $23 billion acquisition details",
        "Bloomberg",
        "https://bloomberg.com/alphabet-wiz",
        category=NewsCategory.INTERNATIONAL,
    )
    primary.content_text = "Reuters reported Alphabet's cybersecurity expansion and transaction terms. " + " ".join(f"primary_detail_{i}" for i in range(60))
    secondary.content_text = "Bloomberg described Wiz's sale and the strategic technology rationale. " + " ".join(f"secondary_detail_{i}" for i in range(60))
    event = _create_mock_event(
        "upgrade_event",
        primary.title,
        ["Alphabet", "Wiz"],
        [primary.id],
        category=NewsCategory.INTERNATIONAL,
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        figures=["$23 billion"],
    )

    result = TwoSourceVerifier().verify_event(event, [primary, secondary])

    assert result.is_verified is True
    assert event.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED
    from run_pipeline import get_section_quality_state
    state = get_section_quality_state([event], [event], NewsCategory.INTERNATIONAL)
    assert state["two_source_count"] == 1
    assert state["single_source_count"] == 0


def test_generic_gold_commentary_entity_is_removed_upstream():
    from run_pipeline import populate_event_companies

    article = _create_mock_article(
        "gold_commentary",
        "A massive trade just happened in gold. The options market is buzzing",
        "CNBC",
        "https://www.cnbc.com/gold-commentary",
        category=NewsCategory.INTERNATIONAL,
    )

    companies = populate_event_companies(article, [])

    assert companies == []
    assert "The" not in companies


def test_gold_commentary_does_not_qualify_without_named_company():
    article = _create_mock_article(
        "gold_commentary_eval",
        "A massive trade just happened in gold. The options market is buzzing",
        "CNBC",
        "https://www.cnbc.com/gold-commentary-eval",
        category=NewsCategory.INTERNATIONAL,
    )
    event = _create_mock_event(
        "gold_commentary_event",
        article.title,
        [],
        [article.id],
        category=NewsCategory.INTERNATIONAL,
        tier=VerificationTier.UNVERIFIED,
        figures=["$2 billion"],
    )

    is_eligible, _, _ = SingleSourceEvaluator().evaluate_event(event, article)

    assert is_eligible is False


def test_expansion_entity_fallback_preserves_valid_company_names():
    from run_pipeline import populate_event_companies

    cases = [
        ("NVent Electric Bolsters Data-Center Offerings With Up to $2.3 Billion Acquisition", "NVent Electric"),
        ("Shein targets $27 billion Hong Kong IPO", "Shein"),
        ("The Trade Desk announces quarterly results", "The Trade Desk"),
        ("SoftBank sells shares in Lenskart", "SoftBank"),
        ("Xpeng announces delivery forecast", "Xpeng"),
    ]
    for index, (title, expected) in enumerate(cases):
        article = _create_mock_article(
            f"entity_case_{index}",
            title,
            "Reuters",
            f"https://www.reuters.com/entity-case-{index}",
            category=NewsCategory.INTERNATIONAL,
        )
        companies = populate_event_companies(article, [])
        assert expected in companies


def test_expansion_entity_fallback_preserves_nvent_and_shein_entities():
    from run_pipeline import populate_event_companies

    nvent = EventQueryBuilder.extract_entities(
        _create_mock_article(
            "nvent_entity_article",
            "NVent Electric Bolsters Data-Center Offerings With Up to $2.3 Billion Acquisition",
            "MarketWatch",
            "https://www.marketwatch.com/story/nvent-entity",
        )
    )
    shein = EventQueryBuilder.extract_entities(
        _create_mock_article(
            "shein_entity_article",
            "Shein targets $27 billion Hong Kong IPO",
            "Reuters",
            "https://www.reuters.com/business/shein-ipo",
        )
    )
    nvent_clean = populate_event_companies(
        _create_mock_article(
            "nvent_entity_article",
            "NVent Electric Bolsters Data-Center Offerings With Up to $2.3 Billion Acquisition",
            "MarketWatch",
            "https://www.marketwatch.com/story/nvent-entity",
        ),
        [],
    )
    shein_clean = populate_event_companies(
        _create_mock_article(
            "shein_entity_article",
            "Shein targets $27 billion Hong Kong IPO",
            "Reuters",
            "https://www.reuters.com/business/shein-ipo",
        ),
        [],
    )

    assert nvent_clean == ["NVent Electric"]
    assert any(entity.lower() == "shein" for entity in shein_clean)


def test_numeric_and_generic_entity_candidates_are_rejected():
    from app.verification.query_builder import GENERIC_ENTITY_BLACKLIST
    from app.verification.single_source import is_valid_named_company_entity

    assert is_valid_named_company_entity("20") is False
    assert is_valid_named_company_entity("$2.3 billion") is False
    assert "investors" in GENERIC_ENTITY_BLACKLIST


def test_single_source_rejects_untrusted_publisher():
    """Test single source evaluator rejects blog / aggregator."""
    evaluator = SingleSourceEvaluator()
    art = _create_mock_article(
        "a1",
        "Startup raises $50M in Series B",
        "TechSparksBlog",
        "https://techsparksblog.com/deal",
    )
    ev = _create_mock_event("e1", art.title, ["Startup"], [art.id], NewsCategory.INDIA, VerificationTier.UNVERIFIED, ["$50M"])
    is_eligible, score, reason = evaluator.evaluate_event(ev, art)
    assert is_eligible is False
    assert "Untrusted publisher" in reason


def test_single_source_rejects_stale_article():
    """Test single source evaluator strictly rejects age > 24 hours."""
    evaluator = SingleSourceEvaluator()
    art = _create_mock_article(
        "a1",
        "Tata Motors Q1 net profit up 74% to ₹5,566 crore",
        "Livemint",
        "https://livemint.com/tata-motors",
        age_hours=26.5,
    )
    ev = _create_mock_event("e1", art.title, ["Tata Motors"], [art.id], NewsCategory.INDIA, VerificationTier.UNVERIFIED, ["₹5,566 crore"])
    is_eligible, score, reason = evaluator.evaluate_event(ev, art)
    assert is_eligible is False
    assert "Stale" in reason



def test_single_source_rejects_short_body():
    """Test single source evaluator rejects article with short body (< 50 words)."""
    evaluator = SingleSourceEvaluator()
    pub_time = datetime.now(timezone.utc) - timedelta(hours=2.0)
    art = Article(
        id="a1",
        title="L&T secures mega order worth ₹2,500 crore",
        source_name="Business Standard",
        url="https://business-standard.com/lt",
        content_text="Short snippet text.",  # < 50 words
        published_at=pub_time,
        category=NewsCategory.INDIA,
    )
    ev = _create_mock_event("e1", art.title, ["L&T"], [art.id], NewsCategory.INDIA, VerificationTier.UNVERIFIED, ["₹2,500 crore"])
    is_eligible, score, reason = evaluator.evaluate_event(ev, art)
    assert is_eligible is False
    assert "Insufficient article extraction body" in reason


def test_multi_event_roundup_detection():
    """Test multi-event roundups are detected and rejected."""
    assert is_multi_event_roundup("Morning Squawk: 5 things you need to know before the bell") is True
    assert is_multi_event_roundup("Markets Today: Top 10 stocks in focus including Reliance and HDFC") is True
    assert is_multi_event_roundup("Daily Briefing: Today's top corporate developments") is True
    assert is_multi_event_roundup("Stock Market Highlights: Sensex ends 300 pts higher; Nifty above 24,800") is True

    # Single event headline should NOT be flagged
    assert is_multi_event_roundup("Investcorp buys 20Cube in ₹500 crore logistics bet") is False
    assert is_multi_event_roundup("Tata Motors Q1 Net Profit Jumps 74% to ₹5,566 Crore") is False


# =========================================================================
# 3. Alphanumeric Entity Preservation
# =========================================================================

def test_alphanumeric_entity_preservation():
    """Test alphanumeric entity names starting with digits are preserved."""
    art = Article(
        id="a1",
        title="Investcorp buys 20Cube Logistics in ₹500 crore deal; 3M India expands capex",
        source_name="Business Standard",
        url="https://business-standard.com/20cube",
        category=NewsCategory.INDIA,
    )
    entities = EventQueryBuilder.extract_entities(art)
    assert "20Cube Logistics" in entities or "20Cube" in entities
    assert "3M India" in entities or "3M" in entities


# =========================================================================
# 4. Candidate Pool Ranker Verification Tier Hierarchy
# =========================================================================

def test_ranker_prioritizes_two_source_verified():
    """Test CandidatePoolRanker sorts two-source verified events ahead of high-confidence single source."""
    ranker = CandidatePoolRanker()

    ev_two_src = Event(
        id="ev_two",
        canonical_title="Two Source Deal ₹100 Crore",
        description="description",
        companies_involved=["CompA"],
        article_ids=["a1", "a2"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    ev_single = Event(
        id="ev_single",
        canonical_title="Single Source Mega Deal ₹5000 Crore",
        description="description",
        companies_involved=["CompB"],
        article_ids=["a3"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        single_source_confidence_score=95.0,
    )

    ranked_pool = ranker.rank_events([ev_single, ev_two_src])
    assert len(ranked_pool.india_candidates) == 2
    # Two-source verified event must be ranked #1
    assert ranked_pool.india_candidates[0].event.id == "ev_two"
    assert ranked_pool.india_candidates[1].event.id == "ev_single"


# =========================================================================
# 5. Formatter Clean Output (No Verification Tier Tags)
# =========================================================================

def test_formatter_no_verification_tier_tags():
    """Test BriefingFormatter does not print verification tier tags in final text."""
    formatter = BriefingFormatter()
    dom_stories, _, _ = _make_mock_domestic_fixtures()
    india_stories = [
        EditorialStorySelection(
            section="india",
            event_id=f"e_in_{i}",
            headline=f"India Story {i} with corporate action",
            source="The Economic Times",
            url=f"https://economictimes.indiatimes.com/story-{i}",
        )
        for i in range(5)
    ]
    intl_stories = [
        EditorialStorySelection(
            section="international",
            event_id=f"e_int_{i}",
            headline=f"International Story {i} with financial metrics",
            source="Reuters",
            url=f"https://reuters.com/story-{i}",
        )
        for i in range(5)
    ]
    payload = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=india_stories,
        international_stories=intl_stories,
    )
    formatted = formatter.format(payload, briefing_date=date.today())
    text = formatted.text
    assert "TWO_SOURCE_VERIFIED" not in text
    assert "HIGH_CONFIDENCE_SINGLE_SOURCE" not in text
    assert "VERIFICATION_TIER" not in text
    assert "India Story 0" in text


# =========================================================================
# 6. Final Hybrid Safety Fix: Verification Tier Authoritative Signal Tests
# =========================================================================

def test_event_with_2_article_ids_without_two_source_tier_does_not_count_as_two_source():
    """1. Event with 2 article_ids but UNVERIFIED/None tier does NOT count as two-source."""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    # 5 India stories all with 2 article_ids but UNVERIFIED tier
    for i in range(5):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        # UNVERIFIED tier despite having 2 article_ids
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.UNVERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    assert "lacks TWO_SOURCE_VERIFIED tier" in (report.failure_reason or "") or "minimum 3 required" in (report.failure_reason or "")


def test_event_with_3_article_ids_with_single_source_tier_does_not_count_as_two_source():
    """2. Event with 3 article_ids but HIGH_CONFIDENCE_SINGLE_SOURCE tier does NOT count as two-source."""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    # 5 India stories with 3 article_ids each, but tier == HIGH_CONFIDENCE_SINGLE_SOURCE
    for i in range(5):
        h = f"India Corp {i} Net Profit Jumps 25% to ₹1200 crore"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "Livemint", f"https://livemint.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} extra 1", "Livemint", f"https://livemint.com/story-{i}-extra1")
        art3 = _create_mock_article(f"art_in_3_{i}", f"{h} extra 2", "Livemint", f"https://livemint.com/story-{i}-extra2")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        articles_map[art3.id] = art3
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id, art3.id], NewsCategory.INDIA, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, ["₹1200 crore", "25%"])
        ev.primary_publisher = art1.source_name
        ev.primary_url = art1.url
        ev.single_source_confidence_score = 90.0
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    # Should count as 0 two-source, 5 single-source -> fail
    assert "minimum 3 required" in (report.failure_reason or "") or "maximum 2 permitted" in (report.failure_reason or "")


def test_two_source_verified_with_2_independent_sources_counts_correctly():
    """3. TWO_SOURCE_VERIFIED with 2 independent sources counts correctly."""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    for i in range(5):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True
    assert report.passed_checks >= 18


def test_3_two_source_plus_2_high_confidence_single_is_valid():
    """4. 3 TWO_SOURCE + 2 HIGH_CONFIDENCE_SINGLE => valid."""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    for i in range(3):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(3, 5):
        h = f"India Corp {i} Net Profit Jumps 25% to ₹1200 crore"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "Livemint", f"https://livemint.com/story-{i}")
        articles_map[art1.id] = art1
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id], NewsCategory.INDIA, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, ["₹1200 crore", "25%"])
        ev.primary_publisher = art1.source_name
        ev.primary_url = art1.url
        ev.single_source_confidence_score = 90.0
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True
    assert report.passed_checks >= 18


def test_2_two_source_plus_3_high_confidence_single_is_invalid():
    """5. 2 TWO_SOURCE + 3 HIGH_CONFIDENCE_SINGLE => invalid."""
    engine = FinalValidationEngine()
    dom_stories, dom_events, dom_articles = _make_mock_domestic_fixtures()
    events_map = dict(dom_events)
    articles_map = dict(dom_articles)
    india_stories = []
    intl_stories = []

    for i in range(2):
        h = f"India Corp {i} signs ₹500 crore contract"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "The Economic Times", f"https://economictimes.com/story-{i}")
        art2 = _create_mock_article(f"art_in_2_{i}", f"{h} Details", "Business Standard", f"https://business-standard.com/story-{i}")
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id, art2.id], NewsCategory.INDIA, VerificationTier.TWO_SOURCE_VERIFIED)
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(2, 5):
        h = f"India Corp {i} Net Profit Jumps 25% to ₹1200 crore"
        art1 = _create_mock_article(f"art_in_1_{i}", h, "Livemint", f"https://livemint.com/story-{i}")
        articles_map[art1.id] = art1
        ev = _create_mock_event(f"ev_in_{i}", h, [f"India Corp {i}"], [art1.id], NewsCategory.INDIA, VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, ["₹1200 crore", "25%"])
        ev.primary_publisher = art1.source_name
        ev.primary_url = art1.url
        ev.single_source_confidence_score = 90.0
        events_map[ev.id] = ev
        india_stories.append(EditorialStorySelection(section="india", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    for i in range(5):
        h = f"Global Corp {i} acquires rival for $1B"
        art1 = _create_mock_article(f"art_int_1_{i}", h, "Reuters", f"https://reuters.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        art2 = _create_mock_article(f"art_int_2_{i}", f"{h} Details", "Bloomberg", f"https://bloomberg.com/story-{i}", category=NewsCategory.INTERNATIONAL)
        articles_map[art1.id] = art1
        articles_map[art2.id] = art2
        ev = _create_mock_event(f"ev_int_{i}", h, [f"Global Corp {i}"], [art1.id, art2.id], NewsCategory.INTERNATIONAL, VerificationTier.TWO_SOURCE_VERIFIED, ["$1B"])
        events_map[ev.id] = ev
        intl_stories.append(EditorialStorySelection(section="international", event_id=ev.id, headline=ev.canonical_title, source=art1.source_name, url=art1.url))

    payload = BriefingEditorialPayload(domestic_stories=dom_stories, india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    assert "minimum 3 required" in (report.failure_reason or "")


