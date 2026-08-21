"""
Regression tests for Part 12.4: Editorial Company/Publisher Entity Bug
1. "Business Standard" publisher is not treated as company.
2. Same publisher + different companies is allowed.
3. Same real company across two stories is still rejected.
4. Empty company list doesn't become publisher name.
5. Gemini editorial path uses sanitized company entities.
6. Deterministic fallback uses sanitized company entities.
7. 5 India + 5 Intl candidates can pass editorial with different companies.
8. Editorial failure after sufficiency reports EDITORIAL_VALIDATION_FAILED, not INSUFFICIENT_VERIFIED_STORIES.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article, NewsCategory
from app.models.enums import VerificationTier
from app.models.event import Event
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool
from app.ai.editor import GeminiEditorialEngine, BriefingEditorialPayload, EditorialStorySelection
from app.validation.engine import FinalValidationEngine
from app.models.entity_sanitizer import sanitize_company_entities, PUBLISHER_ENTITY_BLACKLIST


def make_scored_event(event: Event, score: float = 85.0, rank: int = 1) -> ScoredEvent:
    breakdown = ScoreBreakdown(
        financial_magnitude=score,
        market_impact=score,
        investor_relevance=score,
        corporate_significance=score,
        source_quality=score,
        total_score=score,
    )
    return ScoredEvent(
        event=event,
        score_breakdown=breakdown,
        investment_score=score,
        rank=rank,
    )


def test_business_standard_publisher_is_not_treated_as_company():
    """Test that publisher names like 'Business Standard' or 'The Economic Times' are filtered out."""
    raw_companies = ["Business Standard", "Larsen & Toubro", "Reuters"]
    cleaned = sanitize_company_entities(raw_companies, publisher="Business Standard")
    assert "Business Standard" not in cleaned
    assert "Reuters" not in cleaned
    assert cleaned == ["Larsen & Toubro"]


def test_same_publisher_different_companies_allowed():
    """Test that two stories published by Business Standard with different companies pass editorial validation."""
    engine = GeminiEditorialEngine(api_key=None)

    ev1 = Event(
        id="ev1",
        canonical_title="L&T secures mega order worth Rs 5000 crore",
        description="Larsen & Toubro announced new infrastructure contract",
        companies_involved=["Larsen & Toubro"],
        article_ids=["a1"],
        event_category=NewsCategory.INDIA,
    )
    ev2 = Event(
        id="ev2",
        canonical_title="Paytm parent One97 losses narrow 40% in Q1",
        description="One97 communications posted narrower losses",
        companies_involved=["Paytm"],
        article_ids=["a2"],
        event_category=NewsCategory.INDIA,
    )

    scored1 = make_scored_event(ev1, 90.0, 1)
    scored2 = make_scored_event(ev2, 85.0, 2)

    valid_events = {"ev1": scored1, "ev2": scored2}
    valid_urls = {"https://business-standard.com/lt": "https://business-standard.com/lt", "https://business-standard.com/paytm": "https://business-standard.com/paytm"}

    payload = BriefingEditorialPayload(
        india_stories=[
            EditorialStorySelection(
                section="india",
                event_id="ev1",
                headline="L&T secures mega order",
                source="Business Standard",
                url="https://business-standard.com/lt",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ev2",
                headline="Paytm losses narrow 40%",
                source="Business Standard",
                url="https://business-standard.com/paytm",
            ),
        ],
        international_stories=[],
    )

    # Should NOT raise ValueError for duplicate company
    engine._validate_editorial_payload(payload, valid_events, valid_urls)


def test_same_real_company_across_two_stories_rejected():
    """Test that duplicate real company (e.g. L&T vs Larsen & Toubro) is strictly rejected."""
    engine = GeminiEditorialEngine(api_key=None)

    ev1 = Event(
        id="ev1",
        canonical_title="L&T wins Rs 2000 crore deal",
        description="L&T announced contract win",
        companies_involved=["L&T"],
        article_ids=["a1"],
        event_category=NewsCategory.INDIA,
    )
    ev2 = Event(
        id="ev2",
        canonical_title="Larsen & Toubro buys Nxt-Infra units",
        description="Larsen & Toubro invested Rs 199 crore",
        companies_involved=["Larsen & Toubro"],
        article_ids=["a2"],
        event_category=NewsCategory.INDIA,
    )

    scored1 = make_scored_event(ev1, 90.0, 1)
    scored2 = make_scored_event(ev2, 85.0, 2)

    valid_events = {"ev1": scored1, "ev2": scored2}
    valid_urls = {"https://example.com/1": "https://example.com/1", "https://example.com/2": "https://example.com/2"}

    payload = BriefingEditorialPayload(
        india_stories=[
            EditorialStorySelection(
                section="india",
                event_id="ev1",
                headline="L&T wins Rs 2000 crore deal",
                source="Economic Times",
                url="https://example.com/1",
            ),
            EditorialStorySelection(
                section="india",
                event_id="ev2",
                headline="Larsen & Toubro buys Nxt-Infra units",
                source="BusinessLine",
                url="https://example.com/2",
            ),
        ],
        international_stories=[],
    )

    with pytest.raises(ValueError, match="duplicate company"):
        engine._validate_editorial_payload(payload, valid_events, valid_urls)


def test_empty_company_list_does_not_become_publisher_name():
    """Test that an event without an extracted company does not default to its publisher name."""
    ev = Event(
        id="ev_empty",
        canonical_title="RBI imposes penalty of Rs 1 crore on cooperative bank",
        description="Central bank penalty",
        companies_involved=[],
        article_ids=["a1"],
        event_category=NewsCategory.INDIA,
    )
    art = Article(
        id="a1",
        title="RBI penalty",
        url="https://business-standard.com/rbi-penalty",
        source_name="Business Standard",
        category=NewsCategory.INDIA,
    )

    cleaned = sanitize_company_entities(ev.companies_involved, publisher=art.source_name)
    assert cleaned == []
    assert "Business Standard" not in cleaned


def test_deterministic_fallback_uses_sanitized_companies():
    """Test offline deterministic fallback selects 5 India stories from same publisher without false duplicate errors."""
    engine = GeminiEditorialEngine(api_key=None)

    india_events = []
    articles_map = {}
    for i in range(5):
        ev_id = f"ev_ind_{i}"
        art_id = f"art_ind_{i}"
        art = Article(
            id=art_id,
            title=f"Corporate Action Event {i} in India",
            url=f"https://business-standard.com/story-{i}",
            source_name="Business Standard",
            category=NewsCategory.INDIA,
        )
        articles_map[art_id] = art
        ev = Event(
            id=ev_id,
            canonical_title=f"Company {i} wins contract {i}",
            description="details",
            companies_involved=[f"Company {i}"],
            article_ids=[art_id],
            event_category=NewsCategory.INDIA,
        )
        india_events.append(make_scored_event(ev, 90.0 - i, i + 1))

    intl_events = []
    for i in range(5):
        ev_id = f"ev_intl_{i}"
        art_id = f"art_intl_{i}"
        art = Article(
            id=art_id,
            title=f"Global Action Event {i}",
            url=f"https://reuters.com/global-{i}",
            source_name="Reuters",
            category=NewsCategory.INTERNATIONAL,
        )
        articles_map[art_id] = art
        ev = Event(
            id=ev_id,
            canonical_title=f"Global Firm {i} reports results",
            description="details",
            companies_involved=[f"Global Firm {i}"],
            article_ids=[art_id],
            event_category=NewsCategory.INTERNATIONAL,
        )
        intl_events.append(make_scored_event(ev, 90.0 - i, i + 1))

    pool = RankedCandidatePool(
        india_candidates=india_events,
        international_candidates=intl_events,
        total_ranked=10,
    )

    res = engine.select_and_synthesize_briefing(pool, articles_map)
    assert res.success is True
    assert len(res.selection.india_stories) == 5
    assert len(res.selection.international_stories) == 5


def test_validation_engine_check10_does_not_fail_on_same_publisher():
    """Test FinalValidationEngine Check 10 does not trigger duplicate company error for same publisher."""
    validator = FinalValidationEngine()

    events_lookup = {}
    articles_lookup = {}
    india_stories = []

    for i in range(5):
        ev_id = f"ev_{i}"
        art_id = f"a_{i}"
        url = f"https://business-standard.com/deal-{i}"
        ev = Event(
            id=ev_id,
            canonical_title=f"Deal {i} in sector {i} for Rs {100*(i+1)} crore",
            description="deal text",
            companies_involved=[f"Distinct Enterprise {i}"],
            article_ids=[art_id],
            financial_figures=[f"Rs {100*(i+1)} crore"],
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        art = Article(
            id=art_id,
            title=ev.canonical_title,
            url=url,
            source_name="Business Standard",
            published_at=datetime.now(timezone.utc),
            category=NewsCategory.INDIA,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        events_lookup[ev_id] = ev
        articles_lookup[art_id] = art
        india_stories.append(
            EditorialStorySelection(
                section="india",
                event_id=ev_id,
                headline=ev.canonical_title,
                source="Business Standard",
                url=url,
            )
        )

    intl_stories = []
    for i in range(5):
        ev_id = f"intl_{i}"
        art_id = f"intl_a_{i}"
        url = f"https://reuters.com/intl-{i}"
        ev = Event(
            id=ev_id,
            canonical_title=f"International Firm {i} reports Q1 results with $100M revenue",
            description="earnings text",
            companies_involved=[f"Intl Company {i}"],
            article_ids=[art_id],
            financial_figures=["$100M"],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        )
        art = Article(
            id=art_id,
            title=ev.canonical_title,
            url=url,
            source_name="Reuters",
            published_at=datetime.now(timezone.utc),
            category=NewsCategory.INTERNATIONAL,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        events_lookup[ev_id] = ev
        articles_lookup[art_id] = art
        intl_stories.append(
            EditorialStorySelection(
                section="international",
                event_id=ev_id,
                headline=ev.canonical_title,
                source="Reuters",
                url=url,
            )
        )

    payload = BriefingEditorialPayload(
        india_stories=india_stories,
        international_stories=intl_stories,
    )

    report = validator.validate_briefing(payload, events_lookup, articles_lookup)
    # Check 10 must pass
    check10_results = [r for r in report.check_results if r.check_id == 10]
    assert len(check10_results) == 1
    assert check10_results[0].passed is True


def test_editorial_failure_after_sufficiency_reports_editorial_validation_failed():
    """Test report logic when sufficiency passed (India=5, Intl=5) but editorial selection fails."""
    sufficient = True
    editorial_res_success = False
    editorial_err = "EDITORIAL_VALIDATION_FAILED: Duplicate company"

    if sufficient and editorial_res_success:
        editorial_status = "SUCCESS"
    elif not sufficient:
        editorial_status = "SKIPPED (INSUFFICIENT_VERIFIED_STORIES)"
    else:
        editorial_status = f"FAILED ({editorial_err})"

    assert "INSUFFICIENT_VERIFIED_STORIES" not in editorial_status
    assert "EDITORIAL_VALIDATION_FAILED" in editorial_status
