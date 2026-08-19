"""
Filtering Package.

Provides deterministic filter rules and the Hard Filter Engine for candidate articles.
"""

from app.filtering.engine import HardFilterEngine
from app.filtering.models import FilterResult
from app.filtering.rules import (
    BaseFilterRule,
    DateFilterRule,
    SourceFilterRule,
    StoryTypeFilterRule,
    URLFilterRule,
)

__all__ = [
    "BaseFilterRule",
    "DateFilterRule",
    "FilterResult",
    "HardFilterEngine",
    "SourceFilterRule",
    "StoryTypeFilterRule",
    "URLFilterRule",
]
