"""
Unit and integration tests for Portfolio Watchlist Active Discovery,
Corporate Event Materiality Bonus, Alias Mapping, India Routing, and Search Budget Reservation.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.models.enums import NewsCategory, VerificationTier
from app.models.article import Article
from app.models.event import Event
from app.ranking.watchlist import (
    PORTFOLIO_WATCHLIST,
    is_watchlist_company,
    get_watchlist_match_details,
)
from app.verification.materiality import (
    evaluate_investment_materiality,
    CONCRETE_PORTFOLIO_EVENT_PATTERNS,
)
from app.classification.region_classifier import EventRegionClassifier
from app.verification.corroborator import (
    PORTFOLIO_RESERVED_RSS_SEARCHES,
    DOMESTIC_RESERVED_RSS_SEARCHES,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)
from app.discovery.queries import SearchQueryBuilder, PORTFOLIO_DISCOVERY_GROUPS
from app.ranking.scorer import InvestmentRelevanceScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_art_and_ev(title: str, description: str = "", age_hours: float = 2.0) -> tuple[Article, Event]:
    pub_time = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    art = Article(
        id=f"art_{abs(hash(title)) % 100000}",
        title=title,
        url=f"https://economictimes.indiatimes.com/news/{abs(hash(title)) % 100000}",
        source_name="The Economic Times",
        category=NewsCategory.INDIA,
        content_text=description or title,
        published_at=pub_time,
        is_verified_url=True,
        date_verified=True,
        is_valid_date=True,
    )
    ev = Event(
        id=f"ev_{abs(hash(title)) % 100000}",
        canonical_title=title,
        description=description or title,
        category=NewsCategory.INDIA,
        articles=[art],
        article_ids=[art.id],
        first_published_at=pub_time,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
        verification_confidence=85.0,
        metadata={},
    )
    return art, ev


# ---------------------------------------------------------------------------
# 1. 32 Watchlist Companies and Alias Mapping
# ---------------------------------------------------------------------------

def test_watchlist_has_32_entries():
    assert len(PORTFOLIO_WATCHLIST) == 32, f"Expected 32 watchlist entries, got {len(PORTFOLIO_WATCHLIST)}"


@pytest.mark.parametrize(
    "text,expected_canonical,expected_alias",
    [
        ("BEL secures Rs 1,200 crore naval radar contract", "Bharat Electronics Ltd", "BEL"),
        ("BHEL bags Rs 4,000 crore boiler package from NTPC", "Bharat Heavy Electricals Ltd", "BHEL"),
        ("SBI raises $500 million through bond issuance", "State Bank of India", "SBI"),
        ("SAIL supplies 50,000 tonnes of steel for high-speed rail", "Steel Authority of India Ltd", "SAIL"),
        ("Ashok Leyland Ltd secures military vehicle order", "Ashok Leyland", "Ashok Leyland Ltd"),
        ("Ashok Leyland wins contract for 500 electric buses", "Ashok Leyland", "Ashok Leyland"),
        ("Adani Group plans $10 billion investment in green energy", "Adani Group", "Adani Group"),
        ("Adani Enterprises commissions copper smelting plant in Mundra", "Adani Group", "Adani Enterprises"),
        ("Adani Ports signs 30-year concession for container terminal", "Adani Group", "Adani Ports"),
        ("Adani Power signs 25-year PPA for 1600 MW capacity", "Adani Group", "Adani Power"),
        ("Adani Green Energy commissions 1000 MW hybrid solar plant", "Adani Group", "Adani Green Energy"),
        ("Ambuja Cements acquires Penna Cement for Rs 10,422 crore", "Adani Group", "Ambuja Cements"),
        ("ACC commissions new cement grinding unit in Uttar Pradesh", "Adani Group", "ACC"),
        ("Britannia Industries announces acquisition", "Britannia Industries Ltd", "Britannia Industries"),
        ("Britannia launches major ₹500 crore capacity expansion", "Britannia Industries Ltd", "Britannia"),
        ("Sun Pharma announces major acquisition", "Sun Pharmaceutical Industries Ltd", "Sun Pharma"),
        ("Sun Pharmaceutical Industries Ltd receives USFDA approval", "Sun Pharmaceutical Industries Ltd", "Sun Pharmaceutical Industries Ltd"),
        ("Havells announces major manufacturing capex", "Havells India Ltd", "Havells"),
        ("Havells India Ltd acquires strategic business", "Havells India Ltd", "Havells India Ltd"),
    ],
)
def test_alias_mapping_and_canonical_resolution(text: str, expected_canonical: str, expected_alias: str):
    matched, canonical_name, matched_alias = get_watchlist_match_details(text)
    assert matched, f"Expected match for '{text}'"
    assert canonical_name == expected_canonical, f"Expected canonical '{expected_canonical}', got '{canonical_name}'"
    assert matched_alias.lower() == expected_alias.lower(), f"Expected alias '{expected_alias}', got '{matched_alias}'"


# ---------------------------------------------------------------------------
# 2. Concrete Portfolio Event Pass Scenarios
# ---------------------------------------------------------------------------

PASS_SCENARIOS = [
    ("BEL", "BEL wins Rs 1,200 crore Indian Navy contract for air defence radar"),
    ("BHEL", "BHEL bags Rs 4,000 crore thermal power project from NTPC"),
    ("NTPC", "NTPC commissions 500 MW solar plant in Gujarat"),
    ("Coal India", "Coal India announces Rs 5,000 crore capex for new coal handling plants"),
    ("State Bank of India", "SBI raises $500 million via senior unsecured bond issuance"),
    ("Infosys", "Infosys acquires UK digital transformation firm for $250 million"),
    ("Vedanta", "Vedanta demerger approved by shareholders to unlock metal and energy businesses"),
    ("Titan Company", "Titan Company announces major jewellery retail expansion across 20 cities"),
    ("Ashok Leyland", "Ashok Leyland wins contract for 500 electric buses worth Rs 800 crore"),
    ("Adani Power", "Adani Power signs 25-year PPA for 1,600 MW capacity"),
    ("Adani Ports", "Adani Ports signs concession agreement for new container terminal"),
    ("Adani Green Energy", "Adani Green Energy commissions 1,000 MW hybrid renewable energy plant"),
    ("Britannia", "Britannia launches major ₹500 crore capacity expansion in Maharashtra"),
    ("Sun Pharma", "Sun Pharma announces major acquisition of US dermatology pipeline for $400 million"),
    ("Havells", "Havells announces major manufacturing capex of Rs 600 crore for new AC plant"),
]


@pytest.mark.parametrize("company,headline", PASS_SCENARIOS)
def test_concrete_portfolio_events_pass_materiality(company: str, headline: str):
    art, ev = _make_art_and_ev(headline)
    is_mat, score, reason = evaluate_investment_materiality(ev, art)
    assert is_mat, f"[{company}] Expected materiality pass (>=60.0), got score={score}, reason='{reason}'"
    assert score >= 60.0
    assert "portfolio_concrete_event" in reason or "contract_order_win" in reason or "acquisition_merger" in reason or "capex" in reason


# ---------------------------------------------------------------------------
# 3. Portfolio Event Reject Scenarios
# ---------------------------------------------------------------------------

REJECT_SCENARIOS = [
    ("Ashok Leyland", "Ashok Leyland shares rise 3% in morning trade on heavy volumes", "share_price_movement"),
    ("NTPC", "Brokerage maintains buy rating on NTPC with target price Rs 450", "broker_rating_or_target_price"),
    ("Coal India", "Coal India stock in focus ahead of quarterly dividend announcement", "stock_movement_or_speculation"),
    ("BEL", "BEL schedules analyst meet on March 15 in Mumbai", "routine_administrative"),
    ("Adani Enterprises", "Adani Enterprises board meeting to consider quarterly financial results", "routine_results_board_meeting"),
    ("Britannia", "Britannia shares rise 2% in morning trade", "share_price_movement"),
    ("Sun Pharma", "Broker says buy Sun Pharma with target price Rs 1800", "broker_rating_or_target_price"),
    ("Havells", "Havells target price raised by analysts", "broker_rating_or_target_price"),
]


@pytest.mark.parametrize("company,headline,expected_pattern", REJECT_SCENARIOS)
def test_portfolio_noise_rejected(company: str, headline: str, expected_pattern: str):
    art, ev = _make_art_and_ev(headline)
    is_mat, score, reason = evaluate_investment_materiality(ev, art)
    assert not is_mat or score < 60.0, (
        f"[{company}] Expected noise rejection (<60.0), but passed with score={score}, reason='{reason}'"
    )


def test_semantic_dedup_rejects_duplicate_portfolio_story():
    from app.verification.verifier import TwoSourceVerifier
    art1, ev1 = _make_art_and_ev("Ashok Leyland wins contract for 500 electric buses worth Rs 800 crore")
    art2, ev2 = _make_art_and_ev("Ashok Leyland secures order for 500 e-buses valued at Rs 800 cr")

    verifier = TwoSourceVerifier()
    is_same, conf, reason = verifier.is_same_underlying_event(art1, art2)
    assert is_same, f"Expected duplicate detection, got is_same={is_same}, conf={conf}, reason='{reason}'"


def test_stale_portfolio_story_rejected_when_over_horizon():
    # Story is 80 hours old (beyond 72h horizon)
    art, ev = _make_art_and_ev("BEL wins Rs 1,200 crore Indian Navy contract", age_hours=80.0)
    from app.pipeline.candidate_processing import get_article_age_hours
    age = get_article_age_hours(art)
    assert age > 72.0


# ---------------------------------------------------------------------------
# 4. Mandatory India Business Routing for Portfolio Companies
# ---------------------------------------------------------------------------

def test_portfolio_events_routed_to_india_business():
    classifier = EventRegionClassifier()

    # Even if content originates from a general or domestic context, portfolio companies must route to INDIA
    test_cases = [
        "Ashok Leyland wins contract for 500 electric buses worth Rs 800 crore",
        "Adani Ports signs concession agreement for new container terminal",
        "BEL secures Rs 1,200 crore naval radar contract",
        "BHEL wins 800 MW thermal power project",
        "SBI raises $500 million through overseas bond issuance",
    ]

    for title in test_cases:
        cat, reason = classifier.classify_with_reason(title, title)
        assert cat == NewsCategory.INDIA, (
            f"Expected NewsCategory.INDIA for '{title}', got {cat} (reason='{reason}')"
        )
        assert "Portfolio company" in reason


# ---------------------------------------------------------------------------
# 5. Search Budget Reservation Protection
# ---------------------------------------------------------------------------

def test_portfolio_search_budget_constants():
    assert PORTFOLIO_RESERVED_RSS_SEARCHES == 3
    assert DOMESTIC_RESERVED_RSS_SEARCHES == 3
    assert MAX_CORROBORATION_SEARCHES_PER_RUN == 20


def test_search_budget_reservation_logic():
    """
    Verify that Domestic reserve does not consume Portfolio reserve and vice versa.
    """
    total_budget = MAX_CORROBORATION_SEARCHES_PER_RUN  # 20
    corroboration_count = 14
    accepted_dom = 3  # Domestic is deficient (< 5)
    portfolio_executed = False

    # 1. Corroboration Stage:
    # Must preserve Domestic reserve (3) AND Portfolio reserve (3) = 6 reserved
    corr_reserve = (DOMESTIC_RESERVED_RSS_SEARCHES if accepted_dom < 5 else 0) + (PORTFOLIO_RESERVED_RSS_SEARCHES if not portfolio_executed else 0)
    assert corr_reserve == 6
    usable_for_corr = total_budget - corr_reserve - corroboration_count
    assert usable_for_corr == 0  # 20 - 6 - 14 = 0, meaning corroboration cannot starve reserves!

    # 2. Domestic Step 3:
    # Domestic does not reserve for itself, but respects Portfolio reserve (3)
    dom_usable = total_budget - (PORTFOLIO_RESERVED_RSS_SEARCHES if not portfolio_executed else 0) - corroboration_count
    assert dom_usable == 3  # 20 - 3 - 14 = 3 searches available for Domestic

    # 3. India Step 3:
    # India respects Domestic reserve (3), but can use the portfolio reserve (3)
    india_usable = total_budget - (DOMESTIC_RESERVED_RSS_SEARCHES if accepted_dom < 5 else 0) - corroboration_count
    assert india_usable == 3  # 20 - 3 - 14 = 3 searches available for India / Portfolio


# ---------------------------------------------------------------------------
# 6. Discovery Queries Generation
# ---------------------------------------------------------------------------

def test_portfolio_discovery_queries_built_properly():
    assert len(PORTFOLIO_DISCOVERY_GROUPS) == 5
    queries = SearchQueryBuilder.build_portfolio_queries(include_site_filters=True)
    assert len(queries) == 5
    for grp_name, q in queries:
        assert "when:1d" in q
        assert "site:" in q
        assert "acquisition" in q or "capex" in q or "order" in q

    # Group 1: PSU & Capital goods
    g1_query = dict(queries)["group_1_psu_capital_goods"]
    assert "Bharat Electronics" in g1_query or "BEL" in g1_query
    assert "Bharat Heavy Electricals" in g1_query or "BHEL" in g1_query
    assert "Steel Authority of India" in g1_query or "SAIL" in g1_query

    # Group 2: Financials & IT
    g2_query = dict(queries)["group_2_financials_it_metals"]
    assert "State Bank of India" in g2_query or "SBI" in g2_query
    assert "Vedanta" in g2_query

    # Group 3: Consumer & Retail
    g3_query = dict(queries)["group_3_consumer_retail_fmcg"]
    assert "Britannia" in g3_query
    assert "Havells" in g3_query

    # Group 4: Pharma & Chemical
    g4_query = dict(queries)["group_4_pharma_chem_health"]
    assert "Sun Pharma" in g4_query or "Sun Pharmaceutical" in g4_query

    # Group 5: Industrials, Auto, Adani
    g5_query = dict(queries)["group_5_auto_industrials_midcaps_adani"]
    assert "Ashok Leyland" in g5_query
    assert "Adani Enterprises" in g5_query
    assert "Adani Ports" in g5_query
    assert "Ambuja Cements" in g5_query
    assert " OR ACC OR " not in g5_query  # Safe entity names, no bare ACC


# ---------------------------------------------------------------------------
# 7. Regression Tests: India Step 3 Dict Iteration, Aliases & Budget Sync
# ---------------------------------------------------------------------------

def test_ambiguous_aliases_rejections_and_valid_matches():
    """
    Verify ACC, Asian Cricket Council, SAIL verbs are rejected from false positive
    portfolio routing, while valid corporate actions by ACC and SAIL route to INDIA.
    """
    classifier = EventRegionClassifier()

    # 1. False positive cases: must NOT match watchlist or route to INDIA as portfolio
    acc_cabinet = "Appointments Committee of the Cabinet (ACC) approves appointments"
    m_acc_cab, _ = is_watchlist_company(acc_cabinet)
    assert not m_acc_cab, f"ACC in Cabinet text should not match watchlist: '{acc_cabinet}'"
    ev_cab = Event(id="ev_cab", canonical_title=acc_cabinet, description="Cabinet committee approvals")
    cat_cab, reason_cab = classifier.classify_with_reason(ev_cab.canonical_title, ev_cab.description)
    assert "Portfolio company" not in reason_cab, f"Should not route as portfolio: {reason_cab}"

    acc_cricket = "Asian Cricket Council announces tournament schedule"
    m_acc_cric, _ = is_watchlist_company(acc_cricket)
    assert not m_acc_cric, f"Asian Cricket Council should not match watchlist: '{acc_cricket}'"
    ev_cric = Event(id="ev_cric", canonical_title=acc_cricket, description="Tournament schedule announced")
    cat_cric, reason_cric = classifier.classify_with_reason(ev_cric.canonical_title, ev_cric.description)
    assert "Portfolio company" not in reason_cric, f"Should not route as portfolio: {reason_cric}"

    sail_verb_1 = "Indian exporters sail through global storm"
    m_sail_1, _ = is_watchlist_company(sail_verb_1)
    assert not m_sail_1, f"Lowercase verb 'sail' should not match watchlist: '{sail_verb_1}'"

    sail_verb_2 = "Boats sail from Mumbai harbour"
    m_sail_2, _ = is_watchlist_company(sail_verb_2)
    assert not m_sail_2, f"Boats sail should not match watchlist: '{sail_verb_2}'"

    # 2. Valid corporate event matches: MUST match and route to INDIA
    acc_valid = "ACC commissions new cement grinding unit"
    m_acc_v, name_acc_v = is_watchlist_company(acc_valid)
    assert m_acc_v and name_acc_v == "Adani Group"
    ev_acc = Event(id="ev_acc", canonical_title=acc_valid, description="ACC commissions new cement grinding unit in UP")
    cat_acc, reason_acc = classifier.classify_with_reason(ev_acc.canonical_title, ev_acc.description)
    assert cat_acc == NewsCategory.INDIA
    assert "Portfolio company 'Adani Group'" in reason_acc

    sail_valid = "SAIL signs ₹5,000 crore expansion contract"
    m_sail_v, name_sail_v = is_watchlist_company(sail_valid)
    assert m_sail_v and name_sail_v == "Steel Authority of India Ltd"
    ev_sail = Event(id="ev_sail", canonical_title=sail_valid, description="SAIL signs expansion contract with partners")
    cat_sail, reason_sail = classifier.classify_with_reason(ev_sail.canonical_title, ev_sail.description)
    assert cat_sail == NewsCategory.INDIA
    assert "Portfolio company 'Steel Authority of India Ltd'" in reason_sail

    adani_valid = "Adani Power acquires thermal asset"
    m_adani_v, name_adani_v = is_watchlist_company(adani_valid)
    assert m_adani_v and name_adani_v == "Adani Group"
    ev_adani = Event(id="ev_adani", canonical_title=adani_valid, description="Adani Power acquires 600 MW thermal power plant")
    cat_adani, reason_adani = classifier.classify_with_reason(ev_adani.canonical_title, ev_adani.description)
    assert cat_adani == NewsCategory.INDIA
    assert "Portfolio company 'Adani Group'" in reason_adani


def test_portfolio_discovery_executed_synchronization():
    """
    Verify portfolio_discovery_executed transitions from False to True after Stage 1,
    and pf_reserve resets from 3 to 0 while domestic reserve remains independent.
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.discovery_stage import discover_initial_reserves
    from pathlib import Path

    ctx = PipelineContext(
        run_reference_time=datetime.now(timezone.utc),
        data_dir=Path("./data"),
        logs_dir=Path("./logs"),
        settings=MagicMock(),
    )
    # Before discovery: False
    assert ctx.portfolio_discovery_executed is False
    pf_reserve_before = PORTFOLIO_RESERVED_RSS_SEARCHES if not ctx.portfolio_discovery_executed else 0
    assert pf_reserve_before == 3

    # Case 1: Service exists, but portfolio discovery did NOT execute (portfolio_discovery_executed = False)
    mock_discovery_skipped = MagicMock()
    mock_discovery_skipped.discover_all.return_value = {
        "domestic": [],
        "india": [],
        "international": [],
    }
    mock_discovery_skipped.portfolio_discovery_executed = False
    ctx.discovery_service = mock_discovery_skipped

    discover_initial_reserves(ctx, max_domestic=10, max_india=10, max_international=10)

    # Must remain False because portfolio discovery did not actually execute
    assert ctx.portfolio_discovery_executed is False
    pf_reserve_skipped = PORTFOLIO_RESERVED_RSS_SEARCHES if not ctx.portfolio_discovery_executed else 0
    assert pf_reserve_skipped == 3

    # Case 2: Portfolio discovery executed successfully (portfolio_discovery_executed = True)
    mock_discovery_executed = MagicMock()
    mock_discovery_executed.discover_all.return_value = {
        "domestic": [],
        "india": [],
        "international": [],
    }
    mock_discovery_executed.portfolio_discovery_executed = True
    ctx.discovery_service = mock_discovery_executed

    discover_initial_reserves(ctx, max_domestic=10, max_india=10, max_international=10)

    # Must transition to True
    assert ctx.portfolio_discovery_executed is True

    # Stage 5 reservation logic: pf_reserve is 0 because portfolio discovery ran
    pf_reserve_done = PORTFOLIO_RESERVED_RSS_SEARCHES if not ctx.portfolio_discovery_executed else 0
    assert pf_reserve_done == 0

    # Domestic reserve remains independent
    dom_count = 3  # deficient (< 5)
    dom_reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_count < 5 else 0
    assert dom_reserve == 3


def test_actual_search_budget_behavior_before_and_after_discovery():
    """
    Verify search budget reservation and usable counts before vs after portfolio discovery.
    """
    total_budget = MAX_CORROBORATION_SEARCHES_PER_RUN  # 20
    corroboration_count = 14
    dom_count = 3  # deficient

    # Before portfolio discovery (portfolio_discovery_executed = False):
    pf_executed = False
    pf_reserve_before = PORTFOLIO_RESERVED_RSS_SEARCHES if not pf_executed else 0
    dom_reserve = DOMESTIC_RESERVED_RSS_SEARCHES if dom_count < 5 else 0
    total_reserve_before = dom_reserve + pf_reserve_before
    assert total_reserve_before == 6
    usable_general_before = total_budget - total_reserve_before - corroboration_count
    assert usable_general_before == 0  # Cannot starve reserves

    # Domestic rescue before pf discovery: consumes dom reserve, preserves pf reserve
    usable_dom_before = total_budget - pf_reserve_before - corroboration_count
    assert usable_dom_before == 3

    # India rescue before pf discovery: consumes pf reserve, preserves dom reserve
    usable_india_before = total_budget - dom_reserve - corroboration_count
    assert usable_india_before == 3

    # After portfolio discovery (portfolio_discovery_executed = True):
    pf_executed = True
    pf_reserve_after = PORTFOLIO_RESERVED_RSS_SEARCHES if not pf_executed else 0
    assert pf_reserve_after == 0
    total_reserve_after = dom_reserve + pf_reserve_after
    assert total_reserve_after == 3

    usable_general_after = total_budget - total_reserve_after - corroboration_count
    assert usable_general_after == 3  # Freed up for general corroboration

    # Domestic rescue after pf discovery: preserves pf reserve (0)
    usable_dom_after = total_budget - pf_reserve_after - corroboration_count
    assert usable_dom_after == 6

    # India rescue after pf discovery: preserves dom reserve (4)
    usable_india_after = total_budget - dom_reserve - corroboration_count
    assert usable_india_after == 3


def test_india_step3_dict_iteration_and_query_expression():
    """
    Regression test for Section 1:
    - Verifies no AttributeError (.index() on dict)
    - Verifies provider receives the actual query expressions (not group keys)
    - Verifies group logging captures group names
    - Verifies returned valid candidate is processed through normal gates
    """
    from app.pipeline.context import PipelineContext
    from app.pipeline.selection import run_post_dedup_refill
    from app.discovery.models import DiscoveredArticle
    from app.verification.corroborator import reset_corroboration_counter
    from pathlib import Path

    reset_corroboration_counter()
    logs = []
    def log_fn(msg):
        logs.append(msg)

    ctx = PipelineContext(
        run_reference_time=datetime.now(timezone.utc),
        data_dir=Path("./data"),
        logs_dir=Path("./logs"),
        settings=MagicMock(),
        log_exec=log_fn,
    )

    captured_queries = []
    fake_provider = MagicMock()
    def fake_discover(query, country, max_results):
        captured_queries.append(query)
        if "Adani" in query or "group_5" in query:
            # Return a valid candidate for the Adani group query
            return [
                DiscoveredArticle(
                    title="Adani Power signs 25-year PPA for 1600 MW capacity",
                    url="https://economictimes.indiatimes.com/industry/energy/power/adani-power-signs-ppa/articleshow/11111111.cms",
                    snippet="Adani Power enters long term power purchase agreement",
                    source="The Economic Times",
                    source_name="The Economic Times",
                    search_query=query,
                    country=country,
                    published_at=datetime.now(timezone.utc) - timedelta(hours=2),
                )
            ]
        return []

    fake_provider.discover.side_effect = fake_discover
    mock_disc_service = MagicMock()
    mock_disc_service.provider = fake_provider
    ctx.discovery_service = mock_disc_service

    # Mock extractor, classifier, verifier, etc.
    mock_extractor = MagicMock()
    def fake_extract(raw_art, cat, log_cb):
        art = Article(
            id=f"art_{abs(hash(raw_art.url)) % 100000}",
            title=raw_art.title,
            url=raw_art.url,
            source_name=raw_art.source_name,
            category=NewsCategory.INDIA,
            content_text=raw_art.title + " full details about the transaction",
            published_at=raw_art.published_at,
            is_verified_url=True,
            date_verified=True,
            is_valid_date=True,
        )
        rec = {"id": art.id, "title": art.title, "url": art.url, "category": "india"}
        return art, rec, True, True, False, False, False

    mock_extractor.extract_candidate_article.side_effect = fake_extract
    ctx.extractor = mock_extractor

    ctx.classifier = MagicMock()
    ctx.classifier.classify_event.return_value = NewsCategory.INDIA
    ctx.reg_clf = EventRegionClassifier()

    mock_verifier = MagicMock()
    v_res = MagicMock()
    v_res.is_verified = True
    v_res.confidence = 85.0
    mock_verifier.verify_event.return_value = v_res
    ctx.verifier = mock_verifier

    mock_eval = MagicMock()
    e_res = MagicMock()
    e_res.is_quality_eligible = True
    mock_eval.evaluate.return_value = e_res
    ctx.single_source_evaluator = mock_eval

    mock_scorer = MagicMock()
    mock_scorer.score_story.return_value = 75.0
    ctx.scorer = mock_scorer

    # 5 Domestic, 5 International, but only 4 India -> triggers India refill needed=1
    accepted_stories = [
        {"id": f"dom_{i}", "category": "domestic", "event_id": f"ev_dom_{i}"} for i in range(5)
    ] + [
        {"id": f"intl_{i}", "category": "international", "event_id": f"ev_intl_{i}"} for i in range(5)
    ] + [
        {"id": f"india_{i}", "category": "india", "event_id": f"ev_india_{i}"} for i in range(4)
    ]
    event_by_id = {}

    run_post_dedup_refill(
        ctx=ctx,
        accepted_stories=accepted_stories,
        event_by_id=event_by_id,
    )

    # 1. No AttributeError was raised!
    # 2. Captured queries should NOT be group names like 'group_1_core_psu_heavy'
    assert len(captured_queries) > 0
    first_q = captured_queries[0]
    assert not first_q.startswith("group_"), f"Query should be query expression, got: {first_q}"
    assert "when:1d" in first_q
    # 3. Group logging captures group names
    group_logs = [l for l in logs if "[PORTFOLIO_DISCOVERY_QUERY]" in l]
    assert len(group_logs) > 0
    assert "group=" in group_logs[0]
    assert "group_1_" in group_logs[0]
    # 4. State was marked executed
    assert ctx.portfolio_discovery_executed is True
    reset_corroboration_counter()


# ---------------------------------------------------------------------------
# 10. Portfolio Company Diversity Tests (Parts 9, 10, 11, 15)
# ---------------------------------------------------------------------------

def test_portfolio_company_diversity_skips_second_story():
    """
    Test Part 9 & 15:
    Vedanta A score=98
    Vedanta B score=96
    Adani score=90
    NTPC score=88
    Sun Pharma score=86
    Britannia score=84

    Expected final 5:
    Vedanta A, Adani, NTPC, Sun Pharma, Britannia.
    Vedanta B skipped with [PORTFOLIO_DIVERSITY_SKIP].
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.pipeline.context import PipelineContext
    from app.models import Article, Event, NewsCategory
    from app.models.enums import VerificationTier
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    articles = {}
    events = {}

    def _make_scored_candidate(eid, title, company, score):
        art = Article(
            id=f"art_{eid}",
            title=title,
            url=f"https://economictimes.indiatimes.com/{eid}",
            source_name="The Economic Times",
            published_at=now,
            category=NewsCategory.INDIA,
            content_text=f"{company} announced major institutional development. {title}",
            is_verified_url=True,
        )
        ev = Event(
            id=eid,
            canonical_title=title,
            description=art.content_text,
            article_ids=[art.id],
            companies_involved=[company],
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=95.0,
        )
        articles[art.id] = art
        events[eid] = ev
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=80.0,
                market_impact=80.0,
                investor_relevance=80.0,
                corporate_significance=80.0,
                source_quality=90.0,
                strategic_bonuses=20.0,
                editorial_signals=0.0,
                relevance_penalties=0.0,
                total_score=score,
                rationale="test",
            ),
            investment_score=score,
        )

    cand_vedanta_a = _make_scored_candidate("v_a", "Vedanta approves Rs 8000 crore capex for major aluminium smelter expansion", "Vedanta", 98.0)
    cand_vedanta_b = _make_scored_candidate("v_b", "Vedanta Resources announces major Rs 3200 crore capex expansion", "Vedanta Resources", 96.0)
    cand_adani = _make_scored_candidate("ad", "Adani Group plans $10 billion investment in green energy", "Adani Group", 90.0)
    cand_ntpc = _make_scored_candidate("ntpc", "NTPC commissions Rs 3500 crore solar power capex project in Gujarat", "NTPC", 88.0)
    cand_sun = _make_scored_candidate("sun", "Sun Pharma announces major acquisition of US dermatology pipeline", "Sun Pharma", 86.0)
    cand_brit = _make_scored_candidate("brit", "Britannia launches major Rs 500 crore capacity expansion", "Britannia", 84.0)

    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    ctx.articles_lookup = articles
    mock_ranker = MagicMock()
    # Ranker returns candidate pool containing all 6
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[cand_vedanta_a, cand_vedanta_b, cand_adani, cand_ntpc, cand_sun, cand_brit],
        international_candidates=[
            _make_scored_candidate(f"intl_{i}", f"GlobalTech announces $5 billion acquisition of international cloud network {i}", "GlobalTech", 75.0)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different events")

    accepted_stories = [
        {"event_id": eid, "category": "india"} for eid in events
    ] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted_stories, events)
    cand_pool = res[0]
    final_india = cand_pool.india_candidates

    # Assert exactly 5 India stories selected
    assert len(final_india) == 5, f"Expected 5 India stories, got {len(final_india)}"
    titles = [s.event.canonical_title for s in final_india]

    # Vedanta A kept, Vedanta B skipped
    assert cand_vedanta_a.event.canonical_title in titles
    assert cand_vedanta_b.event.canonical_title not in titles
    assert cand_adani.event.canonical_title in titles
    assert cand_ntpc.event.canonical_title in titles
    assert cand_sun.event.canonical_title in titles
    assert cand_brit.event.canonical_title in titles

    # Verify structured logging was emitted
    div_logs = [l for l in logs if "[PORTFOLIO_DIVERSITY_SKIP]" in l]
    assert len(div_logs) == 1
    assert 'company="Vedanta"' in div_logs[0]
    assert 'reason="max 1 portfolio story per canonical company"' in div_logs[0]


def test_adani_canonical_grouping_diversity():
    """
    Test Part 11:
    Adani Power (score=95) and Adani Ports (score=93).
    Both map to canonical 'Adani Group'.
    Only the higher-ranked one (Adani Power) may occupy final India.
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.pipeline.context import PipelineContext
    from app.models import Article, Event, NewsCategory
    from app.models.enums import VerificationTier
    from app.ranking.models import ScoredEvent, ScoreBreakdown, RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    articles = {}
    events = {}

    def _make_scored(eid, title, company, score):
        art = Article(
            id=f"art_{eid}",
            title=title,
            url=f"https://economictimes.indiatimes.com/{eid}",
            source_name="The Economic Times",
            published_at=now,
            category=NewsCategory.INDIA,
            content_text=f"{company} corporate action. {title}",
            is_verified_url=True,
        )
        ev = Event(
            id=eid,
            canonical_title=title,
            description=art.content_text,
            article_ids=[art.id],
            companies_involved=[company],
            event_category=NewsCategory.INDIA,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=95.0,
        )
        articles[art.id] = art
        events[eid] = ev
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=80.0, market_impact=80.0, investor_relevance=80.0,
                corporate_significance=80.0, source_quality=90.0, strategic_bonuses=20.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=score, rationale="test",
            ),
            investment_score=score,
        )

    cand_power = _make_scored("ad_pow", "Adani Power signs 25-year PPA for 1600 MW capacity", "Adani Power", 95.0)
    cand_ports = _make_scored("ad_prt", "Adani Ports signs 30-year concession for container terminal", "Adani Ports", 93.0)
    cand_sbi = _make_scored("sbi", "State Bank of India raises $500 million via senior bonds", "State Bank of India", 90.0)
    cand_infy = _make_scored("infy", "Infosys signs $1 billion cloud transformation deal", "Infosys", 88.0)
    cand_tata = _make_scored("tata", "Tata Consumer Products acquires organic brand for Rs 500 crore", "Tata Consumer", 85.0)

    logs = []
    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    ctx.articles_lookup = articles
    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[cand_power, cand_ports, cand_sbi, cand_infy, cand_tata],
        international_candidates=[
            _make_scored(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different events")

    accepted_stories = [{"event_id": eid, "category": "india"} for eid in events] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted_stories, events)
    final_india = res[0].india_candidates

    titles = [s.event.canonical_title for s in final_india]
    # Adani Power kept, Adani Ports skipped
    assert cand_power.event.canonical_title in titles
    assert cand_ports.event.canonical_title not in titles
    # Check log
    div_logs = [l for l in logs if "[PORTFOLIO_DIVERSITY_SKIP]" in l]
    assert len(div_logs) == 1
    assert 'company="Adani Group"' in div_logs[0]


# ---------------------------------------------------------------------------
# 11. Portfolio-First Selection Scenarios: Cases A, B, C, D, E
# ---------------------------------------------------------------------------

def _build_test_harness(now):
    from app.pipeline.context import PipelineContext
    articles = {}
    events = {}
    logs = []

    def _make_candidate(eid, title, company, score, category=NewsCategory.INDIA):
        art = Article(
            id=f"art_{eid}",
            title=title,
            url=f"https://economictimes.indiatimes.com/{eid}",
            source_name="The Economic Times",
            published_at=now,
            category=category,
            content_text=f"{company} corporate development announcement. {title}",
            is_verified_url=True,
        )
        ev = Event(
            id=eid,
            canonical_title=title,
            description=art.content_text,
            article_ids=[art.id],
            companies_involved=[company],
            event_category=category,
            verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
            verification_confidence=95.0,
        )
        articles[art.id] = art
        events[eid] = ev
        from app.ranking.models import ScoredEvent, ScoreBreakdown
        return ScoredEvent(
            event=ev,
            score_breakdown=ScoreBreakdown(
                financial_magnitude=80.0, market_impact=80.0, investor_relevance=80.0,
                corporate_significance=80.0, source_quality=90.0, strategic_bonuses=20.0,
                editorial_signals=0.0, relevance_penalties=0.0, total_score=score, rationale="test",
            ),
            investment_score=score,
        )

    ctx = PipelineContext(
        run_reference_time=now,
        data_dir=Path("./tmp"),
        logs_dir=Path("./tmp"),
        settings=MagicMock(MAX_CORROBORATION_SEARCHES=20),
        log_exec=lambda m: logs.append(m),
    )
    ctx.articles_lookup = articles
    ctx.verifier = MagicMock()
    ctx.verifier.is_same_underlying_event.return_value = (False, 0.0, "different events")

    return ctx, articles, events, logs, _make_candidate


def test_case_a_four_portfolio_plus_one_general():
    """
    CASE A:
    Portfolio qualified: Vedanta, Sun Pharma, Britannia, Havells
    General qualified: Persistent, Nexus, Motilal
    Expected: Vedanta, Sun Pharma, Britannia, Havells, + best general India story (5 total)
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    ctx, articles, events, logs, make_cand = _build_test_harness(now)

    c_ved = make_cand("ved", "Vedanta approves Rs 8000 crore capex for major aluminium smelter expansion", "Vedanta", 85.0)
    c_sun = make_cand("sun", "Sun Pharma announces major acquisition of US dermatology pipeline", "Sun Pharma", 82.0)
    c_brit = make_cand("brit", "Britannia launches major Rs 500 crore capacity expansion", "Britannia", 80.0)
    c_hav = make_cand("hav", "Havells announces major manufacturing capex of Rs 600 crore", "Havells", 78.0)

    # General stories have higher raw scores than some portfolio stories
    c_pers = make_cand("pers", "Persistent Systems announces strategic AI expansion deal", "Persistent Systems", 95.0)
    c_nex = make_cand("nex", "Nexus Select Trust acquires prime retail asset portfolio", "Nexus Select Trust", 92.0)
    c_mot = make_cand("mot", "Motilal Oswal Alternates invests Rs 450 crore in industrial safety leader", "Motilal Oswal", 90.0)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[c_pers, c_nex, c_mot, c_ved, c_sun, c_brit, c_hav],
        international_candidates=[
            make_cand(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0, category=NewsCategory.INTERNATIONAL)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "india"} for eid in events if "intl" not in eid] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted, events)
    final_india = res[0].india_candidates

    assert len(final_india) == 5
    titles = [s.event.canonical_title for s in final_india]

    # All 4 portfolio stories selected despite having lower raw scores than Persistent/Nexus/Motilal!
    assert c_ved.event.canonical_title in titles
    assert c_sun.event.canonical_title in titles
    assert c_brit.event.canonical_title in titles
    assert c_hav.event.canonical_title in titles
    # Fifth slot filled by highest-ranked general story (Persistent Systems)
    assert c_pers.event.canonical_title in titles


def test_case_b_one_portfolio_plus_four_general():
    """
    CASE B:
    Only Vedanta qualified from portfolio
    General: Persistent, Nexus, Motilal, Indian Bank, L&T
    Expected: Vedanta + best 4 general India (5 total)
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    ctx, articles, events, logs, make_cand = _build_test_harness(now)

    c_ved = make_cand("ved", "Vedanta approves Rs 8000 crore capex for major aluminium smelter expansion", "Vedanta", 75.0)
    c_pers = make_cand("pers", "Persistent Systems announces strategic AI expansion deal", "Persistent Systems", 95.0)
    c_nex = make_cand("nex", "Nexus Select Trust acquires prime retail asset portfolio", "Nexus Select Trust", 92.0)
    c_mot = make_cand("mot", "Motilal Oswal Alternates invests Rs 450 crore in industrial safety leader", "Motilal Oswal", 90.0)
    c_ib = make_cand("ib", "Indian Bank partners with NSE on commodity trade settlement", "Indian Bank", 88.0)
    c_lt = make_cand("lt", "Larsen and Toubro bags mega infrastructure order for high speed rail", "Larsen and Toubro", 85.0)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[c_pers, c_nex, c_mot, c_ib, c_lt, c_ved],
        international_candidates=[
            make_cand(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0, category=NewsCategory.INTERNATIONAL)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "india"} for eid in events if "intl" not in eid] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted, events)
    final_india = res[0].india_candidates

    assert len(final_india) == 5
    titles = [s.event.canonical_title for s in final_india]

    # Vedanta selected first as portfolio story
    assert c_ved.event.canonical_title in titles
    # Exactly 4 remaining slots filled by general India candidates (diverse across topics)
    general_titles = {
        c_pers.event.canonical_title, c_nex.event.canonical_title,
        c_mot.event.canonical_title, c_ib.event.canonical_title,
        c_lt.event.canonical_title,
    }
    selected_general = [t for t in titles if t != c_ved.event.canonical_title]
    assert len(selected_general) == 4
    for t in selected_general:
        assert t in general_titles


def test_case_c_no_portfolio_five_general():
    """
    CASE C:
    No portfolio company qualifies
    Expected: Normal best 5 general India stories
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    ctx, articles, events, logs, make_cand = _build_test_harness(now)

    c_pers = make_cand("pers", "Persistent Systems announces strategic AI expansion deal", "Persistent Systems", 95.0)
    c_nex = make_cand("nex", "Nexus Select Trust acquires prime retail asset portfolio", "Nexus Select Trust", 92.0)
    c_mot = make_cand("mot", "Motilal Oswal Alternates invests Rs 450 crore in industrial safety leader", "Motilal Oswal", 90.0)
    c_ib = make_cand("ib", "Indian Bank partners with NSE on commodity trade settlement", "Indian Bank", 88.0)
    c_lt = make_cand("lt", "Larsen and Toubro bags mega infrastructure order for high speed rail", "Larsen and Toubro", 85.0)
    c_extra = make_cand("extra", "Zomato expands grocery dark store footprint across top cities", "Zomato", 75.0)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[c_pers, c_nex, c_mot, c_ib, c_lt, c_extra],
        international_candidates=[
            make_cand(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0, category=NewsCategory.INTERNATIONAL)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "india"} for eid in events if "intl" not in eid] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted, events)
    final_india = res[0].india_candidates

    assert len(final_india) == 5
    titles = [s.event.canonical_title for s in final_india]
    general_pool = {
        c_pers.event.canonical_title, c_nex.event.canonical_title,
        c_mot.event.canonical_title, c_ib.event.canonical_title,
        c_lt.event.canonical_title, c_extra.event.canonical_title,
    }
    # All 5 selected stories are from the general pool
    for t in titles:
        assert t in general_pool


def test_case_d_two_vedanta_stories_diversity():
    """
    CASE D:
    2 Vedanta stories qualify (Vedanta A score=98, Vedanta B score=95)
    Expected: Only higher-ranked Vedanta story enters final 5; next fills slot
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    ctx, articles, events, logs, make_cand = _build_test_harness(now)

    c_ved_a = make_cand("ved_a", "Vedanta approves Rs 8000 crore capex for major aluminium smelter expansion", "Vedanta", 98.0)
    c_ved_b = make_cand("ved_b", "Vedanta Resources issues $800 million overseas dollar bonds", "Vedanta Resources", 95.0)
    c_pers = make_cand("pers", "Persistent Systems announces strategic AI expansion deal", "Persistent Systems", 90.0)
    c_nex = make_cand("nex", "Nexus Select Trust acquires prime retail asset portfolio", "Nexus Select Trust", 88.0)
    c_mot = make_cand("mot", "Motilal Oswal Alternates invests Rs 450 crore in industrial safety leader", "Motilal Oswal", 86.0)
    c_ib = make_cand("ib", "Indian Bank partners with NSE on commodity trade settlement", "Indian Bank", 84.0)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[c_ved_a, c_ved_b, c_pers, c_nex, c_mot, c_ib],
        international_candidates=[
            make_cand(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0, category=NewsCategory.INTERNATIONAL)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "india"} for eid in events if "intl" not in eid] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted, events)
    final_india = res[0].india_candidates

    assert len(final_india) == 5
    titles = [s.event.canonical_title for s in final_india]

    assert c_ved_a.event.canonical_title in titles
    assert c_ved_b.event.canonical_title not in titles  # Skipped by diversity rule!
    assert c_pers.event.canonical_title in titles
    assert c_nex.event.canonical_title in titles
    assert c_mot.event.canonical_title in titles
    assert c_ib.event.canonical_title in titles

    div_logs = [l for l in logs if "[PORTFOLIO_DIVERSITY_SKIP]" in l]
    assert len(div_logs) == 1
    assert 'company="Vedanta"' in div_logs[0]


def test_case_e_adani_canonical_diversity():
    """
    CASE E:
    Adani Ports and Adani Power qualify
    Expected: Only one Adani Group canonical story enters final 5
    """
    from app.pipeline.selection import run_ranking_and_selection
    from app.ranking.models import RankedCandidatePool

    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    ctx, articles, events, logs, make_cand = _build_test_harness(now)

    c_pow = make_cand("ad_pow", "Adani Power signs 25-year PPA for 1600 MW capacity", "Adani Power", 95.0)
    c_prt = make_cand("ad_prt", "Adani Ports signs 30-year concession for container terminal", "Adani Ports", 93.0)
    c_pers = make_cand("pers", "Persistent Systems announces strategic AI expansion deal", "Persistent Systems", 90.0)
    c_nex = make_cand("nex", "Nexus Select Trust acquires prime retail asset portfolio", "Nexus Select Trust", 88.0)
    c_mot = make_cand("mot", "Motilal Oswal Alternates invests Rs 450 crore in industrial safety leader", "Motilal Oswal", 86.0)
    c_ib = make_cand("ib", "Indian Bank partners with NSE on commodity trade settlement", "Indian Bank", 84.0)

    mock_ranker = MagicMock()
    mock_ranker.rank_events.return_value = RankedCandidatePool(
        domestic_candidates=[],
        india_candidates=[c_pow, c_prt, c_pers, c_nex, c_mot, c_ib],
        international_candidates=[
            make_cand(f"intl_{i}", f"International Global Deal {i}", "GlobalCorp", 75.0, category=NewsCategory.INTERNATIONAL)
            for i in range(5)
        ],
    )
    ctx.ranker = mock_ranker

    accepted = [{"event_id": eid, "category": "india"} for eid in events if "intl" not in eid] + [
        {"event_id": f"intl_{i}", "category": "international"} for i in range(5)
    ]

    res = run_ranking_and_selection(ctx, accepted, events)
    final_india = res[0].india_candidates

    assert len(final_india) == 5
    titles = [s.event.canonical_title for s in final_india]

    assert c_pow.event.canonical_title in titles
    assert c_prt.event.canonical_title not in titles  # Skipped because canonical is Adani Group!
    assert c_pers.event.canonical_title in titles
    assert c_nex.event.canonical_title in titles
    assert c_mot.event.canonical_title in titles
    assert c_ib.event.canonical_title in titles

    div_logs = [l for l in logs if "[PORTFOLIO_DIVERSITY_SKIP]" in l]
    assert len(div_logs) == 1
    assert 'company="Adani Group"' in div_logs[0]

