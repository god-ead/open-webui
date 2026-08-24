"""Public behavior feature extractor."""

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


class PublicBehaviorExtractor(BaseExtractor):
    """Extracts public behavior features from market_activity data."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        activity = raw_data.market_activity or {}
        features = [
            self._extract_ad_activity(activity),
            self._extract_social_media_activity(activity),
            self._extract_recent_marketing_events(activity),
            self._extract_ecommerce_presence(activity),
        ]
        return DimensionFeatures(
            dimension_name="public_behavior", features=features
        )

    # ------------------------------------------------------------------
    def _extract_ad_activity(self, activity: dict[str, Any]) -> FeatureValue:
        ad_platforms = activity.get("ad_platforms")
        if ad_platforms is None:
            return FeatureValue(
                name="ad_activity",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        if not isinstance(ad_platforms, list):
            return FeatureValue(
                name="ad_activity",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        count = len(ad_platforms)
        if count >= 2:
            raw_value = "multi_platform"
            normalized = 1.0
        elif count == 1:
            raw_value = "single_platform"
            normalized = 0.6
        else:
            raw_value = "none"
            normalized = 0.0
        return FeatureValue(
            name="ad_activity",
            raw_value=raw_value,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE,
            source="market_activity",
        )

    # ------------------------------------------------------------------
    def _extract_social_media_activity(
        self, activity: dict[str, Any]
    ) -> FeatureValue:
        # Try multiple field names for compatibility
        platforms = activity.get("social_media_platforms") or activity.get("social_media_accounts")
        if platforms is None:
            return FeatureValue(
                name="social_media_activity",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        if not isinstance(platforms, list):
            return FeatureValue(
                name="social_media_activity",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        count = len(platforms)
        normalized = min(count / 5.0, 1.0)
        return FeatureValue(
            name="social_media_activity",
            raw_value=count,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE,
            source="market_activity",
        )

    # ------------------------------------------------------------------
    def _extract_recent_marketing_events(
        self, activity: dict[str, Any]
    ) -> FeatureValue:
        events = activity.get("marketing_events")
        if events is None:
            return FeatureValue(
                name="recent_marketing_events",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        if isinstance(events, str):
            # Direct string value: "major", "regular", "none"
            if events in ("major", "regular", "none"):
                norm_map = {"major": 1.0, "regular": 0.67, "none": 0.0}
                return FeatureValue(
                    name="recent_marketing_events",
                    raw_value=events,
                    normalized_value=norm_map[events],
                    status=FeatureStatus.AVAILABLE,
                    source="market_activity",
                )
        if isinstance(events, list):
            has_major = any(
                "品牌升级" in str(e) or "新品发布" in str(e) or "major" in str(e)
                for e in events
            )
            if has_major:
                raw_value = "major"
                normalized = 1.0
            elif len(events) > 0:
                raw_value = "regular"
                normalized = 0.67
            else:
                raw_value = "none"
                normalized = 0.0
            return FeatureValue(
                name="recent_marketing_events",
                raw_value=raw_value,
                normalized_value=normalized,
                status=FeatureStatus.AVAILABLE,
                source="market_activity",
            )
        return FeatureValue(
            name="recent_marketing_events",
            status=FeatureStatus.UNAVAILABLE,
            source="market_activity",
        )

    # ------------------------------------------------------------------
    def _extract_ecommerce_presence(self, activity: dict[str, Any]) -> FeatureValue:
        platforms = activity.get("ecommerce_platforms")
        if platforms is None:
            return FeatureValue(
                name="ecommerce_presence",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        if not isinstance(platforms, list):
            return FeatureValue(
                name="ecommerce_presence",
                status=FeatureStatus.UNAVAILABLE,
                source="market_activity",
            )
        count = len(platforms)
        normalized = min(count / 5.0, 1.0)
        return FeatureValue(
            name="ecommerce_presence",
            raw_value=count,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE,
            source="market_activity",
        )
