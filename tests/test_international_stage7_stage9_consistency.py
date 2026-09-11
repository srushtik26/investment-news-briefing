"""
Regression tests for International Stage-7 / Stage-9 geopolitical quality consistency.

Bug fixed: International geopolitical stories without quantified market impact numbers
(e.g., "Trump's oil investments have gained millions during Iran war as his accounts keep trading")
were accepted by Stage 7 (_intl_select) but later rejected by Stage 9 (FinalValidationEngine Check 19),
preventing final briefing generation.

Fix: A canonical `is_geopolitical_market_impact_eligible` / `is_international_final_eligible` helper
in app/verification/international.py is called by BOTH stages, ensuring the same story cannot be accepted
by Stage 7 and rejected by Stage 9.
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import MagicMock

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.verification.international import (
    is_geopolitical_market_impact_eligible,
    is_international_final_eligible,
    is_geopolitical_story,
)
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.validation.engine import FinalValidationEngine, ValidationStatus

NOW = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)
TRUSTED_SOURCE = "Reuters"
TRUSTED_URL = "https://reuters.com/business/world-article"


def _make_article(
    title: str,
    body: str = "Detailed financial reporting on international market developments and corporate transactions.",
    source: str = TRUSTED_SOURCE,
    url: str = TRUSTED_URL,
    age_hours: float = 4.0,
) -> Article:
    return Article(
        id=f"art-{abs(hash(title)) % 100000}",
        title=title,
        content_text=body,
        url=url,
        source_name=source,
        published_at=NOW - timedelta(hours=age_hours),
        is_verified_url=True,
    )


def _make_event(
    title: str,
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    confidence: float = 90.0,
    category: NewsCategory = NewsCategory.INTERNATIONAL,
    article_id: str = "art1",
) -> Event:
    return Event(
        id=f"ev-{abs(hash(title)) % 100000}",
        canonical_title=title,
        description=title,
        article_ids=[article_id],
        event_category=category,
        verification_tier=tier,
        verification_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Smoke test: Canonical helpers exist and work as expected
# ---------------------------------------------------------------------------
def test_canonical_helpers_exist():
    assert callable(is_geopolitical_market_impact_eligible)
    assert callable(is_international_final_eligible)
    assert callable(is_geopolitical_story)


# ---------------------------------------------------------------------------
# CASE A: Geopolitical International story without quantified market impact -> INELIGIBLE
# ---------------------------------------------------------------------------
def test_case_a_geopolitical_story_without_numbers_ineligible():
    """
    A geopolitical story (mentions war/sanctions/tariffs/ceasefire/geopolitical)
    without quantified digits is rejected by the canonical gate.
    """
    trump_headline = "Trump's oil investments have gained millions during Iran war as his accounts keep trading"
    art = _make_article(trump_headline)
    ev = _make_event(trump_headline, article_id=art.id)

    is_elig, reason = is_geopolitical_market_impact_eligible(ev, art)
    assert not is_elig
    assert "lacks quantified market impact figures" in reason

    is_intl_elig, score, intl_reason = is_international_final_eligible(ev, art)
    assert not is_intl_elig
    assert "lacks quantified market impact figures" in intl_reason


# ---------------------------------------------------------------------------
# CASE B: Geopolitical story with valid quantified market impact -> ELIGIBLE
# ---------------------------------------------------------------------------
def test_case_b_geopolitical_story_with_numbers_eligible():
    """
    A geopolitical story with quantified figures (digits) is eligible.
    """
    quantified_headline = "US Imposes 25% Tariffs on European Steel Imports Worth $14 Billion"
    art = _make_article(quantified_headline)
    ev = _make_event(quantified_headline, article_id=art.id)

    is_elig, reason = is_geopolitical_market_impact_eligible(ev, art)
    assert is_elig
    assert reason == ""

    is_intl_elig, score, intl_reason = is_international_final_eligible(ev, art)
    assert is_intl_elig
    assert score >= 80.0
    assert intl_reason == ""


# ---------------------------------------------------------------------------
# Non-geopolitical story -> ELIGIBLE
# ---------------------------------------------------------------------------
def test_non_geopolitical_story_passes():
    """
    A regular non-geopolitical business story passes without requiring digits.
    """
    headline = "Microsoft Signs Strategic Cloud Partnership With Vodafone"
    art = _make_article(headline)
    ev = _make_event(headline, article_id=art.id)

    is_elig, reason = is_geopolitical_market_impact_eligible(ev, art)
    assert is_elig
    assert reason == ""


# ---------------------------------------------------------------------------
# CASE C: Invalid geopolitical candidate + valid reserve candidate -> Invalid skipped, reserve fills slot
# ---------------------------------------------------------------------------
def test_case_c_invalid_geopolitical_skipped_reserve_fills_slot():
    """
    When candidate pool contains 5 valid stories and 1 unquantified geopolitical story,
    Stage 7 selection rejects the unquantified geopolitical story and the valid reserve
    candidate fills the slot, maintaining 5 International candidates.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)

    # 5 valid domestic
    dom_data = [
        ("Cabinet approves major national railway infrastructure project phase 1", "The Hindu", "https://thehindu.com/1"),
        ("Cabinet approves major national railway infrastructure project phase 2", "The Hindu", "https://thehindu.com/2"),
        ("Cabinet approves major national railway infrastructure project phase 3", "The Hindu", "https://thehindu.com/3"),
        ("Cabinet approves major national railway infrastructure project phase 4", "The Hindu", "https://thehindu.com/4"),
        ("Cabinet approves major national railway infrastructure project phase 5", "The Hindu", "https://thehindu.com/5"),
    ]
    dom_content_bodies = {
        h: (
            f"The Union Cabinet chaired by Prime Minister Narendra Modi today approved a major "
            f"national infrastructure project worth Rs 45,000 crore. The project will improve "
            f"connectivity across Indian states. Indian Railways will execute the work under "
            f"the ministry. Modi announced it as a landmark decision for India."
        )
        for h, _, _ in dom_data
    }

    dom_stories = []
    dom_events = []
    articles_lookup = {}
    event_by_id = {}

    for i, (h, pub, url) in enumerate(dom_data):
        b = dom_content_bodies[h]
        art = Article(id=f"art-dom-{i}", title=h, content_text=b, url=url, source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(id=f"ev-dom-{i}", canonical_title=h, description=h, article_ids=[art.id], event_category=NewsCategory.DOMESTIC, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        dom_events.append(ev)
        dom_stories.append({"event_id": ev.id, "headline": h, "category": "domestic"})

    # 5 valid India
    india_data = [
        ("Tata Motors board approves Rs 9,000 crore investment for new electric vehicle manufacturing plant", "Tata Motors", "Business Standard", "https://bs.com/1"),
        ("Reliance Industries signs Rs 12,000 crore green hydrogen agreement with European energy consortium", "Reliance Industries", "Business Standard", "https://bs.com/2"),
        ("Infosys signs $1.5 billion multi-year cloud transformation contract with global financial institution", "Infosys", "Business Standard", "https://bs.com/3"),
        ("Sun Pharma acquires US-based specialty dermatology company for $450 million in all-cash transaction", "Sun Pharma", "Business Standard", "https://bs.com/4"),
        ("Larsen & Toubro bags Rs 4,500 crore EPC order for high-speed rail corridor project", "Larsen & Toubro", "Business Standard", "https://bs.com/5"),
    ]

    india_stories = []
    india_events = []
    for i, (h, comp, pub, url) in enumerate(india_data):
        b = f"{comp} today officially announced {h}. The management confirmed the strategic and financial details."
        art = Article(id=f"art-in-{i}", title=h, content_text=b, url=url, source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(id=f"ev-in-{i}", canonical_title=h, description=h, article_ids=[art.id], companies_involved=[comp], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        india_events.append(ev)
        india_stories.append({"event_id": ev.id, "headline": h, "company_name": comp, "category": "india"})

    # 6 International candidates:
    # A (valid), B (valid), C (unquantified geopolitical - INVALID), D (valid), E (valid), F (valid reserve)
    intl_specs = [
        ("art-int-0", "ev-int-0", "ASML Reports €2.1 Billion Quarterly Profit", "Bloomberg", 95.0),
        ("art-int-1", "ev-int-1", "Toyota Acquires 15% Stake in Battery Manufacturer", "Financial Times", 90.0),
        ("art-int-2", "ev-int-2", "Trump's oil investments have gained millions during Iran war as his accounts keep trading", "Bloomberg", 88.0),  # INVALID
        ("art-int-3", "ev-int-3", "Microsoft Signs $1.5 Billion Cloud Infrastructure Deal with G42", "The Wall Street Journal", 85.0),
        ("art-int-4", "ev-int-4", "Rio Tinto Approves $350 Million Solar Power Capex Project", "CNBC", 82.0),
        ("art-int-5", "ev-int-5", "Shell Signs $1.2 Billion LNG Supply Contract", "Reuters", 80.0),  # RESERVE
    ]

    intl_stories = []
    intl_events = []
    for art_id, ev_id, h, pub, conf in intl_specs:
        b = f"Global reporting on {h} with net profit and crude oil market dynamics for energy accounts."
        art = Article(id=art_id, title=h, content_text=b, url=f"https://source.com/{art_id}", source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(
            id=ev_id,
            canonical_title=h,
            description=b,
            article_ids=[art.id],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=conf,
            metadata={"business_relevance_score": 85.0},
        )
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        intl_events.append(ev)
        intl_stories.append({"event_id": ev.id, "headline": h, "category": "international"})

    all_accepted = dom_stories + india_stories + intl_stories

    logs = []
    # Setup context
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=None,
        logs_dir=None,
        settings=None,
        max_india=5,
        max_international=5,
        log_exec=lambda msg: logs.append(msg),
        articles_lookup=articles_lookup,
        verified_events=dom_events + india_events + intl_events,
    )
    from app.verification.domestic_trending import DomesticTrendingEvaluator
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    mock_verifier = MagicMock()
    mock_verifier.is_same_underlying_event.return_value = (False, 0.0, "different")
    ctx.verifier = mock_verifier

    def _make_breakdown(score: float = 90.0) -> ScoreBreakdown:
        return ScoreBreakdown(
            financial_magnitude=0.0,
            market_impact=0.0,
            investor_relevance=0.0,
            corporate_significance=0.0,
            source_quality=score,
            strategic_bonuses=0.0,
            editorial_signals=0.0,
            relevance_penalties=0.0,
            total_score=score,
            rationale="test",
        )

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in dom_events],
        india_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in india_events],
        international_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in intl_events],
    )
    ctx.ranker = mock_ranker

    cand_pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx, all_accepted, event_by_id
    )

    assert sufficient, f"Stage 7 was not sufficient. Logs:\n" + "\n".join(logs)
    assert len(int_p) == 5
    selected_intl_titles = [s.event.canonical_title for s in int_p]

    # "Trump's oil investments..." MUST NOT be in selected 5
    assert not any("Trump's oil investments" in t for t in selected_intl_titles)
    # Reserve "Shell Signs $1.2 Billion LNG Supply Contract" MUST be in selected 5
    assert "Shell Signs $1.2 Billion LNG Supply Contract" in selected_intl_titles

    # Verify reject log entry
    rej_logs = [l for l in logs if "[INTERNATIONAL_FINAL_ELIGIBILITY_REJECT]" in l]
    assert len(rej_logs) >= 1
    assert "fails geopolitical quantified market-impact requirement" in rej_logs[0]


# ---------------------------------------------------------------------------
# CASE D: Stage 7 and Stage 9 same story -> same eligibility result
# ---------------------------------------------------------------------------
def test_case_d_stage7_and_stage9_consistency_proof():
    """
    For the exact same story, Stage 7 and Stage 9 use the exact same canonical helper
    and always produce identical eligibility outcomes.
    """
    # 1. Invalid geopolitical story
    invalid_headline = "Trump's oil investments have gained millions during Iran war as his accounts keep trading"
    art_inv = _make_article(invalid_headline)
    ev_inv = _make_event(invalid_headline, article_id=art_inv.id)

    # Stage 7 helper result:
    s7_elig, s7_reason = is_geopolitical_market_impact_eligible(ev_inv, art_inv)
    # Stage 9 helper result:
    s9_elig, s9_reason = is_geopolitical_market_impact_eligible(invalid_headline)

    assert s7_elig == s9_elig == False
    assert s7_reason == s9_reason
    assert "lacks quantified market impact figures" in s7_reason

    # 2. Valid geopolitical story with numbers
    valid_headline = "US Imposes 25% Tariffs on Steel Imports Amounting to $10 Billion"
    art_val = _make_article(valid_headline)
    ev_val = _make_event(valid_headline, article_id=art_val.id)

    s7_val_elig, s7_val_reason = is_geopolitical_market_impact_eligible(ev_val, art_val)
    s9_val_elig, s9_val_reason = is_geopolitical_market_impact_eligible(valid_headline)

    assert s7_val_elig == s9_val_elig == True
    assert s7_val_reason == s9_val_reason == ""


# ---------------------------------------------------------------------------
# CASE E: No replacement -> International insufficiency before Stage 9
# ---------------------------------------------------------------------------
def test_case_e_no_replacement_intl_insufficient_before_stage9():
    """
    When International candidate pool only has 4 valid stories and 1 unquantified
    geopolitical story (total 5 candidates, no reserve), the unquantified story is
    rejected, leaving 4 candidates, and Stage 7 reports sufficient=False BEFORE Stage 9.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)

    # 5 valid domestic
    dom_data = [
        ("Cabinet approves major national railway infrastructure project phase 1", "The Hindu", "https://thehindu.com/1"),
        ("Cabinet approves major national railway infrastructure project phase 2", "The Hindu", "https://thehindu.com/2"),
        ("Cabinet approves major national railway infrastructure project phase 3", "The Hindu", "https://thehindu.com/3"),
        ("Cabinet approves major national railway infrastructure project phase 4", "The Hindu", "https://thehindu.com/4"),
        ("Cabinet approves major national railway infrastructure project phase 5", "The Hindu", "https://thehindu.com/5"),
    ]
    dom_content_bodies = {
        h: (
            f"The Union Cabinet chaired by Prime Minister Narendra Modi today approved a major "
            f"national infrastructure project worth Rs 45,000 crore. The project will improve "
            f"connectivity across Indian states. Indian Railways will execute the work under "
            f"the ministry. Modi announced it as a landmark decision for India."
        )
        for h, _, _ in dom_data
    }

    dom_stories = []
    dom_events = []
    articles_lookup = {}
    event_by_id = {}

    for i, (h, pub, url) in enumerate(dom_data):
        b = dom_content_bodies[h]
        art = Article(id=f"art-dom-e-{i}", title=h, content_text=b, url=url, source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(id=f"ev-dom-e-{i}", canonical_title=h, description=h, article_ids=[art.id], event_category=NewsCategory.DOMESTIC, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        dom_events.append(ev)
        dom_stories.append({"event_id": ev.id, "headline": h, "category": "domestic"})

    # 5 valid India
    india_data = [
        ("Tata Motors board approves Rs 9,000 crore investment for new electric vehicle manufacturing plant", "Tata Motors", "Business Standard", "https://bs.com/1"),
        ("Reliance Industries signs Rs 12,000 crore green hydrogen agreement with European energy consortium", "Reliance Industries", "Business Standard", "https://bs.com/2"),
        ("Infosys signs $1.5 billion multi-year cloud transformation contract with global financial institution", "Infosys", "Business Standard", "https://bs.com/3"),
        ("Sun Pharma acquires US-based specialty dermatology company for $450 million in all-cash transaction", "Sun Pharma", "Business Standard", "https://bs.com/4"),
        ("Larsen & Toubro bags Rs 4,500 crore EPC order for high-speed rail corridor project", "Larsen & Toubro", "Business Standard", "https://bs.com/5"),
    ]

    india_stories = []
    india_events = []
    for i, (h, comp, pub, url) in enumerate(india_data):
        b = f"{comp} today officially announced {h}. The management confirmed the strategic and financial details."
        art = Article(id=f"art-in-e-{i}", title=h, content_text=b, url=url, source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(id=f"ev-in-e-{i}", canonical_title=h, description=h, article_ids=[art.id], companies_involved=[comp], event_category=NewsCategory.INDIA, verification_tier=VerificationTier.TWO_SOURCE_VERIFIED, verification_confidence=95.0)
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        india_events.append(ev)
        india_stories.append({"event_id": ev.id, "headline": h, "company_name": comp, "category": "india"})

    # Only 5 International stories, 1 is unquantified geopolitical (no reserve)
    intl_specs = [
        ("art-int-e-0", "ev-int-e-0", "ASML Reports €2.1 Billion Quarterly Profit", "Bloomberg"),
        ("art-int-e-1", "ev-int-e-1", "Toyota Acquires 15% Stake in Battery Manufacturer", "Financial Times"),
        ("art-int-e-2", "ev-int-e-2", "Trump's oil investments have gained millions during Iran war as his accounts keep trading", "The Guardian"),  # INVALID
        ("art-int-e-3", "ev-int-e-3", "Microsoft Signs $1.5 Billion Cloud Infrastructure Deal with G42", "The Wall Street Journal"),
        ("art-int-e-4", "ev-int-e-4", "Rio Tinto Approves $350 Million Solar Power Capex Project", "CNBC"),
    ]

    intl_stories = []
    intl_events = []
    for art_id, ev_id, h, pub in intl_specs:
        art = Article(id=art_id, title=h, content_text="Global reporting.", url=f"https://source.com/{art_id}", source_name=pub, published_at=now, is_verified_url=True)
        ev = Event(
            id=ev_id,
            canonical_title=h,
            description=h,
            article_ids=[art.id],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=90.0,
            metadata={"business_relevance_score": 85.0},
        )
        articles_lookup[art.id] = art
        event_by_id[ev.id] = ev
        intl_events.append(ev)
        intl_stories.append({"event_id": ev.id, "headline": h, "category": "international"})

    all_accepted = dom_stories + india_stories + intl_stories

    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=None,
        logs_dir=None,
        settings=None,
        max_india=5,
        max_international=5,
        log_exec=lambda msg: logs.append(msg),
        articles_lookup=articles_lookup,
        verified_events=dom_events + india_events + intl_events,
    )
    from app.verification.domestic_trending import DomesticTrendingEvaluator
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    mock_verifier = MagicMock()
    mock_verifier.is_same_underlying_event.return_value = (False, 0.0, "different")
    ctx.verifier = mock_verifier

    def _make_breakdown(score: float = 90.0) -> ScoreBreakdown:
        return ScoreBreakdown(
            financial_magnitude=0.0,
            market_impact=0.0,
            investor_relevance=0.0,
            corporate_significance=0.0,
            source_quality=score,
            strategic_bonuses=0.0,
            editorial_signals=0.0,
            relevance_penalties=0.0,
            total_score=score,
            rationale="test",
        )

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in dom_events],
        india_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in india_events],
        international_candidates=[ScoredEvent(event=e, investment_score=90.0, score_breakdown=_make_breakdown(90.0)) for e in intl_events],
    )
    ctx.ranker = mock_ranker

    cand_pool, dom_p, ind_p, int_p, sufficient, status = run_ranking_and_selection(
        ctx, all_accepted, event_by_id
    )

    # Intl has only 4 candidates (invalid candidate rejected)
    assert len(int_p) == 4
    # Pipeline is NOT sufficient, correctly caught BEFORE Stage 9
    assert not sufficient
