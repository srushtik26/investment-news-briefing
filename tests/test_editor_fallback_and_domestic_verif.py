"""
Regression tests for two production bugs found in the live 15-story pipeline:

BUG 1: Editor 429 deterministic fallback with domestic candidates
-----------------------------------------------------------------
valid_events_map was built from only india + international candidates, so the
deterministic fallback raised:
    "Selected event_id '...' was not in the verified candidate list"
for every domestic story.

BUG 2: Organic verify_event() false domestic two-source match
-------------------------------------------------------------
verify_event() calls is_same_underlying_event(art1, art2) but the
is_domestic_case guard relied solely on art.category, which is UNKNOWN
for domestic expansion articles (extractor never mapped "domestic" -> DOMESTIC).
This allowed unrelated Supreme Court domestic stories to pass as TWO_SOURCE_VERIFIED.
"""

from datetime import datetime, timezone
from typing import Dict
from unittest.mock import patch

from app.ai.editor import GeminiEditorialEngine
from app.ai.models import BriefingEditorialPayload
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.verification.verifier import TwoSourceVerifier
from app.verification.models import VerificationStatus

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _score_breakdown():
    return ScoreBreakdown(
        financial_magnitude=80.0, market_impact=80.0, investor_relevance=80.0,
        corporate_significance=80.0, source_quality=80.0, total_score=80.0,
    )


def _make_scored_event(ev_id, title, section,
                       tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE, rank=1):
    return ScoredEvent(
        event=Event(
            id=ev_id, canonical_title=title, description=f"Description of {title}",
            article_ids=[f"art_{ev_id}"], event_category=section,
            companies_involved=[f"Co {ev_id}"], verification_tier=tier,
        ),
        score_breakdown=_score_breakdown(),
        investment_score=85.0 - rank,
        rank=rank,
    )


def _make_article(art_id, title, url, source, category=NewsCategory.UNKNOWN):
    return Article(
        id=art_id, title=title, url=url, source_name=source,
        published_at=_NOW, date_verified=True,
        content_text=f"Full body text for: {title}. Financial details here.",
        category=category,
    )


def _make_event(ev_id, title, cat, art_ids):
    return Event(
        id=ev_id, canonical_title=title, description=f"Description of {title}",
        article_ids=art_ids, event_category=cat, companies_involved=[],
    )


def _build_pool_and_articles():
    """5-domestic + 5-india + 5-international pool with articles_map."""
    domestic_scored = [
        _make_scored_event(f"dom_{i}", f"Supreme Court orders probe {i}",
                           NewsCategory.DOMESTIC, rank=i + 1)
        for i in range(5)
    ]
    india_scored = [
        _make_scored_event(
            f"ind_{i}", f"Tata Motors Q1 profit {i}", NewsCategory.INDIA,
            tier=VerificationTier.TWO_SOURCE_VERIFIED if i < 2
                 else VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
            rank=i + 1,
        ) for i in range(5)
    ]
    intl_scored = [
        _make_scored_event(f"intl_{i}", f"Apple acquisition {i}",
                           NewsCategory.INTERNATIONAL, rank=i + 1)
        for i in range(5)
    ]
    pool = RankedCandidatePool(
        domestic_candidates=domestic_scored,
        india_candidates=india_scored,
        international_candidates=intl_scored,
    )
    articles_map: Dict[str, Article] = {}
    for s in domestic_scored:
        aid = s.event.article_ids[0]
        articles_map[aid] = _make_article(
            aid, s.event.canonical_title,
            f"https://thehindu.com/national/{aid}", "The Hindu",
            category=NewsCategory.DOMESTIC,
        )
    for s in india_scored:
        aid = s.event.article_ids[0]
        articles_map[aid] = _make_article(
            aid, s.event.canonical_title,
            f"https://economictimes.com/{aid}", "The Economic Times",
            category=NewsCategory.INDIA,
        )
    for s in intl_scored:
        aid = s.event.article_ids[0]
        articles_map[aid] = _make_article(
            aid, s.event.canonical_title,
            f"https://reuters.com/{aid}", "Reuters",
            category=NewsCategory.INTERNATIONAL,
        )
    return pool, articles_map


# ===========================================================================
# BUG 1 REGRESSION: Editor 429 fallback with domestic candidates
# ===========================================================================

def test_gemini_429_fallback_5_plus_5_plus_5_no_event_id_error():
    """
    BUG 1 REGRESSION:
    _call_model always raises 429 -> two consecutive 429s -> deterministic fallback.
    Pool: 5 domestic + 5 india + 5 international.
    Expected: success=True, domestic=5, india=5, international=5.
    Must NOT raise 'Selected event_id was not in verified candidate list'.

    Note: mock_responder=None so the fallback path (if not self.mock_responder) is reachable.
    _call_model is patched instead.
    """
    engine = GeminiEditorialEngine(api_key="dummy-key-for-test")
    pool, articles_map = _build_pool_and_articles()

    with patch.object(
        engine, "_call_model",
        side_effect=Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded"),
    ):
        result = engine.select_and_synthesize_briefing(pool, articles_map)

    assert result.success, (
        f"Expected fallback success, got failure: {result.error_message}"
    )
    payload: BriefingEditorialPayload = result.selection
    assert payload is not None

    domestic = getattr(payload, "domestic_stories", []) or []
    india = payload.india_stories
    intl = payload.international_stories

    assert len(domestic) == 5, f"Expected 5 domestic, got {len(domestic)}"
    assert len(india) == 5, f"Expected 5 india, got {len(india)}"
    assert len(intl) == 5, f"Expected 5 intl, got {len(intl)}"

    all_event_ids = {s.event_id for s in domestic + india + intl}
    valid_ids = {
        s.event.id for s in
        (pool.domestic_candidates or []) + pool.india_candidates + pool.international_candidates
    }
    invalid = all_event_ids - valid_ids
    assert not invalid, (
        f"Fallback selected event_ids not in candidate manifest: {invalid}"
    )


def test_offline_fallback_directly_5_plus_5_plus_5():
    """Direct _generate_offline_editorial_fallback produces exactly 5+5+5."""
    engine = GeminiEditorialEngine(api_key="")
    pool, articles_map = _build_pool_and_articles()
    raw_json = engine._generate_offline_editorial_fallback(pool, articles_map)
    payload = BriefingEditorialPayload.model_validate_json(raw_json)
    domestic = getattr(payload, "domestic_stories", []) or []
    assert len(domestic) == 5, f"Expected 5 domestic, got {len(domestic)}"
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5


# ===========================================================================
# BUG 2 REGRESSION: Organic verify_event false domestic two-source match
# ===========================================================================

class TestVerifyEventDomesticTopicGuard:
    """
    verify_event() must reject unrelated domestic pairs and accept valid ones.
    The DOMESTIC_TOPIC_MISMATCH guard must fire even when art.category == UNKNOWN
    (expansion path), using the parent event.event_category as the signal.
    """

    def test_invalid_pair_unrelated_supreme_court_rejected(self):
        """
        BUG 2 REGRESSION (invalid pair / live failure reproduction):
        A: Supreme Court orders state SITs to investigate fraudulent insurance claims.
        B: Supreme Court orders probe into police conduct at Delhi student protest.
        DIFFERENT events -> NOT TWO_SOURCE_VERIFIED.
        Reason must include DOMESTIC_TOPIC_MISMATCH.
        art.category = UNKNOWN (simulates expansion path).
        event.event_category = DOMESTIC (set by pipeline's reg_clf.classify_event).
        """
        verifier = TwoSourceVerifier()
        art_a = _make_article(
            "art_a_ins",
            "Supreme Court orders state SITs to investigate fraudulent insurance claims",
            "https://thehindu.com/news/national/sc-sits-insurance-fraud",
            "The Hindu", category=NewsCategory.UNKNOWN,
        )
        art_b = _make_article(
            "art_b_protest",
            "Supreme Court orders probe into police conduct at Delhi student protest involving pellet guns",
            "https://indianexpress.com/article/india/sc-delhi-protest-probe",
            "The Indian Express", category=NewsCategory.UNKNOWN,
        )
        event = _make_event("ev_sc_false", art_a.title, NewsCategory.DOMESTIC,
                            [art_a.id, art_b.id])

        result = verifier.verify_event(event, [art_a, art_b])

        assert not result.is_verified, (
            f"Expected NOT verified for unrelated domestic pair; "
            f"got {result.verification_status}: {result.matching_details}"
        )
        assert result.verification_status != VerificationStatus.VERIFIED
        assert "DOMESTIC_TOPIC_MISMATCH" in result.matching_details, (
            f"Expected DOMESTIC_TOPIC_MISMATCH in reason, got: {result.matching_details}"
        )

    def test_valid_pair_same_supreme_court_insurance_fraud_accepted(self):
        """
        BUG 2 REGRESSION (valid control):
        A: Supreme Court directs states to form SITs over fraudulent insurance claims.
        B: SC orders special investigation teams to investigate nationwide insurance fraud claims.
        SAME event, independent publishers -> TWO_SOURCE_VERIFIED.
        """
        verifier = TwoSourceVerifier()
        art_a = _make_article(
            "art_a_sc_ins1",
            "Supreme Court directs states to form SITs over fraudulent insurance claims",
            "https://thehindu.com/news/national/sc-sits-insurance-1",
            "The Hindu", category=NewsCategory.DOMESTIC,
        )
        art_b = _make_article(
            "art_b_sc_ins2",
            "SC orders special investigation teams to investigate nationwide insurance fraud claims",
            "https://ndtv.com/india-news/sc-insurance-fraud-sit",
            "NDTV", category=NewsCategory.DOMESTIC,
        )
        event = _make_event("ev_sc_valid", art_a.title, NewsCategory.DOMESTIC,
                            [art_a.id, art_b.id])

        result = verifier.verify_event(event, [art_a, art_b])

        assert result.is_verified, (
            f"Expected TWO_SOURCE_VERIFIED for valid domestic pair; "
            f"got {result.verification_status}: {result.matching_details}"
        )
        assert result.verification_status == VerificationStatus.VERIFIED

    def test_invalid_pair_expansion_path_category_unknown_rejected(self):
        """
        Expansion-path variant (exact live failure log articles):
          A: 'Supreme Court orders SITs in all states to probe suspected insurance fraud'
          B: 'Let July 20 probe into Delhi protest kick off: Supreme Court'
        Both arrive with art.category=UNKNOWN (extractor did not map 'domestic').
        event.event_category=DOMESTIC (set by classify_event after expansion).
        DOMESTIC_TOPIC_MISMATCH guard must fire via event param.
        """
        verifier = TwoSourceVerifier()
        art_a = _make_article(
            "art_exp_a",
            "Supreme Court orders SITs in all states to probe suspected insurance fraud",
            "https://livemint.com/news/india/sc-sit-insurance",
            "Mint", category=NewsCategory.UNKNOWN,
        )
        art_b = _make_article(
            "art_exp_b",
            "Let July 20 probe into Delhi protest kick off: Supreme Court",
            "https://business-standard.com/india/news/sc-delhi-july20",
            "Business Standard", category=NewsCategory.UNKNOWN,
        )
        event = _make_event("ev_exp_false", art_a.title, NewsCategory.DOMESTIC,
                            [art_a.id, art_b.id])

        result = verifier.verify_event(event, [art_a, art_b])

        assert not result.is_verified, (
            f"Expansion-path: expected NOT verified for unrelated domestic pair; "
            f"got {result.verification_status}: {result.matching_details}"
        )
        assert "DOMESTIC_TOPIC_MISMATCH" in result.matching_details, (
            f"Expected DOMESTIC_TOPIC_MISMATCH, got: {result.matching_details}"
        )
