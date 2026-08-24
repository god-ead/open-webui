"""Marketing intent extractor — evaluates a company's marketing willingness.

Reads from market_activity fields that Kimi actually returns:
- social_media_accounts / social_media_activity
- ad_platforms / ad_activity_level
- ecommerce_platforms / ecommerce_presence_count
- marketing_events

Produces a single marketing_willingness_score (0-100) used by scoring engine.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import DimensionFeatures, FeatureStatus, FeatureValue, RawCompanyData

logger = logging.getLogger(__name__)


class MarketingIntentExtractor:
    """Extracts marketing intent features from market_activity data."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        activity = raw_data.market_activity or {}

        score, factors = self._calculate_score(activity)

        return DimensionFeatures(
            dimension_name="marketing_intent",
            features=[
                FeatureValue(
                    name="marketing_willingness_score",
                    raw_value=score,
                    normalized_value=score / 100.0,
                    status=FeatureStatus.AVAILABLE if score > 0 else FeatureStatus.UNAVAILABLE,
                    source="market_activity",
                ),
                FeatureValue(
                    name="key_factors",
                    raw_value=factors,
                    normalized_value=0.0,
                    status=FeatureStatus.AVAILABLE,
                    source="market_activity",
                ),
            ],
        )

    @staticmethod
    def _calculate_score(activity: dict[str, Any]) -> tuple[int, list[str]]:
        """Calculate marketing willingness score (0-100) from market_activity."""
        score = 0
        factors: list[str] = []

        # ── 社交媒体账号（0-25分）──
        social = activity.get("social_media_accounts") or []
        if isinstance(social, list):
            n = len(social)
            if n >= 4:
                score += 25
                factors.append(f"社交媒体覆盖广（{n}个平台）")
            elif n >= 2:
                score += 15
                factors.append(f"社交媒体覆盖中等（{n}个平台）")
            elif n >= 1:
                score += 8
                factors.append(f"社交媒体覆盖较少（{n}个平台）")

        # 社媒活跃度加分
        social_level = activity.get("social_media_activity")
        if social_level == "high":
            score += 10
            factors.append("社交媒体高频更新")
        elif social_level == "medium":
            score += 5

        # ── 广告投放（0-20分）──
        ad_platforms = activity.get("ad_platforms") or []
        if isinstance(ad_platforms, list):
            n = len(ad_platforms)
            if n >= 3:
                score += 20
                factors.append(f"多平台广告投放（{n}个）")
            elif n >= 1:
                score += 10
                factors.append(f"有广告投放（{n}个平台）")

        ad_level = activity.get("ad_activity_level")
        if ad_level == "high":
            score += 5

        # ── 电商入驻（0-20分）──
        ecom = activity.get("ecommerce_platforms") or []
        if isinstance(ecom, list):
            n = len(ecom)
            if n >= 3:
                score += 20
                factors.append(f"电商覆盖广（{n}个平台）")
            elif n >= 1:
                score += 10
                factors.append(f"有电商入驻（{n}个平台）")

        # ── 营销事件（0-10分）──
        events = activity.get("marketing_events") or []
        if isinstance(events, list) and len(events) > 0:
            has_major = any(
                any(kw in str(e) for kw in ("品牌升级", "新品发布", "品牌", "升级", "发布会"))
                for e in events
            )
            if has_major:
                score += 10
                factors.append("有重大营销事件")
            else:
                score += 5
                factors.append(f"有常规营销活动（{len(events)}项）")

        # Cap at 100
        score = min(score, 100)

        if not factors:
            factors.append("无营销活动信息")

        return score, factors
