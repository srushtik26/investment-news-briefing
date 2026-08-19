"""
Discovery Data Models.

Defines schemas for discovered candidate business news articles
prior to deep content extraction.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.article import Article
from app.models.enums import NewsCategory


class DiscoveredArticle(BaseModel):
    """
    Model representing a discovered candidate news article from a search provider or feed.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the discovered candidate",
    )
    title: str = Field(
        ...,
        min_length=3,
        description="Article headline as discovered from the search provider",
    )
    url: str = Field(
        ...,
        description="Exact URL returned by the search provider (never fabricated)",
    )
    source: str = Field(
        ...,
        min_length=1,
        description="Publishing outlet name (e.g., 'Economic Times', 'Reuters')",
    )
    snippet: Optional[str] = Field(
        default=None,
        description="Short excerpt / snippet returned in search results",
    )
    published_at: Optional[datetime] = Field(
        default=None,
        description="Publication date/time if available in search metadata",
    )
    search_query: str = Field(
        ...,
        description="Search query or category feed that yielded this candidate",
    )
    country: str = Field(
        ...,
        description="Geographic scope: 'India' or 'International'",
    )
    category_tag: Optional[str] = Field(
        default=None,
        description="Identified hard business event category (e.g., 'earnings', 'mergers')",
    )
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when article was discovered",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional search provider metadata",
    )

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        """Clean extraneous whitespace and trailing source suffixes from title."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Title cannot be empty or whitespace only")
        return cleaned

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL scheme and host."""
        cleaned = v.strip()
        parsed = urlparse(cleaned)
        if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Must be http or https.")
        if not parsed.netloc:
            raise ValueError("URL must contain a valid domain network location.")
        return cleaned

    @field_validator("country")
    @classmethod
    def normalize_country(cls, v: str) -> str:
        """Normalize country name to India or International."""
        cleaned = v.strip().title()
        if cleaned in ("India", "In"):
            return "India"
        if cleaned in ("International", "Global", "Intl", "Us", "Europe", "Asia"):
            return "International"
        return cleaned

    def to_article(self, content_text: str = "") -> Article:
        """
        Convert candidate into base Article model for subsequent pipeline stages.
        """
        category_enum = (
            NewsCategory.INDIA
            if self.country.lower() == "india"
            else NewsCategory.INTERNATIONAL
            if self.country.lower() == "international"
            else NewsCategory.UNKNOWN
        )

        return Article(
            id=self.id,
            title=self.title,
            url=self.url,
            source_name=self.source,
            published_at=self.published_at,
            extracted_at=datetime.now(timezone.utc),
            content_text=content_text or (self.snippet or ""),
            summary=self.snippet,
            category=category_enum,
            is_verified_url=bool(self.url),
            is_valid_date=bool(self.published_at),
            metadata={
                **self.metadata,
                "search_query": self.search_query,
                "category_tag": self.category_tag,
            },
        )
