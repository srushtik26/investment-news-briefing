"""
Tests for deterministic editorial fallback and headline grounding.

Verifies:
1. Deterministic fallback headlines preserve numeric figures (e.g. ₹100 crore, not 100b).
2. Archetype synthesis rejects generic placeholder entities ("Market Entity", "Target Enterprise", "Antitrust Authority").
3. Multi-tier safety ladder (Level 1, Level 2, Level 3) in `generate_grounded_fallback_headline`.
4. Stage 9 Check #6 (semantic token overlap >= 1) passes on deterministic editorial fallback stories.
5. Check #6 is NOT weakened (unrelated headlines with zero overlap still fail).
6. Offline/rate-limited editorial engine fallback generates valid 15-story payload passing Check #6.
"""

import json
import pytest
from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown
from app.ai.headline_synthesis import (
    synthesize_investment_headline,
    generate_grounded_fallback_headline,
    validate_numeric_grounding,
    validate_entity_grounding,
    _clean_headline_text,
)
from app.validation.engine import calculate_semantic_token_overlap, FinalValidationEngine
from app.ai.editor import GeminiEditorialEngine, generate_deterministic_summary, BriefingEditorialPayload


def test_numeric_grounding_rejects_unanchored_and_mutated_units():
    """Ensure validate_numeric_grounding rejects corrupted numbers or unit mutations."""
    source = "RBI imposes Rs 100 crore penalty on standard chartered bank for non compliance"
    # Same number / currency:
    assert validate_numeric_grounding("RBI Imposes Rs 100 Crore Penalty on Standard Chartered Bank", source)[0] is True
    # Mutated unit ("100b" or "100 billion" instead of 100 crore):
    assert validate_numeric_grounding("RBI Imposes 100b Penalty on Bank", source)[0] is False
    assert validate_numeric_grounding("RBI Imposes 100 Billion Penalty on Bank", source)[0] is False
    # Unanchored number:
    assert validate_numeric_grounding("RBI Imposes 500 Crore Penalty on Bank", source)[0] is False


def test_entity_grounding_rejects_generic_placeholders():
    """Ensure generic placeholders like Market Entity, Antitrust Authority are rejected."""
    source = "Reserve Bank of India penalises bank"
    assert validate_entity_grounding("RBI Imposes 100 Crore Penalty on Market Entity", source)[0] is False
    assert validate_entity_grounding("Antitrust Authority Orders Operational Adjustments", source)[0] is False
    assert validate_entity_grounding("Target Enterprise Faces Regulatory Sanction", source)[0] is False
    assert validate_entity_grounding("Regulator Penalizes Standard Chartered Bank", source)[0] is True


def test_archetype5_no_market_entity_fabrication():
    """Archetype 5 must extract the real entity or fall back without inventing Market Entity."""
    ev = Event(
        canonical_title="RBI imposes Rs 100 crore penalty on standard chartered bank for regulatory lapse",
        summary_bullets=["Penalty imposed"],
        description="RBI penalty event on bank",
        category=NewsCategory.INDIA,
    )
    art = Article(
        title="RBI imposes Rs 100 crore penalty on standard chartered bank for regulatory lapse",
        body_text="The Reserve Bank of India has penalised Standard Chartered Bank Rs 100 crore.",
        lead_paragraph="The Reserve Bank of India has penalised Standard Chartered Bank.",
        source_name="Livemint",
        url="https://livemint.com/rbi-penalty",
    )
    hl = synthesize_investment_headline(ev.canonical_title, event=ev, article=art)
    assert "Market Entity" not in hl
    assert "Antitrust Authority" not in hl
    assert "100b" not in hl
    # Should contain Chartered or Bank or Standard
    has_overlap, count, _ = calculate_semantic_token_overlap(hl, ev.canonical_title)
    assert has_overlap is True
    assert count >= 1


def test_safety_ladder_level_1_level_2_level_3():
    """Test the 3-level safety ladder in generate_grounded_fallback_headline."""
    ev = Event(
        canonical_title="Infosys signs $1.5 billion deal with global enterprise client",
        summary_bullets=["Deal signed"],
        description="Infosys enterprise deal",
        category=NewsCategory.INDIA,
    )
    art = Article(
        title="Infosys signs $1.5 billion deal with global enterprise client",
        body_text="Infosys on Monday announced an enterprise deal valued at $1.5 billion.",
        lead_paragraph="Infosys announced a major contract.",
        source_name="Economic Times",
        url="https://economictimes.indiatimes.com/infosys-deal",
    )

    # Level 1 or 2: synthesize investment headline should be grounded
    hl = generate_grounded_fallback_headline(event=ev, article=art)
    assert "Infosys" in hl
    has_ov, count, _ = calculate_semantic_token_overlap(hl, ev.canonical_title)
    assert has_ov is True

    # If candidate headline is completely ungrounded, it falls back to Level 2 or Level 3
    bad_candidate = "Market Entity Achieves Unrelated Transformation Across Sectors"
    hl2 = generate_grounded_fallback_headline(event=ev, article=art, candidate_headline=bad_candidate)
    has_ov2, count2, _ = calculate_semantic_token_overlap(hl2, ev.canonical_title)
    assert has_ov2 is True
    assert "Market Entity" not in hl2


def test_level_3_reverts_to_clean_canonical_when_synthesis_diverges():
    """When both candidate and synthesis lack overlap, level 3 must cleanly extract canonical."""
    raw = "Adani Ports acquires 80% stake in Astro Offshore for $185 million"
    ev = Event(
        canonical_title=raw,
        summary_bullets=["Acquisition details"],
        description="Adani acquisition",
        category=NewsCategory.INDIA,
    )
    art = Article(
        title=raw,
        body_text="Adani Ports and Special Economic Zone has acquired Astro Offshore.",
        lead_paragraph="Adani Ports acquisition.",
        source_name="Business Standard",
        url="https://business-standard.com/adani-astro",
    )
    hl = generate_grounded_fallback_headline(event=ev, article=art)
    has_ov, count, tokens = calculate_semantic_token_overlap(hl, raw)
    assert has_ov is True
    assert count >= 1


def test_check_6_not_weakened_rejects_zero_overlap():
    """Verify that Check #6 still strictly fails if headline has 0 semantic overlap with source text."""
    ev = Event(
        canonical_title="TCS signs multi-year digital transformation partnership with European bank",
        summary_bullets=["IT deal"],
        description="TCS deal",
        category=NewsCategory.INDIA,
    )
    art = Article(
        title="TCS signs multi-year digital transformation partnership with European bank",
        body_text="Tata Consultancy Services announced a major banking contract.",
        lead_paragraph="TCS banking partnership.",
        source_name="Moneycontrol",
        url="https://moneycontrol.com/tcs-deal",
    )

    # Direct calculate_semantic_token_overlap check:
    has_ov, count, tokens = calculate_semantic_token_overlap(
        "TCS Secures Multi-Year Transformation Contract with European Financial Group", art
    )
    assert has_ov is True
    assert count >= 1

    # Zero overlap must be rejected:
    has_ov_bad, count_bad, _ = calculate_semantic_token_overlap(
        "Solar Panel Manufacturer Opens Facility in Arizona Following Subsidies", art
    )
    assert has_ov_bad is False
    assert count_bad == 0


def test_editorial_offline_fallback_produces_fully_grounded_15_stories():
    """Test that _generate_offline_editorial_fallback produces 15 stories all passing Check #6."""
    def make_breakdown(score: float) -> ScoreBreakdown:
        return ScoreBreakdown(
            financial_magnitude=25.0,
            market_impact=20.0,
            investor_relevance=20.0,
            corporate_significance=15.0,
            source_quality=10.0,
            total_score=score,
        )

    articles_map = {}
    events_lookup = {}
    dom_cands = []
    for i in range(5):
        art_id = f"dom_art_{i}"
        ev_id = f"dom_ev_{i}"
        art = Article(
            id=art_id,
            title=f"Domestic Corporate Event {i}: Major Expansion in Gujarat Plant",
            body_text=f"A domestic company announced a major expansion of its Gujarat manufacturing facility numbered {i}.",
            lead_paragraph=f"Expansion announcement {i}.",
            source_name="Livemint",
            url=f"https://livemint.com/dom-{i}",
        )
        ev = Event(
            id=ev_id,
            canonical_title=f"Domestic Corporate Event {i}: Major Expansion in Gujarat Plant",
            summary_bullets=["Manufacturing expansion"],
            description="Domestic corporate event description",
            category=NewsCategory.DOMESTIC,
            article_ids=[art_id],
        )
        articles_map[art_id] = art
        events_lookup[ev_id] = ev
        dom_cands.append(ScoredEvent(event=ev, score_breakdown=make_breakdown(90.0 - i), investment_score=90.0 - i))

    india_cands = []
    for i in range(5):
        art_id = f"india_art_{i}"
        ev_id = f"india_ev_{i}"
        art = Article(
            id=art_id,
            title=f"India Macro Event {i}: RBI Announces Liquidity Measures for Banking Sector",
            body_text=f"The Reserve Bank of India introduced targeted liquidity facilities for commercial banks {i}.",
            lead_paragraph=f"RBI liquidity facility {i}.",
            source_name="Economic Times",
            url=f"https://economictimes.indiatimes.com/india-{i}",
        )
        ev = Event(
            id=ev_id,
            canonical_title=f"India Macro Event {i}: RBI Announces Liquidity Measures for Banking Sector",
            summary_bullets=["Banking liquidity"],
            description="India macro event description",
            category=NewsCategory.INDIA,
            article_ids=[art_id],
        )
        articles_map[art_id] = art
        events_lookup[ev_id] = ev
        india_cands.append(ScoredEvent(event=ev, score_breakdown=make_breakdown(85.0 - i), investment_score=85.0 - i))

    intl_cands = []
    for i in range(5):
        art_id = f"intl_art_{i}"
        ev_id = f"intl_ev_{i}"
        art = Article(
            id=art_id,
            title=f"Global Markets Event {i}: Federal Reserve Signals Policy Path on Inflation",
            body_text=f"The US Federal Reserve released its latest monetary policy statement regarding interest rates {i}.",
            lead_paragraph=f"Fed interest rate policy {i}.",
            source_name="Reuters",
            url=f"https://reuters.com/intl-{i}",
        )
        ev = Event(
            id=ev_id,
            canonical_title=f"Global Markets Event {i}: Federal Reserve Signals Policy Path on Inflation",
            summary_bullets=["US monetary policy"],
            description="Global markets event description",
            category=NewsCategory.INTERNATIONAL,
            article_ids=[art_id],
        )
        articles_map[art_id] = art
        events_lookup[ev_id] = ev
        intl_cands.append(ScoredEvent(event=ev, score_breakdown=make_breakdown(80.0 - i), investment_score=80.0 - i))

    pool = RankedCandidatePool(
        domestic_candidates=dom_cands,
        india_candidates=india_cands,
        international_candidates=intl_cands,
        total_evaluated=15,
    )

    engine = GeminiEditorialEngine()
    raw_json = engine._generate_offline_editorial_fallback(pool, articles_map)
    payload_dict = json.loads(raw_json)
    payload = BriefingEditorialPayload.model_validate(payload_dict)

    assert len(payload.domestic_stories) == 5
    assert len(payload.india_stories) == 5
    assert len(payload.international_stories) == 5

    all_stories = payload.domestic_stories + payload.india_stories + payload.international_stories
    assert len(all_stories) == 15

    for s in all_stories:
        ev = events_lookup[s.event_id]
        art = articles_map[ev.article_ids[0]]
        has_ov, count, _ = calculate_semantic_token_overlap(s.headline, art)
        assert has_ov is True, f"Headline '{s.headline}' failed semantic overlap with article"
        assert count >= 1
