"""
Repository for dashboard database access and persistence.
Supports Neon PostgreSQL via SQLAlchemy or SQLite fallback at data/dashboard.db.
"""

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, List, Optional, Dict, Any, Union

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

from app.dashboard.models import DashboardBriefing, DashboardStory


DEFAULT_DB_PATH = Path("data") / "dashboard.db"


class DashboardSyncConflictError(Exception):
    """Raised when an incoming briefing differs from an existing stored briefing for the same date."""
    pass


def normalize_database_url(url: str) -> str:
    """
    Normalize postgres database URLs so SQLAlchemy uses the psycopg 3 driver.
    Converts plain postgresql:// and postgres:// to postgresql+psycopg://.
    Leaves other URLs unchanged.
    """
    url_clean = url.strip()
    if url_clean.startswith("postgres://"):
        return "postgresql+psycopg://" + url_clean[len("postgres://"):]
    if url_clean.startswith("postgresql://") and not url_clean.startswith("postgresql+"):
        return "postgresql+psycopg://" + url_clean[len("postgresql://"):]
    return url_clean


def compute_briefing_content_hash(briefing: DashboardBriefing) -> str:
    """Compute a deterministic SHA-256 fingerprint for a briefing's stories."""
    canonical_parts = []
    for s in sorted(briefing.stories, key=lambda x: (x.section, x.position)):
        canonical_parts.append(
            f"{s.section.lower()}:{s.position}:{s.headline.strip().lower()}:{s.url.strip()}"
        )
    raw_str = "|".join(canonical_parts)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


# SQLAlchemy table definitions for PostgreSQL schema management
metadata = MetaData()

briefings_table = Table(
    "briefings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("briefing_date", String(50), unique=True, nullable=False),
    Column("generated_at", DateTime, nullable=True),
    Column("full_text", Text, nullable=True),
    Column("story_count", Integer, nullable=False, default=0),
    Column("content_hash", String(128), nullable=True),
    Column("sync_source", String(255), nullable=True),
    Column("synced_at", DateTime, nullable=True),
    Index("idx_briefings_date", "briefing_date"),
)

stories_table = Table(
    "stories",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("briefing_id", Integer, ForeignKey("briefings.id", ondelete="CASCADE"), nullable=False),
    Column("section", String(50), nullable=False),
    Column("position", Integer, nullable=False),
    Column("headline", Text, nullable=False),
    Column("summary", Text, nullable=True),
    Column("source", Text, nullable=True),
    Column("url", Text, nullable=False),
    UniqueConstraint("briefing_id", "section", "position", name="uq_stories_briefing_section_position"),
    Index("idx_stories_briefing", "briefing_id"),
)


class DashboardRepository:
    """
    Data access layer for stored dashboard briefings and stories.
    Supports Neon PostgreSQL via SQLAlchemy or SQLite fallback.
    Completely isolated from the pipeline's history and working databases.
    """

    def __init__(
        self,
        db_path: Optional[Union[Path, str]] = None,
        database_url: Optional[str] = None,
        engine: Optional[Engine] = None,
    ) -> None:
        self._database_url = database_url or os.getenv("DASHBOARD_DATABASE_URL")
        self.engine: Optional[Engine] = None
        self.db_path: Optional[Path] = None

        if db_path is not None:
            # Explicit db_path forces SQLite (for tests and local sqlite overrides)
            self.backend = "sqlite"
            self.db_path = Path(db_path)
            if str(self.db_path) != ":memory:":
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
        elif engine is not None:
            self.backend = "postgresql" if "postgres" in engine.dialect.name else "sqlite"
            self.engine = engine
        elif self._database_url:
            self.backend = "postgresql"
            norm_url = normalize_database_url(self._database_url)
            self.engine = create_engine(norm_url, pool_pre_ping=True)
        else:
            self.backend = "sqlite"
            self.db_path = DEFAULT_DB_PATH
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.init_db()

    def __repr__(self) -> str:
        if self.backend == "postgresql":
            return "<DashboardRepository backend=postgresql>"
        return f"<DashboardRepository backend=sqlite db_path={self.db_path}>"

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        if self.db_path is None:
            raise RuntimeError("Cannot open SQLite connection when db_path is None")
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
        if self.backend == "postgresql" or self.engine is not None:
            metadata.create_all(self.engine)
            return

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
        Save a briefing and its stories into the database.

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

        if self.backend == "postgresql" or self.engine is not None:
            with self.engine.begin() as conn:
                select_stmt = text(
                    "SELECT id, story_count, content_hash FROM briefings WHERE briefing_date = :b_date"
                )
                row = conn.execute(select_stmt, {"b_date": date_str}).mappings().fetchone()

                if row:
                    existing_id = row["id"]
                    existing_hash = row["content_hash"]

                    if existing_hash and existing_hash == new_hash:
                        return existing_id

                    if not allow_replace:
                        raise DashboardSyncConflictError(
                            f"[DASHBOARD_SYNC_CONFLICT] date={date_str} existing briefing differs from incoming briefing. "
                            f"Use --replace-date {date_str} to overwrite."
                        )

                    update_stmt = text(
                        """
                        UPDATE briefings
                        SET generated_at = :gen_at, full_text = :full_text, story_count = :story_count,
                            content_hash = :content_hash, sync_source = :sync_source, synced_at = :synced_at
                        WHERE id = :existing_id
                        """
                    )
                    conn.execute(
                        update_stmt,
                        {
                            "gen_at": briefing.generated_at or sync_time,
                            "full_text": briefing.full_text,
                            "story_count": story_count,
                            "content_hash": new_hash,
                            "sync_source": briefing.sync_source or "direct_save",
                            "synced_at": sync_time,
                            "existing_id": existing_id,
                        },
                    )
                    conn.execute(
                        text("DELETE FROM stories WHERE briefing_id = :b_id"),
                        {"b_id": existing_id},
                    )
                    briefing_id = existing_id
                else:
                    insert_stmt = text(
                        """
                        INSERT INTO briefings (briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at)
                        VALUES (:b_date, :gen_at, :full_text, :story_count, :content_hash, :sync_source, :synced_at)
                        RETURNING id
                        """
                    )
                    res = conn.execute(
                        insert_stmt,
                        {
                            "b_date": date_str,
                            "gen_at": briefing.generated_at or sync_time,
                            "full_text": briefing.full_text,
                            "story_count": story_count,
                            "content_hash": new_hash,
                            "sync_source": briefing.sync_source or "direct_save",
                            "synced_at": sync_time,
                        },
                    )
                    briefing_id = res.scalar()

                insert_story_stmt = text(
                    """
                    INSERT INTO stories (briefing_id, section, position, headline, summary, source, url)
                    VALUES (:b_id, :sec, :pos, :headline, :summary, :source, :url)
                    """
                )
                for story in briefing.stories:
                    conn.execute(
                        insert_story_stmt,
                        {
                            "b_id": briefing_id,
                            "sec": story.section.lower(),
                            "pos": story.position,
                            "headline": story.headline,
                            "summary": story.summary,
                            "source": story.source,
                            "url": story.url,
                        },
                    )

                return briefing_id

        # SQLite implementation
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

                if existing_hash and existing_hash == new_hash:
                    return existing_id

                if not allow_replace:
                    raise DashboardSyncConflictError(
                        f"[DASHBOARD_SYNC_CONFLICT] date={date_str} existing briefing differs from incoming briefing. "
                        f"Use --replace-date {date_str} to overwrite."
                    )

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
        if self.backend == "postgresql" or self.engine is not None:
            with self.engine.connect() as conn:
                stmt = text(
                    """
                    SELECT id, briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at
                    FROM briefings
                    ORDER BY briefing_date DESC
                    LIMIT 1
                    """
                )
                b_row = conn.execute(stmt).mappings().fetchone()
                if not b_row:
                    return None
                return self._build_briefing_from_sa_row(conn, b_row)

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
        if self.backend == "postgresql" or self.engine is not None:
            with self.engine.connect() as conn:
                stmt = text(
                    """
                    SELECT id, briefing_date, generated_at, full_text, story_count, content_hash, sync_source, synced_at
                    FROM briefings
                    WHERE briefing_date = :b_date
                    """
                )
                b_row = conn.execute(stmt, {"b_date": date_str}).mappings().fetchone()
                if not b_row:
                    return None
                return self._build_briefing_from_sa_row(conn, b_row)

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
        if self.backend == "postgresql" or self.engine is not None:
            with self.engine.connect() as conn:
                stmt = text(
                    """
                    SELECT id, briefing_date, generated_at, story_count, content_hash, sync_source
                    FROM briefings
                    ORDER BY briefing_date DESC
                    """
                )
                rows = conn.execute(stmt).mappings().fetchall()
                archive = []
                for r in rows:
                    b_date_val = r["briefing_date"]
                    b_date = b_date_val if isinstance(b_date_val, date) else date.fromisoformat(str(b_date_val))
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

    def _build_briefing_from_sa_row(self, conn, b_row: Any) -> DashboardBriefing:
        briefing_id = b_row["id"]
        stories_stmt = text(
            """
            SELECT id, briefing_id, section, position, headline, summary, source, url
            FROM stories
            WHERE briefing_id = :b_id
            ORDER BY
                CASE section
                    WHEN 'india' THEN 1
                    WHEN 'domestic' THEN 2
                    WHEN 'international' THEN 3
                    ELSE 4
                END,
                position ASC
            """
        )
        s_rows = conn.execute(stories_stmt, {"b_id": briefing_id}).mappings().fetchall()
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

        b_date_val = b_row["briefing_date"]
        b_date = b_date_val if isinstance(b_date_val, date) else date.fromisoformat(str(b_date_val))
        return DashboardBriefing(
            id=briefing_id,
            briefing_date=b_date,
            generated_at=b_row["generated_at"],
            full_text=b_row["full_text"],
            story_count=b_row["story_count"],
            stories=stories,
            content_hash=b_row.get("content_hash"),
            sync_source=b_row.get("sync_source"),
            synced_at=b_row.get("synced_at"),
        )

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
