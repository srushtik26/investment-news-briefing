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
)

__all__ = [
    "EventSourceVerification",
    "TwoSourceVerifier",
    "VerificationStatus",
    "ActiveCorroborator",
    "CorroborationResult",
    "reset_corroboration_counter",
    "get_corroboration_count",
]
