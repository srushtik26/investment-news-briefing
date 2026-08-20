"""
AI Article Classification Data Models.

Defines schemas, event categories, and validation models for Gemini AI-based
article classification and factual entity extraction.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArticleEventType(str, Enum):
    """Business news event classifications."""

    EARNINGS = "EARNINGS"
    MA = "M&A"
    FUNDRAISING = "FUNDRAISING"
    IPO = "IPO"
    REGULATORY = "REGULATORY"
    COURT = "COURT"
    LEADERSHIP = "LEADERSHIP"
    POLICY = "POLICY"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    SECTOR = "SECTOR"
    MARKET = "MARKET"
    ANALYST = "ANALYST"
    OPINION = "OPINION"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object):
        """Handle common variations like 'M_AND_A' or 'MERGERS'."""
        if isinstance(value, str):
            val_upper = value.upper().strip()
            if val_upper in ("M&A", "MA", "M_AND_A", "MERGERS_AND_ACQUISITIONS", "MERGER", "ACQUISITION"):
                return cls.MA
            for member in cls:
                if member.value.upper() == val_upper or member.name == val_upper:
                    return member
        return cls.OTHER


class AIArticleClassification(BaseModel):
    """
    Structured classification and entity payload produced by the Gemini AI layer.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="ignore",
    )

    event_type: ArticleEventType = Field(
        ...,
        description="Core category classification for the business news article",
    )
    company_names: List[str] = Field(
        default_factory=list,
        description="Standardized legal/brand company names directly referenced in the story",
    )
    financial_numbers: List[str] = Field(
        default_factory=list,
        description="Explicit financial figures mentioned (e.g., '₹16,175 crore', '$30 billion')",
    )
    percentages: List[str] = Field(
        default_factory=list,
        description="Specific percentages mentioned in results or movements (e.g., '18%', '122%')",
    )
    deal_value: Optional[str] = Field(
        default=None,
        description="Overall valuation or transaction size if applicable (e.g., '$6.7 billion'), else null",
    )
    market_indices: List[str] = Field(
        default_factory=list,
        description="Referenced benchmark stock indices (e.g., 'Nifty 50', 'S&P 500')",
    )
    commodity_prices: List[str] = Field(
        default_factory=list,
        description="Referenced commodities and spot/futures prices (e.g., 'Brent Crude $82.40/bbl')",
    )
    currency_values: List[str] = Field(
        default_factory=list,
        description="Referenced exchange rates or currency moves (e.g., 'USD/INR 83.95')",
    )
    key_outcome: Optional[str] = Field(
        default=None,
        description="One-sentence objective factual summary of the decision, result, or event",
    )
    is_hard_business_event: bool = Field(
        default=False,
        description="True if article covers a concrete factual event rather than generic opinion/summary",
    )
    has_specific_quantified_impact: bool = Field(
        default=False,
        description="True if article includes specific numbers, revenue metrics, or quantified impact",
    )
    is_investment_relevant: bool = Field(
        default=False,
        description="True if story is of material relevance to an Investment Committee / CIO",
    )

    @field_validator("deal_value", mode="before")
    @classmethod
    def clean_deal_value(cls, v: Optional[str]) -> Optional[str]:
        """Convert placeholder strings like 'N/A' or 'None' to None."""
        if isinstance(v, str):
            cleaned = v.strip()
            if cleaned.lower() in ("none", "n/a", "null", "", "nil"):
                return None
            return cleaned
        return v


class ClassificationResult(BaseModel):
    """
    Wrapper for AI classification outcomes including error handling and retry tracking.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    success: bool = Field(
        ...,
        description="True if article classification was successfully produced and validated",
    )
    classification: Optional[AIArticleClassification] = Field(
        default=None,
        description="Structured classification data on success",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Diagnostic failure reason if classification could not be obtained",
    )
    attempts: int = Field(
        default=1,
        ge=0,
        description="Number of API attempts executed before concluding result",
    )
    raw_response: Optional[str] = Field(
        default=None,
        description="Raw response string from the model",
    )
