"""
Final Validation Engine Data Models.

Defines schemas for deterministic gatekeeping checks and briefing validation reports.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.ai.models import EditorialStorySelection


class ValidationStatus(str, Enum):
    """Overall outcome of the 20 gatekeeping checks."""

    PASSED = "PASSED"
    FAILED = "FAILED"


class ValidationCheckResult(BaseModel):
    """
    Individual result for each of the 20 deterministic gatekeeping checks.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    check_id: int = Field(
        ...,
        ge=1,
        le=20,
        description="Check ID (1 through 20)",
    )
    check_name: str = Field(
        ...,
        description="Descriptive identifier for the verification rule",
    )
    passed: bool = Field(
        ...,
        description="True if rule passed without violation",
    )
    failure_reason: Optional[str] = Field(
        default=None,
        description="Diagnostic failure explanation if rule was violated",
    )
    failed_story_id: Optional[str] = Field(
        default=None,
        description="Event ID or title of the story that triggered the failure",
    )


class BriefingValidationReport(BaseModel):
    """
    Comprehensive gatekeeping report before sending the briefing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    status: ValidationStatus = Field(
        ...,
        description="PASSED if all 20 checks succeed; FAILED if any check fails",
    )
    is_valid: bool = Field(
        ...,
        description="Convenience boolean flag indicating full verification",
    )
    passed_checks: int = Field(
        default=0,
        ge=0,
        le=20,
        description="Number of checks passed (0 to 20)",
    )
    failed_checks: int = Field(
        default=0,
        ge=0,
        le=20,
        description="Number of checks failed (0 to 20)",
    )
    failure_reason: Optional[str] = Field(
        default=None,
        description="Primary critical failure reason if status is FAILED",
    )
    failed_story: Optional[EditorialStorySelection] = Field(
        default=None,
        description="The specific story that triggered the critical failure",
    )
    failed_check_id: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="ID of the first failed check (1 to 20)",
    )
    check_results: List[ValidationCheckResult] = Field(
        default_factory=list,
        description="List of all 20 individual check results",
    )
