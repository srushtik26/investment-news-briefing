"""
AI Package.

Provides Gemini AI-based editorial curation, structured classification, and headline synthesis.
"""

from app.ai.editor import GeminiEditorialEngine, RATE_LIMITED_PREFIX
from app.ai.models import (
    BriefingEditorialPayload,
    EditorialResult,
    EditorialStorySelection,
)
from app.ai.prompts import (
    SYSTEM_EDITORIAL_PROMPT,
    build_editorial_user_prompt,
)
from app.ai.usage_logger import GeminiUsageLogger

__all__ = [
    "BriefingEditorialPayload",
    "EditorialResult",
    "EditorialStorySelection",
    "GeminiEditorialEngine",
    "GeminiUsageLogger",
    "RATE_LIMITED_PREFIX",
    "SYSTEM_EDITORIAL_PROMPT",
    "build_editorial_user_prompt",
]
