"""
Article Classification Package.

Provides Gemini AI-based article classification and factual entity extraction.
"""

from app.classification.classifier import AIArticleClassifier, GeminiRateLimitError
from app.classification.models import (
    AIArticleClassification,
    ArticleEventType,
    ClassificationResult,
)
from app.classification.prompts import (
    SYSTEM_CLASSIFICATION_PROMPT,
    build_classification_user_prompt,
)
from app.classification.region_classifier import EventRegionClassifier

__all__ = [
    "AIArticleClassifier",
    "AIArticleClassification",
    "ArticleEventType",
    "ClassificationResult",
    "EventRegionClassifier",
    "GeminiRateLimitError",
    "SYSTEM_CLASSIFICATION_PROMPT",
    "build_classification_user_prompt",
]
