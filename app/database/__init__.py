"""
Database Package.

Provides SQLite persistence, table schemas, and database connection management.
"""

from app.database.connection import get_connection, init_db, resolve_db_path
from app.database.schema import BriefingHistoryRecord, HistoricalStoryRecord

__all__ = [
    "BriefingHistoryRecord",
    "HistoricalStoryRecord",
    "get_connection",
    "init_db",
    "resolve_db_path",
]
