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
    MAX_CORROBORATION_SEARCHES_PER_RUN,
)
from app.verification.serpapi_corroborator import (
    SerpAPICorroborator,
    reset_serpapi_counter,
    get_serpapi_count,
)

__all__ = [
    "EventSourceVerification",
    "TwoSourceVerifier",
    "VerificationStatus",
    "ActiveCorroborator",
    "CorroborationResult",
    "reset_corroboration_counter",
    "get_corroboration_count",
    "MAX_CORROBORATION_SEARCHES_PER_RUN",
    "SerpAPICorroborator",
    "reset_serpapi_counter",
    "get_serpapi_count",
]
