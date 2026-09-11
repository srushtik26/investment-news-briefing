"""
Regression Tests: Portfolio Watchlist Score Boost.

Proves:
1.  Each of the 27 watchlist companies triggers is_watchlist_company().
2.  A non-watchlist company does NOT trigger the boost.
3.  Case-insensitivity works.
4.  Common short-name / ticker aliases work (SBI, BEL, NTPC, BHEL, SAIL, CIL).
5.  The watchlist_boost label appears in ScoreBreakdown.rationale.
6.  Materiality thresholds are unchanged (no regression).
7.  The bonus cap (35 pts) is still enforced when watchlist bonus stacks with other bonuses.
8.  Watchlist boost does NOT change is_accepted filter decisions.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.models.enums import NewsCategory, VerificationTier
from app.models.event import Event
from app.ranking.scorer import InvestmentRelevanceScorer
from app.ranking.watchlist import PORTFOLIO_WATCHLIST, is_watchlist_company


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(title: str, description: str = "") -> Event:
    ev = MagicMock(spec=Event)
    ev.canonical_title = title
    ev.description = description
    ev.financial_figures = []
    ev.event_category = NewsCategory.INDIA
    ev.verification_tier = VerificationTier.TWO_SOURCE_VERIFIED
    ev.metadata = {}
    return ev


def _score(title: str, description: str = "") -> float:
    scorer = InvestmentRelevanceScorer()
    ev = _make_event(title, description)
    result = scorer.score_event(ev, source_count=2, is_multi_source_verified=True)
    return result.investment_score


def _rationale(title: str, description: str = "") -> str:
    scorer = InvestmentRelevanceScorer()
    ev = _make_event(title, description)
    result = scorer.score_event(ev, source_count=2, is_multi_source_verified=True)
    return result.score_breakdown.rationale or ""


# ---------------------------------------------------------------------------
# 1. is_watchlist_company: all 27 companies match
# ---------------------------------------------------------------------------

# (test_name, text_to_match)
WATCHLIST_MATCH_CASES = [
    ("Accelya Solutions India",      "Accelya Solutions India reports strong Q2 revenue"),
    ("BASF India",                   "BASF India opens new plant in Gujarat"),
    ("Bharat Electronics",           "Bharat Electronics Ltd wins defence radar order"),
    ("BEL alias",                    "BEL secures Rs 1,200 crore radar system contract"),
    ("Bharat Heavy Electricals",     "Bharat Heavy Electricals bags NTPC turbine order"),
    ("BHEL alias",                   "BHEL wins 800 MW thermal power project"),
    ("Bombay Dyeing",                "Bombay Dyeing & Manufacturing reports FY26 results"),
    ("Cartrade Tech",                "Cartrade acquires stake in online vehicle platform"),
    ("Choice International",         "Choice International launches NCD issue worth Rs 200 crore"),
    ("Coal India",                   "Coal India increases e-auction quota to meet demand"),
    ("CIL alias",                    "CIL declares interim dividend of Rs 5 per share"),
    ("Cohance Lifesciences",         "Cohance Lifesciences files DRHP for IPO"),
    ("Eternal Ltd",                  "Eternal Ltd expands quick commerce network"),
    ("Granules India",               "Granules India receives USFDA clearance for paracetamol plant"),
    ("Harsha Engineers",             "Harsha Engineers wins automotive component export order"),
    ("Infosys",                      "Infosys bags $1.5 billion deal with European insurance group"),
    ("Kaya Ltd",                     "Kaya Ltd reports 18% revenue growth in Q3"),
    ("Nazara Technologies",          "Nazara acquires gaming studio Nodwin for Rs 300 crore"),
    ("NTPC",                         "NTPC commissions 500 MW solar project in Rajasthan"),
    ("Rategain Travel Technologies", "Rategain raises $50 million from Softbank for global expansion"),
    ("State Bank of India",          "State Bank of India cuts home loan rates by 25 bps"),
    ("SBI alias",                    "SBI posts record quarterly profit of Rs 18,000 crore"),
    ("Steel Authority of India",     "Steel Authority of India signs MoU for green steel"),
    ("SAIL alias",                   "SAIL bags Rs 900 crore rail supply order from Railways"),
    ("Sumitomo Chemical India",      "Sumitomo Chemical India launches new agrochemical range"),
    ("Tata Consumer Products",       "Tata Consumer Products acquires Capital Foods for Rs 5,100 crore"),
    ("Tega Industries",              "Tega Industries wins mining consumables order from Africa"),
    ("Tilaknagar Industries",        "Tilaknagar Industries posts 35% jump in IMFL volumes"),
    ("Titan Company",                "Titan Company reports 22% revenue growth in watches segment"),
    ("Trent Ltd",                    "Trent Ltd opens 50 new Zudio stores in Q2"),
    ("Vedanta",                      "Vedanta demerger plan receives NCLT approval"),
    ("Windsor Machines",             "Windsor Machines secures Rs 75 crore CNC machine order"),
    ("Ashok Leyland",                "Ashok Leyland bags order for 1,200 electric buses"),
    ("Ashok Leyland Ltd",            "Ashok Leyland Ltd secures military vehicle contract"),
    ("Adani Group",                  "Adani Group announces Rs 20,000 crore capex"),
    ("Adani Enterprises",            "Adani Enterprises commissions copper smelter in Gujarat"),
    ("Adani Ports",                  "Adani Ports bags Rs 2,000 crore order"),
    ("Adani Power",                  "Adani Power signs PPA for 1600 MW capacity"),
    ("Adani Energy Solutions",       "Adani Energy Solutions wins transmission line project"),
    ("Adani Green Energy",           "Adani Green Energy adds 1000 MW operational portfolio"),
    ("Adani Total Gas",              "Adani Total Gas expands CNG network across 5 cities"),
    ("Ambuja Cements",               "Ambuja Cements acquires Penna Cement for Rs 10,422 crore"),
    ("ACC",                          "ACC commissions new grinding unit in UP"),
]


@pytest.mark.parametrize("label,text", WATCHLIST_MATCH_CASES)
def test_is_watchlist_company_matches(label: str, text: str):
    matched, name = is_watchlist_company(text)
    assert matched, f"[{label}] Expected match for: '{text}', got no match"
    assert name != "", f"[{label}] Matched but returned empty name"


# ---------------------------------------------------------------------------
# 2. Non-watchlist company does NOT match
# ---------------------------------------------------------------------------

NON_WATCHLIST_CASES = [
    "Reliance Industries reports quarterly earnings",
    "TCS wins $500 million contract from US bank",
    "Wipro acquires UK cybersecurity firm",
    "HDFC Bank cuts savings rate by 10 bps",
    "Bajaj Finance raises NCD issue worth Rs 1,000 crore",
    "Zomato reports first annual profit",
    "Larsen & Toubro bags Rs 4,000 crore hydrocarbon order",
    "Maruti Suzuki posts record monthly sales",
]


@pytest.mark.parametrize("text", NON_WATCHLIST_CASES)
def test_is_watchlist_company_no_false_positives(text: str):
    matched, name = is_watchlist_company(text)
    assert not matched, f"Unexpected watchlist match for non-watchlist text: '{text}' -> '{name}'"


# ---------------------------------------------------------------------------
# 3. Case-insensitivity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "INFOSYS bags deal",
    "infosys bags deal",
    "Infosys bags deal",
    "inFoSyS bags deal",
    "ntpc commissions project",
    "NTPC commissions project",
    "sbi cuts rates",
    "SBI cuts rates",
])
def test_is_watchlist_company_case_insensitive(text: str):
    matched, _ = is_watchlist_company(text)
    assert matched, f"Expected case-insensitive match for: '{text}'"


# ---------------------------------------------------------------------------
# 4. Word-boundary safety (no false positives from substring matches)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,should_match", [
    # "trent" is a watchlist company but must NOT match inside other words
    ("The current trend in retail is bullish", False),
    ("Trent Ltd opens new Zudio store", True),
    # "titan" must NOT match "Titanic" or "titanium"
    ("Titanium prices rise on supply squeeze", False),
    ("Titan Company Q2 revenue jumps 22%", True),
    # "sail" must NOT match "sailing" or "sales"
    ("Retail sales hit record high in festive season", False),
    ("SAIL signs green steel MoU", True),
    # "bil" / "cil" must not match inside other words
    ("Civilian aviation sector sees rebound", False),
    ("CIL declares dividend", True),
])
def test_word_boundary_safety(text: str, should_match: bool):
    matched, name = is_watchlist_company(text)
    if should_match:
        assert matched, f"Expected match for '{text}'"
    else:
        assert not matched, f"Unexpected match ('{name}') for '{text}'"


# ---------------------------------------------------------------------------
# 5. Scorer emits watchlist_boost label in rationale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected_company", [
    ("Infosys bags $1.5 billion AI deal", "Infosys"),
    ("NTPC commissions 500 MW solar plant", "NTPC"),
    ("SBI cuts home loan rates", "State Bank of India"),
    ("Vedanta demerger gets NCLT nod", "Vedanta"),
    ("Coal India declares Rs 5 dividend", "Coal India"),
])
def test_scorer_rationale_contains_watchlist_boost(title: str, expected_company: str):
    rat = _rationale(title)
    assert "watchlist_boost" in rat, (
        f"Expected 'watchlist_boost' in rationale for '{title}'\nGot: {rat}"
    )
    assert expected_company in rat, (
        f"Expected company name '{expected_company}' in rationale\nGot: {rat}"
    )


# ---------------------------------------------------------------------------
# 6. Non-watchlist story does NOT get watchlist_boost in rationale
# ---------------------------------------------------------------------------

def test_scorer_non_watchlist_no_boost_in_rationale():
    rat = _rationale("Reliance Industries posts record Q2 profit")
    assert "watchlist_boost" not in rat, (
        f"Unexpected 'watchlist_boost' in rationale for non-watchlist story\nGot: {rat}"
    )


# ---------------------------------------------------------------------------
# 7. Watchlist story scores HIGHER than identical non-watchlist story
# ---------------------------------------------------------------------------

def test_watchlist_story_scores_higher_than_non_watchlist():
    """Same event text, only company name differs — watchlist version must score higher."""
    non_wl_score = _score("Reliance Industries bags $500 million deal from European bank")
    wl_score = _score("Infosys bags $500 million deal from European bank")
    assert wl_score > non_wl_score, (
        f"Watchlist story score ({wl_score}) should exceed non-watchlist ({non_wl_score})"
    )


# ---------------------------------------------------------------------------
# 8. Bonus cap (35 pts) is still enforced
# ---------------------------------------------------------------------------

def test_bonus_cap_enforced_with_watchlist():
    """
    A story that already triggers M&A (+20) + capex (+20) = 40 would exceed the cap.
    Adding the watchlist boost (+10) must not push beyond the cap of 35.
    """
    scorer = InvestmentRelevanceScorer()
    # Craft text triggering M&A + capex + watchlist simultaneously
    title = (
        "Infosys acquires 100% stake in AI startup via all-cash deal "
        "and invests Rs 2,000 crore in new manufacturing facility"
    )
    ev = _make_event(title)
    result = scorer.score_event(ev, source_count=2, is_multi_source_verified=True)
    # The bonus in score_breakdown.strategic_bonuses must not exceed 35
    assert result.score_breakdown.strategic_bonuses <= 35.0, (
        f"Strategic bonus exceeded cap: {result.score_breakdown.strategic_bonuses}"
    )


# ---------------------------------------------------------------------------
# 9. All 32 watchlist entries are present in PORTFOLIO_WATCHLIST
# ---------------------------------------------------------------------------

def test_portfolio_watchlist_has_32_entries():
    assert len(PORTFOLIO_WATCHLIST) == 32, (
        f"Expected 32 watchlist entries, got {len(PORTFOLIO_WATCHLIST)}"
    )


# ---------------------------------------------------------------------------
# 10. Materiality threshold unchanged (watchlist boost does not affect acceptance)
# ---------------------------------------------------------------------------

def test_watchlist_boost_does_not_bypass_materiality():
    """
    The watchlist boost lives only in the scorer (post-acceptance).
    It must NOT be visible in, or change the behaviour of, evaluate_investment_materiality.
    """
    from app.verification.materiality import evaluate_investment_materiality
    from app.models import Article, Event, NewsCategory
    from app.models.enums import VerificationTier
    from datetime import datetime, timezone

    # A deliberately weak headline about a watchlist company
    art = Article(
        id="art_low_mat",
        title="Infosys holds annual general meeting",
        url="https://example.com/art1",
        source_name="Moneycontrol",
        category=NewsCategory.INDIA,
        content_text="Shareholders attended the annual general meeting in Bengaluru today.",
        published_at=datetime.now(timezone.utc),
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    ev = Event(
        id="ev_low_mat",
        canonical_title=art.title,
        description=art.content_text,
        category=NewsCategory.INDIA,
        articles=[art],
        first_published_at=datetime.now(timezone.utc),
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        confidence_score=75.0,
    )
    is_mat, score, reason = evaluate_investment_materiality(ev, art)
    # AGM headlines are low-materiality regardless of company name
    assert score < 60.0, (
        f"Materiality check unexpectedly raised score to {score} for '{art.title}'"
    )


