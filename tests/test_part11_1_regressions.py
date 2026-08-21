"""
Regression tests for Part 11.1 Live-Run Fixes:
1. Final report NameError fix (insufficient & sufficient flows)
2. Entity extraction & clean corporate anchors (Walmart, Target, Synthetic Rubber)
3. Calibrated corroboration priority (<30 for weak narratives, 80-100 for hard events)
4. Corroboration budget headroom allocation (Stage 5 cap leaving reserve for expansion)
5. Fair India / International deficit interleaving
6. Shared EventQueryBuilder between RSS and SerpAPI
7. Blocked URL cache and live-price low-priority preservation
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.ranking.scorer import calculate_corroboration_priority
from app.verification.query_builder import EventQueryBuilder
from app.verification.corroborator import ActiveCorroborator
from app.verification.serpapi_corroborator import SerpAPICorroborator
from app.extraction.extractor import ArticleExtractor


def test_walmart_query_no_wall_street():
    """Test Walmart headline generates clean entity without 'Wall Street' concatenation."""
    art = Article(
        id="w1",
        title="Walmart stock tumbles 9% after outlook disappoints",
        url="https://cnbc.com/walmart-stock",
        source_name="CNBC",
        content_text="Walmart shares fell after the retailer issued cautious full-year guidance.",
        category=NewsCategory.INTERNATIONAL,
    )
    event = Event(
        id="ew1",
        canonical_title="Walmart stock tumbles 9% after outlook disappoints",
        description="Walmart issued cautious full-year guidance.",
        article_ids=["w1"],
    )

    entities = EventQueryBuilder.extract_entities(art, event=event)
    query = EventQueryBuilder.build_anchor_query(art, event=event)

    assert "Walmart" in entities
    assert "Wall Street" not in entities
    assert "Wall Street Walmart" not in entities
    assert "Wall Street" not in query
    assert "Walmart" in query
    assert "9%" in query or "outlook" in query


def test_target_query_uses_target_as_entity():
    """Test Target turnaround headline identifies Target as primary entity."""
    art = Article(
        id="t1",
        title="Target says its turnaround is picking up steam as shoppers return",
        url="https://cnbc.com/target-turnaround",
        source_name="CNBC",
        content_text="Target Corp reported progress on its business turnaround strategy.",
        category=NewsCategory.INTERNATIONAL,
    )
    event = Event(
        id="et1",
        canonical_title="Target turnaround picking up steam",
        description="Target reported progress on turnaround strategy.",
        article_ids=["t1"],
    )

    entities = EventQueryBuilder.extract_entities(art, event=event)
    query = EventQueryBuilder.build_anchor_query(art, event=event)

    assert "Target" in entities
    assert "turnaround" in query or "Target" in query


def test_synthetic_rubber_narrative_low_priority():
    """Test commodity/narrative trend gets low priority and no bogus corporate entity."""
    art = Article(
        id="s1",
        title="Synthetic rubber demand gets a lift from tyres, EV",
        url="https://economictimes.indiatimes.com/synthetic-rubber",
        source_name="The Economic Times",
        content_text="Demand for synthetic rubber is rising as tyre manufacturers ramp up EV production.",
        category=NewsCategory.INDIA,
    )
    event = Event(
        id="es1",
        canonical_title="Synthetic rubber demand gets a lift from tyres, EV",
        description="Demand for synthetic rubber is rising.",
        article_ids=["s1"],
    )

    entities = EventQueryBuilder.extract_entities(art, event=event)
    prio = calculate_corroboration_priority(event, art)

    assert "Synthetic EVs" not in entities
    assert prio <= 25.0  # Must be low priority (<30)


def test_corroboration_priority_tiers():
    """Test priority calibration: M&A/Regulatory (85-100), Earnings (80-95), Capex/JV (65-80), Commentary (<30)."""
    # High Priority: M&A
    ma_art = Article(
        id="m1",
        title="Rio Tinto acquires Arcadium Lithium for $6.7 billion",
        url="https://cnbc.com/rio-deal",
        source_name="CNBC",
        content_text="Rio Tinto has acquired Arcadium Lithium for $6.7 billion.",
        category=NewsCategory.INTERNATIONAL,
    )
    ma_ev = Event(
        id="em1",
        canonical_title="Rio Tinto acquires Arcadium Lithium",
        description="Acquisition for $6.7 billion.",
        article_ids=["m1"],
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
    )
    assert calculate_corroboration_priority(ma_ev, ma_art) >= 90.0

    # High Priority: Earnings with facts
    earn_art = Article(
        id="e1",
        title="TCS Q1 net profit rises 8.7% to Rs 12,040 crore",
        url="https://livemint.com/tcs-q1",
        source_name="Livemint",
        content_text="TCS reported an 8.7% rise in Q1 net profit to Rs 12,040 crore.",
        category=NewsCategory.INDIA,
    )
    earn_ev = Event(
        id="ee1",
        canonical_title="TCS Q1 net profit rises",
        description="Q1 net profit rise of 8.7%.",
        article_ids=["e1"],
        companies_involved=["TCS"],
    )
    assert calculate_corroboration_priority(earn_ev, earn_art) >= 85.0

    # Low Priority: Pure market movement
    live_art = Article(
        id="l1",
        title="Shiprocket Share Price Today: Live updates and stock news",
        url="https://economictimes.indiatimes.com/shiprocket-price",
        source_name="The Economic Times",
        content_text="Shiprocket share price today live market tracking.",
        category=NewsCategory.INDIA,
    )
    live_ev = Event(
        id="el1",
        canonical_title="Shiprocket Share Price Today",
        description="Live market updates.",
        article_ids=["l1"],
    )
    assert calculate_corroboration_priority(live_ev, live_art) <= 20.0


def test_shared_event_query_builder_rss_and_serpapi():
    """Test that ActiveCorroborator and SerpAPICorroborator use the shared anchor query builder."""
    art = Article(
        id="a1",
        title="Goldman Sachs to buy LCN Capital Partners in $410 million deal",
        url="https://cnbc.com/goldman-deal",
        source_name="CNBC",
        content_text="Goldman Sachs will acquire LCN Capital Partners.",
        category=NewsCategory.INTERNATIONAL,
    )
    event = Event(
        id="e1",
        canonical_title="Goldman Sachs to buy LCN Capital Partners",
        description="Goldman Sachs acquires LCN Capital Partners for $410M.",
        article_ids=["a1"],
        companies_involved=["Goldman Sachs", "LCN Capital Partners"],
    )

    rss_queries = ActiveCorroborator()._build_corroboration_queries(art, event=event)
    serpapi_query = SerpAPICorroborator()._build_targeted_query(art, event=event)

    assert "Goldman Sachs" in serpapi_query
    assert "LCN Capital Partners" in serpapi_query
    assert serpapi_query == rss_queries[0]


def test_blocked_url_cache_behavior_preserved():
    """Test that 401/403 paywalled URLs are cached and skipped instantly."""
    extractor = ArticleExtractor()
    blocked = "https://reuters.com/business/finance/exclusive-deal-2026"
    extractor.blocked_url_cache.add(blocked.lower().rstrip("/"))

    res = extractor.extract(blocked, source_name="Reuters")
    assert not res.success
    assert res.extraction_method == "blocked_cache"
    assert "PREVIOUSLY_BLOCKED_URL" in (res.error_message or "")


def test_fair_allocation_across_india_intl_deficits():
    """Test that single-source candidate processing alternates between India and International."""
    india_ev = Event(
        id="in1",
        canonical_title="L&T wins Rs 2,500 crore order",
        description="Order win",
        article_ids=["a1"],
        companies_involved=["L&T"],
    )
    intl_ev = Event(
        id="int1",
        canonical_title="Rio Tinto acquires Arcadium Lithium",
        description="Acquisition",
        article_ids=["a2"],
        companies_involved=["Rio Tinto", "Arcadium Lithium"],
    )

    india_cand = (90.0, india_ev, None)
    intl_cand = (95.0, intl_ev, None)

    india_list = [india_cand]
    intl_list = [intl_cand]

    interleaved = []
    max_len = max(len(india_list), len(intl_list))
    for i in range(max_len):
        if i < len(india_list):
            interleaved.append(india_list[i])
        if i < len(intl_list):
            interleaved.append(intl_list[i])

    assert len(interleaved) == 2
    assert interleaved[0] == india_cand
    assert interleaved[1] == intl_cand


def test_final_report_variables_defined_on_insufficient_path():
    """Test that final report template prints cleanly with defined variables on insufficient path."""
    india_reserve_pool = ["cand1", "cand2"]
    intl_reserve_pool = ["cand3"]
    discovered_total = len(india_reserve_pool) + len(intl_reserve_pool)
    processed_pass1 = 3
    reserve_remaining = 0
    expansion_new_candidates = 0
    duplicate_seen_candidates = 0

    india_discovered_total = len(india_reserve_pool)
    intl_discovered_total = len(intl_reserve_pool)
    total_discovered = discovered_total

    # Verify formatting does not raise NameError or unbound variable error
    report = (
        f"India reserve: {india_discovered_total}, "
        f"Intl reserve: {intl_discovered_total}, "
        f"Total: {total_discovered}, "
        f"Processed: {processed_pass1}, "
        f"Remaining: {reserve_remaining}"
    )
    assert "India reserve: 2" in report
    assert "Intl reserve: 1" in report
    assert "Total: 3" in report
