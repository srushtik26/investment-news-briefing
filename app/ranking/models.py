"""
Investment Relevance Scoring Data Models.

Defines dimensional score breakdowns, scored event representations, and candidate pools.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.event import Event


class ScoreBreakdown(BaseModel):
    """
    Dimensional breakdown of an event's investment relevance score.
    Weights:
        Financial magnitude:     30% (0.30)
        Market impact:           25% (0.25)
        Investor relevance:      20% (0.20)
        Corporate significance:  15% (0.15)
        Source quality:          10% (0.10)
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    financial_magnitude: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Magnitude of deal size, earnings scale, or revenue metrics (weight: 30%)",
    )
    market_impact: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Breadth of systemic, sector, index, or macroeconomic impact (weight: 25%)",
    )
    investor_relevance: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Direct relevance to portfolio managers / CIO / Investment Committee (weight: 20%)",
    )
    corporate_significance: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Significance of structural change, leadership, or regulatory action (weight: 15%)",
    )
    source_quality: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Corroboration strength and publisher tier quality (weight: 10%)",
    )
    total_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Weighted composite score (0.0 to 100.0)",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Diagnostic explanation summarizing the score calculation",
    )


class ScoredEvent(BaseModel):
    """
    An Event augmented with its calculated investment relevance score and rank.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    event: Event = Field(
        ...,
        description="Canonical business event model",
    )
    score_breakdown: ScoreBreakdown = Field(
        ...,
        description="Detailed dimensional breakdown of the score",
    )
    investment_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Final investment score (0.0 to 100.0)",
    )
    rank: Optional[int] = Field(
        default=None,
        ge=1,
        description="Ranking within its regional pool",
    )


class RankedCandidatePool(BaseModel):
    """
    Top candidate pool (8–10 events per section) prepared for subsequent briefing synthesis.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )

    domestic_candidates: List[ScoredEvent] = Field(
        default_factory=list,
        description="Top ranked Domestic India macro/policy event candidates (8–10 items)",
    )
    india_candidates: List[ScoredEvent] = Field(
        default_factory=list,
        description="Top ranked India business event candidates (8–10 items)",
    )
    international_candidates: List[ScoredEvent] = Field(
        default_factory=list,
        description="Top ranked International business event candidates (8–10 items)",
    )
    total_evaluated: int = Field(
        default=0,
        ge=0,
        description="Total count of verified events evaluated during ranking",
    )
