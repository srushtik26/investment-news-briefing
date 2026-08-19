"""
SQLite Briefing History Store.

Manages persistence of generated briefing stories and executes 3-day lookback queries
to prevent re-selecting previously reported business events.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Set
import uuid

from app.logging_config import get_logger
from app.database.connection import get_connection, init_db

logger = get_logger("deduplication.history")


class HistoryStore:
    """
    Interface for querying and persisting briefing story history in SQLite.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path
        self._keepalive_conn = get_connection(self.db_path)
        init_db(self.db_path)

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
        conn = get_connection(self.db_path)
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

            logger.info("Saved %d historical stories for briefing date %s", len(stories), briefing_date)
            return briefing_id
        finally:
            conn.close()

    def get_recent_fingerprints(
        self,
        lookback_days: int = 3,
        target_date: Optional[date] = None,
    ) -> Set[str]:
        """
        Fetch all event fingerprints recorded in the previous N days.

        Lookback window: [target_date - lookback_days, target_date]
        """
        current_date = target_date or date.today()
        start_date = current_date - timedelta(days=lookback_days)

        start_str = start_date.isoformat()
        end_str = current_date.isoformat()

        conn = get_connection(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT DISTINCT event_fingerprint
                FROM historical_stories
                WHERE published_date >= ? AND published_date <= ?
                """,
                (start_str, end_str),
            )
            rows = cursor.fetchall()
            fingerprints = {row[0] for row in rows if row[0]}
            logger.debug("Found %d historical fingerprints in %d-day lookback window", len(fingerprints), lookback_days)
            return fingerprints
        finally:
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
