"""Base extractor abstract class for feature extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DimensionFeatures, RawCompanyData


class BaseExtractor(ABC):
    """Abstract base class for all feature extractors.

    Each extractor is responsible for one of the six evaluation dimensions,
    transforming raw company data into structured dimension features.
    """

    @abstractmethod
    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        """Extract features for a single dimension from raw company data.

        Args:
            raw_data: The raw data collected from external sources.

        Returns:
            Extracted features for this dimension.
        """
