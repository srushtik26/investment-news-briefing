"""
Filtering Package.

Provides deterministic filter rules and the Hard Filter Engine for candidate articles.
"""

from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
from app.filtering.models import FilterResult
from app.filtering.rules import (
    BaseFilterRule,
    DateFilterRule,
    DomesticSourceFilterRule,
    SourceFilterRule,
    StoryTypeFilterRule,
    URLFilterRule,
)

__all__ = [
    "BaseFilterRule",
    "DateFilterRule",
    "DomesticHardFilterEngine",
    "DomesticSourceFilterRule",
    "FilterResult",
    "HardFilterEngine",
    "SourceFilterRule",
    "StoryTypeFilterRule",
    "URLFilterRule",
]
