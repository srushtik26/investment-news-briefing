"""
Database Schema and Data Models for Briefing History and Story Records.
"""

from datetime import date, datetime, timezone
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class BriefingHistoryRecord(BaseModel):
    """Data model for recorded daily briefings."""

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the briefing run",
    )
    briefing_date: date = Field(
        description="Date of the briefing in YYYY-MM-DD format",
    )
    status: str = Field(
        default="COMPLETED",
        description="Execution status (COMPLETED, FAILED, DRAFT)",
    )
    story_count: int = Field(
        default=0,
        description="Total number of stories included in this briefing",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when briefing was created",
    )


class HistoricalStoryRecord(BaseModel):
    """Data model for historical briefing stories used in lookback deduplication."""

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique story record identifier",
    )
    briefing_id: Optional[str] = Field(
        default=None,
        description="Foreign key to briefing_history record",
    )
    event_id: str = Field(
        description="Canonical ID of the business event",
    )
    event_fingerprint: str = Field(
        description="Deterministic hash/key representing the event facts",
    )
    headline: str = Field(
        description="Selected headline of the story",
    )
    company_name: str = Field(
        description="Primary company or entity name associated with the event",
    )
    category: str = Field(
        description="Category classification (india or international)",
    )
    source_count: int = Field(
        default=1,
        description="Number of verified sources backing this story",
    )
    published_date: Optional[date] = Field(
        default=None,
        description="Original publication date of the story",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record insertion timestamp",
    )
