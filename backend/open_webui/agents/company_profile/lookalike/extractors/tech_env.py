"""Technology environment feature extractor."""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseExtractor
from ..models import (
    DimensionFeatures,
    FeatureStatus,
    FeatureValue,
    RawCompanyData,
)

logger = logging.getLogger(__name__)


class TechEnvironmentExtractor(BaseExtractor):
    """Extracts technology environment features from tech_products data."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        tech = raw_data.tech_products or {}
        features = [
            self._extract_has_app(tech),
            self._extract_has_mini_program(tech),
            self._extract_has_game(tech),
            self._extract_website_complexity(tech),
        ]
        return DimensionFeatures(
            dimension_name="tech_environment", features=features
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _to_tristate(value: Any) -> bool | None:
        """Convert a value to True/False/None (three-value encoding)."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        s = str(value).lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
        return None

    # ------------------------------------------------------------------
    def _extract_has_app(self, tech: dict[str, Any]) -> FeatureValue:
        raw = tech.get("has_app")
        tristate = self._to_tristate(raw)
        if tristate is None and raw is None:
            return FeatureValue(
                name="has_app",
                status=FeatureStatus.UNAVAILABLE,
                source="tech_products",
            )
        return FeatureValue(
            name="has_app",
            raw_value=tristate,
            normalized_value=1.0 if tristate else 0.0,
            status=FeatureStatus.AVAILABLE if tristate is not None else FeatureStatus.UNAVAILABLE,
            source="tech_products",
        )

    # ------------------------------------------------------------------
    def _extract_has_mini_program(self, tech: dict[str, Any]) -> FeatureValue:
        raw = tech.get("has_mini_program")
        tristate = self._to_tristate(raw)
        if tristate is None and raw is None:
            return FeatureValue(
                name="has_mini_program",
                status=FeatureStatus.UNAVAILABLE,
                source="tech_products",
            )
        return FeatureValue(
            name="has_mini_program",
            raw_value=tristate,
            normalized_value=1.0 if tristate else 0.0,
            status=FeatureStatus.AVAILABLE if tristate is not None else FeatureStatus.UNAVAILABLE,
            source="tech_products",
        )

    # ------------------------------------------------------------------
    def _extract_has_game(self, tech: dict[str, Any]) -> FeatureValue:
        raw = tech.get("has_game")
        tristate = self._to_tristate(raw)
        if tristate is None and raw is None:
            return FeatureValue(
                name="has_game",
                status=FeatureStatus.UNAVAILABLE,
                source="tech_products",
            )
        return FeatureValue(
            name="has_game",
            raw_value=tristate,
            normalized_value=1.0 if tristate else 0.0,
            status=FeatureStatus.AVAILABLE if tristate is not None else FeatureStatus.UNAVAILABLE,
            source="tech_products",
        )

    # ------------------------------------------------------------------
    def _extract_website_complexity(self, tech: dict[str, Any]) -> FeatureValue:
        # Try multiple field names
        complexity = tech.get("website_complexity") or tech.get("tech_complexity")
        if complexity is None:
            # Infer from has_website
            has_website = tech.get("has_website")
            if has_website is True or str(has_website).lower() in ("true", "1", "yes"):
                return FeatureValue(
                    name="website_complexity",
                    raw_value="template",
                    normalized_value=0.5,
                    status=FeatureStatus.INFERRED,
                    source="tech_products",
                )
            return FeatureValue(
                name="website_complexity",
                status=FeatureStatus.UNAVAILABLE,
                source="tech_products",
            )
        complexity = str(complexity).lower()
        # Map tech_complexity values (high/medium/low) to website complexity
        complexity_map = {
            "custom": 1.0, "high": 1.0,
            "template": 0.5, "medium": 0.5,
            "none": 0.0, "low": 0.25,
        }
        if complexity not in complexity_map:
            return FeatureValue(
                name="website_complexity",
                raw_value="template",
                normalized_value=0.5,
                status=FeatureStatus.INFERRED,
                source="tech_products",
            )
        # Map to the scoring config expected values
        config_value_map = {
            "custom": "custom", "high": "custom",
            "template": "template", "medium": "template",
            "none": "none", "low": "none",
        }
        return FeatureValue(
            name="website_complexity",
            raw_value=config_value_map.get(complexity, complexity),
            normalized_value=complexity_map[complexity],
            status=FeatureStatus.AVAILABLE,
            source="tech_products",
        )
