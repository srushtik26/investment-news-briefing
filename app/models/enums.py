"""
Enumerations for the Investment Committee News Briefing system.
"""

from enum import Enum


class NewsCategory(str, Enum):
    """Category classification for business news articles and stories."""

    INDIA = "india"
    INTERNATIONAL = "international"
    UNKNOWN = "unknown"


class BriefingStatus(str, Enum):
    """Lifecycle status of a generated briefing."""

    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    FAILED = "failed"


class RelevanceGrade(str, Enum):
    """Investment relevance grading."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    IRRELEVANT = "irrelevant"
