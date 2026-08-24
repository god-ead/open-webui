"""Copyright risk feature extractor."""

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


class CopyrightRiskExtractor(BaseExtractor):
    """Extracts copyright risk features from litigation_info data."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        litigation = raw_data.litigation_info or {}
        market = raw_data.market_activity or {}
        features = [
            self._extract_font_infringement(litigation),
            self._extract_other_ip_litigation(litigation),
            self._extract_affiliated_infringement(litigation),
            self._extract_infringement_peak_period(litigation, market),
        ]
        return DimensionFeatures(
            dimension_name="copyright_risk", features=features
        )

    # ------------------------------------------------------------------
    def _extract_font_infringement(self, litigation: dict[str, Any]) -> FeatureValue:
        has_font = litigation.get("font_infringement")
        if has_font is None:
            return FeatureValue(
                name="font_infringement_litigation",
                status=FeatureStatus.UNAVAILABLE,
                source="litigation_info",
            )
        is_true = str(has_font).lower() in ("true", "1", "yes")
        return FeatureValue(
            name="font_infringement_litigation",
            raw_value="true" if is_true else "false",
            normalized_value=1.0 if is_true else 0.0,
            status=FeatureStatus.AVAILABLE,
            source="litigation_info",
        )

    # ------------------------------------------------------------------
    def _extract_other_ip_litigation(self, litigation: dict[str, Any]) -> FeatureValue:
        count = litigation.get("ip_litigation_count")
        if count is None:
            return FeatureValue(
                name="other_ip_litigation",
                status=FeatureStatus.UNAVAILABLE,
                source="litigation_info",
            )
        try:
            count = int(count)
        except (TypeError, ValueError):
            return FeatureValue(
                name="other_ip_litigation",
                status=FeatureStatus.UNAVAILABLE,
                source="litigation_info",
            )
        normalized = min(max(count, 0) / 10.0, 1.0)
        return FeatureValue(
            name="other_ip_litigation",
            raw_value=count,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE,
            source="litigation_info",
        )

    # ------------------------------------------------------------------
    def _extract_affiliated_infringement(
        self, litigation: dict[str, Any]
    ) -> FeatureValue:
        # Try multiple field names for compatibility
        affiliates = litigation.get("affiliated_companies")
        if affiliates is None:
            affiliates = litigation.get("related_companies")
        if affiliates is None:
            return FeatureValue(
                name="affiliated_infringement",
                status=FeatureStatus.UNAVAILABLE,
                source="litigation_info",
            )
        if not isinstance(affiliates, list):
            return FeatureValue(
                name="affiliated_infringement",
                status=FeatureStatus.UNAVAILABLE,
                source="litigation_info",
            )
        # Count affiliated companies with litigation > 0
        risk_count = sum(
            1
            for a in affiliates
            if isinstance(a, dict) and int(a.get("litigation_count", 0)) > 0
        )
        has_risk = risk_count > 0
        return FeatureValue(
            name="affiliated_infringement",
            raw_value="true" if has_risk else "false",
            normalized_value=1.0 if has_risk else 0.0,
            status=FeatureStatus.AVAILABLE,
            source="litigation_info",
        )

    # ------------------------------------------------------------------
    def _extract_infringement_peak_period(
        self, litigation: dict[str, Any], market: dict[str, Any]
    ) -> FeatureValue:
        period = litigation.get("infringement_peak_period")
        if period is not None:
            period = str(period)
            period_map = {
                "active_promotion": 1.0,
                "normal_operation": 0.33,
                "none": 0.0,
            }
            if period in period_map:
                return FeatureValue(
                    name="infringement_peak_period",
                    raw_value=period,
                    normalized_value=period_map[period],
                    status=FeatureStatus.AVAILABLE,
                    source="litigation_info",
                )

        # Infer from market activity: if actively promoting, higher risk period
        ad_level = market.get("ad_activity_level")
        events = market.get("marketing_events")
        has_active_promotion = (
            ad_level in ("high",)
            or (isinstance(events, list) and len(events) > 0)
        )
        if has_active_promotion:
            return FeatureValue(
                name="infringement_peak_period",
                raw_value="active_promotion",
                normalized_value=1.0,
                status=FeatureStatus.INFERRED,
                source="market_activity",
            )

        # If we have market data but no active promotion
        if market:
            return FeatureValue(
                name="infringement_peak_period",
                raw_value="normal_operation",
                normalized_value=0.33,
                status=FeatureStatus.INFERRED,
                source="market_activity",
            )

        return FeatureValue(
            name="infringement_peak_period",
            status=FeatureStatus.UNAVAILABLE,
            source="litigation_info",
        )
