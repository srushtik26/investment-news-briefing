"""
Pipeline package for the Automated Investment News Briefing System.
"""

from app.pipeline.context import PipelineContext
from app.pipeline.runner import run_pipeline
from app.pipeline.candidate_processing import (
    _extract_candidates,
    get_candidate_published_at,
    get_article_age_hours,
    populate_event_companies,
)
from app.pipeline.discovery_stage import (
    _score_discovery_candidate,
    get_fallback_search_window,
    discover_initial_reserves,
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
    run_expansion_and_fallbacks,
)
from app.pipeline.enrichment import run_second_source_enrichment
from app.pipeline.selection import (
    ladder_quality_key,
    get_final_selectable_unique_events,
    get_unique_candidate_events,
    count_unique_section_events,
    is_domestic_diversity_satisfied,
    run_deduplication,
    run_ranking_and_selection,
)

__all__ = [
    "PipelineContext",
    "run_pipeline",
    "_extract_candidates",
    "get_candidate_published_at",
    "get_article_age_hours",
    "populate_event_companies",
    "_score_discovery_candidate",
    "get_fallback_search_window",
    "discover_initial_reserves",
    "evaluate_single_source_for_horizon",
    "get_quality_level",
    "get_section_quality_state",
    "prioritize_intl_final_mile_candidates",
    "get_intl_serpapi_reservation",
    "get_serpapi_event_key",
    "has_unattempted_intl_upgrade",
    "get_general_serpapi_limit",
    "should_stop_expansion",
    "run_expansion_and_fallbacks",
    "run_second_source_enrichment",
    "ladder_quality_key",
    "get_final_selectable_unique_events",
    "get_unique_candidate_events",
    "count_unique_section_events",
    "is_domestic_diversity_satisfied",
    "run_deduplication",
    "run_ranking_and_selection",
]
