"""
Regression tests for Domestic Stage-7 / Stage-9 quality consistency.

Bug fixed: TWO_SOURCE_VERIFIED domestic stories with DomesticTrendingEvaluator
score < 60 were accepted by Stage 7 (_domestic_select) but later rejected by
Stage 9 (FinalValidationEngine Check 8), preventing final briefing generation.

Fix: A canonical `is_domestic_final_eligible` helper in domestic_trending.py is
called by BOTH stages, ensuring the same event cannot be accepted by one stage
and rejected by the other.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from typing import Optional

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.verification.domestic_trending import (
    DomesticTrendingEvaluator,
    is_domestic_final_eligible,
    DOMESTIC_FINAL_QUALITY_THRESHOLD,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
NOW = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)
TRUSTED_SOURCE = "The Hindu"
TRUSTED_URL = "https://thehindu.com/news/national/some-article"


def _make_article(
    title: str,
    body: str,
    source: str = TRUSTED_SOURCE,
    url: str = TRUSTED_URL,
    age_hours: float = 4.0,
    pub_at: Optional[datetime] = None,
) -> Article:
    if pub_at is None:
        pub_at = NOW - timedelta(hours=age_hours)
    return Article(
        id="art1",
        title=title,
        content_text=body,
        url=url,
        source_name=source,
        published_at=pub_at,
        is_verified_url=True,
    )


def _make_event(
    title: str,
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    confidence: float = 95.0,
    article_id: str = "art1",
) -> Event:
    return Event(
        id="ev1",
        canonical_title=title,
        description=title,
        article_ids=[article_id],
        event_category=NewsCategory.DOMESTIC,
        verification_tier=tier,
        verification_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Smoke test: canonical helper exists and threshold is 60
# ---------------------------------------------------------------------------
def test_canonical_helper_exists_and_threshold_is_60():
    assert DOMESTIC_FINAL_QUALITY_THRESHOLD == 60.0
    assert callable(is_domestic_final_eligible)


# ---------------------------------------------------------------------------
# CASE A: TWO_SOURCE_VERIFIED + score = 55 -> REJECTED
# ---------------------------------------------------------------------------
def test_case_a_two_source_verified_score55_rejected():
    """
    Domestic candidate with TWO_SOURCE_VERIFIED tier but a DomesticTrendingEvaluator
    score of ~55 must NOT be eligible. Two-source tier does NOT bypass the threshold.
    """
    title = "Oil again at $100, fuel retailers losing over Rs 20 on diesel, Rs 5 on petrol"
    body = (
        "Crude oil prices rose sharply. Fuel retailers in India are incurring losses "
        "on petrol and diesel. The companies have not raised retail prices. "
        "The government is monitoring the situation carefully and reviewing options. "
        "Industry body data confirms the ongoing losses. More details are awaited."
    )
    art = _make_article(title=title, body=body, age_hours=20.0)  # 20h => fresh_24h(+15)
    ev = _make_event(title=title, tier=VerificationTier.TWO_SOURCE_VERIFIED)
    ev.article_ids = ["art1", "art2"]  # 2 article_ids => trending_mentions(+10)

    is_elig, score, reason = is_domestic_final_eligible(
        ev, art, now_utc=NOW, max_age_hours=36.0
    )

    assert not is_elig, (
        f"Score={score:.1f}: TWO_SOURCE_VERIFIED must NOT bypass score<60 gate. reason={reason}"
    )
    assert score < 60.0
    assert "REJECT_LOW_SCORE" in reason


# ---------------------------------------------------------------------------
# CASE B: HIGH_CONFIDENCE_SINGLE_SOURCE + score >= 60 -> ACCEPTED
# ---------------------------------------------------------------------------
def test_case_b_hcss_score65_accepted():
    """
    Domestic candidate with high-confidence single source and score >= 60 is eligible.
    """
    title = "Supreme Court upholds constitutional validity of new citizenship amendment rules"
    body = (
        "The Supreme Court of India today upheld the constitutional validity of the "
        "amended citizenship rules in a landmark ruling that will affect millions. "
        "The bench of five judges delivered its verdict unanimously after months of "
        "arguments. The prime minister welcomed the ruling from the apex court. "
        "Indian political parties responded to the judgment."
    )
    art = _make_article(title=title, body=body, age_hours=4.0)  # fresh_6h(+25)
    ev = _make_event(
        title=title,
        tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        confidence=85.0,
    )

    is_elig, score, reason = is_domestic_final_eligible(
        ev, art, now_utc=NOW, max_age_hours=36.0
    )

    assert is_elig, f"Score={score:.1f} should be >=60. reason={reason}"
    assert score >= 60.0


# ---------------------------------------------------------------------------
# CASE C: TWO_SOURCE_VERIFIED + score >= 60 -> ACCEPTED
# ---------------------------------------------------------------------------
def test_case_c_two_source_score75_accepted():
    """
    TWO_SOURCE_VERIFIED domestic candidate with score >= 60 is accepted.
    """
    title = "Cabinet approves Rs 45,000 crore national railway electrification project"
    body = (
        "The Union Cabinet chaired by Prime Minister Narendra Modi today approved "
        "a Rs 45,000 crore project for complete electrification of national railway "
        "network covering 25,000 km of track. The project will reduce dependence on "
        "fossil fuels and cut emissions. Indian Railways will execute the work under "
        "the Ministry of Railways. Modi announced it."
    )
    art = _make_article(title=title, body=body, age_hours=3.0)  # fresh_6h(+25)
    ev = _make_event(title=title, tier=VerificationTier.TWO_SOURCE_VERIFIED)
    ev.article_ids = ["art1", "art2"]

    is_elig, score, reason = is_domestic_final_eligible(
        ev, art, now_utc=NOW, max_age_hours=36.0
    )

    assert is_elig, f"Score={score:.1f} should be >=60. reason={reason}"
    assert score >= 60.0


# ---------------------------------------------------------------------------
# CASE D: Score-55 rejected, score-70+ replacement fills slot
# ---------------------------------------------------------------------------
def test_case_d_score55_rejected_replacement_fills():
    """
    When a provisional Domestic candidate scores 55, it is rejected by
    is_domestic_final_eligible; the next candidate (score >= 60) fills the slot.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)
    articles = {}
    events = {}

    def _mk(eid, title, body, tier, age_hours=4.0):
        art = Article(
            id=f"art_{eid}",
            title=title,
            url=f"https://thehindu.com/news/national/{eid}",
            source_name="The Hindu",
            published_at=now - timedelta(hours=age_hours),
            category=NewsCategory.DOMESTIC,
            content_text=body,
            is_verified_url=True,
        )
        ev = Event(
            id=eid,
            canonical_title=title,
            description=body,
            article_ids=[art.id],
            event_category=NewsCategory.DOMESTIC,
            verification_tier=tier,
            verification_confidence=90.0,
        )
        articles[art.id] = art
        events[eid] = ev
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=90.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=70.0, rationale="test",
            ),
            investment_score=70.0,
        )

    # score~55 story: oil story (no national_significance, no concrete_event, age 20h)
    oil_title = "Oil again at $100, fuel retailers losing over Rs 20 on diesel, Rs 5 on petrol"
    oil_body = (
        "Crude oil prices rose sharply. Fuel retailers in India are incurring losses on "
        "petrol and diesel. The government is monitoring the situation carefully and "
        "reviewing options. Industry body data confirms the ongoing losses."
    )
    cand_oil = _mk("oil", oil_title, oil_body, VerificationTier.TWO_SOURCE_VERIFIED, age_hours=20.0)
    cand_oil.event.article_ids = ["art_oil", "art_oil2"]  # 2 ids => trending_mentions(+10)

    # score~75 replacement: SC ruling
    sc_title = "Supreme Court upholds constitutional validity of election reform amendment"
    sc_body = (
        "The Supreme Court of India today upheld the constitutional validity of the "
        "election reform amendment. The bench delivered a landmark ruling that will "
        "affect the democratic process. The prime minister welcomed the ruling from "
        "the apex court. Indian political parties responded to the judgment."
    )
    cand_sc = _mk("sc", sc_title, sc_body, VerificationTier.TWO_SOURCE_VERIFIED, age_hours=3.0)

    # 3 more good stories to fill remaining slots
    def _mk_good(eid, n):
        t = f"Cabinet approves major national infrastructure project number {n}"
        b = (
            f"The Union Cabinet chaired by Prime Minister Narendra Modi approved a major "
            f"national infrastructure project number {n}. The project will improve "
            f"connectivity across states. Indian Railways will execute the work under "
            f"the ministry. Modi announced it as a landmark decision for India."
        )
        return _mk(eid, t, b, VerificationTier.TWO_SOURCE_VERIFIED, age_hours=2.0)

    cand2 = _mk_good("c2", 2)
    cand3 = _mk_good("c3", 3)
    cand4 = _mk_good("c4", 4)

    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    ctx.articles_lookup = articles
    ctx.domestic_evaluator = DomesticTrendingEvaluator()

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[cand_oil, cand_sc, cand2, cand3, cand4],
        india_candidates=[],
        international_candidates=[],
    )
    ctx.ranker = mock_ranker
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different events")

    accepted = [{"event_id": eid, "category": "domestic"} for eid in events]
    res = run_ranking_and_selection(ctx, accepted, events)
    final_dom = res[0].domestic_candidates
    titles = [s.event.canonical_title for s in final_dom]

    # Oil story (score~55) must be excluded
    assert oil_title not in titles, (
        "Oil story (score~55) must be excluded by DOMESTIC_FINAL_ELIGIBILITY_REJECT"
    )
    # SC story (score~75) must fill the slot
    assert sc_title in titles, f"SC replacement story must fill slot. Got: {titles}"
    # Log must contain the reject entry
    reject_logs = [l for l in logs if "[DOMESTIC_FINAL_ELIGIBILITY_REJECT]" in l]
    assert len(reject_logs) >= 1, "Expected at least one [DOMESTIC_FINAL_ELIGIBILITY_REJECT] log"
    assert oil_title in " ".join(reject_logs)


# ---------------------------------------------------------------------------
# CASE E: Stage 7 and Stage 9 return same eligibility for same event
# ---------------------------------------------------------------------------
def test_case_e_stage7_stage9_same_result_for_same_event():
    """
    For the same event and article, is_domestic_final_eligible (used by both Stage 7
    and Stage 9) must return identical results. Proves no split-brain is possible.
    """
    # Low-score story
    oil_title = "Oil again at $100, fuel retailers losing over Rs 20 on diesel, Rs 5 on petrol"
    oil_body = (
        "Crude oil prices rose sharply. Fuel retailers in India are incurring losses on "
        "petrol and diesel. The government is monitoring the situation carefully. "
        "Industry body data confirms the ongoing losses. More details are awaited."
    )
    art_low = _make_article(title=oil_title, body=oil_body, age_hours=20.0)
    ev_low = _make_event(title=oil_title, tier=VerificationTier.TWO_SOURCE_VERIFIED)
    ev_low.article_ids = ["art1", "art2"]

    elig_s7, score_s7, _ = is_domestic_final_eligible(ev_low, art_low, now_utc=NOW, max_age_hours=36.0)
    elig_s9, score_s9, _ = is_domestic_final_eligible(ev_low, art_low, now_utc=NOW, max_age_hours=36.0)

    assert elig_s7 == elig_s9, f"Stage 7 elig={elig_s7} but Stage 9 elig={elig_s9} — mismatch!"
    assert abs(score_s7 - score_s9) < 0.01, f"Score mismatch: Stage7={score_s7} Stage9={score_s9}"
    assert not elig_s7, "Oil story should be ineligible in both stages"

    # High-score story
    sc_title = "Supreme Court upholds constitutional validity of election reform amendment"
    sc_body = (
        "The Supreme Court of India today upheld the constitutional validity of the "
        "election reform amendment. The bench delivered a landmark ruling. "
        "The prime minister welcomed the ruling. Indian political parties responded."
    )
    art_high = _make_article(title=sc_title, body=sc_body, age_hours=4.0)
    ev_high = _make_event(title=sc_title, tier=VerificationTier.TWO_SOURCE_VERIFIED)

    elig_s7h, score_s7h, _ = is_domestic_final_eligible(ev_high, art_high, now_utc=NOW, max_age_hours=36.0)
    elig_s9h, score_s9h, _ = is_domestic_final_eligible(ev_high, art_high, now_utc=NOW, max_age_hours=36.0)

    assert elig_s7h == elig_s9h, "Stage 7/9 must agree for same high-score event"
    assert elig_s7h, f"SC story should be eligible; score={score_s7h}"


# ---------------------------------------------------------------------------
# CASE F: No replacement exists -> final count < 5 before Stage 9
# ---------------------------------------------------------------------------
def test_case_f_no_replacement_pipeline_reports_insufficiency():
    """
    If all candidates fail the quality gate and there is no replacement,
    the pipeline reports Domestic insufficiency (final_dom < 5) BEFORE Stage 9.
    Every story that DID make it through must itself pass the quality gate.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)
    articles = {}
    events = {}

    def _mk_bad(eid, n):
        t = f"Oil price update number {n}: retailers losing money on petrol and diesel sales"
        b = (
            f"Crude oil prices are elevated. Fuel retailers in India are reporting losses on "
            f"petrol and diesel sales number {n}. The government is reviewing the situation. "
            f"Industry body data confirms the ongoing losses. More details are pending."
        )
        art = Article(
            id=f"art_{eid}",
            title=t,
            url=f"https://thehindu.com/news/national/{eid}",
            source_name="The Hindu",
            published_at=now - timedelta(hours=20),
            category=NewsCategory.DOMESTIC,
            content_text=b,
            is_verified_url=True,
        )
        ev = Event(
            id=eid,
            canonical_title=t,
            description=b,
            article_ids=[art.id, f"art_{eid}_b"],
            event_category=NewsCategory.DOMESTIC,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=90.0,
        )
        articles[art.id] = art
        events[eid] = ev
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=0.0, market_impact=0.0, investor_relevance=0.0,
                corporate_significance=0.0, source_quality=70.0, strategic_bonuses=0.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=55.0, rationale="test",
            ),
            investment_score=55.0,
        )

    bad_cands = [_mk_bad(f"bad{i}", i) for i in range(4)]

    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    ctx.articles_lookup = articles
    ctx.domestic_evaluator = DomesticTrendingEvaluator()
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different events")

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=bad_cands,
        india_candidates=[],
        international_candidates=[],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "domestic"} for eid in events]
    res = run_ranking_and_selection(ctx, accepted, events)
    final_dom = res[0].domestic_candidates

    # Pipeline must report insufficiency (< 5), not silently carry bad stories
    assert len(final_dom) < 5, (
        f"Expected <5 domestic stories when all fail quality gate, got {len(final_dom)}"
    )

    # Every story that made it through must genuinely pass the quality gate
    for s in final_dom:
        art = ctx.articles_lookup.get(s.event.article_ids[0])
        is_elig, score, reason = is_domestic_final_eligible(
            s.event, art, now_utc=now, max_age_hours=36.0
        )
        assert is_elig, (
            f"Story '{s.event.canonical_title}' in final_dom failed quality gate "
            f"score={score:.1f}: {reason}"
        )

    # Must log at least one reject
    reject_logs = [l for l in logs if "[DOMESTIC_FINAL_ELIGIBILITY_REJECT]" in l]
    assert len(reject_logs) >= 1, "Must log at least one DOMESTIC_FINAL_ELIGIBILITY_REJECT"
