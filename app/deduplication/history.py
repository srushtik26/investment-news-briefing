"""
SQLite Briefing History Store.

Manages persistence of generated briefing stories and executes 3-day lookback queries
to prevent re-selecting previously reported business events.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Set
import uuid

import sqlite3
from pathlib import Path
from app.logging_config import get_logger
from app.database.connection import get_connection, init_db, resolve_db_path, init_db_conn
from config import is_testing_or_dry_run

logger = get_logger("deduplication.history")


class HistoryStore:
    """
    Interface for querying and persisting briefing story history in SQLite.
    Supports isolated in-memory history for dry-run / testing mode to prevent disk mutations.
    """

    def __init__(self, db_path: Optional[str] = None, is_isolated: Optional[bool] = None) -> None:
        self.db_path = db_path
        if is_isolated is None:
            self.is_isolated = is_testing_or_dry_run()
        else:
            self.is_isolated = is_isolated

        if self.is_isolated:
            logger.info("DRY_RUN_HISTORY_MODE=isolated")
            self._mem_conn: Optional[sqlite3.Connection] = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            target_path = resolve_db_path(self.db_path)
            if not target_path.startswith("file:") and Path(target_path).exists():
                try:
                    disk_conn = sqlite3.connect(target_path, check_same_thread=False)
                    disk_conn.backup(self._mem_conn)
                    disk_conn.close()
                    logger.info("DRY_RUN_HISTORY: Cloned %s into in-memory store for isolated read", target_path)
                except Exception as e:
                    logger.warning("Could not backup disk DB into isolated memory store: %s", e)
                    init_db_conn(self._mem_conn)
            else:
                init_db_conn(self._mem_conn)
            self._keepalive_conn = self._mem_conn
        else:
            self._mem_conn = None
            self._keepalive_conn = get_connection(self.db_path)
            init_db(self.db_path)

    def _get_active_conn(self) -> Tuple[sqlite3.Connection, bool]:
        """Return active connection and a boolean indicating whether it should be closed."""
        if self.is_isolated and self._mem_conn is not None:
            return self._mem_conn, False
        return get_connection(self.db_path), True

    def save_briefing(
        self,
        briefing_date: date,
        stories: List[dict],
        status: str = "COMPLETED",
    ) -> str:
        """
        Record a completed briefing run and its selected stories in SQLite.

        Args:
            briefing_date: The date of the briefing.
            stories: List of story dictionaries containing event_id, fingerprint, headline, company, category.
            status: Status string.

        Returns:
            The created BriefingHistory ID.
        """
        conn, should_close = self._get_active_conn()
        briefing_id = str(uuid.uuid4())
        created_at_str = datetime.now(timezone.utc).isoformat()
        date_str = briefing_date.isoformat()

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO briefing_history (id, briefing_date, status, story_count, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (briefing_id, date_str, status, len(stories), created_at_str),
                )

                for item in stories:
                    story_id = str(uuid.uuid4())
                    pub_date = item.get("published_date")
                    pub_date_str = pub_date.isoformat() if isinstance(pub_date, (date, datetime)) else date_str

                    conn.execute(
                        """
                        INSERT INTO historical_stories (
                            id, briefing_id, event_id, event_fingerprint, headline,
                            company_name, category, source_count, published_date, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            story_id,
                            briefing_id,
                            item.get("event_id", ""),
                            item.get("event_fingerprint", ""),
                            item.get("headline", ""),
                            item.get("company_name", ""),
                            item.get("category", "india"),
                            item.get("source_count", 1),
                            pub_date_str,
                            created_at_str,
                        ),
                    )

            if self.is_isolated:
                logger.info("Saved %d historical stories to isolated history (DRY_RUN_HISTORY_MODE=isolated) for briefing date %s", len(stories), briefing_date)
            else:
                logger.info("Saved %d historical stories for briefing date %s", len(stories), briefing_date)
            return briefing_id
        finally:
            if should_close:
                conn.close()

    def get_recent_stories(
        self,
        lookback_days: int = 3,
        target_date: Optional[date] = None,
    ) -> List[dict]:
        """
        Fetch all historical stories recorded in the previous N days.
        """
        current_date = target_date or date.today()
        start_date = current_date - timedelta(days=lookback_days)

        start_str = start_date.isoformat()
        end_str = current_date.isoformat()

        conn, should_close = self._get_active_conn()
        try:
            cursor = conn.execute(
                """
                SELECT id, event_id, event_fingerprint, headline, company_name, category, published_date
                FROM historical_stories
                WHERE published_date >= ? AND published_date <= ?
                """,
                (start_str, end_str),
            )
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "event_id": r[1],
                    "event_fingerprint": r[2],
                    "headline": r[3],
                    "company_name": r[4],
                    "category": r[5],
                    "published_date": r[6],
                }
                for r in rows
            ]
        finally:
            if should_close:
                conn.close()

    def get_recent_fingerprints(
        self,
        lookback_days: int = 3,
        target_date: Optional[date] = None,
        reference_date: Optional[date] = None,
        days: Optional[int] = None,
    ) -> Set[str]:
        """
        Fetch all event fingerprints and headlines recorded in the previous N days.

        Lookback window: [target_date - lookback_days, target_date]
        """
        effective_days = days if days is not None else lookback_days
        effective_date = reference_date or target_date
        current_date = effective_date or date.today()
        start_date = current_date - timedelta(days=effective_days)

        start_str = start_date.isoformat()
        end_str = current_date.isoformat()

        conn, should_close = self._get_active_conn()
        try:
            cursor = conn.execute(
                """
                SELECT DISTINCT event_fingerprint, headline
                FROM historical_stories
                WHERE published_date >= ? AND published_date <= ?
                """,
                (start_str, end_str),
            )
            rows = cursor.fetchall()
            fingerprints = set()
            from app.deduplication.fingerprint import strip_date_from_fingerprint
            import hashlib

            for r in rows:
                if r[0]:
                    fp = r[0]
                    fingerprints.add(fp)
                    stable_fp = strip_date_from_fingerprint(fp)
                    if stable_fp:
                        fingerprints.add(stable_fp)
                        fingerprints.add(hashlib.sha256(stable_fp.encode("utf-8")).hexdigest())
                if r[1]:
                    fingerprints.add(r[1])
            logger.debug("Found %d historical fingerprints/headlines in %d-day lookback window", len(fingerprints), lookback_days)
            return fingerprints
        finally:
            if should_close:
                conn.close()

    def is_event_in_previous_days(
        self,
        fingerprint_key: str,
        lookback_days: int = 3,
        target_date: Optional[date] = None,
    ) -> bool:
        """
        Check if an event fingerprint has already appeared in the previous lookback_days.
        """
        recent_fps = self.get_recent_fingerprints(lookback_days=lookback_days, target_date=target_date)
        return fingerprint_key in recent_fps

    def close(self) -> None:
        """Close the keepalive connection."""
        if hasattr(self, "_mem_conn") and self._mem_conn:
            try:
                self._mem_conn.close()
            except Exception:
                pass
            self._mem_conn = None
        if hasattr(self, "_keepalive_conn") and self._keepalive_conn:
            try:
                self._keepalive_conn.close()
            except Exception:
                pass
            self._keepalive_conn = None


