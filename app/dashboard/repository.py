"""
Repository for dashboard database access and persistence.
Uses a completely separate database at data/dashboard.db.
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, List, Optional, Dict, Any, Union

from app.dashboard.models import DashboardBriefing, DashboardStory


DEFAULT_DB_PATH = Path("data") / "dashboard.db"


class DashboardSyncConflictError(Exception):
    """Raised when an incoming briefing differs from an existing stored briefing for the same date."""
    pass


def compute_briefing_content_hash(briefing: DashboardBriefing) -> str:
    """Compute a deterministic SHA-256 fingerprint for a briefing's stories."""
    canonical_parts = []
    for s in sorted(briefing.stories, key=lambda x: (x.section, x.position)):
        canonical_parts.append(
            f"{s.section.lower()}:{s.position}:{s.headline.strip().lower()}:{s.url.strip()}"
        )
    raw_str = "|".join(canonical_parts)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


class DashboardRepository:
    """
    Data access layer for stored dashboard briefings and stories.
    Completely isolated from the pipeline's history and working databases.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create tables and unique constraints if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS briefings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    briefing_date TEXT UNIQUE NOT NULL,
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    full_text TEXT,
                    story_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT,
                    sync_source TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Ensure metadata columns exist in case table was created previously without them
            cursor.execute("PRAGMA table_info(briefings)")
            cols = {row["name"] for row in cursor.fetchall()}
            if "content_hash" not in cols:
                cursor.execute("ALTER TABLE briefings ADD COLUMN content_hash TEXT")
            if "sync_source" not in cols:
                cursor.execute("ALTER TABLE briefings ADD COLUMN sync_source TEXT")
            if "synced_at" not in cols:
                cursor.execute("ALTER TABLE briefings ADD COLUMN synced_at TIMESTAMP")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    briefing_id INTEGER NOT NULL REFERENCES briefings(id) ON DELETE CASCADE,
                    section TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    headline TEXT NOT NULL,
                    summary TEXT,
                    source TEXT,
                    url TEXT NOT NULL,
                    UNIQUE(briefing_id, section, position)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_briefings_date ON briefings(briefing_date)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_stories_briefing ON stories(briefing_id)"
            )

    def save_briefing(self, briefing: DashboardBriefing, allow_replace: bool = False) -> int:
        """
        Save a briefing and its stories into dashboard.db.

        Idempotency and Conflict Protection:
        1. If no briefing exists for briefing_date: inserts cleanly.
        2. If a briefing already exists for briefing_date:
           - Compares content_hash.
           - If content_hash matches: idempotent no-op (returns existing ID).
           - If content_hash differs and not allow_replace:
             raises DashboardSyncConflictError.
           - If allow_replace is True:
             safely updates the record and replaces stories.
        """
        date_str = briefing.briefing_date.isoformat()
        story_count = len(briefing.stories)
        new_hash = briefing.content_hash or compute_briefing_content_hash(briefing)
        briefing.content_hash = new_hash
        sync_time = datetime.utcnow()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, story_count, content_hash FROM briefings WHERE briefing_date = ?",
                (date_str,),
            )
            row = cursor.fetchone()

            if row:
                existing_id = row["id"]
                existing_hash = row["content_hash"]

                # If hashes match (or existing has no hash but count is 15 and texts identical):
                if existing_hash and existing_hash == new_hash:
                    return existing_id

                # If content differs and overwrite was not explicitly authorized:
                if not allow_replace:
                    raise DashboardSyncConflictError(
                        f"[DASHBOARD_SYNC_CONFLICT] date={date_str} existing briefing differs from incoming briefing. "
                        f"Use --replace-date {date_str} to overwrite."
                    )

                # Overwrite explicitly permitted via --replace-date
                cursor.execute(
                    """
                    UPDATE briefings
                    SET generated_at = ?, full_text = ?, story_count = ?, content_hash = ?, sync_source = ?, synced_at = ?
                    WHERE id = ?
                    """,
                    (
                        briefing.generated_at or sync_time,
                        briefing.full_text,
                        story_count,
                        new_hash,
                        briefing.sync_source or "direct_save",
                        sync_time,
                        existing_id,
                    ),
                )
                cursor.execute("DELETE FROM stories WHERE briefing_id = ?", (existing_id,))
                briefing_id = existing_id
            else:
                cursor.execute(
                    """
                    INSERT INTO briefings (briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        date_str,
                        briefing.generated_at or sync_time,
                        briefing.full_text,
                        story_count,
                        new_hash,
                        briefing.sync_source or "direct_save",
                        sync_time,
                    ),
                )
                briefing_id = cursor.lastrowid

            # Insert stories
            for story in briefing.stories:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO stories (briefing_id, section, position, headline, summary, source, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        briefing_id,
                        story.section.lower(),
                        story.position,
                        story.headline,
                        story.summary,
                        story.source,
                        story.url,
                    ),
                )

            return briefing_id

    def get_latest_briefing(self) -> Optional[DashboardBriefing]:
        """Fetch the most recent briefing with all associated stories."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at
                FROM briefings
                ORDER BY briefing_date DESC
                LIMIT 1
                """
            )
            b_row = cursor.fetchone()
            if not b_row:
                return None

            return self._build_briefing_from_row(cursor, b_row)

    def get_briefing_by_date(self, target_date: Union[date, str]) -> Optional[DashboardBriefing]:
        """Fetch briefing for a specific date with all associated stories."""
        date_str = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at
                FROM briefings
                WHERE briefing_date = ?
                """,
                (date_str,),
            )
            b_row = cursor.fetchone()
            if not b_row:
                return None

            return self._build_briefing_from_row(cursor, b_row)

    def get_archive_dates(self) -> List[Dict[str, Any]]:
        """Fetch all stored briefing dates and metadata for the archive view."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, briefing_date, generated_at, story_count, content_hash, sync_source
                FROM briefings
                ORDER BY briefing_date DESC
                """
            )
            rows = cursor.fetchall()
            archive = []
            for r in rows:
                b_date = date.fromisoformat(r["briefing_date"])
                archive.append({
                    "id": r["id"],
                    "briefing_date": b_date,
                    "date_formatted": b_date.strftime("%A, %d %B %Y"),
                    "generated_at": r["generated_at"],
                    "story_count": r["story_count"],
                    "content_hash": r["content_hash"],
                    "sync_source": r["sync_source"],
                })
            return archive

    def _build_briefing_from_row(self, cursor: sqlite3.Cursor, b_row: sqlite3.Row) -> DashboardBriefing:
        briefing_id = b_row["id"]
        cursor.execute(
            """
            SELECT id, briefing_id, section, position, headline, summary, source, url
            FROM stories
            WHERE briefing_id = ?
            ORDER BY
                CASE section
                    WHEN 'india' THEN 1
                    WHEN 'domestic' THEN 2
                    WHEN 'international' THEN 3
                    ELSE 4
                END,
                position ASC
            """,
            (briefing_id,),
        )
        s_rows = cursor.fetchall()
        stories = [
            DashboardStory(
                id=sr["id"],
                briefing_id=sr["briefing_id"],
                section=sr["section"],
                position=sr["position"],
                headline=sr["headline"],
                summary=sr["summary"],
                source=sr["source"],
                url=sr["url"],
            )
            for sr in s_rows
        ]

        b_date = date.fromisoformat(b_row["briefing_date"])
        return DashboardBriefing(
            id=briefing_id,
            briefing_date=b_date,
            generated_at=b_row["generated_at"],
            full_text=b_row["full_text"],
            story_count=b_row["story_count"],
            stories=stories,
            content_hash=b_row["content_hash"] if "content_hash" in b_row.keys() else None,
            sync_source=b_row["sync_source"] if "sync_source" in b_row.keys() else None,
            synced_at=b_row["synced_at"] if "synced_at" in b_row.keys() else None,
        )
