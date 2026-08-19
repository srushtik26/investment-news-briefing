"""
Article Data Model.

Represents a single extracted news article with metadata, raw content,
and validation flags for URLs and publication dates.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import NewsCategory


class Article(BaseModel):
    """
    Model representing an extracted news article from a source.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the article",
    )
    title: str = Field(
        ...,
        min_length=3,
        description="Article title / headline",
    )
    url: str = Field(
        ...,
        description="Canonical URL of the article",
    )
    source_name: str = Field(
        ...,
        min_length=1,
        description="Name of the publishing source/outlet (e.g., Reuters, Economic Times)",
    )
    published_at: Optional[datetime] = Field(
        default=None,
        description="Extracted or declared publication timestamp",
    )
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the article content was extracted",
    )
    raw_html: Optional[str] = Field(
        default=None,
        description="Raw HTML content (optional storage)",
    )
    content_text: str = Field(
        default="",
        description="Extracted plain text body of the article",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Short summary of article contents",
    )
    category: NewsCategory = Field(
        default=NewsCategory.UNKNOWN,
        description="Geographical or business classification category",
    )
    author: Optional[str] = Field(
        default=None,
        description="Author or byline of the article if available",
    )
    is_verified_url: bool = Field(
        default=False,
        description="Flag indicating deterministic URL reachability/validation passed",
    )
    date_verified: bool = Field(
        default=False,
        description="Flag indicating publication date was reliably determined without guessing",
    )
    is_valid_date: bool = Field(
        default=False,
        description="Flag indicating publication date meets the lookback freshness window",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional metadata (author, tags, headers, etc.)",
    )

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        """Strip extraneous whitespace from title."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Article title cannot be empty")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str) -> str:
        """Validate URL contains a valid scheme and domain netloc."""
        parsed = urlparse(v.strip())
        if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Must be http or https.")
        if not parsed.netloc:
            raise ValueError("URL must have a valid domain network location (netloc).")
        return v.strip()

    @property
    def word_count(self) -> int:
        """Calculate word count of the content body."""
        return len(self.content_text.split()) if self.content_text else 0

    @property
    def has_content(self) -> bool:
        """Check if substantive content is present."""
        return self.word_count >= 10
