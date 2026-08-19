"""
Event Data Model.

Represents a distinct business/market event synthesized from one or more
news articles, tracking affected companies, financial figures, and deduplication status.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import NewsCategory


class Event(BaseModel):
    """
    Model representing a unique business occurrence synthesized across articles.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the business event",
    )
    canonical_title: str = Field(
        ...,
        min_length=3,
        description="Standardized canonical title of the business event",
    )
    description: str = Field(
        ...,
        min_length=5,
        description="Comprehensive factual description of the event",
    )
    companies_involved: List[str] = Field(
        default_factory=list,
        description="List of standardized company/organization names involved",
    )
    sectors: List[str] = Field(
        default_factory=list,
        description="Impacted industrial or economic sectors (e.g., Banking, Tech, Pharma)",
    )
    financial_figures: List[str] = Field(
        default_factory=list,
        description="Key financial metrics mentioned (e.g., '$1.2B', '₹5,000 Cr', '18% YoY')",
    )
    event_category: NewsCategory = Field(
        default=NewsCategory.UNKNOWN,
        description="Category classification (India vs. International)",
    )
    article_ids: List[str] = Field(
        default_factory=list,
        description="IDs of all articles referencing this event",
    )
    detected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when event was first identified",
    )
    relevance_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="Investment relevance score (0.0 to 10.0)",
    )
    is_duplicate: bool = Field(
        default=False,
        description="Flag indicating if event duplicates a story from previous days or active queue",
    )

    @field_validator("canonical_title", "description")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        """Ensure title and description are clean strings."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or whitespace only")
        return cleaned

    @property
    def source_count(self) -> int:
        """Number of distinct articles linked to this event."""
        return len(self.article_ids)
