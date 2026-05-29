"""Extractor manager that coordinates all six dimension extractors."""

from __future__ import annotations

import logging

from ..models import RawCompanyData, SixDimensionFeatures

logger = logging.getLogger(__name__)


class ExtractorManager:
    """Coordinates the six dimension extractors and assembles the result."""

    def __init__(self) -> None:
        from .basic_attr import BasicAttributeExtractor
        from .business_rel import BusinessRelevanceExtractor
        from .copyright_risk import CopyrightRiskExtractor
        from .decision_chain import DecisionChainExtractor
        from .marketing_intent import MarketingIntentExtractor
        from .public_behavior import PublicBehaviorExtractor
        from .tech_env import TechEnvironmentExtractor

        self._extractors = {
            "basic_attributes": BasicAttributeExtractor(),
            "business_relevance": BusinessRelevanceExtractor(),
            "public_behavior": PublicBehaviorExtractor(),
            "copyright_risk": CopyrightRiskExtractor(),
            "tech_environment": TechEnvironmentExtractor(),
            "decision_chain": DecisionChainExtractor(),
            "marketing_intent": MarketingIntentExtractor(),
        }

    def extract_all(self, raw_data: RawCompanyData) -> SixDimensionFeatures:
        """Run all six extractors and assemble the result.

        Each extractor runs independently; a failure in one does not
        block the others — the failed dimension keeps its default
        (empty) features.

        Args:
            raw_data: The raw data collected from external sources.

        Returns:
            Combined features across all six dimensions.
        """
        features = SixDimensionFeatures()

        for dimension_name, extractor in self._extractors.items():
            try:
                result = extractor.extract(raw_data)
                setattr(features, dimension_name, result)
            except Exception:
                logger.error(
                    "Extractor for '%s' failed, using default features",
                    dimension_name,
                    exc_info=True,
                )

        return features
