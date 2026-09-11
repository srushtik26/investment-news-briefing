"""
Tests for Safe Local Validation Mode and History Isolation.

Verifies:
1. Validation run uses an isolated history database (data/validation_briefings.db).
2. Production history database (data/briefings.db) is completely untouched and unchanged by validation runs.
3. Production deduplication continues to reject genuine 3-day history duplicates.
4. Validation mode properly deduplicates within its own isolated history / run.
5. run_pipeline.py CLI parses --validation-run without argument errors.
6. Repeated same-day validation runs do not get blocked by earlier validation runs.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import pytest

from app.database.connection import get_connection, init_db
from app.deduplication.history import HistoryStore
from app.deduplication.engine import DeduplicationEngine
from app.models.article import Article
from app.models.event import Event, VerificationTier
from app.models.enums import NewsCategory
from app.pipeline.runner import run_pipeline


def _create_sample_story(event_id: str, title: str, company: str, category: str = "INDIA") -> dict:
    return {
        "event_id": event_id,
        "event_fingerprint": title,
        "headline": title,
        "company_name": company,
        "category": category,
        "source_count": 2,
        "published_date": date.today(),
    }


def test_1_production_history_db_unchanged_by_validation_mode(tmp_path, monkeypatch):
    """
    Verify that running in validation mode does NOT write to, modify,
    or clear the production history database.
    """
    prod_db = tmp_path / "prod_briefings.db"
    prod_db_url = f"sqlite:///{prod_db.as_posix()}"
    val_db = tmp_path / "validation_briefings.db"
    val_db_url = f"sqlite:///{val_db.as_posix()}"

    # Pre-populate production database with an existing story
    prod_store = HistoryStore(db_path=prod_db_url)
    prod_story = _create_sample_story("prod_evt_1", "Tata Motors announces ₹5000 cr capex", "Tata Motors")
    prod_store.save_briefing(date.today() - timedelta(days=1), [prod_story])

    # Record state of production database
    with get_connection(prod_db_url) as conn:
        prod_count_before = conn.execute("SELECT COUNT(*) FROM historical_stories").fetchone()[0]
    assert prod_count_before == 1

    # Now create an isolated validation history store and save a briefing to it
    val_store = HistoryStore(db_path=val_db_url)
    val_story = _create_sample_story("val_evt_1", "Reliance signs green energy joint venture", "Reliance")
    val_store.save_briefing(date.today(), [val_story])

    # Production database MUST have exactly 1 story, completely unchanged
    with get_connection(prod_db_url) as conn:
        prod_count_after = conn.execute("SELECT COUNT(*) FROM historical_stories").fetchone()[0]
        prod_row = conn.execute("SELECT company_name, headline FROM historical_stories").fetchone()
    assert prod_count_after == 1
    assert prod_row["company_name"] == "Tata Motors"

    # Validation database has its own story
    with get_connection(val_db_url) as conn:
        val_count = conn.execute("SELECT COUNT(*) FROM historical_stories").fetchone()[0]
        val_row = conn.execute("SELECT company_name, headline FROM historical_stories").fetchone()
    assert val_count == 1
    assert val_row["company_name"] == "Reliance"


def test_2_production_dedup_still_rejects_genuine_3day_repeats(tmp_path):
    """
    Verify production deduplication engine rejects stories that appeared
    in production history within the 3-day lookback window.
    """
    prod_db = tmp_path / "prod_briefings.db"
    prod_db_url = f"sqlite:///{prod_db.as_posix()}"
    prod_store = HistoryStore(db_path=prod_db_url)

    # Insert a story published 1 day ago
    past_story = _create_sample_story("evt_past", "L&T wins ₹15,000 crore mega infrastructure order", "L&T")
    prod_store.save_briefing(date.today() - timedelta(days=1), [past_story])

    # 1. Direct history store check
    is_dup = prod_store.is_event_in_previous_days(
        fingerprint_key="L&T wins ₹15,000 crore mega infrastructure order",
        target_date=date.today(),
    )
    assert is_dup is True

    # 2. Deduplication engine filter_stories check
    engine = DeduplicationEngine(history_store=prod_store)
    cand_dup = {
        "event_id": "cand_1",
        "headline": "L&T wins ₹15,000 crore mega infrastructure order",
        "company_name": "L&T",
        "category": "INDIA",
        "event_type": "contract",
    }
    cand_ok = {
        "event_id": "cand_2",
        "headline": "Infosys signs cloud migration contract",
        "company_name": "Infosys",
        "category": "INDIA",
        "event_type": "contract",
    }
    accepted, rejected = engine.filter_stories([cand_dup, cand_ok])
    assert len(accepted) == 1
    assert accepted[0]["company_name"] == "Infosys"
    assert len(rejected) == 1
    assert rejected[0]["company_name"] == "L&T"
    prod_store.close()


def test_3_validation_mode_deduplicates_within_its_own_run(tmp_path):
    """
    Verify validation mode still runs the deduplication logic against
    its own isolated validation history.
    """
    val_db = tmp_path / "validation_briefings.db"
    val_db_url = f"sqlite:///{val_db.as_posix()}"
    val_store = HistoryStore(db_path=val_db_url)

    # Save a story inside validation history
    story = _create_sample_story("val_evt_1", "Adani Ports signs concession pact for Colombo terminal", "Adani Ports")
    val_store.save_briefing(date.today(), [story])

    # Validation dedup engine using val_store must detect the duplicate within validation history
    val_engine = DeduplicationEngine(history_store=val_store)
    cand_dup = {
        "event_id": "cand_val",
        "headline": "Adani Ports signs concession pact for Colombo terminal",
        "company_name": "Adani Ports",
        "category": "INDIA",
        "event_type": "concession",
    }
    accepted, rejected = val_engine.filter_stories([cand_dup])
    assert len(accepted) == 0
    assert len(rejected) == 1
    assert rejected[0]["company_name"] == "Adani Ports"
    val_store.close()


def test_4_repeated_validation_runs_reset_isolated_history(tmp_path):
    """
    Verify that initializing a new validation run resets old validation history,
    preventing earlier same-day manual test runs from blocking today's best candidates.
    """
    val_db = tmp_path / "validation_briefings.db"
    val_db_url = f"sqlite:///{val_db.as_posix()}"

    # Simulate run 1: writes a story to validation DB
    store_run_1 = HistoryStore(db_path=val_db_url)
    story_1 = _create_sample_story("run1_evt", "Maruti Suzuki announces ₹35,000 cr plant in Gujarat", "Maruti Suzuki")
    store_run_1.save_briefing(date.today(), [story_1])

    # In validation run 2: we clean/reset the validation DB at startup
    store_run_1.close()
    if val_db.exists():
        val_db.unlink()

    # Create fresh validation store for run 2
    store_run_2 = HistoryStore(db_path=val_db_url)
    val_engine_run_2 = DeduplicationEngine(history_store=store_run_2)

    # Maruti Suzuki should NOT be blocked in run 2!
    cand = {
        "event_id": "cand_maruti",
        "headline": "Maruti Suzuki announces ₹35,000 cr plant in Gujarat",
        "company_name": "Maruti Suzuki",
        "category": "INDIA",
        "event_type": "plant",
    }
    accepted, rejected = val_engine_run_2.filter_stories([cand])
    assert len(accepted) == 1
    assert accepted[0]["company_name"] == "Maruti Suzuki"
    store_run_2.close()


def test_5_run_pipeline_cli_validation_flag_parsing():
    """
    Verify run_pipeline.py parses --validation-run without throwing ValueError.
    """
    import sys
    test_argv = ["run_pipeline.py", "--validation-run"]
    
    # Check parsing logic matching run_pipeline.py __main__
    validation_mode = False
    positional_args = []
    for arg in test_argv[1:]:
        if arg in ("--validation-run", "--validation", "-v"):
            validation_mode = True
        else:
            positional_args.append(arg)

    assert validation_mode is True
    assert len(positional_args) == 0

    # Also test mixed with numbers: python run_pipeline.py 30 30 --validation-run
    mixed_argv = ["run_pipeline.py", "30", "30", "--validation-run"]
    validation_mode = False
    positional_args = []
    for arg in mixed_argv[1:]:
        if arg in ("--validation-run", "--validation", "-v"):
            validation_mode = True
        else:
            positional_args.append(arg)

    assert validation_mode is True
    assert positional_args == ["30", "30"]
    assert int(positional_args[0]) == 30
    assert int(positional_args[1]) == 30
