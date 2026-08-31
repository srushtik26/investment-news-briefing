"""
Editorial AI Data Models.

Defines schemas for final editorial selection and concise headline synthesis.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EditorialStorySelection(BaseModel):
    """
    A single story selected and edited by Gemini for the final briefing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    section: str = Field(
        ...,
        description="Briefing section ('india' or 'international')",
    )
    event_id: str = Field(
        ...,
        description="Canonical ID of the verified business event",
    )
    headline: str = Field(
        ...,
        min_length=10,
        description="Concise, punchy headline containing key figures/numbers",
    )
    source: str = Field(
        ...,
        description="Exact primary publisher/outlet name",
    )
    url: str = Field(
        ...,
        description="Exact unedited URL of the verified source article",
    )
    summary: Optional[str] = Field(
        default=None,
        description="One-line factual summary of the story (15-25 words, strictly factual)",
    )
    secondary_source: Optional[str] = Field(
        default=None,
        description="Secondary publisher name if two-source verified",
    )
    secondary_url: Optional[str] = Field(
        default=None,
        description="Secondary article URL if two-source verified",
    )

    @field_validator("section")
    @classmethod
    def normalize_section(cls, v: str) -> str:
        """Ensure section is lowercase 'domestic', 'india' or 'international'."""
        cleaned = v.strip().lower()
        if "domestic" in cleaned:
            return "domestic"
        if "india" in cleaned:
            return "india"
        if "intl" in cleaned or "international" in cleaned:
            return "international"
        return cleaned

    @field_validator("headline")
    @classmethod
    def clean_headline(cls, v: str) -> str:
        """
        Normalise whitespace only.

        Structural markdown artefacts (``**``, ``##``, leading bullets, etc.)
        are intentionally NOT stripped here — they are detected and rejected by
        the downstream ``BriefingFormatter`` so that malformed upstream AI
        output surfaces as a hard ``MalformedHeadlineError`` rather than being
        silently repaired.
        """
        return v.strip()


class BriefingEditorialPayload(BaseModel):
    """
    Complete editorial selection covering Domestic India, India Business, and International sections.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    domestic_stories: List[EditorialStorySelection] = Field(
        default_factory=list,
        description="Final selected Domestic India macro/policy stories (target: 5)",
    )
    india_stories: List[EditorialStorySelection] = Field(
        default_factory=list,
        description="Final selected India business stories (target: 5)",
    )
    international_stories: List[EditorialStorySelection] = Field(
        default_factory=list,
        description="Final selected International business stories (target: 5)",
    )


class EditorialResult(BaseModel):
    """
    Outcome wrapper for the Gemini final editorial selection execution.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    success: bool = Field(
        ...,
        description="True if selection passed schema and programmatic validation",
    )
    selection: Optional[BriefingEditorialPayload] = Field(
        default=None,
        description="Structured briefing editorial payload on success",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Diagnostic failure reason if validation failed",
    )
    attempts: int = Field(
        default=1,
        ge=0,
        description="Number of API attempts executed (0 if skipped due to zero candidates)",
    )
    raw_response: Optional[str] = Field(
        default=None,
        description="Raw output received from the model",
    )
