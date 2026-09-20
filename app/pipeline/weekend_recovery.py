"""
Weekend State Persistence & Targeted Recovery.

Saves non-secret candidate pool state when a weekend pipeline run finishes
with fewer than 15 stories, and restores the partial pool on the next
scheduled recovery attempt so targeted rescue passes can complete the briefing
without re-running full baseline discovery from scratch.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.models.article import Article
from app.models.event import Event
from app.discovery.models import DiscoveredArticle
from app.pipeline.context import PipelineContext

logger = logging.getLogger("pipeline.weekend_recovery")

PARTIAL_STATE_FILENAME = "partial_weekend_state.json"


def save_partial_weekend_state(
    ctx: PipelineContext,
    domestic_count: int,
    india_count: int,
    intl_count: int,
    data_dir: Path,
) -> Optional[Path]:
    """
    Save non-secret partial state for weekend recovery if run ended below 15 stories.
    """
    if not ctx.is_weekend:
        return None

    if domestic_count >= 5 and india_count >= 5 and intl_count >= 5:
        # Full success, no need to persist partial state
        cleanup_partial_weekend_state(data_dir)
        return None

    if (
        domestic_count == 0
        and india_count == 0
        and intl_count == 0
        and not getattr(ctx, "verified_events", None)
        and not getattr(ctx, "high_confidence_single_candidates", None)
    ):
        return None

    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / PARTIAL_STATE_FILENAME
    tmp_file = data_dir / f"{PARTIAL_STATE_FILENAME}.tmp"

    try:
        payload = {
            "target_date": ctx.target_date.isoformat(),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "section_counts": {
                "domestic": domestic_count,
                "india": india_count,
                "international": intl_count,
            },
            "verified_events": [e.model_dump(mode="json") for e in ctx.verified_events],
            "high_confidence_single_candidates": [
                e.model_dump(mode="json") for e in ctx.high_confidence_single_candidates
            ],
            "articles_lookup": {
                aid: art.model_dump(mode="json") for aid, art in ctx.articles_lookup.items()
            },
            "seen_urls": list(ctx.seen_urls),
            "degraded_domains": list(getattr(ctx.extractor, "degraded_domains", set())),
            "domestic_reserve_pool": [
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c.__dict__
                for c in ctx.domestic_reserve_pool
            ],
            "india_reserve_pool": [
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c.__dict__
                for c in ctx.india_reserve_pool
            ],
            "intl_reserve_pool": [
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c.__dict__
                for c in ctx.intl_reserve_pool
            ],
        }

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, state_file)

        msg = (
            f"[WEEKEND_PARTIAL_STATE_SAVED] Saved partial state "
            f"(Dom={domestic_count}/5, India={india_count}/5, Intl={intl_count}/5) to {state_file.name}"
        )
        ctx.log_exec(msg)
        logger.info(msg)
        return state_file
    except Exception as e:
        logger.warning("Failed to save partial weekend state: %s", e)
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return None


def restore_partial_weekend_state(ctx: PipelineContext, data_dir: Path) -> bool:
    """
    Restore partial state from previous weekend run for the same target date.
    Returns True if successfully restored, False otherwise.
    """
    if not ctx.is_weekend:
        return False

    state_file = data_dir / PARTIAL_STATE_FILENAME
    if not state_file.exists():
        return False

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_date_str = data.get("target_date")
        if saved_date_str != ctx.target_date.isoformat():
            logger.info("Partial weekend state is for date %s, current is %s; removing stale state", saved_date_str, ctx.target_date.isoformat())
            cleanup_partial_weekend_state(data_dir)
            return False

        # Restore articles lookup
        for aid, art_dict in data.get("articles_lookup", {}).items():
            try:
                ctx.articles_lookup[aid] = Article.model_validate(art_dict)
            except Exception as e:
                logger.debug("Failed restoring article %s: %s", aid, e)

        ctx.all_extracted = list(ctx.articles_lookup.values())

        # Restore verified events
        for ev_dict in data.get("verified_events", []):
            try:
                ev = Event.model_validate(ev_dict)
                if not any(x.id == ev.id for x in ctx.verified_events):
                    ctx.verified_events.append(ev)
            except Exception as e:
                logger.debug("Failed restoring verified event: %s", e)

        # Restore high confidence singles
        for ev_dict in data.get("high_confidence_single_candidates", []):
            try:
                ev = Event.model_validate(ev_dict)
                if not any(x.id == ev.id for x in ctx.high_confidence_single_candidates):
                    ctx.high_confidence_single_candidates.append(ev)
            except Exception as e:
                logger.debug("Failed restoring high confidence event: %s", e)

        # Restore seen URLs
        for u in data.get("seen_urls", []):
            ctx.seen_urls.add(u.strip().lower().rstrip("/"))

        # Restore degraded domains
        if hasattr(ctx.extractor, "degraded_domains"):
            for d in data.get("degraded_domains", []):
                ctx.extractor.degraded_domains.add(d)

        # Restore reserve pools
        for pool_key, target_list in [
            ("domestic_reserve_pool", ctx.domestic_reserve_pool),
            ("india_reserve_pool", ctx.india_reserve_pool),
            ("intl_reserve_pool", ctx.intl_reserve_pool),
        ]:
            for c_dict in data.get(pool_key, []):
                try:
                    target_list.append(DiscoveredArticle.model_validate(c_dict))
                except Exception:
                    pass

        total_restored = len(ctx.verified_events) + len(ctx.high_confidence_single_candidates)
        msg = f"WEEKEND_RESTORED_PARTIAL_POOL count={total_restored}"
        ctx.log_exec(msg)
        logger.info(msg)
        if total_restored == 0:
            logger.info("No verified events or candidates restored from partial weekend state; proceeding with full run.")
            return False
        return True

    except Exception as e:
        logger.warning("Failed restoring partial weekend state: %s", e)
        return False


def cleanup_partial_weekend_state(data_dir: Path) -> None:
    """Clean up partial weekend state file upon full success or date rollover."""
    state_file = data_dir / PARTIAL_STATE_FILENAME
    if state_file.exists():
        try:
            state_file.unlink()
            logger.info("Cleaned up partial weekend state %s", state_file)
        except Exception as e:
            logger.warning("Could not unlink %s: %s", state_file, e)
