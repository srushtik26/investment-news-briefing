"""
Filter Engine Data Models.

Defines schemas and results for deterministic filtering rules.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FilterResult(BaseModel):
    """
    Result model representing the outcome of deterministic filtering on an article.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    is_accepted: bool = Field(
        ...,
        description="True if article passed all deterministic hard filters",
    )
    article_url: str = Field(
        ...,
        description="Exact URL of the evaluated candidate",
    )
    article_title: str = Field(
        default="",
        description="Headline/title of the candidate article",
    )
    rule_failed: Optional[str] = Field(
        default=None,
        description="Identifier of the rule that rejected the article (e.g., 'DATE', 'SOURCE', 'URL', 'STORY_TYPE')",
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Precise human-readable explanation why candidate was rejected",
    )
    matched_patterns: List[str] = Field(
        default_factory=list,
        description="List of specific keyword/regex patterns matched during filtering",
    )
