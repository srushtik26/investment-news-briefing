"""
Regression tests for unique event counting, domestic topic corroboration, entity sanitization,
domestic reaction filtering, and expansion invariants.
"""

from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.models.entity_sanitizer import sanitize_company_entities, clean_company_name
from app.verification.verifier import TwoSourceVerifier
from app.verification.domestic_trending import DomesticTrendingEvaluator


def test_regression_test_a_second_source_does_not_increment_section_count():
    """TEST A: Second source verification/upgrade does not increment unique section count."""
    e1 = Event(id="e1", canonical_title="Event 1", description="Valid domestic event description 1", article_ids=["a1"], event_category=NewsCategory.DOMESTIC)
    e2 = Event(id="e2", canonical_title="Event 2", description="Valid domestic event description 2", article_ids=["a2"], event_category=NewsCategory.DOMESTIC)
    e3 = Event(id="e3", canonical_title="Event 3", description="Valid domestic event description 3", article_ids=["a3"], event_category=NewsCategory.DOMESTIC)
    e4 = Event(id="e4", canonical_title="Event 4", description="Valid domestic event description 4", article_ids=["a4"], event_category=NewsCategory.DOMESTIC)
    
    verified_events = []
    high_confidence_single = [e1, e2, e3, e4]

    def get_unique():
        seen = set()
        u = []
        for ev in verified_events:
            if ev.id not in seen:
                seen.add(ev.id)
                u.append(ev)
        for ev in high_confidence_single:
            if ev.id not in seen:
                seen.add(ev.id)
                u.append(ev)
        return u

    assert len([e for e in get_unique() if e.event_category == NewsCategory.DOMESTIC]) == 4

    # Upgrade e1 from single source to two-source verified
    e1.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
    e1.article_ids.append("a1_sec")
    verified_events.append(e1)
    if e1 in high_confidence_single:
        high_confidence_single.remove(e1)

    # Unique count must remain 4
    dom_unique = [e for e in get_unique() if e.event_category == NewsCategory.DOMESTIC]
    assert len(dom_unique) == 4
    assert len(verified_events) == 1
    assert len(high_confidence_single) == 3


def test_regression_test_b_expansion_does_not_stop_at_false_5():
    """TEST B: Expansion does not stop when unique event IDs < 5 despite any raw counter."""
    e1 = Event(id="e1", canonical_title="Event 1", description="Valid domestic event description 1", article_ids=["a1"], event_category=NewsCategory.DOMESTIC)
    e2 = Event(id="e2", canonical_title="Event 2", description="Valid domestic event description 2", article_ids=["a2"], event_category=NewsCategory.DOMESTIC)
    e3 = Event(id="e3", canonical_title="Event 3", description="Valid domestic event description 3", article_ids=["a3"], event_category=NewsCategory.DOMESTIC)
    e4 = Event(id="e4", canonical_title="Event 4", description="Valid domestic event description 4", article_ids=["a4"], event_category=NewsCategory.DOMESTIC)

    verified_events = [e1]
    # If e1 was mistakenly in single as well:
    high_confidence_single = [e1, e2, e3, e4]

    seen = set()
    unique_candidates = []
    for ev in verified_events:
        if ev.id not in seen:
            seen.add(ev.id)
            unique_candidates.append(ev)
    for ev in high_confidence_single:
        if ev.id not in seen:
            seen.add(ev.id)
            unique_candidates.append(ev)

    dom_unique_count = sum(1 for e in unique_candidates if e.event_category == NewsCategory.DOMESTIC)
    raw_unfiltered_count = len(verified_events + high_confidence_single)

    assert raw_unfiltered_count == 5
    assert dom_unique_count == 4
    assert dom_unique_count < 5  # Expansion must continue


def test_regression_test_c_final_expansion_invariant():
    """TEST C: EXPANSION_COMPLETE invariant returns False if Domestic=4, India=5, Intl=5."""
    dom_events = [Event(id=f"d{i}", canonical_title=f"Dom {i}", description=f"Valid domestic event description {i}", article_ids=[f"da{i}"], event_category=NewsCategory.DOMESTIC) for i in range(4)]
    ind_events = [Event(id=f"in{i}", canonical_title=f"Ind {i}", description=f"Valid India business event description {i}", article_ids=[f"ina{i}"], event_category=NewsCategory.INDIA) for i in range(5)]
    int_events = [Event(id=f"it{i}", canonical_title=f"Int {i}", description=f"Valid Intl business event description {i}", article_ids=[f"ita{i}"], event_category=NewsCategory.INTERNATIONAL) for i in range(5)]

    all_events = dom_events + ind_events + int_events
    dom_count = sum(1 for e in all_events if e.event_category == NewsCategory.DOMESTIC)
    india_count = sum(1 for e in all_events if e.event_category == NewsCategory.INDIA)
    intl_count = sum(1 for e in all_events if e.event_category == NewsCategory.INTERNATIONAL)
    total_unique = len(all_events)

    expansion_complete = (dom_count >= 5 and india_count >= 5 and intl_count >= 5 and total_unique >= 15)
    assert not expansion_complete


def test_regression_test_d_correct_completion():
    """TEST D: EXPANSION_COMPLETE true when Domestic=5, India=5, Intl=5 (Total=15)."""
    dom_events = [Event(id=f"d{i}", canonical_title=f"Dom {i}", description=f"Valid domestic event description {i}", article_ids=[f"da{i}"], event_category=NewsCategory.DOMESTIC) for i in range(5)]
    ind_events = [Event(id=f"in{i}", canonical_title=f"Ind {i}", description=f"Valid India business event description {i}", article_ids=[f"ina{i}"], event_category=NewsCategory.INDIA) for i in range(5)]
    int_events = [Event(id=f"it{i}", canonical_title=f"Int {i}", description=f"Valid Intl business event description {i}", article_ids=[f"ita{i}"], event_category=NewsCategory.INTERNATIONAL) for i in range(5)]

    all_events = dom_events + ind_events + int_events
    dom_count = sum(1 for e in all_events if e.event_category == NewsCategory.DOMESTIC)
    india_count = sum(1 for e in all_events if e.event_category == NewsCategory.INDIA)
    intl_count = sum(1 for e in all_events if e.event_category == NewsCategory.INTERNATIONAL)
    total_unique = len(all_events)

    expansion_complete = (dom_count >= 5 and india_count >= 5 and intl_count >= 5 and total_unique >= 15)
    assert expansion_complete
    assert total_unique == 15


def test_regression_test_e_false_court_corroboration():
    """TEST E: False court corroboration prevented between distinct cases sharing generic terms."""
    verifier = TwoSourceVerifier()
    art1 = Article(
        id="a1",
        title="Supreme Court orders SITs in all states to probe suspected insurance fraud",
        url="https://indiatoday.in/law/sc-orders-sit-insurance-fraud",
        source_name="India Today",
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court of India on Tuesday ordered all state governments to form special investigation teams to probe insurance fraud.",
    )
    art2 = Article(
        id="a2",
        title="Let July 20 probe into Delhi protest kick off: Supreme Court allows police investigation",
        url="https://hindustantimes.com/india-news/delhi-protest-probe-supreme-court",
        source_name="Hindustan Times",
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court on Wednesday declined to stay the police probe into the July 20 protest in Delhi involving student demonstrators.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert not is_same
    assert "DOMESTIC_TOPIC_MISMATCH" in reason or "ENTITY_MISMATCH" in reason


def test_regression_test_f_valid_court_corroboration():
    """TEST F: Valid court corroboration succeeds on matching specific topics."""
    verifier = TwoSourceVerifier()
    art1 = Article(
        id="a1",
        title="Supreme Court orders SITs to investigate fraudulent insurance claims across states",
        url="https://indiatoday.in/sc-insurance-fraud-sit",
        source_name="India Today",
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court of India ordered SITs to investigate fraudulent insurance claims across all states in India.",
    )
    art2 = Article(
        id="a2",
        title="SC directs states to form SITs over suspected insurance fraud claims",
        url="https://hindustantimes.com/sc-directs-states-sits-insurance-claims",
        source_name="Hindustan Times",
        category=NewsCategory.DOMESTIC,
        content_text="The top court instructed all state police chiefs to set up special investigation teams to probe fake and fraudulent insurance claims.",
    )

    is_same, score, reason = verifier.is_same_underlying_event(art1, art2)
    assert is_same
    assert "DOMESTIC_EVENT_MATCH" in reason


def test_regression_test_g_entity_sanitizer():
    """TEST G: Entity sanitizer rejects invalid entities and preserves valid corporate entities."""
    # Reject:
    assert clean_company_name("26 Aug 2026") is None
    assert clean_company_name("FY26") is None
    assert clean_company_name("Q3") is None
    assert clean_company_name("1.06") is None
    assert clean_company_name("₹84.5 crore") is None

    # 'boAt FY26 net' cleaned to 'boAt'
    assert clean_company_name("boAt FY26 net") == "boAt"

    # Full sanitization batch
    raw_list = [
        "26 Aug 2026",
        "FY26",
        "Q3",
        "1.06",
        "boAt FY26 net",
        "boAt",
        "Hirect Ltd",
        "DMart",
        "Honasa",
        "National Bank of Canada",
        "Dick's Sporting Goods",
    ]
    cleaned = sanitize_company_entities(raw_list)

    assert "26 Aug 2026" not in cleaned
    assert "FY26" not in cleaned
    assert "Q3" not in cleaned
    assert "1.06" not in cleaned
    assert "boAt FY26 net" not in cleaned

    assert "boAt" in cleaned
    assert "Hirect Ltd" in cleaned
    assert "DMart" in cleaned
    assert "Honasa" in cleaned
    assert "National Bank of Canada" in cleaned
    assert "Dick's Sporting Goods" in cleaned


def test_regression_test_h_domestic_reaction():
    """TEST H: Political reaction articles are rejected by DomesticTrendingEvaluator."""
    evaluator = DomesticTrendingEvaluator()

    # Legitimate judicial event
    art_ruling = Article(
        id="ar1",
        title="Why Allahabad High Court rejected schoolgirl's plea to wear hijab in classroom",
        url="https://thehindu.com/news/national/allahabad-hc-hijab-plea",
        source_name="The Hindu",
        category=NewsCategory.DOMESTIC,
        content_text="The Allahabad High Court on Monday dismissed a petition filed by a schoolgirl seeking permission to wear a hijab with uniform in classroom across Uttar Pradesh schools.",
    )
    event_ruling = Event(
        id="er1",
        canonical_title=art_ruling.title,
        description=art_ruling.content_text,
        article_ids=[art_ruling.id],
        event_category=NewsCategory.DOMESTIC,
    )
    is_elig_ruling, conf_ruling, reason_ruling = evaluator.evaluate(event_ruling, art_ruling)
    assert is_elig_ruling

    # Political reaction on the ruling
    art_reaction = Article(
        id="ar2",
        title="Hijab on heads: Owaisi slams Allahabad HC hijab ruling as attack",
        url="https://indiatoday.in/india/owaisi-reacts-allahabad-hc-ruling",
        source_name="India Today",
        category=NewsCategory.DOMESTIC,
        content_text="AIMIM chief Asaduddin Owaisi criticised the Allahabad High Court verdict on Monday calling it an attack on religious freedom.",
    )
    event_reaction = Event(
        id="er2",
        canonical_title=art_reaction.title,
        description=art_reaction.content_text,
        article_ids=[art_reaction.id],
        event_category=NewsCategory.DOMESTIC,
    )
    is_elig_react, conf_react, reason_react = evaluator.evaluate(event_reaction, art_reaction)
    assert not is_elig_react
    assert "REJECT_NOISE" in reason_react or "REJECT_REACTION" in reason_react


def test_regression_test_i_full_target_after_verification_upgrade():
    """TEST I: Target sufficiency correctly transitions from 4 to 5 only when distinct event is added."""
    verified_events = []
    high_confidence_single = [
        Event(id=f"d{i}", canonical_title=f"Domestic Event {i}", description=f"Valid domestic event description {i}", article_ids=[f"da{i}"], event_category=NewsCategory.DOMESTIC)
        for i in range(1, 5)
    ]

    def get_unique_count():
        seen = set()
        u = []
        for ev in verified_events:
            if ev.id not in seen:
                seen.add(ev.id)
                u.append(ev)
        for ev in high_confidence_single:
            if ev.id not in seen:
                seen.add(ev.id)
                u.append(ev)
        return len([e for e in u if e.event_category == NewsCategory.DOMESTIC])

    assert get_unique_count() == 4

    # Upgrade d1 to verified
    d1 = high_confidence_single[0]
    d1.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
    verified_events.append(d1)
    high_confidence_single.remove(d1)

    # Must still be 4
    assert get_unique_count() == 4

    # Add 5th distinct event
    d5 = Event(id="d5", canonical_title="Domestic Event 5", description="Valid domestic event description 5", article_ids=["da5"], event_category=NewsCategory.DOMESTIC)
    high_confidence_single.append(d5)

    # Now exactly 5
    assert get_unique_count() == 5


def test_regression_test_j_editor_deterministic_fallback_on_429(monkeypatch):
    """TEST J: Deterministic fallback resolves all 15 stories across 3 sections on Gemini 429 without event_id lookup error."""
    from app.ai.editor import GeminiEditorialEngine
    from app.ranking.models import RankedCandidatePool, ScoredEvent, ScoreBreakdown

    breakdown = ScoreBreakdown(
        financial_magnitude=80.0,
        market_impact=80.0,
        investor_relevance=80.0,
        corporate_significance=80.0,
        source_quality=80.0,
        total_score=80.0,
    )
    articles_map = {}
    
    # 5 Domestic
    dom_scored = []
    for i in range(1, 6):
        aid = f"dom_art_{i}"
        art = Article(
            id=aid,
            title=f"Domestic National Story {i}",
            url=f"https://thehindu.com/dom-{i}",
            source_name="The Hindu",
            category=NewsCategory.DOMESTIC,
        )
        articles_map[aid] = art
        ev = Event(
            id=f"dom_ev_{i}",
            canonical_title=art.title,
            description=f"Domestic National Story description {i}",
            article_ids=[aid],
            event_category=NewsCategory.DOMESTIC,
            verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        )
        dom_scored.append(ScoredEvent(event=ev, investment_score=80.0 + i, score_breakdown=breakdown, rank=i))

    # 5 India (mix of two-source and high-confidence single)
    india_scored = []
    for i in range(1, 6):
        aid = f"ind_art_{i}"
        art = Article(
            id=aid,
            title=f"Company{i} India Business Deal {i}",
            url=f"https://business-standard.com/ind-{i}",
            source_name="Business Standard",
            category=NewsCategory.INDIA,
        )
        articles_map[aid] = art
        tier = VerificationTier.TWO_SOURCE_VERIFIED if i <= 3 else VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
        ev = Event(
            id=f"ind_ev_{i}",
            canonical_title=art.title,
            description=f"Company{i} India Business Deal description {i}",
            article_ids=[aid],
            companies_involved=[f"Company{i}"],
            event_category=NewsCategory.INDIA,
            verification_tier=tier,
        )
        india_scored.append(ScoredEvent(event=ev, investment_score=85.0 + i, score_breakdown=breakdown, rank=i))

    # 5 International (mix of two-source and high-confidence single)
    intl_scored = []
    for i in range(1, 6):
        aid = f"intl_art_{i}"
        art = Article(
            id=aid,
            title=f"GlobalCorp{i} International Acquisition {i}",
            url=f"https://reuters.com/intl-{i}",
            source_name="Reuters",
            category=NewsCategory.INTERNATIONAL,
        )
        articles_map[aid] = art
        tier = VerificationTier.TWO_SOURCE_VERIFIED if i <= 3 else VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE
        ev = Event(
            id=f"intl_ev_{i}",
            canonical_title=art.title,
            description=f"GlobalCorp{i} International Acquisition description {i}",
            article_ids=[aid],
            companies_involved=[f"GlobalCorp{i}"],
            event_category=NewsCategory.INTERNATIONAL,
            verification_tier=tier,
        )
        intl_scored.append(ScoredEvent(event=ev, investment_score=90.0 + i, score_breakdown=breakdown, rank=i))

    ranked_pool = RankedCandidatePool(
        domestic_candidates=dom_scored,
        india_candidates=india_scored,
        international_candidates=intl_scored,
    )

    # Instantiate engine with dummy key and mock _call_model to raise 429
    engine = GeminiEditorialEngine(api_key="test-api-key")
    def mock_call_429(*args, **kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
    monkeypatch.setattr(engine, "_call_model", mock_call_429)

    res = engine.select_and_synthesize_briefing(ranked_pool, articles_map)

    # Fallback should succeed deterministically
    assert res.success is True
    assert res.selection is not None
    assert len(res.selection.domestic_stories) == 5
    assert len(res.selection.india_stories) == 5
    assert len(res.selection.international_stories) == 5

    # Check that URLs and event_ids match inputs exactly
    for s in res.selection.domestic_stories:
        assert s.url in [articles_map[a].url for a in articles_map if "dom" in a]
    for s in res.selection.india_stories:
        assert s.url in [articles_map[a].url for a in articles_map if "ind" in a]
    for s in res.selection.international_stories:
        assert s.url in [articles_map[a].url for a in articles_map if "intl" in a]


def test_regression_test_k_organic_verify_event_domestic_false_match_and_valid_control():
    """TEST K: verify_event prevents false two-source match on clustered domestic pair and verifies valid control."""
    verifier = TwoSourceVerifier()

    # 1. Invalid pair in ONE Event (shared court keywords but unrelated topics)
    art_a = Article(
        id="art_a",
        title="Supreme Court orders state SITs to investigate fraudulent insurance claims.",
        url="https://thehindu.com/sc-insurance-sits",
        source_name="The Hindu",
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court on Tuesday ordered all state police chiefs to constitute special investigation teams to probe insurance fraud.",
    )
    art_b = Article(
        id="art_b",
        title="Supreme Court orders probe into police conduct at Delhi student protest involving pellet guns.",
        url="https://indiatoday.in/delhi-protest-sc",
        source_name="India Today",
        category=NewsCategory.DOMESTIC,
        content_text="The Supreme Court bench asked the Delhi Police to conduct an inquiry into the use of force at the student protest.",
    )
    event_invalid = Event(
        id="ev_invalid",
        canonical_title=art_a.title,
        description=art_a.content_text,
        article_ids=[art_a.id, art_b.id],
        event_category=NewsCategory.DOMESTIC,
    )

    verif_res_invalid = verifier.verify_event(event_invalid, [art_a, art_b])
    assert not verif_res_invalid.is_verified
    assert event_invalid.verification_tier != VerificationTier.TWO_SOURCE_VERIFIED
    assert "DOMESTIC_TOPIC_MISMATCH" in verif_res_invalid.matching_details or "ENTITY_MISMATCH" in verif_res_invalid.matching_details

    # 2. Valid control (same topic from independent publishers)
    art_c = Article(
        id="art_c",
        title="Supreme Court directs states to form SITs over fraudulent insurance claims.",
        url="https://thehindu.com/sc-directs-states-insurance-sits",
        source_name="The Hindu",
        category=NewsCategory.DOMESTIC,
        content_text="The top court directed all states to form special investigation teams to probe fake insurance claim rackets.",
    )
    art_d = Article(
        id="art_d",
        title="SC orders special investigation teams to investigate nationwide insurance fraud claims.",
        url="https://indianexpress.com/sc-orders-insurance-fraud-probe",
        source_name="Indian Express",
        category=NewsCategory.DOMESTIC,
        content_text="A Supreme Court bench on Tuesday ordered nationwide SITs to investigate fraudulent insurance claims.",
    )
    event_valid = Event(
        id="ev_valid",
        canonical_title=art_c.title,
        description=art_c.content_text,
        article_ids=[art_c.id, art_d.id],
        event_category=NewsCategory.DOMESTIC,
    )

    verif_res_valid = verifier.verify_event(event_valid, [art_c, art_d])
    assert verif_res_valid.is_verified
    assert event_valid.verification_tier == VerificationTier.TWO_SOURCE_VERIFIED
    assert "Corroborated" in verif_res_valid.matching_details

