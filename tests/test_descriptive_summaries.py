"""
Comprehensive tests for descriptive Investment Committee summaries:
1. repeated headline summary gets replaced.
2. M&A summary includes parties + deal structure + implication.
3. capex summary includes value/capacity/purpose.
4. regulatory summary includes authority + decision + affected companies/sector.
5. no invented numbers.
6. summary 35-55 words under normal conditions.
7. max <=65 words.
8. no extra Gemini call count.
"""

from datetime import datetime, timezone
import re
from typing import Optional
import pytest

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.ai.editor import GeminiEditorialEngine, generate_deterministic_summary
from app.ai.models import (
    BriefingEditorialPayload,
    EditorialStorySelection,
)
from app.ai.summary_grounding import (
    is_summary_substantially_identical_to_headline,
    build_descriptive_investment_summary,
    select_grounded_summary_sentence,
    clamp_summary_length,
)
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown


def test_1_repeated_headline_summary_gets_replaced():
    """Test 1: Repeated headline summary gets detected and replaced by descriptive fallback."""
    headline = "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80"
    repeated_summary = "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80."

    # 1. Verify duplicate detector catches exact match + punctuation
    assert is_summary_substantially_identical_to_headline(repeated_summary, headline) is True
    assert is_summary_substantially_identical_to_headline(headline, headline) is True
    assert is_summary_substantially_identical_to_headline(headline + " (BSE)", headline) is True

    # 2. Generate replacement summary
    art = Article(
        url="https://www.bseindia.com/corporates/results-complete-sports.html",
        title=headline,
        source_name="BSE",
        content_text=headline,
    )
    ev = Event(
        id="evt-csml",
        canonical_title=headline,
        description=headline,
        companies_involved=["Complete Sports and Management India Limited"],
        financial_figures=["157.80"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-csml"],
    )

    new_summary = generate_deterministic_summary(art, ev, headline=headline)

    # 3. Verify replacement summary is descriptive and NOT duplicate
    assert not is_summary_substantially_identical_to_headline(new_summary, headline)
    assert "Complete Sports and Management India Limited" in new_summary
    assert "157.80" in new_summary
    # Explains what happened (quarterly financial results)
    assert "quarterly financial results" in new_summary.lower()
    # Explains scale/magnitude (trading shares at 157.80)
    assert "157.80" in new_summary
    # Explains why it matters for investors
    assert "institutional investors" in new_summary.lower() or "margin" in new_summary.lower()
    # Word count bounds
    words = new_summary.split()
    assert 35 <= len(words) <= 55
    assert len(words) <= 65
    assert new_summary.endswith(".")


def test_2_m_and_a_summary_includes_parties_structure_and_implication():
    """Test 2: M&A summary includes parties, deal structure/value, and strategic implication."""
    headline = "Rio Tinto to acquire Arcadium Lithium for $6.7 billion"
    ev = Event(
        id="evt-rio",
        canonical_title=headline,
        description="Rio Tinto agreed to acquire Arcadium Lithium for $6.7 billion in cash.",
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
        financial_figures=["$6.7 billion"],
        event_category=NewsCategory.INTERNATIONAL,
        article_ids=["art-rio"],
    )
    art = Article(
        url="https://www.reuters.com/business/rio-tinto-arcadium.html",
        title=headline,
        source_name="Reuters",
        content_text="Rio Tinto agreed to acquire Arcadium Lithium for $6.7 billion to expand lithium mining assets.",
    )

    summary = generate_deterministic_summary(art, ev, headline=headline)

    # Parties
    assert "Rio Tinto" in summary
    assert "Arcadium Lithium" in summary
    # Deal structure and value
    assert "$6.7 billion" in summary
    assert "acquire" in summary.lower() or "acquisition" in summary.lower() or "transaction" in summary.lower()
    # Strategic implication
    assert "positioning" in summary.lower() or "consolidates" in summary.lower() or "presence" in summary.lower() or "expansion" in summary.lower()
    # Word count bounds
    words = summary.split()
    assert 35 <= len(words) <= 55
    assert len(words) <= 65
    assert summary.endswith(".")


def test_3_capex_summary_includes_value_capacity_and_purpose():
    """Test 3: Capex summary includes value, capacity, and operational purpose."""
    headline = "Tata Power commits ₹5,000 crore investment to build 500 MW renewable facility"
    ev = Event(
        id="evt-tp",
        canonical_title=headline,
        description="Tata Power commits ₹5,000 crore to construct a 500 MW renewable power plant in Gujarat.",
        companies_involved=["Tata Power"],
        financial_figures=["₹5,000 crore", "500 MW"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-tp"],
    )
    art = Article(
        url="https://www.business-standard.com/tata-power-capex.html",
        title=headline,
        source_name="Business Standard",
        content_text="Tata Power announced capital expenditure of ₹5,000 crore to develop 500 MW clean generation capacity.",
    )

    summary = generate_deterministic_summary(art, ev, headline=headline)

    # Value
    assert "₹5,000 crore" in summary
    # Capacity
    assert "500 MW" in summary
    # Purpose & Implication
    assert "manufacturing" in summary.lower() or "facility" in summary.lower() or "capacity" in summary.lower()
    assert "demand" in summary.lower() or "resilience" in summary.lower() or "economics" in summary.lower()
    # Word count bounds
    words = summary.split()
    assert 35 <= len(words) <= 55
    assert len(words) <= 65
    assert summary.endswith(".")


def test_4_regulatory_summary_includes_authority_decision_and_affected_sector():
    """Test 4: Regulatory summary includes authority, decision, and affected companies/sector."""
    headline = "Competition Commission of India issues penalty notice to digital platform operators"
    ev = Event(
        id="evt-cci",
        canonical_title=headline,
        description="CCI issued penalty against major digital platform companies over anti-competitive practices.",
        companies_involved=["digital platform operators"],
        financial_figures=["₹213 crore"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-cci"],
    )
    art = Article(
        url="https://www.thehindu.com/business/cci-antitrust.html",
        title=headline,
        source_name="The Hindu",
        content_text="The Competition Commission of India has imposed penalty on digital platform operators for anti-competitive agreements.",
    )

    summary = generate_deterministic_summary(art, ev, headline=headline)

    # Authority
    assert "Competition Commission of India" in summary or "CCI" in summary
    # Decision
    assert "regulatory order" in summary.lower() or "enforcement" in summary.lower() or "penalty" in summary.lower()
    # Affected sector / governance implication
    assert "compliance" in summary.lower() or "governance" in summary.lower() or "precedents" in summary.lower()
    # Word count bounds
    words = summary.split()
    assert 35 <= len(words) <= 55
    assert len(words) <= 65
    assert summary.endswith(".")


def test_5_no_invented_numbers():
    """Test 5: Summary must NOT invent or hallucinate any numbers when none exist in source."""
    headline = "Tata Consumer expands distribution network across southern regional markets"
    art = Article(
        url="https://www.business-standard.com/tata-consumer-expansion.html",
        title=headline,
        source_name="Business Standard",
        content_text="Tata Consumer announced strategic expansion of its distribution network across southern regional markets to deepen retail reach.",
    )
    ev = Event(
        id="evt-tc",
        canonical_title=headline,
        description="Tata Consumer expands retail footprint across southern markets.",
        companies_involved=["Tata Consumer"],
        financial_figures=[],  # Zero figures provided
        event_category=NewsCategory.INDIA,
        article_ids=["art-tc"],
    )

    summary = generate_deterministic_summary(art, ev, headline=headline)

    # Check that no digits exist in the summary
    digit_matches = re.findall(r"\d+", summary)
    assert digit_matches == [], f"Invented digits found in summary: {digit_matches}"

    # Verify summary is still descriptive and institutional
    words = summary.split()
    assert 35 <= len(words) <= 55
    assert len(words) <= 65
    assert "Tata Consumer" in summary
    assert summary.endswith(".")


def test_6_summary_word_count_target_35_to_55_words_under_normal_conditions():
    """Test 6: All synthesized summaries fall within 35-55 words under normal conditions."""
    test_cases = [
        (
            "L&T bags ₹2,400 crore offshore contract from ONGC",
            ["L&T"],
            ["₹2,400 crore"],
        ),
        (
            "Maruti Suzuki to invest ₹3,200 crore to expand Gujarat manufacturing plant",
            ["Maruti Suzuki"],
            ["₹3,200 crore"],
        ),
        (
            "HDFC Bank Q2 Net Profit rises 17% YoY to ₹16,820 crore",
            ["HDFC Bank"],
            ["17%", "₹16,820 crore"],
        ),
        (
            "SEBI issues strict disclosure directives for mutual fund asset valuations",
            ["SEBI"],
            [],
        ),
    ]

    for title, comps, figs in test_cases:
        ev = Event(
            id=f"evt-{title[:10]}",
            canonical_title=title,
            description=title,
            companies_involved=comps,
            financial_figures=figs,
            event_category=NewsCategory.INDIA,
            article_ids=[],
        )
        summary = generate_deterministic_summary(article=None, event=ev, headline=title)
        words = summary.split()
        assert 35 <= len(words) <= 55, f"Summary for '{title}' had {len(words)} words, outside 35-55: '{summary}'"
        assert summary.endswith(".")


def test_7_max_words_hard_ceiling_at_65():
    """Test 7: Summary strictly respects hard ceiling of 65 words and ends cleanly."""
    verbose_text = (
        "Reliance Industries announced an unprecedented nationwide expansion program spanning twenty distinct manufacturing facilities "
        "across western India in an effort to accelerate supply chain sovereignty, optimize heavy industrial output, and position domestic "
        "energy networks for sustainable multi-decade industrial transition amidst complex global macroeconomic developments and geopolitical tensions "
        "that have significantly altered cross-border capital reallocation patterns across multiple international institutional asset management portfolios worldwide."
    )
    clamped = clamp_summary_length(verbose_text, max_words=65)
    words = clamped.split()
    assert len(words) <= 65
    assert clamped.endswith(".")
    # Must not end in incomplete grammatical token
    last_word = re.sub(r"[^\w]", "", words[-1]).lower()
    assert last_word not in {"and", "or", "the", "a", "in", "with", "of", "to", "for", "by"}


def test_8_no_extra_gemini_call_count():
    """Test 8: Summaries are repaired/generated without adding any extra Gemini API calls."""
    # Initialize editorial engine with offline mode (api_key="")
    engine = GeminiEditorialEngine(api_key="")
    assert engine._client is None  # Offline mode guaranteed, 0 API calls possible

    headline = "Complete Sports and Management India Limited Quarterly Results, 08 Sept 2026 - BSE 157.80"
    ev = Event(
        id="evt-csml-test",
        canonical_title=headline,
        description=headline,
        companies_involved=["Complete Sports and Management India Limited"],
        financial_figures=["157.80"],
        event_category=NewsCategory.INDIA,
        article_ids=["art-csml-test"],
    )
    art = Article(
        id="art-csml-test",
        url="https://www.bseindia.com/corporates/results-complete-sports.html",
        title=headline,
        source_name="BSE",
        content_text=headline,
    )

    breakdown = ScoreBreakdown(
        financial_magnitude=75.0,
        market_impact=75.0,
        investor_relevance=75.0,
        corporate_significance=75.0,
        source_quality=80.0,
        total_score=75.0,
    )
    scored = ScoredEvent(
        event=ev,
        article=art,
        score=75.0,
        composite_score=75.0,
        investment_score=75.0,
        score_breakdown=breakdown,
    )
    ranked_pool = RankedCandidatePool(
        india_candidates=[scored] * 5,
        international_candidates=[scored] * 5,
        domestic_candidates=[scored] * 5,
    )
    articles_map = {"art-csml-test": art}

    # Execute editorial selection in offline mode
    result = engine.select_and_synthesize_briefing(ranked_pool, articles_map)
    assert result.success is True
    assert result.attempts == 1

    # Verify that every story in payload has a valid descriptive summary (35-55 words, max 65)
    payload = result.selection
    assert payload is not None
    for story in payload.india_stories:
        assert not is_summary_substantially_identical_to_headline(story.summary, story.headline)
        words = story.summary.split()
        assert 35 <= len(words) <= 55
        assert len(words) <= 65
        assert story.summary.endswith(".")
