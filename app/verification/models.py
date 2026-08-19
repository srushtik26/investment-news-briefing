"""
Source Verification Models and Status Enums.

Defines schemas for two-source independent corroboration tracking.
"""

from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class VerificationStatus(str, Enum):
    """Status outcomes for multi-source event verification."""

    VERIFIED = "VERIFIED"
    UNVERIFIED_SINGLE_SOURCE = "UNVERIFIED_SINGLE_SOURCE"
    REJECTED_SAME_PUBLISHER = "REJECTED_SAME_PUBLISHER"
    REJECTED_SYNDICATED = "REJECTED_SYNDICATED"
    REJECTED_UNRELATED = "REJECTED_UNRELATED"


class EventSourceVerification(BaseModel):
    """
    Detailed verification record for a specific business event.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique verification record identifier",
    )
    event_id: str = Field(
        ...,
        description="ID of the event under corroboration",
    )
    primary_source: str = Field(
        ...,
        description="Publisher/outlet name of the primary article",
    )
    secondary_source: Optional[str] = Field(
        default=None,
        description="Publisher/outlet name of the corroborating secondary article",
    )
    source_count: int = Field(
        default=1,
        ge=1,
        description="Number of distinct articles evaluated for this event",
    )
    is_independent: bool = Field(
        default=False,
        description="True only if primary and secondary sources are genuinely distinct publications and not syndicated",
    )
    verification_status: VerificationStatus = Field(
        ...,
        description="Standardized verification status outcome",
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Corroboration confidence metric (0.0 to 1.0)",
    )
    article_urls: List[str] = Field(
        default_factory=list,
        description="URLs of all articles evaluated in corroborating this event",
    )
    matching_details: Optional[str] = Field(
        default=None,
        description="Diagnostic explanation of matching rationale or rejection reason",
    )

    @property
    def is_verified(self) -> bool:
        """True only if event passes all two-source independence criteria."""
        return (
            self.verification_status == VerificationStatus.VERIFIED
            and self.source_count >= 2
            and self.is_independent is True
        )
