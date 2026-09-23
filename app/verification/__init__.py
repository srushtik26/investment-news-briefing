"""
Source Verification Package.

Provides two-source independent corroboration and syndication detection for business events.
"""

from app.verification.models import EventSourceVerification, VerificationStatus
from app.verification.verifier import TwoSourceVerifier
from app.verification.corroborator import (
    ActiveCorroborator,
    CorroborationResult,
    reset_corroboration_counter,
    get_corroboration_count,
    increment_corroboration_count,
    MAX_CORROBORATION_SEARCHES_PER_RUN,
    DOMESTIC_RESERVED_RSS_SEARCHES,
    PORTFOLIO_RESERVED_RSS_SEARCHES,
)
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_count,
    get_serpapi_candidates_returned,
    get_serpapi_accepted_sources,
    get_serpapi_rejection_reasons,
    MAX_SERPAPI_INDIA_SEARCHES_PER_RUN,
    get_serpapi_india_count,
    get_serpapi_intl_count,
    compute_serpapi_section_budget,
)

from app.verification.materiality import (
    InvestmentMaterialityEvaluator,
    evaluate_investment_materiality,
    INVESTMENT_MATERIALITY_THRESHOLD,
    is_final_india_candidate_eligible,
    is_india_final_eligible,
    IndiaEligibilityResult,
)
from app.verification.international import (
    is_geopolitical_market_impact_eligible,
    is_international_final_eligible,
    is_geopolitical_story,
)

__all__ = [
    "EventSourceVerification",
    "TwoSourceVerifier",
    "VerificationStatus",
    "ActiveCorroborator",
    "CorroborationResult",
    "reset_corroboration_counter",
    "get_corroboration_count",
    "increment_corroboration_count",
    "MAX_CORROBORATION_SEARCHES_PER_RUN",
    "DOMESTIC_RESERVED_RSS_SEARCHES",
    "PORTFOLIO_RESERVED_RSS_SEARCHES",
    "SerpAPICorroborator",
    "reset_serpapi_counter",
    "get_serpapi_count",
    "get_serpapi_candidates_returned",
    "get_serpapi_accepted_sources",
    "get_serpapi_rejection_reasons",
    "InvestmentMaterialityEvaluator",
    "evaluate_investment_materiality",
    "INVESTMENT_MATERIALITY_THRESHOLD",
    "is_final_india_candidate_eligible",
    "is_india_final_eligible",
    "IndiaEligibilityResult",
    "is_geopolitical_market_impact_eligible",
    "is_international_final_eligible",
    "is_geopolitical_story",
]
