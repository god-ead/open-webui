"""Business relevance feature extractor."""

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

# Industries with high font-infringement risk
HIGH_RISK_INDUSTRIES = {"广告", "电商", "游戏", "教育", "传媒", "广告传媒", "互联网"}

# Keywords that indicate design/creative positions
DESIGN_KEYWORDS = {"设计", "美工", "UI", "UX", "视觉", "创意", "平面", "品牌设计"}

# Keywords for brand/marketing departments
BRAND_DEPT_KEYWORDS = {"品牌", "市场", "设计部", "创意", "营销"}


class BusinessRelevanceExtractor(BaseExtractor):
    """Extracts business relevance features from recruitment and business info."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        recruitment = raw_data.recruitment_info or {}
        business = raw_data.business_info or {}
        tech = raw_data.tech_products or {}
        market = raw_data.market_activity or {}
        features = [
            self._extract_business_model(business, market),
            self._extract_design_positions(recruitment),
            self._extract_brand_marketing_dept(recruitment, business),
            self._extract_content_output_density(business, market),
        ]
        return DimensionFeatures(
            dimension_name="business_relevance", features=features
        )

    # ------------------------------------------------------------------
    def _extract_business_model(
        self, business: dict[str, Any], market: dict[str, Any]
    ) -> FeatureValue:
        """Classify business model: b2c / b2b2c / b2b_brand / b2b / g2b."""
        # Direct field from LLM extraction
        model = business.get("business_model")
        if model and str(model) in ("b2c", "b2b2c", "b2b_brand", "b2b", "g2b"):
            norm_map = {"b2c": 1.0, "b2b2c": 0.83, "b2b_brand": 0.5, "b2b": 0.17, "g2b": 0.0}
            return FeatureValue(
                name="business_model", raw_value=str(model),
                normalized_value=norm_map[str(model)],
                status=FeatureStatus.AVAILABLE, source="business_info",
            )

        # Infer from industry + market signals
        industry = str(business.get("industry", "") or "")
        name = str(business.get("name", "") or "")
        main_biz = str(business.get("main_business", "") or "")
        text = industry + name + main_biz

        # G2B signals
        g2b_kws = ["政府", "公共", "事业单位", "城投", "交投", "水务", "燃气", "公用事业"]
        if any(kw in text for kw in g2b_kws):
            return FeatureValue(
                name="business_model", raw_value="g2b",
                normalized_value=0.0, status=FeatureStatus.INFERRED, source="business_info",
            )

        # B2C signals: ecommerce presence, consumer-facing industry
        b2c_kws = ["消费", "零售", "电商", "餐饮", "外卖", "快消", "直播", "游戏", "教育", "医药", "酒", "美妆"]
        ecom = market.get("ecommerce_platforms")
        has_ecom = isinstance(ecom, list) and len(ecom) > 0
        if has_ecom or any(kw in text for kw in b2c_kws):
            return FeatureValue(
                name="business_model", raw_value="b2c",
                normalized_value=1.0, status=FeatureStatus.INFERRED, source="business_info",
            )

        # B2B with brand signals
        brand_kws = ["品牌", "市场", "广告", "传媒", "互联网", "科技", "软件"]
        if any(kw in text for kw in brand_kws):
            return FeatureValue(
                name="business_model", raw_value="b2b_brand",
                normalized_value=0.5, status=FeatureStatus.INFERRED, source="business_info",
            )

        # Default B2B
        b2b_kws = ["制造", "工业", "化工", "钢铁", "建筑", "工程", "农业", "能源"]
        if any(kw in text for kw in b2b_kws):
            return FeatureValue(
                name="business_model", raw_value="b2b",
                normalized_value=0.17, status=FeatureStatus.INFERRED, source="business_info",
            )

        return FeatureValue(
            name="business_model", status=FeatureStatus.UNAVAILABLE, source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_design_positions(self, recruitment: dict[str, Any]) -> FeatureValue:
        positions = recruitment.get("positions")
        if positions is None:
            return FeatureValue(
                name="design_positions",
                status=FeatureStatus.UNAVAILABLE,
                source="recruitment_info",
            )
        if not isinstance(positions, list):
            return FeatureValue(
                name="design_positions",
                status=FeatureStatus.UNAVAILABLE,
                source="recruitment_info",
            )
        count = sum(
            1
            for p in positions
            if any(kw in str(p) for kw in DESIGN_KEYWORDS)
        )
        normalized = min(count / 10.0, 1.0)
        return FeatureValue(
            name="design_positions",
            raw_value=count,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE,
            source="recruitment_info",
        )

    # ------------------------------------------------------------------
    def _extract_high_risk_industry(self, business: dict[str, Any]) -> FeatureValue:
        industry = business.get("industry")
        if not industry:
            return FeatureValue(
                name="high_risk_industry",
                status=FeatureStatus.UNAVAILABLE,
                source="business_info",
            )
        industry_str = str(industry)
        # Fuzzy match against high-risk industry keywords
        is_high_risk = any(kw in industry_str for kw in HIGH_RISK_INDUSTRIES)
        return FeatureValue(
            name="high_risk_industry",
            raw_value="true" if is_high_risk else "false",
            normalized_value=1.0 if is_high_risk else 0.0,
            status=FeatureStatus.AVAILABLE,
            source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_brand_marketing_dept(
        self, recruitment: dict[str, Any], business: dict[str, Any]
    ) -> FeatureValue:
        # Check departments from both business_info and recruitment_info
        departments = business.get("departments") or recruitment.get("departments")
        positions = recruitment.get("positions")

        if departments is None and positions is None:
            return FeatureValue(
                name="brand_marketing_dept",
                status=FeatureStatus.UNAVAILABLE,
                source="business_info",
            )

        # Check explicit departments first
        if isinstance(departments, list):
            for dept in departments:
                if any(kw in str(dept) for kw in BRAND_DEPT_KEYWORDS):
                    return FeatureValue(
                        name="brand_marketing_dept",
                        raw_value="explicit",
                        normalized_value=1.0,
                        status=FeatureStatus.AVAILABLE,
                        source="business_info",
                    )

        # Check related positions
        if isinstance(positions, list):
            for pos in positions:
                if any(kw in str(pos) for kw in BRAND_DEPT_KEYWORDS):
                    return FeatureValue(
                        name="brand_marketing_dept",
                        raw_value="related",
                        normalized_value=0.6,
                        status=FeatureStatus.AVAILABLE,
                        source="recruitment_info",
                    )

        return FeatureValue(
            name="brand_marketing_dept",
            raw_value="none",
            normalized_value=0.0,
            status=FeatureStatus.AVAILABLE,
            source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_brand_product_count(
        self, business: dict[str, Any], tech: dict[str, Any], market: dict[str, Any]
    ) -> FeatureValue:
        # Try direct field first
        count = business.get("brand_product_count")
        if count is not None:
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = None

        # Infer from tech_products and market_activity if not directly available
        if count is None:
            inferred = 0
            # Count apps
            app_names = tech.get("app_names")
            if isinstance(app_names, list):
                inferred += len(app_names)
            # Count game names
            game_names = tech.get("game_names")
            if isinstance(game_names, list):
                inferred += len(game_names)
            # Count ecommerce platforms
            ecom = market.get("ecommerce_platforms")
            if isinstance(ecom, list):
                inferred += len(ecom)
            # Count ad platforms as brand presence
            ad_platforms = market.get("ad_platforms")
            if isinstance(ad_platforms, list):
                inferred += len(ad_platforms)

            if inferred > 0:
                count = inferred
                status = FeatureStatus.INFERRED
            else:
                return FeatureValue(
                    name="brand_product_count",
                    status=FeatureStatus.UNAVAILABLE,
                    source="business_info",
                )
        else:
            status = FeatureStatus.AVAILABLE

        normalized = min(max(count, 0) / 10.0, 1.0)
        return FeatureValue(
            name="brand_product_count",
            raw_value=count,
            normalized_value=normalized,
            status=status,
            source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_content_output_density(
        self, business: dict[str, Any], market: dict[str, Any]
    ) -> FeatureValue:
        # Try direct field first
        density = business.get("content_output_density")
        if density is not None:
            density = str(density)
            density_map = {"high": 1.0, "medium": 0.67, "low": 0.33, "none": 0.0}
            if density in density_map:
                return FeatureValue(
                    name="content_output_density",
                    raw_value=density,
                    normalized_value=density_map[density],
                    status=FeatureStatus.AVAILABLE,
                    source="business_info",
                )

        # Infer from market activity signals
        signals = 0
        ad_level = market.get("ad_activity_level")
        if ad_level in ("high",):
            signals += 2
        elif ad_level in ("medium",):
            signals += 1

        social_level = market.get("social_media_activity")
        if social_level in ("high",):
            signals += 2
        elif social_level in ("medium",):
            signals += 1

        events = market.get("marketing_events")
        if isinstance(events, list) and len(events) > 0:
            signals += 1

        if signals == 0 and not market:
            return FeatureValue(
                name="content_output_density",
                status=FeatureStatus.UNAVAILABLE,
                source="business_info",
            )

        if signals >= 4:
            inferred = "high"
        elif signals >= 2:
            inferred = "medium"
        elif signals >= 1:
            inferred = "low"
        else:
            inferred = "none"

        density_map = {"high": 1.0, "medium": 0.67, "low": 0.33, "none": 0.0}
        return FeatureValue(
            name="content_output_density",
            raw_value=inferred,
            normalized_value=density_map[inferred],
            status=FeatureStatus.INFERRED,
            source="market_activity",
        )
