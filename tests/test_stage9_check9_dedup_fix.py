import os
from datetime import date, datetime, timezone
from pathlib import Path
import pytest

from app.models.event import Event
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.ai.models import BriefingEditorialPayload, EditorialStorySelection
from app.deduplication.history import HistoryStore
from app.deduplication.fingerprint import (
    generate_event_fingerprint,
    is_event_historical_repeat,
)
from app.validation.engine import FinalValidationEngine


def test_is_event_historical_repeat_cross_type_general_and_specific(tmp_path: Path):
    db_path = tmp_path / "test_history.db"
    store = HistoryStore(db_path=db_path)

    # 1. Past briefing (2 days ago) saved with "general" event_type
    past_date = date(2026, 9, 28)
    today = date(2026, 9, 30)
    
    fp_key, _ = generate_event_fingerprint(
        company="Reliance Industries",
        event_type="general",
        key_facts=["5000000000"],
        event_date=past_date,
    )
    store.save_briefing(
        briefing_date=past_date,
        stories=[{
            "event_id": "past_1",
            "event_fingerprint": fp_key,
            "headline": "Reliance Industries expands retail footprint with 5000000000 investment",
            "company_name": "Reliance Industries",
            "category": "india",
            "source_count": 2,
            "published_date": past_date,
        }]
    )

    # 2. Incoming candidate has specific event_type "EXPANSION" and different title
    cand_event = Event(
        id="cand_1",
        article_ids=["art_1"],
        canonical_title="Reliance Retail massive scale up",
        description="Reliance Industries expands retail footprint with 5000000000 investment",
        companies_involved=["Reliance Industries"],
        financial_figures=["5000000000"],
        event_category=NewsCategory.INDIA,
        metadata={"event_type": "EXPANSION"},
    )

    is_rep, reason = is_event_historical_repeat(
        event=cand_event,
        history_store=store,
        target_date=today,
        lookback_days=3,
        headline=cand_event.canonical_title,
    )

    assert is_rep is True
    assert "already appeared in briefing within previous 3 days" in reason or "fingerprint" in reason


def test_same_day_rerun_does_not_collide_as_3day_repeat(tmp_path: Path):
    db_path = tmp_path / "test_history_sameday.db"
    store = HistoryStore(db_path=db_path)

    today = date(2026, 9, 30)

    # Simulate earlier failed / partial run today
    fp_key, _ = generate_event_fingerprint(
        company="Tata Motors",
        event_type="general",
        key_facts=["1000000"],
        event_date=today,
    )
    store.save_briefing(
        briefing_date=today,
        stories=[{
            "event_id": "today_run1",
            "event_fingerprint": fp_key,
            "headline": "Tata Motors reports EV sales growth",
            "company_name": "Tata Motors",
            "category": "india",
            "source_count": 2,
            "published_date": today,
        }]
    )

    # On rerun today with target_date=today, lookback should NOT flag today's earlier attempt as a "previous 3 days" duplicate
    recent_fps = store.get_recent_fingerprints(target_date=today, lookback_days=3, include_target_date=False)
    assert fp_key not in recent_fps

    cand_event = Event(
        id="today_run2",
        article_ids=["art_2"],
        canonical_title="Tata Motors reports EV sales growth",
        description="Tata Motors reports EV sales growth",
        companies_involved=["Tata Motors"],
        financial_figures=["1000000"],
        event_category=NewsCategory.INDIA,
    )

    is_rep, _ = is_event_historical_repeat(
        event=cand_event,
        history_store=store,
        target_date=today,
        lookback_days=3,
        headline=cand_event.canonical_title,
    )
    assert is_rep is False

    # Also verify save_briefing replaces previous entries for same date without duplicate explosion
    store.save_briefing(
        briefing_date=today,
        stories=[{
            "event_id": "today_run2_clean",
            "event_fingerprint": fp_key,
            "headline": "Tata Motors reports EV sales growth updated",
            "company_name": "Tata Motors",
            "category": "india",
            "source_count": 2,
            "published_date": today,
        }]
    )
    all_today = store.get_recent_stories(target_date=today, lookback_days=0, include_target_date=True)
    assert len(all_today) == 1
    assert all_today[0]["event_id"] == "today_run2_clean"


def test_final_validation_check_9_with_historical_repeat(tmp_path: Path):
    db_path = tmp_path / "test_val_check9.db"
    store = HistoryStore(db_path=db_path)

    past_date = date(2026, 9, 28)
    today = date(2026, 9, 30)

    fp_key, _ = generate_event_fingerprint(
        company="Infosys",
        event_type="general",
        key_facts=["200000000"],
        event_date=past_date,
    )
    store.save_briefing(
        briefing_date=past_date,
        stories=[{
            "event_id": "infy_old",
            "event_fingerprint": fp_key,
            "headline": "Infosys secures mega deal worth 200M",
            "company_name": "Infosys",
            "category": "india",
            "source_count": 2,
            "published_date": past_date,
        }]
    )

    ev = Event(
        id="infy_new",
        article_ids=["art_infy"],
        canonical_title="Infosys secures mega deal worth 200M",
        description="Infosys secures mega deal worth 200M",
        companies_involved=["Infosys"],
        financial_figures=["200000000"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    art = Article(
        id="art_infy",
        url="https://economictimes.indiatimes.com/tech/infy-deal",
        title="Infosys secures mega deal worth 200M",
        content_text="Infosys has announced a contract valued at 200M with a global enterprise.",
        published_date=datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
        source_domain="economictimes.indiatimes.com",
        source_name="The Economic Times",
    )

    validator = FinalValidationEngine(history_store=store)
    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[
            EditorialStorySelection(
                event_id="infy_new",
                headline="Infosys secures mega deal worth 200M",
                summary="Infosys has announced a contract valued at 200M with a global enterprise.",
                section="india",
                source="The Economic Times",
                url="https://economictimes.indiatimes.com/tech/infy-deal",
            )
        ],
        international_stories=[],
    )

    report = validator.validate_briefing(
        payload=payload,
        events_lookup={"infy_new": ev},
        articles_lookup={"art_infy": art},
        target_date=today,
        strict_5_per_section=False,
    )

    check_9 = next((c for c in report.check_results if c.check_id == 9), None)
    assert check_9 is not None
    assert check_9.passed is False
    assert "already appeared in briefing within previous 3 days" in check_9.failure_reason


def test_stage_8_5_self_healing_replaces_duplicate_with_reserve(tmp_path: Path):
    from unittest.mock import MagicMock
    from app.pipeline.context import PipelineContext
    from app.pipeline.story_context import build_story_context
    from app.pipeline.story_prep import prepare_final_story

    db_path = tmp_path / "test_self_healing.db"
    store = HistoryStore(db_path=db_path)

    past_date = date(2026, 9, 28)
    today = date(2026, 9, 30)

    # Save past event for HDFC Bank
    fp_key, _ = generate_event_fingerprint(
        company="HDFC Bank",
        event_type="general",
        key_facts=["5000000000"],
        event_date=past_date,
    )
    store.save_briefing(
        briefing_date=past_date,
        stories=[{
            "event_id": "hdfc_past",
            "event_fingerprint": fp_key,
            "headline": "HDFC Bank expands digital loan portfolio by 5000000000",
            "company_name": "HDFC Bank",
            "category": "india",
            "source_count": 2,
            "published_date": past_date,
        }]
    )

    # Event 1: Duplicate HDFC event
    ev_dup = Event(
        id="hdfc_dup",
        article_ids=["art_hdfc"],
        canonical_title="HDFC Bank expands digital loan portfolio by 5000000000",
        description="HDFC Bank expands digital loan portfolio by 5000000000",
        companies_involved=["HDFC Bank"],
        financial_figures=["5000000000"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    art_hdfc = Article(
        id="art_hdfc",
        url="https://livemint.com/banking/hdfc-loans",
        title="HDFC Bank expands digital loan portfolio by 5000000000",
        content_text="HDFC Bank expands digital loan portfolio by 5000000000.",
        published_date=datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
        source_domain="livemint.com",
        source_name="Livemint",
    )

    # Event 2: Clean Reserve Wipro event
    ev_reserve = Event(
        id="wipro_clean",
        article_ids=["art_wipro"],
        canonical_title="Wipro wins 500M cloud migration deal",
        description="Wipro wins 500M cloud migration deal from European client.",
        companies_involved=["Wipro"],
        financial_figures=["500000000"],
        event_category=NewsCategory.INDIA,
        verification_tier=VerificationTier.TWO_SOURCE_VERIFIED,
    )
    art_wipro = Article(
        id="art_wipro",
        url="https://economictimes.indiatimes.com/tech/wipro-deal",
        title="Wipro wins 500M cloud migration deal",
        content_text="Wipro has secured a cloud migration deal valued at 500M.",
        published_date=datetime(2026, 9, 30, 8, 30, tzinfo=timezone.utc),
        source_domain="economictimes.indiatimes.com",
        source_name="The Economic Times",
    )

    payload = BriefingEditorialPayload(
        domestic_stories=[],
        india_stories=[
            EditorialStorySelection(
                event_id="hdfc_dup",
                headline="HDFC Bank expands digital loan portfolio by 5000000000",
                summary="HDFC Bank expands digital loan portfolio by 5000000000.",
                section="india",
                source="Livemint",
                url="https://livemint.com/banking/hdfc-loans",
            )
        ],
        international_stories=[],
    )

    event_by_id = {"hdfc_dup": ev_dup, "wipro_clean": ev_reserve}
    articles_lookup = {"art_hdfc": art_hdfc, "art_wipro": art_wipro}

    ctx = MagicMock(spec=PipelineContext)
    ctx.articles_lookup = articles_lookup
    ctx.verified_events = [ev_reserve]
    ctx.dedup_rejected_event_ids = set()

    # Verify is_event_historical_repeat flags ev_dup
    is_rep, rep_reason = is_event_historical_repeat(
        event=ev_dup,
        history_store=store,
        target_date=today,
        lookback_days=3,
        headline=payload.india_stories[0].headline,
    )
    assert is_rep is True

    # Simulate Stage 8.5 swap logic
    raw_cands = ctx.verified_events
    for res_ev in raw_cands:
        cand_rep, _ = is_event_historical_repeat(
            event=res_ev,
            history_store=store,
            target_date=today,
            lookback_days=3,
            headline=res_ev.canonical_title,
        )
        if not cand_rep:
            sc = build_story_context(res_ev, articles_lookup[res_ev.article_ids[0]], ctx=ctx)
            rep_story = prepare_final_story(sc, ctx=ctx)
            payload.india_stories[0] = rep_story
            break

    assert payload.india_stories[0].event_id == "wipro_clean"

    # Now Stage 9 Check 9 validator should pass
    validator = FinalValidationEngine(history_store=store)
    report = validator.validate_briefing(
        payload=payload,
        events_lookup=event_by_id,
        articles_lookup=articles_lookup,
        target_date=today,
        strict_5_per_section=False,
    )
    check_9 = next((c for c in report.check_results if c.check_id == 9), None)
    assert check_9 is not None
    assert check_9.passed is True

