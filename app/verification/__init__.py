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
)

from app.verification.materiality import (
    InvestmentMaterialityEvaluator,
    evaluate_investment_materiality,
    INVESTMENT_MATERIALITY_THRESHOLD,
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
    "is_geopolitical_market_impact_eligible",
    "is_international_final_eligible",
    "is_geopolitical_story",
]
