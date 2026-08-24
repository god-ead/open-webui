"""Core data models for the customer lookalike prediction system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FeatureStatus(Enum):
    """Status of a feature value."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INFERRED = "inferred"


@dataclass
class DataSource:
    """Tracks the origin and collection time of a data field."""

    source_name: str
    url: str
    collected_at: datetime
    field_name: str


@dataclass
class CompanyMatch:
    """A candidate company returned from a search query."""

    company_id: str
    company_name: str
    legal_representative: str
    source: str
    confidence: float


@dataclass
class RawCompanyData:
    """Raw data collected from external sources for a single company."""

    company_id: str
    company_name: str
    business_info: dict[str, Any] = field(default_factory=dict)
    recruitment_info: dict[str, Any] = field(default_factory=dict)
    market_activity: dict[str, Any] = field(default_factory=dict)
    litigation_info: dict[str, Any] = field(default_factory=dict)
    tech_products: dict[str, Any] = field(default_factory=dict)
    governance_info: dict[str, Any] = field(default_factory=dict)
    financial_info: dict[str, Any] = field(default_factory=dict)
    overseas_info: dict[str, Any] = field(default_factory=dict)
    contact_info: dict[str, Any] = field(default_factory=dict)
    sources: list[DataSource] = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.now)


# --- Feature models ---


@dataclass
class FeatureValue:
    """A single extracted feature with its status and normalized value."""

    name: str
    raw_value: Any = None
    normalized_value: float = 0.0
    status: FeatureStatus = FeatureStatus.UNAVAILABLE
    source: str = ""


@dataclass
class DimensionFeatures:
    """Features belonging to a single evaluation dimension."""

    dimension_name: str
    features: list[FeatureValue] = field(default_factory=list)


@dataclass
class SixDimensionFeatures:
    """All six evaluation dimensions for a company."""

    basic_attributes: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="basic_attributes")
    )
    business_relevance: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="business_relevance")
    )
    public_behavior: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="public_behavior")
    )
    copyright_risk: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="copyright_risk")
    )
    tech_environment: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="tech_environment")
    )
    decision_chain: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="decision_chain")
    )
    marketing_intent: DimensionFeatures = field(
        default_factory=lambda: DimensionFeatures(dimension_name="marketing_intent")
    )


# --- Scoring models ---


@dataclass
class FeatureScore:
    """Score for a single feature within a dimension."""

    feature_name: str
    score: float
    max_score: float
    scoring_reason: str = ""


@dataclass
class DimensionScore:
    """Score for an entire evaluation dimension."""

    dimension_name: str
    raw_score: float
    max_score: float
    weight: float
    weighted_score: float
    feature_scores: list[FeatureScore] = field(default_factory=list)
    data_insufficient: bool = False


@dataclass
class ScoreResult:
    """Complete scoring result across all dimensions."""

    dimension_scores: list[DimensionScore] = field(default_factory=list)
    total_score: float = 0.0
    probability_level: str = ""
    top_factors: list[str] = field(default_factory=list)
    low_confidence: bool = False
    follow_up_suggestion: str = ""


# --- Strategy models ---


@dataclass
class CommunicationSuggestion:
    """A single communication suggestion for the sales team."""

    angle: str
    talk_direction: str
    expected_effect: str


@dataclass
class SalesStrategy:
    """Complete sales strategy generated for a company."""

    entry_point: str = ""
    risk_talk_direction: str = ""
    target_role: str = ""
    product_direction: str = ""
    suggestions: list[CommunicationSuggestion] = field(default_factory=list)
    obstacle_note: str = ""


# --- Top-level result ---


@dataclass
class AnalysisResult:
    """Final output of the full analysis pipeline."""

    report_id: str = ""
    raw_data: RawCompanyData = field(
        default_factory=lambda: RawCompanyData(company_id="", company_name="")
    )
    features: SixDimensionFeatures = field(default_factory=SixDimensionFeatures)
    score_result: ScoreResult = field(default_factory=ScoreResult)
    strategy: SalesStrategy = field(default_factory=SalesStrategy)
    analyzed_at: datetime = field(default_factory=datetime.now)
