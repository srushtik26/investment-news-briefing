"""
End-to-End Pipeline Runner and Compatibility Entry Point.

This module provides CLI invocation and backward-compatible re-exports
for all public pipeline helpers, delegating core orchestration to app.pipeline.runner.
"""

import sys
from urllib.parse import urlparse  # Required by tests

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Note: Discovery and final-mile queries strictly use when:1d
from config import get_settings
from app.models import Article, Event, NewsCategory
from app.discovery import NewsDiscoveryService
from app.filtering.engine import HardFilterEngine, DomesticHardFilterEngine
from app.ai import EditorialStorySelection, BriefingEditorialPayload
from app.verification.domestic_trending import DomesticTrendingEvaluator

# Core runner delegation
from app.pipeline.runner import run_pipeline

# Public helper compatibility re-exports
from app.pipeline.candidate_processing import (
    _extract_candidates,
    get_candidate_published_at,
    get_article_age_hours,
    populate_event_companies,
)
from app.pipeline.discovery_stage import (
    _score_discovery_candidate,
    get_fallback_search_window,
)
from app.pipeline.fallback_manager import (
    evaluate_single_source_for_horizon,
    get_quality_level,
    get_section_quality_state,
    prioritize_intl_final_mile_candidates,
    get_intl_serpapi_reservation,
    get_serpapi_event_key,
    has_unattempted_intl_upgrade,
    get_general_serpapi_limit,
    should_stop_expansion,
)
from app.pipeline.selection import ladder_quality_key

__all__ = [
    "run_pipeline",
    "urlparse",
    "Article",
    "Event",
    "NewsCategory",
    "NewsDiscoveryService",
    "HardFilterEngine",
    "DomesticHardFilterEngine",
    "EditorialStorySelection",
    "BriefingEditorialPayload",
    "DomesticTrendingEvaluator",
    "_extract_candidates",
    "get_candidate_published_at",
    "get_article_age_hours",
    "populate_event_companies",
    "_score_discovery_candidate",
    "get_fallback_search_window",
    "evaluate_single_source_for_horizon",
    "get_quality_level",
    "get_section_quality_state",
    "prioritize_intl_final_mile_candidates",
    "get_intl_serpapi_reservation",
    "get_serpapi_event_key",
    "has_unattempted_intl_upgrade",
    "get_general_serpapi_limit",
    "should_stop_expansion",
    "ladder_quality_key",
]


if __name__ == "__main__":
    st = get_settings()
    max_in  = int(sys.argv[1]) if len(sys.argv) > 1 else st.MAX_DISCOVERY_INDIA
    max_int = int(sys.argv[2]) if len(sys.argv) > 2 else st.MAX_DISCOVERY_INTL
    sys.exit(run_pipeline(max_india=max_in, max_international=max_int))
