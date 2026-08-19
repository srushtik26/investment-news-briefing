"""
Article Extraction Data Models.

Defines schemas and results for the article extraction and content parsing pipeline.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.article import Article


class ExtractionResult(BaseModel):
    """
    Result model representing the outcome of extracting an article from a URL.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    success: bool = Field(
        ...,
        description="True if article was successfully retrieved and validated as a legitimate article",
    )
    url: str = Field(
        ...,
        description="Canonical or resolved URL of the article",
    )
    original_url: Optional[str] = Field(
        default=None,
        description="Original RSS URL before resolution",
    )
    resolved_url: Optional[str] = Field(
        default=None,
        description="Resolved direct publisher URL",
    )
    article: Optional[Article] = Field(
        default=None,
        description="Populated Article instance on extraction success",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Detailed diagnostic reason if extraction failed",
    )
    status_code: Optional[int] = Field(
        default=None,
        description="HTTP response status code if available",
    )
    date_verified: bool = Field(
        default=False,
        description="True if publication date was reliably determined from page metadata",
    )
    extraction_method: str = Field(
        default="primary",
        description="Method used for content extraction ('primary', 'fallback', or 'none')",
    )
    word_count: int = Field(
        default=0,
        description="Word count of extracted body content",
    )
