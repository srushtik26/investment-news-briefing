"""
Regression tests for Briefing News-Quality Fixes.

Covers:
1. Gemini 429 quota exhaustion string detection and fallback triggering.
2. Fallback summary grounding:
   - Ashiana Housing land acquisition never emits "manufacturing facility".
   - "Spending" / "Capital spending" is never treated as a primary company entity.
   - Prohibited bare $1 capex is rejected.
   - Wendy's bankruptcy / Chapter 11 produces clean court-supervised restructuring text.
   - RBI penalty stories retain exact figures and reasons without hallucinations.
3. Domestic trending noise rejection:
   - Rejects seat-sharing, alliances, candidate disputes.
   - Rejects campus politics (DUSU, ABVP, student unions).
   - Rejects ceremonial events/yatras/courtesy calls.
   - Rejects local crime/murders/drownings.
4. India materiality gate:
   - Stories with materiality scores below 60 are never selected into final output.
5. International noise rejection:
   - Analyst stock picks ("table-pounding buy", "screaming buy", "stocks to buy right now") are rejected.
6. Institutional headline synthesis:
   - M3M land acquisition synthesizes balanced two-clause headline with city and amount.
   - Land acquisition archetype emits land development pipeline implications, not manufacturing facility.
7. Validation engine Check 20:
   - Requires non-empty summary.
   - Flags boilerplate advertisement text.
"""

from datetime import date, datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from app.models.article import Article
from app.models.event import Event
from app.models.enums import NewsCategory, VerificationTier
from app.models.entity_sanitizer import sanitize_company_entities
from app.ai.editor import GeminiEditorialEngine, generate_deterministic_summary
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.ai.headline_synthesis import (
    synthesize_investment_headline,
    validate_headline_coherence,
)
from app.verification.domestic_trending import DomesticTrendingEvaluator
from app.filtering.rules import StoryTypeFilterRule
from app.validation.engine import FinalValidationEngine
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection
from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown


def _make_article(
    aid: str,
    title: str,
    body: str,
    category: NewsCategory = NewsCategory.INDIA,
    pub_name: str = "The Economic Times",
    url: str = "https://economictimes.indiatimes.com/news/1",
) -> Article:
    return Article(
        id=aid,
        url=url,
        title=title,
        source_name=pub_name,
        category=category,
        content_text=body,
        published_at=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_event(
    eid: str,
    article: Article,
    companies=None,
    figures=None,
    category: NewsCategory = NewsCategory.INDIA,
) -> Event:
    return Event(
        id=eid,
        canonical_title=article.title,
        description=article.content_text,
        article_ids=[article.id],
        companies_involved=companies or [],
        financial_figures=figures or [],
        event_category=category,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=95.0,
        published_date=date(2026, 9, 21),
    )


# =========================================================================
# 1. GEMINI 429 DAILY QUOTA EXHAUSTION DETECTION
# =========================================================================

def _make_breakdown(score: float = 80.0) -> ScoreBreakdown:
    return ScoreBreakdown(
        financial_magnitude=score * 0.3,
        market_impact=score * 0.25,
        investor_relevance=score * 0.25,
        corporate_significance=score * 0.1,
        source_quality=score * 0.1,
        total_score=score,
    )


def test_gemini_quota_exhausted_detection_triggers_fallback():
    """Verify that GEMINI_DAILY_QUOTA_EXHAUSTED errors trigger deterministic fallback."""
    articles_map = {}
    india_cands = []
    intl_cands = []
    dom_cands = []

    for i in range(5):
        art_dom = _make_article(f"art_dom_{i}", f"Union Cabinet Approves Major Highway Corridor {i}", f"Outlay of Rs 10,000 crore for highway {i}.", category=NewsCategory.DOMESTIC)
        ev_dom = _make_event(f"ev_dom_{i}", art_dom, category=NewsCategory.DOMESTIC)
        articles_map[art_dom.id] = art_dom
        dom_cands.append(ScoredEvent(event=ev_dom, investment_score=80.0, score_breakdown=_make_breakdown(80.0)))

        art_in = _make_article(f"art_in_{i}", f"Company_In_{i} commits Rs 5000 crore capex for electric vehicle plant {i}", f"Company_In_{i} invests Rs 5,000 crore in plant {i}.")
        ev_in = _make_event(f"ev_in_{i}", art_in, companies=[f"Company_In_{i}"], figures=["Rs 5,000 crore"])
        articles_map[art_in.id] = art_in
        india_cands.append(ScoredEvent(event=ev_in, investment_score=85.0, score_breakdown=_make_breakdown(85.0)))

        art_int = _make_article(f"art_int_{i}", f"Company_Intl_{i} acquires European chip facility {i} for $2 billion", f"Company_Intl_{i} acquires chip facility {i}.", category=NewsCategory.INTERNATIONAL)
        ev_int = _make_event(f"ev_int_{i}", art_int, companies=[f"Company_Intl_{i}"], figures=["$2 billion"], category=NewsCategory.INTERNATIONAL)
        articles_map[art_int.id] = art_int
        intl_cands.append(ScoredEvent(event=ev_int, investment_score=85.0, score_breakdown=_make_breakdown(85.0)))

    ranked_pool = RankedCandidatePool(
        domestic_candidates=dom_cands,
        india_candidates=india_cands,
        international_candidates=intl_cands,
    )

    engine = GeminiEditorialEngine(api_key="test_key_fake")
    with patch.object(
        engine,
        "_call_model",
        side_effect=RuntimeError("429 RESOURCE_EXHAUSTED: GEMINI_DAILY_QUOTA_EXHAUSTED: daily limit 0 remaining"),
    ):
        res = engine.select_and_synthesize_briefing(ranked_pool, articles_map)
        assert res.success is True
        assert res.selection is not None
        assert len(res.selection.india_stories) == 5
        assert len(res.selection.international_stories) == 5


# =========================================================================
# 2. FALLBACK GROUNDING & HALLUCINATION PREVENTION
# =========================================================================

def test_ashiana_housing_land_acq_never_emits_manufacturing_facility():
    """Ashiana Housing land acquisition must emit land/residential expansion, never manufacturing."""
    body = (
        "Ashiana Housing has acquired 12 acres of prime residential land in Sector 80 Gurugram "
        "for Rs 250 crore. The developer plans to develop a premium senior living housing project."
    )
    art = _make_article("a_ashiana", "Ashiana Housing acquires 12-acre land parcel in Gurugram", body)
    ev = _make_event("e_ashiana", art, companies=["Ashiana Housing"], figures=["Rs 250 crore"])

    summary = generate_deterministic_summary(art, ev, art.title)
    assert "manufacturing facility" not in summary.lower()
    assert "manufacturing" not in summary.lower()
    assert "plant" not in summary.lower()
    assert "land" in summary.lower() or "housing" in summary.lower() or "residential" in summary.lower()

    # Also test headline synthesis
    headline = synthesize_investment_headline(art.title, event=ev, article=art)
    assert "manufacturing" not in headline.lower()
    assert "plant" not in headline.lower()


def test_spending_not_treated_as_entity_and_no_bare_dollar_one():
    """'Spending' must not be recognized as a company entity and bare $1 capex is rejected."""
    body = "Capital spending reached record levels as enterprise spending expanded by $1 across sectors."
    art = _make_article("a_spend", "Capital spending rises across tech sector", body)
    ev = _make_event("e_spend", art, companies=["Spending", "Capital Spending"], figures=["$1"])

    sanitized = sanitize_company_entities(ev.companies_involved)
    assert "Spending" not in sanitized
    assert "Capital Spending" not in sanitized

    summary = generate_deterministic_summary(art, ev, art.title)
    assert not summary.startswith("Spending ")
    assert not summary.startswith("Capital Spending ")
    assert "$1 Capital Expenditure" not in summary
    assert "$1 capital expenditure" not in summary


def test_wendys_bankruptcy_clean_restructuring():
    """Wendy's franchise bankruptcy must produce factual court-supervised restructuring text."""
    body = (
        "Wendy's franchisee Starboard Group filed for Chapter 11 bankruptcy protection in federal court "
        "to restructure $50 million in funded debt while continuing restaurant operations."
    )
    art = _make_article("a_wendy", "Wendy's franchisee files for Chapter 11 bankruptcy", body, category=NewsCategory.INTERNATIONAL)
    ev = _make_event("e_wendy", art, companies=["Starboard Group", "Wendy's"], category=NewsCategory.INTERNATIONAL)

    summary = generate_deterministic_summary(art, ev, art.title)
    assert "chapter 11" in summary.lower() or "restructuring" in summary.lower() or "bankruptcy" in summary.lower()
    assert not summary.endswith(" under")
    assert "files chapter 11 bankruptcy to" not in summary.lower()

    headline = synthesize_investment_headline(art.title, event=ev, article=art)
    is_coh, _ = validate_headline_coherence(headline, event=ev, article=art)
    assert is_coh is True


def test_rbi_penalty_preserves_figures_without_hallucinated_executive():
    """RBI penalty stories retain exact penalty figures and reasons without hallucinated CEO changes."""
    body = (
        "The Reserve Bank of India has imposed a monetary penalty of ₹1.5 crore on XYZ Bank "
        "for non-compliance with statutory directions on Know Your Customer (KYC) norms."
    )
    art = _make_article("a_rbi", "RBI imposes ₹1.5 crore penalty on XYZ Bank", body)
    ev = _make_event("e_rbi", art, companies=["XYZ Bank", "Reserve Bank of India"], figures=["₹1.5 crore"])

    summary = generate_deterministic_summary(art, ev, art.title)
    assert "₹1.5 crore" in summary or "1.5 crore" in summary
    assert "regulatory order" in summary.lower() or "compliance" in summary.lower() or "enforcement" in summary.lower()
    assert "resigns" not in summary.lower()
    assert "steps down" not in summary.lower()


# =========================================================================
# 3. DOMESTIC TRENDING NOISE REJECTION
# =========================================================================

def test_domestic_trending_rejects_political_alliances_and_campus_politics():
    """Domestic evaluator must reject seat-sharing, campus elections, ceremonial yatras, and local crime."""
    evaluator = DomesticTrendingEvaluator()

    # 1. Political alliances / seat-sharing
    art_seat = _make_article(
        "a_seat",
        "Congress and AAP in final seat-sharing talks for assembly polls: sources",
        "Party leaders met to discuss alliance formula, candidate list, and seat distribution in Punjab.",
        category=NewsCategory.DOMESTIC,
    )
    ev_seat = _make_event("e_seat", art_seat, category=NewsCategory.DOMESTIC)
    is_elig, score, reason = evaluator.evaluate(event=ev_seat, primary_article=art_seat)
    assert is_elig is False

    # 2. Campus politics
    art_campus = _make_article(
        "a_campus",
        "ABVP and NSUI clash ahead of DUSU student union elections",
        "Delhi University student union election campaigns witnessed heated sloganeering between rival student wings.",
        category=NewsCategory.DOMESTIC,
    )
    ev_campus = _make_event("e_campus", art_campus, category=NewsCategory.DOMESTIC)
    is_elig, score, reason = evaluator.evaluate(event=ev_campus, primary_article=art_campus)
    assert is_elig is False

    # 3. Ceremonial visits / yatras
    art_yatra = _make_article(
        "a_yatra",
        "Opposition leader pays courtesy call, flags off religious yatra in Haridwar",
        "Leaders offered prayers at temples and addressed a religious gathering before beginning padayatra.",
        category=NewsCategory.DOMESTIC,
    )
    ev_yatra = _make_event("e_yatra", art_yatra, category=NewsCategory.DOMESTIC)
    is_elig, score, reason = evaluator.evaluate(event=ev_yatra, primary_article=art_yatra)
    assert is_elig is False

    # 4. Local crime / murder / drowning
    art_crime = _make_article(
        "a_crime",
        "Two youth drown in lake; police register accidental death report",
        "Local police retrieved bodies from the reservoir and sent them for post-mortem analysis.",
        category=NewsCategory.DOMESTIC,
    )
    ev_crime = _make_event("e_crime", art_crime, category=NewsCategory.DOMESTIC)
    is_elig, score, reason = evaluator.evaluate(event=ev_crime, primary_article=art_crime)
    assert is_elig is False


# =========================================================================
# 4. INDIA MATERIALITY GATE (< 60.0 MUST NOT BE SELECTED)
# =========================================================================

def test_india_materiality_below_60_rejected_from_selection():
    """Stories with materiality score < 60.0 must be rejected and never selected into the final briefing."""
    from app.verification.materiality import evaluate_investment_materiality

    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc))

    # Low materiality routine story (no figures, no strategic action)
    art_low = _make_article(
        "a_low",
        "Smallcap firm conducts routine annual vendor meet in Pune",
        "The company hosted a routine vendor gathering to review supply timelines.",
        category=NewsCategory.INDIA,
    )
    ev_low = _make_event("e_low", art_low, companies=["Smallcap Corp"])

    is_mat, mat_score, _ = evaluate_investment_materiality(ev_low, art_low, ctx=ctx)
    assert is_mat is False
    assert mat_score < 60.0

    # In selection, low materiality candidate must be filtered out
    ctx.articles_lookup = {art_low.id: art_low}
    scored = ScoredEvent(
        event=ev_low,
        score_breakdown=ScoreBreakdown(financial_magnitude=10.0, market_impact=10.0, investor_relevance=10.0,
                                       corporate_significance=10.0, source_quality=50.0, total_score=20.0),
        investment_score=20.0,
    )
    pool, dom, ind, intl, suff, status = run_ranking_and_selection(
        ctx,
        accepted_stories=[{"event_id": ev_low.id, "category": "india"}],
        event_by_id={ev_low.id: ev_low},
    )
    assert not any(s.event.id == ev_low.id for s in ind)


# =========================================================================
# 5. INTERNATIONAL NOISE REJECTION (ANALYST STOCK PICKS)
# =========================================================================

def test_international_analyst_stock_picks_rejected():
    """Analyst recommendation noise (table-pounding buy, screaming buy, top picks) must be rejected."""
    st_rule = StoryTypeFilterRule()

    art_pick = _make_article(
        "a_pick",
        "3 table-pounding buy stocks Wall Street analysts say could double your money",
        "Brokerages issue strong buy ratings on these high-growth momentum stocks for explosive portfolio gains.",
        category=NewsCategory.INTERNATIONAL,
    )
    res_pick = st_rule.evaluate(art_pick)
    assert res_pick.is_accepted is False
    assert "noise" in (res_pick.rejection_reason or "").lower() or "investment_advice" in str(res_pick.matched_patterns)

    art_screaming = _make_article(
        "a_scream",
        "This semiconductor stock is a screaming buy right now ahead of earnings",
        "Top Wall Street analysts urge investors to buy this dip for immediate upside.",
        category=NewsCategory.INTERNATIONAL,
    )
    res_scream = st_rule.evaluate(art_screaming)
    assert res_scream.is_accepted is False


# =========================================================================
# 6. INSTITUTIONAL HEADLINE SYNTHESIS (M3M LAND ACQUISITION)
# =========================================================================

def test_m3m_land_acquisition_headline_synthesis():
    """M3M land acquisition synthesizes balanced institutional headline with city and amount."""
    body = "M3M India has acquired prime commercial land in Noida for Rs 2,000 crore to construct mixed-use development."
    art = _make_article("a_m3m", "M3M acquires Noida land for record Rs 2,000 crore", body)
    ev = _make_event("e_m3m", art, companies=["M3M India"], figures=["Rs 2,000 crore"])

    headline = synthesize_investment_headline(art.title, event=ev, article=art)
    assert ";" in headline
    assert "M3M" in headline
    assert "Noida" in headline
    assert "Rs 2,000 crore" in headline or "2,000 crore" in headline
    assert "manufacturing facility" not in headline.lower()
    words = headline.split()
    assert 16 <= len(words) <= 34


# =========================================================================
# 7. VALIDATION ENGINE CHECK 20
# =========================================================================

def test_check_20_requires_non_empty_summary_and_blocks_boilerplate():
    """Check 20 must reject empty summaries and detect marketing/advertisement boilerplate."""
    validator = FinalValidationEngine()

    def _make_story(eid, hl, summary, sec="india"):
        return EditorialStorySelection(
            section=sec,
            event_id=eid,
            headline=hl,
            summary=summary,
            source="The Economic Times",
            url=f"https://example.com/{eid}",
        )

    # 1. Payload with multi-line summary fails Check 20
    dom_stories = [_make_story(f"d_{i}", f"Government Approves National Policy Plan {i}; Infrastructure Outlay Boosts Manufacturing Across States", "Factual summary of government policy approval.", "domestic") for i in range(5)]
    intl_stories = [_make_story(f"int_{i}", f"Global Tech Leader Secures $2 Billion Cloud Contract {i}; Multinational Enterprise Expands Regional Infrastructure", "Factual summary of international cloud transaction.", "international") for i in range(5)]
    ind_multiline = [
        _make_story("in_0", "Tata Motors Commits ₹12,000 Crore Capital Expenditure for Manufacturing Facility; Investment Expands Electric Vehicle Production", "First sentence of summary.\nSecond sentence on newline."),
    ] + [_make_story(f"in_{i}", f"Strategic Indian Enterprise Secures ₹5,000 Crore Contract Win {i}; Capital Outlay Scales Industrial Operations", "Factual summary of major Indian contract win.") for i in range(1, 5)]

    payload_multiline = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=ind_multiline,
        international_stories=intl_stories,
    )
    report_multiline = validator.validate_briefing(payload_multiline, events_lookup={}, articles_lookup={}, run_reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc))
    assert report_multiline.is_valid is False
    assert any(c.check_id == 20 and not c.passed and "embedded newlines" in (c.failure_reason or "").lower() for c in report_multiline.check_results)

    # 2. Payload with marketing boilerplate fails Check 20
    ind_ad = [
        _make_story("in_0", "Tata Motors Commits ₹12,000 Crore Capital Expenditure for Manufacturing Facility; Investment Expands Electric Vehicle Production", "Click here for live updates and subscribe to our newsletter for exclusive stock analysis."),
    ] + [_make_story(f"in_{i}", f"Strategic Indian Enterprise Secures ₹5,000 Crore Contract Win {i}; Capital Outlay Scales Industrial Operations", "Factual summary of major Indian contract win.") for i in range(1, 5)]

    payload_ad = BriefingEditorialPayload(
        domestic_stories=dom_stories,
        india_stories=ind_ad,
        international_stories=intl_stories,
    )
    report_ad = validator.validate_briefing(payload_ad, events_lookup={}, articles_lookup={}, run_reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc))
    assert report_ad.is_valid is False
    assert any(c.check_id == 20 and not c.passed and "boilerplate" in (c.failure_reason or "").lower() for c in report_ad.check_results)


def test_land_acquisition_tightened_routing():
    """Verify tightened land-acquisition regex: explicit signals pass, non-acquisition discussions rejected."""
    from app.ai.headline_synthesis import is_land_acquisition_signal
    assert is_land_acquisition_signal("M3M acquires Noida land for Rs 2,000 crore") is True
    assert is_land_acquisition_signal("Ashiana Housing buys Gurugram land parcel") is True
    assert is_land_acquisition_signal("government land policy debate") is False
    assert is_land_acquisition_signal("wetland conservation project") is False
    assert is_land_acquisition_signal("agricultural land reform discussion") is False
    assert is_land_acquisition_signal("M3M acquires 73-acre Noida land") is True

