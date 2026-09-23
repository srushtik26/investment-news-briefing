"""
Targeted regression tests for Cognex / RealSense false NSE match and Stage 7 audit accounting:
1. RealSense false NSE match ("Cognex to Acquire RealSense" does NOT match NSE).
2. Nasdaq international acquisition (Cognex acquires RealSense, Nasdaq mentioned, no India nexus -> INTERNATIONAL).
3. Actual NSE story ("National Stock Exchange IPO..." -> INDIA).
4. Real NSE token boundary ("NSE IPO" -> match; "RealSense" -> no match).
5. Final audit consistency (canonical classifier = INTERNATIONAL, India nexus = false -> story remains INTERNATIONAL).
6. Genuine International -> India correction (Indian company / NSE / SEBI event mistakenly in International -> moved to INDIA and International refilled from qualified reserve).
7. Final counts (Start: India=5, International=5 -> Expected: India=5, International=5 without drop).
"""
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from app.models import Article, Event, NewsCategory
from app.models.enums import VerificationTier
from app.classification.region_classifier import EventRegionClassifier, verify_india_business_nexus
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection
from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool


def _mk_scored(eid: str, title: str, content: str, cat: NewsCategory, pub: str = "Reuters", comp: str = ""):
    art = Article(
        id=f"art_{eid}",
        url=f"https://example.com/{eid}",
        title=title,
        source_name=pub,
        category=cat,
        content_text=content,
        published_at=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    ev = Event(
        id=eid,
        canonical_title=title,
        description=content,
        article_ids=[art.id],
        companies_involved=[comp] if comp else [],
        event_category=cat,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=92.0,
    )
    s = ScoredEvent(
        event=ev,
        score_breakdown=ScoreBreakdown(
            financial_magnitude=20.0,
            market_impact=20.0,
            investor_relevance=20.0,
            corporate_significance=15.0,
            source_quality=10.0,
            strategic_bonuses=0.0,
            editorial_signals=0.0,
            relevance_penalties=0.0,
            total_score=85.0,
            rationale="test",
        ),
        investment_score=85.0,
        rank=1,
    )
    return s, art, ev


# ---------------------------------------------------------------------------
# TEST 1: RealSense false NSE match
# ---------------------------------------------------------------------------
def test_realsense_false_nse_match():
    """Text 'Cognex to Acquire RealSense' must NOT match NSE or capital market infrastructure."""
    text = "Cognex to Acquire RealSense"
    for pat in EventRegionClassifier.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE:
        assert re.search(pat, text, re.IGNORECASE) is None

    assert re.search(r"\bnse\b", text, re.IGNORECASE) is None


# ---------------------------------------------------------------------------
# TEST 2: Nasdaq international acquisition
# ---------------------------------------------------------------------------
def test_nasdaq_international_acquisition():
    """Cognex acquires RealSense with Nasdaq context has no India nexus and is INTERNATIONAL."""
    clf = EventRegionClassifier()
    title = "Cognex to Acquire RealSense, Expanding Machine Vision Leadership into High-Growth Robotic Perception Market"
    body = "Cognex Corporation (NASDAQ: CGNX) today announced an agreement to acquire RealSense from Intel Corporation."
    _, art, ev = _mk_scored("ev_cgnx", title, body, NewsCategory.INTERNATIONAL, "Reuters", "Cognex")

    # 1. verify_india_business_nexus must be False
    is_nexus, reason = clf.verify_india_business_nexus(ev, art)
    assert is_nexus is False
    assert "D:indian_transaction" not in reason

    # 2. Canonical classifier must classify as INTERNATIONAL
    full_text = f"{title} {body[:500]}"
    cat, cat_reason = clf.classify_with_reason(full_text)
    assert cat == NewsCategory.INTERNATIONAL


# ---------------------------------------------------------------------------
# TEST 3: Actual NSE story
# ---------------------------------------------------------------------------
def test_actual_nse_story():
    """'National Stock Exchange IPO...' must classify as INDIA."""
    clf = EventRegionClassifier()
    title = "National Stock Exchange IPO: SEBI Clears Draft Red Herring Prospectus"
    body = "The National Stock Exchange of India (NSE) has received regulatory approval for its upcoming public listing."
    _, art, ev = _mk_scored("ev_nse", title, body, NewsCategory.INDIA, "Business Standard", "NSE")

    is_nexus, reason = clf.verify_india_business_nexus(ev, art)
    assert is_nexus is True

    cat, cat_reason = clf.classify_with_reason(title)
    assert cat == NewsCategory.INDIA
    assert "Indian capital markets infrastructure" in cat_reason


# ---------------------------------------------------------------------------
# TEST 4: Real NSE token boundary
# ---------------------------------------------------------------------------
def test_real_nse_token_boundary():
    """'NSE IPO' matches India capital-markets; 'RealSense' does NOT match NSE."""
    pat_cm = EventRegionClassifier.INDIAN_CAPITAL_MARKETS_INFRASTRUCTURE[0]

    # 'NSE IPO' must match
    assert re.search(pat_cm, "NSE IPO", re.IGNORECASE) is not None
    assert re.search(r"\bnse\b", "NSE IPO", re.IGNORECASE) is not None

    # 'RealSense' must NOT match
    assert re.search(pat_cm, "RealSense", re.IGNORECASE) is None
    assert re.search(r"\bnse\b", "RealSense", re.IGNORECASE) is None


# ---------------------------------------------------------------------------
# TEST 5: Final audit consistency
# ---------------------------------------------------------------------------
def test_final_audit_consistency_international_remains_international():
    """Canonical classifier = INTERNATIONAL and India nexus = False -> story remains INTERNATIONAL."""
    # 5 Dom
    dom_s, dom_arts, dom_evs = [], {}, {}
    dom_titles = [
        ("Union Cabinet approves major national highways development plan across India", "Modi Cabinet clears major national highways development plan across India."),
        ("Supreme Court delivers historic verdict on constitutional electoral law", "Supreme Court bench rules on parliamentary election reforms and national democratic policy."),
        ("Rajasthan Urban Local Body polls results: BJP secures 4 of 10 municipal corporations across state", "Jaipur: Rajasthan election results announced for municipal corporations with Prime Minister Narendra Modi welcoming the democratic outcome across the state."),
        ("The Politics Of Blue: Akhilesh Yadav and Mayawati Dalit outreach in Uttar Pradesh election campaign", "Lucknow: Samajwadi Party chief Akhilesh Yadav ramps up UP election outreach as Parliament discusses national political implications."),
        ("Parliament monsoon session passes landmark criminal law amendments", "Indian Parliament clears legislative bills on national public policy and justice reforms."),
    ]
    pubs = ["The Hindu", "Indian Express", "Times of India", "Hindustan Times", "Deccan Herald"]
    for i, ((t, c), p) in enumerate(zip(dom_titles, pubs), 1):
        s, a, e = _mk_scored(f"dom_{i}", t, c, NewsCategory.DOMESTIC, p)
        dom_s.append(s); dom_arts[a.id] = a; dom_evs[e.id] = e

    # 5 India
    ind_s, ind_arts, ind_evs = [], {}, {}
    for i in range(1, 6):
        s, a, e = _mk_scored(f"ind_{i}", f"Indian firm Tata Power approves capital expenditure project {i} worth Rs 5000 crore", "Mumbai: Tata Power announces expansion in Maharashtra.", NewsCategory.INDIA, f"Pub_IN_{i}")
        ind_s.append(s); ind_arts[a.id] = a; ind_evs[e.id] = e

    # 5 Intl (including Cognex)
    intl_s, intl_arts, intl_evs = [], {}, {}
    s_cgnx, a_cgnx, e_cgnx = _mk_scored(
        "ev_cgnx",
        "Cognex to Acquire RealSense, Expanding Machine Vision Leadership into High-Growth Robotic Perception Market",
        "Cognex Corporation (NASDAQ: CGNX) today announced an agreement to acquire RealSense from Intel Corporation.",
        NewsCategory.INTERNATIONAL,
        "Reuters",
        "Cognex",
    )
    intl_s.append(s_cgnx); intl_arts[a_cgnx.id] = a_cgnx; intl_evs[e_cgnx.id] = e_cgnx
    for i in range(2, 6):
        s, a, e = _mk_scored(f"intl_{i}", f"Global market indices move as US Federal Reserve updates growth forecast {i}", "Washington: Global markets rally 2.5% on Fed statements.", NewsCategory.INTERNATIONAL, f"Pub_INTL_{i}")
        intl_s.append(s); intl_arts[a.id] = a; intl_evs[e.id] = e

    all_arts = {**dom_arts, **ind_arts, **intl_arts}
    all_evs = {**dom_evs, **ind_evs, **intl_evs}
    accepted = [{"event_id": k} for k in all_evs]

    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc))
    ctx.articles_lookup = all_arts
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=dom_s,
        india_candidates=ind_s,
        international_candidates=intl_s,
    )
    ctx.ranker = mock_ranker

    _, dom_res, in_res, intl_res, sufficient, _ = run_ranking_and_selection(ctx, accepted, all_evs)

    assert sufficient is True
    assert len(intl_res) == 5
    assert len(in_res) == 5
    assert len(dom_res) == 5

    # Cognex must remain in international
    intl_ids = [s.event.id for s in intl_res]
    assert "ev_cgnx" in intl_ids
    assert "ev_cgnx" not in [s.event.id for s in in_res]


# ---------------------------------------------------------------------------
# TEST 6: Genuine International -> India correction & safe refill
# ---------------------------------------------------------------------------
def test_genuine_international_to_india_correction_and_refill():
    """Indian company / NSE / SEBI event mistakenly in International is moved to INDIA and refilled from reserve."""
    # 5 Dom
    dom_s, dom_arts, dom_evs = [], {}, {}
    dom_titles = [
        ("Union Cabinet approves major national highways development plan across India", "Modi Cabinet clears major national highways development plan across India."),
        ("Supreme Court delivers historic verdict on constitutional electoral law", "Supreme Court bench rules on parliamentary election reforms and national democratic policy."),
        ("Rajasthan Urban Local Body polls results: BJP secures 4 of 10 municipal corporations across state", "Jaipur: Rajasthan election results announced for municipal corporations with Prime Minister Narendra Modi welcoming the democratic outcome across the state."),
        ("The Politics Of Blue: Akhilesh Yadav and Mayawati Dalit outreach in Uttar Pradesh election campaign", "Lucknow: Samajwadi Party chief Akhilesh Yadav ramps up UP election outreach as Parliament discusses national political implications."),
        ("Parliament monsoon session passes landmark criminal law amendments", "Indian Parliament clears legislative bills on national public policy and justice reforms."),
    ]
    pubs = ["The Hindu", "Indian Express", "Times of India", "Hindustan Times", "Deccan Herald"]
    for i, ((t, c), p) in enumerate(zip(dom_titles, pubs), 1):
        s, a, e = _mk_scored(f"dom_{i}", t, c, NewsCategory.DOMESTIC, p)
        dom_s.append(s); dom_arts[a.id] = a; dom_evs[e.id] = e

    # 4 India
    ind_s, ind_arts, ind_evs = [], {}, {}
    for i in range(1, 5):
        s, a, e = _mk_scored(f"ind_{i}", f"Indian firm Tata Power approves capital expenditure project {i} worth Rs 5000 crore", "Mumbai: Tata Power announces expansion in Maharashtra.", NewsCategory.INDIA, f"Pub_IN_{i}", "Tata Power")
        ind_s.append(s); ind_arts[a.id] = a; ind_evs[e.id] = e

    # 5 Intl (with NSE mistakenly categorized in International)
    intl_s, intl_arts, intl_evs = [], {}, {}
    s_nse, a_nse, e_nse = _mk_scored(
        "ev_nse",
        "National Stock Exchange IPO: SEBI Clears Draft Red Herring Prospectus for Rs 10000 crore issue",
        "The National Stock Exchange of India (NSE) receives SEBI approval for public float.",
        NewsCategory.INTERNATIONAL,
        "Reuters",
        "NSE",
    )
    intl_s.append(s_nse); intl_arts[a_nse.id] = a_nse; intl_evs[e_nse.id] = e_nse
    for i in range(2, 6):
        s, a, e = _mk_scored(f"intl_{i}", f"Global market indices move as US Federal Reserve updates growth forecast {i}", "Washington: Global markets rally 2.5% on Fed statements.", NewsCategory.INTERNATIONAL, f"Pub_INTL_{i}")
        intl_s.append(s); intl_arts[a.id] = a; intl_evs[e.id] = e

    # 1 Reserve intl
    s_res, a_res, e_res = _mk_scored(
        "ev_res",
        "Airbus Secures $5 Billion Order for 40 A350 Aircraft from Global Carrier",
        "Toulouse: Airbus inks landmark aircraft agreement.",
        NewsCategory.INTERNATIONAL,
        "Bloomberg",
    )

    all_arts = {**dom_arts, **ind_arts, **intl_arts, a_res.id: a_res}
    all_evs = {**dom_evs, **ind_evs, **intl_evs, e_res.id: e_res}
    accepted = [{"event_id": k} for k in all_evs]

    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc))
    ctx.articles_lookup = all_arts
    ctx.intl_reserve_pool = [e_res]
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=dom_s,
        india_candidates=ind_s,
        international_candidates=intl_s,
    )
    ctx.ranker = mock_ranker

    _, dom_res, in_res, intl_res, sufficient, _ = run_ranking_and_selection(ctx, accepted, all_evs)

    # Both sections must have 5
    assert len(intl_res) == 5
    assert len(in_res) == 5
    assert len(dom_res) == 5
    assert sufficient is True

    # NSE story must have moved to India
    in_ids = [s.event.id for s in in_res]
    assert "ev_nse" in in_ids

    # International must have been refilled by the reserve
    intl_ids = [s.event.id for s in intl_res]
    assert "ev_nse" not in intl_ids
    assert "ev_res" in intl_ids


# ---------------------------------------------------------------------------
# TEST 7: Final counts
# ---------------------------------------------------------------------------
def test_final_counts_preserved():
    """Start: India=5, International=5, Domestic=5 -> Expected: India=5, International=5, Domestic=5."""
    dom_s, dom_arts, dom_evs = [], {}, {}
    dom_titles = [
        ("Union Cabinet approves major national highways development plan across India", "Modi Cabinet clears major national highways development plan across India."),
        ("Supreme Court delivers historic verdict on constitutional electoral law", "Supreme Court bench rules on parliamentary election reforms and national democratic policy."),
        ("Rajasthan Urban Local Body polls results: BJP secures 4 of 10 municipal corporations across state", "Jaipur: Rajasthan election results announced for municipal corporations with Prime Minister Narendra Modi welcoming the democratic outcome across the state."),
        ("The Politics Of Blue: Akhilesh Yadav and Mayawati Dalit outreach in Uttar Pradesh election campaign", "Lucknow: Samajwadi Party chief Akhilesh Yadav ramps up UP election outreach as Parliament discusses national political implications."),
        ("Parliament monsoon session passes landmark criminal law amendments", "Indian Parliament clears legislative bills on national public policy and justice reforms."),
    ]
    pubs = ["The Hindu", "Indian Express", "Times of India", "Hindustan Times", "Deccan Herald"]
    for i, ((t, c), p) in enumerate(zip(dom_titles, pubs), 1):
        s, a, e = _mk_scored(f"dom_{i}", t, c, NewsCategory.DOMESTIC, p)
        dom_s.append(s); dom_arts[a.id] = a; dom_evs[e.id] = e

    ind_s, ind_arts, ind_evs = [], {}, {}
    for i in range(1, 6):
        s, a, e = _mk_scored(f"ind_{i}", f"Indian firm Tata Power approves capital expenditure project {i} worth Rs 5000 crore", "Mumbai: Tata Power announces expansion in Maharashtra.", NewsCategory.INDIA, f"Pub_IN_{i}")
        ind_s.append(s); ind_arts[a.id] = a; ind_evs[e.id] = e

    intl_s, intl_arts, intl_evs = [], {}, {}
    s_cgnx, a_cgnx, e_cgnx = _mk_scored(
        "ev_cgnx",
        "Cognex to Acquire RealSense, Expanding Machine Vision Leadership into High-Growth Robotic Perception Market",
        "Cognex Corporation (NASDAQ: CGNX) today announced an agreement to acquire RealSense from Intel Corporation.",
        NewsCategory.INTERNATIONAL,
        "Reuters",
        "Cognex",
    )
    intl_s.append(s_cgnx); intl_arts[a_cgnx.id] = a_cgnx; intl_evs[e_cgnx.id] = e_cgnx
    for i in range(2, 6):
        s, a, e = _mk_scored(f"intl_{i}", f"Global market indices move as US Federal Reserve updates growth forecast {i}", "Washington: Global markets rally 2.5% on Fed statements.", NewsCategory.INTERNATIONAL, f"Pub_INTL_{i}")
        intl_s.append(s); intl_arts[a.id] = a; intl_evs[e.id] = e

    all_arts = {**dom_arts, **ind_arts, **intl_arts}
    all_evs = {**dom_evs, **ind_evs, **intl_evs}
    accepted = [{"event_id": k} for k in all_evs]

    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc))
    ctx.articles_lookup = all_arts
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different")

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=dom_s,
        india_candidates=ind_s,
        international_candidates=intl_s,
    )
    ctx.ranker = mock_ranker

    _, dom_res, in_res, intl_res, sufficient, _ = run_ranking_and_selection(ctx, accepted, all_evs)

    assert len(dom_res) == 5
    assert len(in_res) == 5
    assert len(intl_res) == 5
    assert sufficient is True
