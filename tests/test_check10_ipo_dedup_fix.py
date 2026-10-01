import pytest
from datetime import datetime, timezone
from pathlib import Path

from app.models.event import Event
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.validation.models import ValidationStatus
from app.models.entity_sanitizer import clean_company_name, sanitize_company_entities
from app.deduplication.fingerprint import normalize_entity_name
from app.validation.engine import FinalValidationEngine
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from tests.test_validation import valid_briefing_fixtures


def test_ipo_and_transaction_terms_not_treated_as_companies():
    """Verify that IPO and generic transaction terms are stripped/sanitized."""
    assert clean_company_name("IPO") is None
    assert clean_company_name("IPO Today") is None
    assert clean_company_name("Pre-IPO") is None
    assert clean_company_name("FPO") is None
    assert clean_company_name("QIP") is None
    assert clean_company_name("Orient Cables IPO") == "Orient Cables"

    sanitized = sanitize_company_entities(["IPO", "IPO Today", "Orient Cables", "FPO"])
    assert sanitized == ["Orient Cables"]

    assert normalize_entity_name("IPO") == "unspecified_entity"
    assert normalize_entity_name("IPO Today") == "unspecified_entity"
    assert normalize_entity_name("FPO") == "unspecified_entity"


def test_final_validation_check_10_does_not_fail_on_multiple_ipo_stories(valid_briefing_fixtures):
    """Test Check 10 passes when multiple stories in India section have 'IPO' in companies_involved."""
    payload, events_map, articles_map, cand_urls = valid_briefing_fixtures

    # Set two India stories to have companies_involved with IPO or IPO Today
    events_map["evt-in-1"].companies_involved = ["IPO", "Anarock Property Consultants"]
    events_map["evt-in-2"].companies_involved = ["IPO"]
    events_map["evt-in-3"].companies_involved = ["IPO Today", "Orient Cables"]


    engine = FinalValidationEngine()
    report = engine.validate_briefing(
        payload=payload,
        events_lookup=events_map,
        articles_lookup=articles_map,
        candidate_urls=cand_urls,
    )

    check_10_results = [r for r in report.check_results if r.check_id == 10]
    for r in check_10_results:
        assert r.passed is True, f"Check 10 failed unexpectedly: {r.failure_reason}"


def test_offline_editorial_fallback_with_two_ipo_stories():
    """Test that _generate_offline_editorial_fallback does not crash when multiple India candidates have IPO."""
    from app.ai.editor import GeminiEditorialEngine
    from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
    import json

    events = []
    articles = {}
    scored_india = []

    companies = [
        ["Anarock Property"],
        ["IPO"],  # Pure IPO
        ["IPO Today", "Orient Cables"],  # IPO Today + Orient Cables
        ["Kotak Mahindra"],
        ["Avalon Technologies"],
    ]

    for i in range(5):
        ev_id = f"ev_ind_{i}"
        art_id = f"art_ind_{i}"
        url = f"https://www.business-standard.com/markets/story-{i}.html"
        ev = Event(
            id=ev_id,
            canonical_title=f"India Story {i} - Rs {1000 * (i + 1)} crore investment",
            description=f"Description {i}",
            companies_involved=companies[i],
            article_ids=[art_id],
            financial_figures=[f"Rs {1000 * (i + 1)} crore"],
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
        events.append(ev)
        articles[art_id] = art
        scored_india.append(ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=20.0, market_impact=20.0, investor_relevance=20.0,
                corporate_significance=15.0, source_quality=25.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=100.0, rationale="ok"
            ),
            investment_score=100.0,
            rank=i + 1,
        ))

    pool = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=scored_india,
        international_candidates=[],
    )

    engine = GeminiEditorialEngine()
    fb_text = engine._generate_offline_editorial_fallback(pool, articles)
    parsed = json.loads(fb_text)

    india_stories = parsed.get("india_stories", [])
    assert len(india_stories) == 5
    # Verify no duplicate companies in parsed output
    selected_event_ids = [s["event_id"] for s in india_stories]
    assert len(selected_event_ids) == len(set(selected_event_ids))


def test_stage_8_5b_company_dedup_self_healing():
    """Verify that Stage 8.5b self-heals duplicate companies in India section."""
    ev_wipro_1 = Event(
        id="ev_wipro_1",
        canonical_title="Wipro signs mega cloud deal",
        description="Wipro deal details text",
        companies_involved=["Wipro"],
        article_ids=["art_w1"],
        financial_figures=["Rs 500 crore"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    ev_wipro_2 = Event(
        id="ev_wipro_2",
        canonical_title="Wipro reports Q1 profit increase",
        description="Wipro profit details text",
        companies_involved=["Wipro"],
        article_ids=["art_w2"],
        financial_figures=["Rs 600 crore"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    ev_infosys = Event(
        id="ev_infosys",
        canonical_title="Infosys expands digital infrastructure",
        description="Infosys expansion details text",
        companies_involved=["Infosys"],
        article_ids=["art_inf"],
        financial_figures=["Rs 800 crore"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )


    art_w1 = Article(
        id="art_w1",
        title=ev_wipro_1.canonical_title,
        url="https://business-standard.com/w1",
        source_name="Business Standard",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art_w2 = Article(
        id="art_w2",
        title=ev_wipro_2.canonical_title,
        url="https://business-standard.com/w2",
        source_name="Business Standard",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    art_inf = Article(
        id="art_inf",
        title=ev_infosys.canonical_title,
        url="https://business-standard.com/inf",
        source_name="Business Standard",
        published_at=datetime.now(timezone.utc),
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )

    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[
            EditorialStorySelection(section="india", event_id="ev_wipro_1", headline="Wipro signs mega cloud deal", source="Business Standard", url="https://business-standard.com/w1"),
            EditorialStorySelection(section="india", event_id="ev_wipro_2", headline="Wipro reports Q1 profit increase", source="Business Standard", url="https://business-standard.com/w2"),
        ],
        international_stories=[],
    )

    event_by_id = {
        "ev_wipro_1": ev_wipro_1,
        "ev_wipro_2": ev_wipro_2,
        "ev_infosys": ev_infosys,
    }
    articles_lookup = {
        "art_w1": art_w1,
        "art_w2": art_w2,
        "art_inf": art_inf,
    }

    # Simulate Stage 8.5b self-healing logic
    seen_section_comps = set()
    used_event_ids = {"ev_wipro_1", "ev_wipro_2"}
    raw_reserves = [ev_infosys]

    for idx, story in enumerate(payload.india_stories):
        ev = event_by_id.get(story.event_id)
        raw_comps = getattr(ev, "companies_involved", []) or []
        clean_comps = sanitize_company_entities(raw_comps, publisher="Business Standard")
        has_dup = False
        for c in clean_comps:
            norm = normalize_entity_name(c)
            if norm in seen_section_comps and norm != "unspecified_entity":
                has_dup = True
                break
        if not has_dup:
            for c in clean_comps:
                norm = normalize_entity_name(c)
                if norm != "unspecified_entity":
                    seen_section_comps.add(norm)
            continue

        # Swap with qualified reserve
        for res_ev in raw_reserves:
            if res_ev.id in used_event_ids:
                continue
            res_comps = sanitize_company_entities(res_ev.companies_involved, publisher="Business Standard")
            if any(normalize_entity_name(rc) in seen_section_comps for rc in res_comps):
                continue
            # Replace
            payload.india_stories[idx] = EditorialStorySelection(
                section="india", event_id=res_ev.id, headline=res_ev.canonical_title, source="Business Standard", url=art_inf.url
            )
            used_event_ids.add(res_ev.id)
            break

    # Now verify Check 10 passes
    validator = FinalValidationEngine()
    report = validator.validate_briefing(
        payload=payload,
        events_lookup=event_by_id,
        articles_lookup=articles_lookup,
        candidate_urls={art_w1.url, art_w2.url, art_inf.url},
        strict_5_per_section=False,
    )
    check_10 = next((c for c in report.check_results if c.check_id == 10), None)
    assert check_10 is not None
    assert check_10.passed is True

