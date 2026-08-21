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



# =========================================================================
# 1. Section Composition Validation (Min 3 Two-Source, Max 2 Single-Source)
# =========================================================================

def test_composition_5_verified_0_single_valid():
    """5 verified + 0 single = VALID"""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True


def test_composition_3_verified_2_singles_valid():
    """3 verified + 2 high-confidence single = VALID"""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True


def test_composition_2_verified_3_singles_fails():
    """2 verified + 3 singles = INVALID (Check 8 fails)"""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
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
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    assert "lacks TWO_SOURCE_VERIFIED tier" in (report.failure_reason or "") or "minimum 3 required" in (report.failure_reason or "")


def test_event_with_3_article_ids_with_single_source_tier_does_not_count_as_two_source():
    """2. Event with 3 article_ids but HIGH_CONFIDENCE_SINGLE_SOURCE tier does NOT count as two-source."""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    # Should count as 0 two-source, 5 single-source -> fail
    assert "minimum 3 required" in (report.failure_reason or "") or "maximum 2 permitted" in (report.failure_reason or "")


def test_two_source_verified_with_2_independent_sources_counts_correctly():
    """3. TWO_SOURCE_VERIFIED with 2 independent sources counts correctly."""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True
    assert report.passed_checks == 20


def test_3_two_source_plus_2_high_confidence_single_is_valid():
    """4. 3 TWO_SOURCE + 2 HIGH_CONFIDENCE_SINGLE => valid."""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is True
    assert report.passed_checks == 20


def test_2_two_source_plus_3_high_confidence_single_is_invalid():
    """5. 2 TWO_SOURCE + 3 HIGH_CONFIDENCE_SINGLE => invalid."""
    engine = FinalValidationEngine()
    events_map = {}
    articles_map = {}
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

    payload = BriefingEditorialPayload(india_stories=india_stories, international_stories=intl_stories)
    report = engine.validate_briefing(payload, events_map, articles_map)
    assert report.is_valid is False
    assert report.failed_check_id == 8
    assert "minimum 3 required" in (report.failure_reason or "")


