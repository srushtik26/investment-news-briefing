"""
Deduplication Package.

Provides event clustering, fingerprinting, 3-day SQLite lookback, and company constraint enforcement.
"""

from app.deduplication.clusterer import EventClusterer
from app.deduplication.engine import DeduplicationEngine
from app.deduplication.fingerprint import (
    are_articles_same_event,
    generate_event_fingerprint,
    normalize_entity_name,
    normalize_metric_facts,
)
from app.deduplication.history import HistoryStore

__all__ = [
    "DeduplicationEngine",
    "EventClusterer",
    "HistoryStore",
    "are_articles_same_event",
    "generate_event_fingerprint",
    "normalize_entity_name",
    "normalize_metric_facts",
]
