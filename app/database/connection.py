"""
Database Connection and Initialization Management using Python SQLite3.
"""

from pathlib import Path
import sqlite3
from typing import Optional

from config import get_settings
from app.logging_config import get_logger

logger = get_logger("database.connection")


def resolve_db_path(database_url: Optional[str] = None) -> str:
    """Resolve database URL string into local file path or shared memory URI."""
    settings = get_settings()
    url = database_url or settings.DATABASE_URL

    if url in (":memory:", "sqlite:///:memory:"):
        return "file:shared_mem_db?mode=memory&cache=shared"

    if url.startswith("sqlite:///"):
        clean_path = url.replace("sqlite:///", "")
        path_obj = Path(clean_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        return str(path_obj)

    return url


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Create a thread-safe sqlite3 database connection with Row factory.
    """
    target_path = resolve_db_path(db_path)
    is_uri = target_path.startswith("file:")
    conn = sqlite3.connect(target_path, check_same_thread=False, uri=is_uri)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initialize SQLite schema tables and indexes.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS briefing_history (
                    id TEXT PRIMARY KEY,
                    briefing_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'COMPLETED',
                    story_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_briefing_date ON briefing_history(briefing_date);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS historical_stories (
                    id TEXT PRIMARY KEY,
                    briefing_id TEXT,
                    event_id TEXT NOT NULL,
                    event_fingerprint TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 1,
                    published_date TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (briefing_id) REFERENCES briefing_history(id)
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hist_fingerprint ON historical_stories(event_fingerprint);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hist_pub_date ON historical_stories(published_date);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hist_company ON historical_stories(company_name);
            """)
        logger.info("Initialized SQLite database schema successfully.")
    finally:
        conn.close()
