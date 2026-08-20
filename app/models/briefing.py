"""
Briefing and BriefingStory Data Models.

Defines the final curated stories and the complete daily Investment
Committee briefing structure, including WhatsApp-ready formatting fields.
"""

from datetime import date, datetime, timezone
from typing import List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import BriefingStatus, NewsCategory


class BriefingStory(BaseModel):
    """
    Model representing a single curated news story in the daily briefing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the story",
    )
    event_id: str = Field(
        ...,
        description="Reference ID to the underlying business event",
    )
    headline: str = Field(
        ...,
        min_length=5,
        description="Polished, concise headline for the Investment Committee",
    )
    category: NewsCategory = Field(
        ...,
        description="Section category (India vs International)",
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="Bullet points detailing core facts, actions, and metrics",
    )
    impact_analysis: Optional[str] = Field(
        default=None,
        description="Investment committee impact / market implication summary",
    )
    primary_company: Optional[str] = Field(
        default=None,
        description="Primary company name (used to enforce one-story-per-company rule)",
    )
    source_citations: List[str] = Field(
        default_factory=list,
        description="Names of corroborating sources (e.g., ['Mint', 'Bloomberg'])",
    )
    source_urls: List[str] = Field(
        default_factory=list,
        description="Verified destination URLs for cited sources",
    )
    investment_relevance_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="Rank score based on investment significance",
    )
    rank: int = Field(
        default=0,
        ge=0,
        description="Display order ranking within its category section",
    )

    @field_validator("headline")
    @classmethod
    def clean_headline(cls, v: str) -> str:
        """Ensure headline has clean formatting."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Headline cannot be empty")
        return cleaned


class Briefing(BaseModel):
    """
    Model representing the complete daily Investment Committee briefing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the briefing issue",
    )
    briefing_date: date = Field(
        default_factory=lambda: datetime.now(timezone.utc).date(),
        description="Date of the briefing issue",
    )
    title: str = Field(
        default="Daily Investment Committee Business Briefing",
        description="Title of the briefing issue",
    )
    india_stories: List[BriefingStory] = Field(
        default_factory=list,
        description="Curated India business stories",
    )
    international_stories: List[BriefingStory] = Field(
        default_factory=list,
        description="Curated International business stories",
    )
    total_stories_count: int = Field(
        default=0,
        ge=0,
        description="Total number of stories included across all sections",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when briefing was generated",
    )
    status: BriefingStatus = Field(
        default=BriefingStatus.DRAFT,
        description="Lifecycle state of the briefing",
    )
    formatted_whatsapp_text: Optional[str] = Field(
        default=None,
        description="Pre-formatted WhatsApp briefing text",
    )

    @model_validator(mode="before")
    @classmethod
    def sync_total_count(cls, data: dict) -> dict:
        """Compute total stories count automatically before validation."""
        if isinstance(data, dict):
            india = data.get("india_stories") or []
            intl = data.get("international_stories") or []
            data["total_stories_count"] = len(india) + len(intl)
        return data

    @property
    def india_companies(self) -> List[str]:
        """List of primary companies featured in the India section."""
        return [
            story.primary_company.strip().lower()
            for story in self.india_stories
            if story.primary_company and story.primary_company.strip()
        ]

    def has_duplicate_india_company(self) -> bool:
        """
        Deterministic check: verify whether any company appears more than once in India section.
        """
        companies = self.india_companies
        return len(companies) != len(set(companies))