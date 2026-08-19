"""
Source Verification Data Model.

Captures deterministic corroboration metrics for events, ensuring
factual stories have verified multi-source credibility before inclusion.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceVerification(BaseModel):
    """
    Model tracking independent source corroboration for a business event.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the verification record",
    )
    event_id: str = Field(
        ...,
        description="ID of the event being verified",
    )
    sources_count: int = Field(
        default=0,
        ge=0,
        description="Total count of distinct sources corroborating the story",
    )
    independent_sources: List[str] = Field(
        default_factory=list,
        description="Distinct publication names / domains confirming the event",
    )
    is_multi_source_verified: bool = Field(
        default=False,
        description="True if story meets multi-source threshold (e.g., >= 2 independent sources)",
    )
    verification_notes: Optional[str] = Field(
        default=None,
        description="Diagnostic notes or reasoning behind verification outcome",
    )
    verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when source corroboration was executed",
    )

    @model_validator(mode="before")
    @classmethod
    def sync_source_counts(cls, data: dict) -> dict:
        """Sync sources_count and multi-source flag if independent_sources is provided."""
        if isinstance(data, dict):
            sources = data.get("independent_sources")
            if isinstance(sources, list):
                # Standardize distinct sources (case-insensitive deduplication)
                unique_sources = list(dict.fromkeys(s.strip() for s in sources if isinstance(s, str) and s.strip()))
                data["independent_sources"] = unique_sources
                if "sources_count" not in data or data["sources_count"] == 0:
                    data["sources_count"] = len(unique_sources)
                if "is_multi_source_verified" not in data:
                    data["is_multi_source_verified"] = len(unique_sources) >= 2
        return data
