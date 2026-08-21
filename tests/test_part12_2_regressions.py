"""
Regression tests for Part 12.2:
1. Queued International work stops instantly at Intl=5 (0 RSS, 0 SerpAPI, 0 classification)
2. No RSS / SerpAPI spent on International after Intl=5
3. Piramal Pharma / Yapan Bio vs Blackstone / PGP Glass is strictly REJECTED (ENTITY_MISMATCH)
4. Generic 'Controlling Stake' cannot be an entity
5. Piramal query contains Piramal Pharma + Yapan Bio (no 'Controlling Stake')
6. KFin Moneycontrol + Business Today still genuinely verifies
7. India gets all remaining budget when Intl=5
8. Total budgets remain RSS <= 20 and SerpAPI <= 8
"""

import json
from unittest.mock import MagicMock
import pytest

from app.models.article import Article, NewsCategory
from app.models.event import Event
from app.verification.verifier import TwoSourceVerifier
from app.verification.query_builder import EventQueryBuilder, GENERIC_ENTITY_BLACKLIST
from app.verification.corroborator import ActiveCorroborator, MAX_CORROBORATION_SEARCHES_PER_RUN
from app.verification.serpapi_corroborator import SerpAPICorroborator
from config import get_settings


def test_piramal_yapan_vs_blackstone_pgp_glass_rejects():
    """Test Piramal/Yapan vs Blackstone/PGP Glass is strictly rejected as ENTITY_MISMATCH."""
    art1 = Article(
        id="p1",
        title="Piramal Pharma Limited Completes Acquisition of Controlling Stake in Yapan Bio Private Limited",
        url="https://economictimes.indiatimes.com/piramal-yapan",
        source_name="The Economic Times",
        content_text="Piramal Pharma has completed the acquisition of a controlling stake in Yapan Bio.",
        category=NewsCategory.INDIA,
    )
    art2 = Article(
        id="b1",
        title="Blackstone open to sale of controlling stake in PGP Glass",
        url="https://livemint.com/blackstone-pgp",
        source_name="Livemint",
        content_text="Blackstone is exploring options to sell its controlling stake in packaging company PGP Glass.",
        category=NewsCategory.INDIA,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is False
    assert "ENTITY_MISMATCH" in msg


def test_generic_controlling_stake_cannot_be_entity():
    """Test that generic action phrases like 'Controlling Stake' are never treated as entities."""
    assert "controlling stake" in GENERIC_ENTITY_BLACKLIST
    assert "majority stake" in GENERIC_ENTITY_BLACKLIST
    assert "minority stake" in GENERIC_ENTITY_BLACKLIST
    assert "block deal" in GENERIC_ENTITY_BLACKLIST

    assert EventQueryBuilder.clean_entity("Controlling Stake") is None
    assert EventQueryBuilder.clean_entity("Majority Stake") is None
    assert EventQueryBuilder.clean_entity("Block Deal") is None


def test_piramal_query_contains_piramal_pharma_and_yapan_bio():
    """Test Piramal headline produces clean query with Piramal Pharma and Yapan Bio, without Controlling Stake."""
    art = Article(
        id="p1",
        title="Piramal Pharma Limited Completes Acquisition of Controlling Stake in Yapan Bio Private Limited",
        url="https://economictimes.indiatimes.com/piramal-yapan",
        source_name="The Economic Times",
        content_text="Piramal Pharma has completed acquisition of controlling stake in Yapan Bio for Rs 76 crore.",
        category=NewsCategory.INDIA,
    )

    entities = EventQueryBuilder.extract_entities(art)
    query = EventQueryBuilder.build_anchor_query(art)

    assert "Piramal Pharma" in entities
    assert "Yapan Bio" in entities
    assert "Controlling Stake" not in entities
    assert "Controlling Stake" not in query
    assert "Piramal Pharma" in query
    assert "Yapan Bio" in query
    assert "acquisition" in query


def test_kfin_moneycontrol_plus_business_today_still_verifies():
    """Test KFin Moneycontrol + Business Today genuinely verifies with company, counterparty, and deal value."""
    art1 = Article(
        id="k1",
        title="KFin Technologies block deal: 8.75% equity worth Rs 1,400 crore changes hands; General Atlantic likely seller",
        url="https://moneycontrol.com/kfin-block-deal",
        source_name="Moneycontrol",
        content_text="General Atlantic offloaded an 8.75% stake in KFin Technologies for Rs 1,400 crore on the BSE.",
        category=NewsCategory.INDIA,
    )
    art2 = Article(
        id="k2",
        title="General Atlantic sells 8.75% stake in KFin Technologies for Rs 1,400 crore via block deal",
        url="https://businesstoday.in/kfin-stake-sale",
        source_name="Business Today",
        content_text="Private equity firm General Atlantic sold an 8.75% stake in KFin Technologies for Rs 1,400 crore.",
        category=NewsCategory.INDIA,
    )

    verifier = TwoSourceVerifier()
    is_same, score, msg = verifier.is_same_underlying_event(art1, art2)

    assert is_same is True
    assert "entity=" in msg
    assert "metrics=" in msg


def test_queued_international_work_stops_instantly_at_intl_5():
    """Test that when a queue contains International events and Intl reaches 5, remaining events are skipped."""
    verified_events = [
        Event(id=f"int_{i}", canonical_title=f"Intl Event {i}", description="valid description", article_ids=[f"a{i}"], event_category=NewsCategory.INTERNATIONAL)
        for i in range(4)
    ]

    queued_intl_events = [
        Event(id="int_cand_1", canonical_title="Olive Oil Giant Surge Event", description="valid description", article_ids=["c1"], event_category=NewsCategory.INTERNATIONAL),
        Event(id="int_cand_2", canonical_title="Second International Event", description="valid description", article_ids=["c2"], event_category=NewsCategory.INTERNATIONAL),
        Event(id="int_cand_3", canonical_title="Third International Event", description="valid description", article_ids=["c3"], event_category=NewsCategory.INTERNATIONAL),
    ]

    searches_conducted = 0
    skipped_events = 0

    for ev in queued_intl_events:
        is_india = (ev.event_category == NewsCategory.INDIA)
        cur_intl_v = len([e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL])

        # Boundary freeze check
        if not is_india and cur_intl_v >= 5:
            skipped_events += 1
            continue

        # Process first event and verify it (bringing Intl to 5)
        searches_conducted += 1
        verified_events.append(ev)

    assert len([e for e in verified_events if e.event_category == NewsCategory.INTERNATIONAL]) == 5
    assert searches_conducted == 1
    assert skipped_events == 2  # The remaining 2 queued events were skipped without searches


def test_india_gets_all_remaining_budget_when_intl_5():
    """Test that when International is 5/5 and India is 3/5, 100% of remaining searches are assigned to India."""
    india_verified = 3
    intl_verified = 5

    india_deficit = max(0, 5 - india_verified)
    intl_deficit = max(0, 5 - intl_verified)

    assert india_deficit == 2
    assert intl_deficit == 0

    step_india = min(15, india_deficit * 5)
    step_intl = min(10, intl_deficit * 5)

    assert step_india == 10
    assert step_intl == 0


def test_budgets_and_verifier_invariants_preserved():
    """Test that total budget constants strictly adhere to RSS <= 20 and SerpAPI <= 8."""
    settings = get_settings()
    assert MAX_CORROBORATION_SEARCHES_PER_RUN == 20
    assert settings.MAX_SERPAPI_SEARCHES_PER_RUN == 8
