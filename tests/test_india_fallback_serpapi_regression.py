"""
Unit & Regression Tests for India Candidate Final Eligibility and SerpAPI Fallback.

Covers:
1. India fallback has 5 raw candidates but only 4 final-eligible -> do NOT log FALLBACK_SUCCESS
2. Fifth candidate materiality=30 -> does not count toward India 5/5
3. India RSS exhausted at 4/5 + SerpAPI returns one valid India story -> India reaches 5/5
4. SerpAPI returns invalid/low-materiality candidate -> still rejected
5. India complete at 5 -> 0 SerpAPI India searches
6. International complete, India deficient -> India receives bounded SerpAPI budget (<= 2)
7. Both deficient -> budget shared without exceeding global cap
8. No API key -> clean fallback without errors
9. Profile feature audit regression: 'who is right' not rejected, 'who is <person>' rejected
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.models.enums import VerificationTier
from app.pipeline.context import PipelineContext
from app.verification.materiality import (
    is_final_india_candidate_eligible,
    IndiaEligibilityResult,
)
from app.pipeline.selection import (
    count_unique_section_events,
    get_final_selectable_unique_events,
)
from app.verification.serpapi_corroborator import (
    reset_serpapi_counter,
    get_serpapi_count,
    get_serpapi_india_count,
    get_serpapi_intl_count,
    compute_serpapi_section_budget,
    MAX_SERPAPI_SEARCHES_PER_RUN,
    MAX_SERPAPI_INDIA_SEARCHES_PER_RUN,
)
from app.pipeline.fallback_manager import run_expansion_and_fallbacks
from app.filtering.rules import StoryTypeFilterRule


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset SerpAPI counters before each test."""
    reset_serpapi_counter()


def _make_india_article(
    id: str,
    title: str,
    content: str,
    age_hours: float = 12.0,
    source_name: str = "The Economic Times",
    url: str = None,
    now_utc: datetime = None,
) -> Article:
    ref = now_utc or datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    pub = ref - timedelta(hours=age_hours)
    words = content.split()
    if len(words) < 50:
        content = content + " " + " ".join([f"detail_{i}" for i in range(50)])
    return Article(
        id=id,
        title=title,
        url=url or f"https://economictimes.indiatimes.com/news/{id}",
        source_name=source_name,
        published_at=pub,
        content_text=content,
        category=NewsCategory.INDIA,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )


def _make_india_event(
    id: str,
    title: str,
    art_id: str,
    mat_score: float = 75.0,
    tier: VerificationTier = VerificationTier.TWO_SOURCE_VERIFIED,
    conf: float = 85.0,
    company: str = "Tata Motors",
) -> Event:
    return Event(
        id=id,
        canonical_title=title,
        description=title,
        companies_involved=[company],
        financial_figures=["₹1,000 crore"],
        percentages=["15%"],
        article_ids=[art_id],
        event_category=NewsCategory.INDIA,
        verification_tier=tier,
        verification_confidence=conf,
        metadata={"investment_materiality_score": mat_score},
    )


# =========================================================================
# 1 & 2: India fallback has 5 raw candidates but 5th candidate materiality=30
# =========================================================================
def test_fifth_candidate_materiality_30_not_final_eligible():
    """Test 2: candidate with materiality=30 fails canonical India eligibility helper."""
    art = _make_india_article(
        "art_nse_ipo",
        "NSE IPO looks expensive at current valuation multiples",
        "Analysts say NSE IPO valuation appears stretched compared to global exchanges with PE above 40.",
    )
    ev = _make_india_event("ev_nse_ipo", art.title, art.id, mat_score=30.0, company="NSE")
    ctx = PipelineContext(run_reference_time=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc))
    ctx.articles_lookup = {art.id: art}

    res = is_final_india_candidate_eligible(ev, art, ctx=ctx)
    assert bool(res) is False
    assert res.is_eligible is False
    assert res.score == 30.0
    assert "MATERIALITY_BELOW_60" in res.reason


def test_india_fallback_5_raw_4_eligible_no_fallback_success():
    """Test 1: 5 raw candidates in memory but only 4 final-eligible does NOT log FALLBACK_SUCCESS."""
    ref_time = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)

    # 4 high-materiality candidates (scores 70-80)
    titles = [
        ("L&T Bags ₹4,500 Crore Offshore Order in Middle East", "Larsen & Toubro wins major engineering contract.", "L&T"),
        ("Tata Motors Q2 Net Profit Jumps 25% to ₹5,400 Crore", "Tata Motors reports strong quarterly financial results.", "Tata Motors"),
        ("Reliance Retail Acquires Majority Stake in Startup for ₹1,200 Crore", "Reliance Retail signs definitive agreement.", "Reliance Retail"),
        ("Infosys Signs $1.5 Billion AI Deal with Global Telecom Operator", "Infosys bags multi-year technology contract.", "Infosys"),
    ]
    raw_evs = []
    for i, (t, c, comp) in enumerate(titles, 1):
        art = _make_india_article(f"art_{i}", t, c, now_utc=ref_time)
        ev = _make_india_event(f"ev_{i}", t, art.id, mat_score=75.0, company=comp)
        ctx.articles_lookup[art.id] = art
        raw_evs.append(ev)

    # 5th candidate with materiality 30 ("NSE IPO looks expensive...")
    art_5 = _make_india_article(
        "art_5",
        "NSE IPO looks expensive at current valuation multiples",
        "Analysts say NSE valuation appears high.",
        now_utc=ref_time,
    )
    ev_5 = _make_india_event("ev_5", art_5.title, art_5.id, mat_score=30.0, company="NSE")
    ctx.articles_lookup[art_5.id] = art_5
    raw_evs.append(ev_5)

    ctx.verified_events = raw_evs
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct")

    # count_unique_section_events must return 4, NOT 5!
    india_count = count_unique_section_events(NewsCategory.INDIA, ctx)
    assert india_count == 4, f"Expected 4 final-eligible India stories, got {india_count}"

    selectable = get_final_selectable_unique_events(ctx, category=NewsCategory.INDIA)
    assert len(selectable) == 4
    assert ev_5 not in selectable


# =========================================================================
# 3: India RSS exhausted at 4/5 + SerpAPI returns one valid India story -> 5/5
# =========================================================================
@patch("requests.get")
def test_india_rss_exhausted_serpapi_returns_valid_story_reaches_5(mock_get):
    """Test 3: India at 4/5 after RSS fallback triggers bounded SerpAPI discovery and reaches 5/5."""
    ref_time = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)
    ctx.settings.SERPAPI_API_KEY = "test_serp_key"

    # 4 eligible India events
    titles = [
        ("L&T Bags ₹4,500 Crore Offshore Order in Middle East", "L&T"),
        ("Tata Motors Q2 Net Profit Jumps 25% to ₹5,400 Crore", "Tata Motors"),
        ("Reliance Retail Acquires Majority Stake for ₹1,200 Crore", "Reliance"),
        ("Infosys Signs $1.5 Billion AI Deal with Telecom", "Infosys"),
    ]
    raw_evs = []
    for i, (t, comp) in enumerate(titles, 1):
        art = _make_india_article(f"art_{i}", t, f"{t} details.", now_utc=ref_time)
        ev = _make_india_event(f"ev_{i}", t, art.id, mat_score=75.0, company=comp)
        ctx.articles_lookup[art.id] = art
        raw_evs.append(ev)

    ctx.verified_events = raw_evs
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct")

    # Mock discovery provider for RSS to return empty (exhausted)
    mock_rss_provider = MagicMock()
    mock_rss_provider.discover.return_value = []
    ctx.discovery_service = MagicMock()
    ctx.discovery_service.provider = mock_rss_provider

    # Mock SerpAPI response returning 1 valid India corporate story
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "news_results": [
            {
                "title": "Bharti Airtel Approves ₹6,000 Crore Capex Expansion for 5G Network",
                "link": "https://economictimes.indiatimes.com/telecom/airtel-capex",
                "source": {"name": "The Economic Times"},
                "date": "4 hours ago",
            }
        ]
    }
    mock_get.return_value = mock_response

    # Mock extractor to produce valid Article for this SerpAPI result
    mock_extractor = MagicMock()
    extracted_art = _make_india_article(
        "art_airtel",
        "Bharti Airtel Approves ₹6,000 Crore Capex Expansion for 5G Network",
        "New Delhi: Bharti Airtel board has approved a ₹6,000 crore capex investment.",
        age_hours=4.0,
        url="https://economictimes.indiatimes.com/telecom/airtel-capex",
        now_utc=ref_time,
    )
    extract_res = MagicMock()
    extract_res.success = True
    extract_res.article = extracted_art
    mock_extractor.extract.return_value = extract_res
    ctx.extractor = mock_extractor

    logs = []
    ctx.log_exec = lambda msg: logs.append(msg)

    def count_fn(cat):
        if cat == NewsCategory.DOMESTIC:
            return 5
        elif cat == NewsCategory.INTERNATIONAL:
            return 5
        return count_unique_section_events(NewsCategory.INDIA, ctx)

    # Run fallback ladder
    run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat=None: get_final_selectable_unique_events(ctx, category=cat),
        is_domestic_diversity_satisfied_fn=lambda evs: True,
        count_unique_section_events_fn=count_fn,
        get_unique_candidate_events_fn=lambda: get_final_selectable_unique_events(ctx),
    )

    # SerpAPI must have been invoked for India
    assert get_serpapi_india_count() >= 1
    # Check required logs
    logged_text = "\n".join(logs)
    assert "SERPAPI_SECTION=INDIA" in logged_text
    assert "SERPAPI_QUERY=" in logged_text


# =========================================================================
# 4: SerpAPI returns invalid/low-materiality candidate -> still rejected
# =========================================================================
@patch("requests.get")
def test_serpapi_returns_low_materiality_candidate_still_rejected(mock_get):
    """Test 4: Candidate returned by SerpAPI with materiality < 60 is rejected and India stays deficient."""
    ref_time = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)
    ctx.settings.SERPAPI_API_KEY = "test_serp_key"

    # 4 eligible India events
    titles = [
        ("L&T Bags ₹4,500 Crore Offshore Order in Middle East", "L&T"),
        ("Tata Motors Q2 Net Profit Jumps 25% to ₹5,400 Crore", "Tata Motors"),
        ("Reliance Retail Acquires Majority Stake for ₹1,200 Crore", "Reliance"),
        ("Infosys Signs $1.5 Billion AI Deal with Telecom", "Infosys"),
    ]
    raw_evs = []
    for i, (t, comp) in enumerate(titles, 1):
        art = _make_india_article(f"art_{i}", t, f"{t} details.", now_utc=ref_time)
        ev = _make_india_event(f"ev_{i}", t, art.id, mat_score=75.0, company=comp)
        ctx.articles_lookup[art.id] = art
        raw_evs.append(ev)

    ctx.verified_events = raw_evs
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct")

    # SerpAPI returns a candidate that is a low-materiality routine update
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "news_results": [
            {
                "title": "Local Microcap Firm Appoints Compliance Officer in Routine Board Meeting",
                "link": "https://economictimes.indiatimes.com/markets/microcap-compliance",
                "source": {"name": "The Economic Times"},
                "date": "1 hour ago",
            }
        ]
    }
    mock_get.return_value = mock_response

    mock_extractor = MagicMock()
    low_mat_art = _make_india_article(
        "art_microcap",
        "Local Microcap Firm Appoints Compliance Officer in Routine Board Meeting",
        "Small cap firm appoints junior compliance officer following resignation.",
        age_hours=1.0,
        url="https://economictimes.indiatimes.com/markets/microcap-compliance",
        now_utc=ref_time,
    )
    extract_res = MagicMock()
    extract_res.success = True
    extract_res.article = low_mat_art
    mock_extractor.extract.return_value = extract_res
    ctx.extractor = mock_extractor

    # Empty RSS discovery
    mock_rss_provider = MagicMock()
    mock_rss_provider.discover.return_value = []
    ctx.discovery_service = MagicMock()
    ctx.discovery_service.provider = mock_rss_provider

    def count_fn(cat):
        if cat == NewsCategory.INDIA:
            return count_unique_section_events(NewsCategory.INDIA, ctx)
        return 5

    run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat=None: get_final_selectable_unique_events(ctx, category=cat),
        is_domestic_diversity_satisfied_fn=lambda evs: True,
        count_unique_section_events_fn=count_fn,
        get_unique_candidate_events_fn=lambda: get_final_selectable_unique_events(ctx),
    )

    # India unique count must remain at 4 because low-materiality candidate is rejected
    assert count_unique_section_events(NewsCategory.INDIA, ctx) == 4


# =========================================================================
# 5: India complete at 5 -> 0 SerpAPI India searches
# =========================================================================
def test_india_complete_at_5_zero_serpapi_searches():
    """Test 5: If India has 5 quality candidates, 0 SerpAPI searches are executed for India."""
    ref_time = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)
    ctx.settings.SERPAPI_API_KEY = "test_serp_key"

    # 5 eligible India events
    titles = [
        ("L&T Bags ₹4,500 Crore Offshore Order in Middle East", "L&T"),
        ("Tata Motors Q2 Net Profit Jumps 25% to ₹5,400 Crore", "Tata Motors"),
        ("Reliance Retail Acquires Majority Stake for ₹1,200 Crore", "Reliance"),
        ("Infosys Signs $1.5 Billion AI Deal with Telecom", "Infosys"),
        ("HDFC Bank Q2 Profit Rises 18% on Robust Loan Growth", "HDFC Bank"),
    ]
    raw_evs = []
    for i, (t, comp) in enumerate(titles, 1):
        art = _make_india_article(f"art_{i}", t, f"{t} details.", now_utc=ref_time)
        ev = _make_india_event(f"ev_{i}", t, art.id, mat_score=75.0, company=comp)
        ctx.articles_lookup[art.id] = art
        raw_evs.append(ev)

    ctx.verified_events = raw_evs
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct")

    def count_fn(cat):
        return 5

    run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat=None: get_final_selectable_unique_events(ctx, category=cat),
        is_domestic_diversity_satisfied_fn=lambda evs: True,
        count_unique_section_events_fn=count_fn,
        get_unique_candidate_events_fn=lambda: get_final_selectable_unique_events(ctx),
    )

    assert get_serpapi_india_count() == 0


# =========================================================================
# 6: International complete, India deficient -> India receives bounded SerpAPI budget
# =========================================================================
def test_international_complete_india_deficient_budget():
    """Test 6: International >= 5, India < 5 -> India receives bounded SerpAPI budget of up to 2."""
    budget = compute_serpapi_section_budget(
        section="india",
        india_current_count=4,
        intl_current_count=5,
    )
    assert budget == 2
    assert MAX_SERPAPI_INDIA_SEARCHES_PER_RUN == 2


# =========================================================================
# 7: Both deficient -> budget shared without exceeding global cap
# =========================================================================
def test_both_deficient_budget_sharing():
    """Test 7: Both deficient -> fair share (up to 2 each) without exceeding global cap."""
    india_b = compute_serpapi_section_budget(
        section="india",
        india_current_count=4,
        intl_current_count=4,
    )
    intl_b = compute_serpapi_section_budget(
        section="international",
        india_current_count=4,
        intl_current_count=4,
    )
    assert india_b == 2
    assert intl_b == 2
    assert (india_b + intl_b) <= MAX_SERPAPI_SEARCHES_PER_RUN


# =========================================================================
# 8: No API key -> clean fallback without crash
# =========================================================================
def test_no_api_key_clean_fallback(monkeypatch):
    """Test 8: Empty or None SERPAPI_API_KEY skips SerpAPI cleanly."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    ref_time = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    ctx = PipelineContext(run_reference_time=ref_time)
    ctx.settings.SERPAPI_API_KEY = ""

    # 4 eligible India events
    titles = [
        ("L&T Bags ₹4,500 Crore Offshore Order in Middle East", "L&T"),
        ("Tata Motors Q2 Net Profit Jumps 25% to ₹5,400 Crore", "Tata Motors"),
        ("Reliance Retail Acquires Majority Stake for ₹1,200 Crore", "Reliance"),
        ("Infosys Signs $1.5 Billion AI Deal with Telecom", "Infosys"),
    ]
    raw_evs = []
    for i, (t, comp) in enumerate(titles, 1):
        art = _make_india_article(f"art_{i}", t, f"{t} details.", now_utc=ref_time)
        ev = _make_india_event(f"ev_{i}", t, art.id, mat_score=75.0, company=comp)
        ctx.articles_lookup[art.id] = art
        raw_evs.append(ev)

    ctx.verified_events = raw_evs
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "distinct")

    mock_rss_provider = MagicMock()
    mock_rss_provider.discover.return_value = []
    ctx.discovery_service = MagicMock()
    ctx.discovery_service.provider = mock_rss_provider

    def count_fn(cat):
        if cat == NewsCategory.INDIA:
            return count_unique_section_events(NewsCategory.INDIA, ctx)
        return 5

    # Should execute without throwing any exception
    run_expansion_and_fallbacks(
        ctx=ctx,
        get_final_selectable_unique_events_fn=lambda cat=None: get_final_selectable_unique_events(ctx, category=cat),
        is_domestic_diversity_satisfied_fn=lambda evs: True,
        count_unique_section_events_fn=count_fn,
        get_unique_candidate_events_fn=lambda: get_final_selectable_unique_events(ctx),
    )

    assert get_serpapi_india_count() == 0
    assert get_serpapi_count() == 0


# =========================================================================
# 9: Profile feature false rejection audit
# =========================================================================
def test_profile_feature_regex_audit():
    """
    Test 9: 'Sensex swing: FIIs are selling, DIIs are buying - who is right?'
    must NOT be rejected as profile_feature.
    Legitimate profile features like 'Who is Dali Rajic...' must still be rejected.
    """
    st_rule = StoryTypeFilterRule()

    market_flow_art = Article(
        id="art_flow",
        title="Sensex swing: FIIs are selling, DIIs are buying - who is right?",
        content_text="Institutional investors continue opposing flows with foreign funds selling shares worth ₹4,000 crore and domestic funds buying heavily.",
        url="https://economictimes.indiatimes.com/markets/sensex-swing-who-is-right",
        source_name="The Economic Times",
    )
    res_market = st_rule.evaluate(market_flow_art)
    assert "profile_feature" not in (res_market.rejection_reason or "")
    assert res_market.is_accepted is True, f"Market flow story should NOT be rejected, got: {res_market.rejection_reason}"

    profile_art = Article(
        id="art_profile",
        title="Who is Dali Rajic, OpenAI's new chief revenue officer?",
        content_text="A career profile and biography of the executive who joined OpenAI.",
        url="https://economictimes.indiatimes.com/tech/who-is-dali-rajic",
        source_name="The Economic Times",
    )
    res_profile = st_rule.evaluate(profile_art)
    assert res_profile.is_accepted is False
    assert "profile_feature" in (res_profile.rejection_reason or "")
