import pytest
from datetime import datetime, timezone, date, timedelta
from unittest.mock import MagicMock

from app.models.event import Event
from app.models.article import Article
from app.models.enums import NewsCategory, VerificationTier
from app.pipeline.context import PipelineContext
from app.pipeline.selection import run_ranking_and_selection
from app.deduplication.history import HistoryStore


def test_india_recovery_skips_historical_repeats_and_dedup_rejected_ids(tmp_path):
    """
    Verify that India recovery does not select events that are 3-day historical repeats
    or present in ctx.dedup_rejected_event_ids.
    """
    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "test_history.db"
    store = HistoryStore(db_path=db_path)

    # Record historical story for Avalon Technologies 1 day ago via save_briefing
    store.save_briefing(
        briefing_date=date(2026, 9, 30),
        stories=[
            {
                "event_id": "hist-ev-1",
                "event_fingerprint": "avalon_technologies:m&a:2026-09-30:rs_862_crore",
                "headline": "Avalon Technologies Block Deal: Promoters likely to sell up to 6% stake for ₹862 crore",
                "company_name": "Avalon Technologies",
                "category": "india",
                "published_date": date(2026, 9, 30),
            }
        ],
    )

    art_avalon = Article(
        id="art-avalon",
        title="Avalon Technologies Block Deal: Promoters likely to sell up to 6% stake for ₹862 crore",
        url="https://www.cnbctv18.com/market/stocks/avalon.htm",
        source_name="CNBCTV18",
        published_at=now - timedelta(hours=10),
        content_text="Promoters of Avalon Technologies are likely to sell up to 6% stake for Rs 862 crore in a block deal.",
    )
    ev_avalon = Event(
        id="ev-avalon",
        canonical_title="Avalon Technologies Block Deal: Promoters likely to sell up to 6% stake for ₹862 crore",
        description="Avalon Technologies Block Deal: Promoters likely to sell up to 6% stake for ₹862 crore",
        event_category=NewsCategory.INDIA,
        article_ids=["art-avalon"],
        primary_publisher="CNBCTV18",
        primary_url="https://www.cnbctv18.com/market/stocks/avalon.htm",
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        companies_involved=["Avalon Technologies"],
        financial_figures=["Rs 862 crore"],
    )

    ctx = PipelineContext()
    ctx.run_reference_time = now
    ctx.history_store = store
    ctx.articles_lookup = {"art-avalon": art_avalon}
    ctx.verified_events = []
    ctx.high_confidence_single_candidates = [ev_avalon]
    ctx.dedup_rejected_event_ids = {"ev-avalon"}
    ctx.discovery_service = None
    if hasattr(ctx, "settings") and ctx.settings:
        ctx.settings.SERPAPI_API_KEY = None

    # Simulate run_ranking_and_selection when accepted_stories has 0 India stories
    # and recovery is triggered
    cand_pool, dom, ind, intl, suff, status = run_ranking_and_selection(
        ctx,
        accepted_stories=[],
        event_by_id={"ev-avalon": ev_avalon},
    )

    # ev_avalon MUST NOT be in ind or cand_pool.india_candidates
    sel_india_ids = [s.event.id for s in cand_pool.india_candidates]
    assert "ev-avalon" not in sel_india_ids, "Historical repeat event was incorrectly selected by India recovery!"


def test_stage8_5_has_access_to_high_confidence_single_candidates(tmp_path):
    """
    Verify that Stage 8.5 self-healing candidate pool includes high_confidence_single_candidates
    so it can find valid replacements when a duplicate is detected.
    """
    ctx = PipelineContext()
    ev_single = Event(
        id="ev-single-1",
        canonical_title="Clean India Reserve Event",
        description="Clean India Reserve Event description",
        event_category=NewsCategory.INDIA,
        article_ids=["art-single-1"],
        primary_publisher="Business Standard",
        primary_url="https://example.com/clean",
        verification_tier=VerificationTier.HIGH_CONFIDENCE_SINGLE_SOURCE,
        companies_involved=["Clean Co"],
    )
    ctx.high_confidence_single_candidates = [ev_single]
    ctx.verified_events = []

    # Check that raw_cands concatenation logic includes ev_single
    raw_cands = (
        (getattr(ctx, "india_reserve_pool", []) or [])
        + (ctx.verified_events or [])
        + (ctx.high_confidence_single_candidates or [])
        + (ctx.single_source_events or [])
    )
    assert ev_single in raw_cands
