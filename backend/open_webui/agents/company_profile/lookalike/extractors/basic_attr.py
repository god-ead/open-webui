"""Basic attribute feature extractor — v3.0

New features aligned with easy-to-close vs hard-to-close company analysis:
- listing_status: listed / soe / foreign / group / private / sme
- industry_type: consumer_internet / finance / media_game / pharma_retail /
                 premium_consumer / education_tech / manufacturing_brand / heavy_industry
- annual_revenue
- region
- employee_scale
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from .base import BaseExtractor
from ..models import (
    DimensionFeatures,
    FeatureStatus,
    FeatureValue,
    RawCompanyData,
)

logger = logging.getLogger(__name__)

TIER1_CITIES = {"北京", "上海", "广州", "深圳"}
NEW_TIER1_CITIES = {"杭州", "成都", "南京", "武汉", "重庆", "苏州", "西安", "长沙", "天津", "宁波", "合肥", "郑州"}

# Industry keyword → industry_type value
# 按优先级排列：先匹配更具体的类别，再匹配宽泛类别
_INDUSTRY_MAP = [
    # consumer_brand: 面向消费者的品牌企业（汽车、家电、食品、服装、美妆等）
    # 虽有制造环节，但核心是品牌营销，字体使用场景极多（包装/广告/门店/电商）
    ("consumer_brand",    ["汽车", "家电", "电器", "家居", "家具", "服装", "服饰", "鞋",
                           "美妆", "化妆品", "护肤", "日化", "洗护",
                           "食品", "饮料", "饮品", "乳业", "乳制品", "零食", "调味",
                           "手机", "消费电子", "数码",
                           "母婴", "宠物", "玩具", "运动", "户外"]),
    # consumer_internet: 互联网/电商/O2O
    ("consumer_internet", ["互联网", "电商", "消费", "零售", "快消", "餐饮", "外卖",
                           "生鲜", "电子商务", "直播", "短视频", "社交"]),
    # finance: 金融/证券/保险
    ("finance",           ["金融", "银行", "证券", "保险", "基金", "投资", "信托",
                           "期货", "资产管理", "财富管理"]),
    # media_game: 文化传媒/游戏/广告
    ("media_game",        ["传媒", "媒体", "广告", "游戏", "影视", "娱乐", "出版",
                           "文化", "动漫", "动画", "设计"]),
    # pharma_retail: 医药/大健康/零售连锁
    ("pharma_retail",     ["医药", "医疗", "健康", "药", "连锁", "超市", "便利店",
                           "药房", "药店", "医院"]),
    # premium_consumer: 高端消费品（酒类/奢侈品/珠宝）
    ("premium_consumer",  ["酒", "白酒", "啤酒", "葡萄酒", "奢侈", "珠宝", "高端",
                           "豪华", "烟草", "茶"]),
    # education_tech: 教育/科技/软件
    ("education_tech",    ["教育", "培训", "科技", "软件", "信息技术", "IT", "SaaS",
                           "人工智能", "AI", "大数据", "云计算"]),
    # real_estate_hotel: 房地产/酒店/旅游（有品牌营销需求）
    ("real_estate_hotel", ["房地产", "地产", "酒店", "旅游", "景区", "物业", "商业管理"]),
    # heavy_industry: 传统重工业/农业/基建/能源/矿业
    ("heavy_industry",    ["制造", "钢铁", "化工", "煤炭", "矿", "能源", "电力",
                           "农业", "农产品", "建筑", "基建", "工程", "水泥", "机械"]),
]

# Company type keywords → listing_status
_LISTING_KEYWORDS = {
    "listed":  ["上市", "股票代码", "A股", "港股", "美股", "纽交所", "纳斯达克", "NYSE", "NASDAQ"],
    "soe":     ["央企", "国企", "国有", "国资", "中央企业", "省属", "市属国有"],
    "foreign": ["外资", "合资", "外商投资", "独资", "跨国"],
    "group":   ["集团", "控股", "子公司", "分公司"],
}


class BasicAttributeExtractor(BaseExtractor):
    """Extracts basic company attribute features from business_info."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        info = raw_data.business_info or {}
        financial = raw_data.financial_info or {}
        governance = raw_data.governance_info or {}
        overseas = raw_data.overseas_info or {}
        features = [
            self._extract_listing_status(info, governance, overseas),
            self._extract_industry_type(info),
            self._extract_annual_revenue(financial, info),
            self._extract_region(info),
            self._extract_employee_scale(info),
        ]
        return DimensionFeatures(dimension_name="basic_attributes", features=features)

    # ------------------------------------------------------------------
    def _extract_listing_status(
        self,
        info: dict[str, Any],
        governance: dict[str, Any],
        overseas: dict[str, Any],
    ) -> FeatureValue:
        """Classify company as: listed / soe / foreign / group / private / sme."""

        # Aggregate all text signals
        signals = " ".join([
            str(info.get("company_type", "")),
            str(info.get("name", "")),
            str(info.get("business_status", "")),
            str(governance.get("governance_type", "")),
            str(governance.get("compliance_awareness", "")),
        ])

        # Check explicit listing_status from governance first
        gov_type = str(governance.get("governance_type", "") or governance.get("governance_structure", ""))
        if gov_type == "foreign" or str(overseas.get("is_foreign_invested", "")).lower() == "true":
            return FeatureValue(
                name="listing_status", raw_value="foreign",
                normalized_value=0.75, status=FeatureStatus.AVAILABLE, source="governance_info",
            )

        # Keyword scan
        for status, keywords in _LISTING_KEYWORDS.items():
            if any(kw in signals for kw in keywords):
                norm_map = {"listed": 1.0, "soe": 0.875, "foreign": 0.75, "group": 0.5, "private": 0.25, "sme": 0.125}
                return FeatureValue(
                    name="listing_status", raw_value=status,
                    normalized_value=norm_map[status],
                    status=FeatureStatus.INFERRED, source="business_info",
                )

        # Infer from employee scale: large = group, small = sme
        scale = self._parse_employee_scale(info.get("employee_scale"))
        if scale is not None:
            if scale > 1000:
                return FeatureValue(
                    name="listing_status", raw_value="group",
                    normalized_value=0.5, status=FeatureStatus.INFERRED, source="business_info",
                )
            if scale < 50:
                return FeatureValue(
                    name="listing_status", raw_value="sme",
                    normalized_value=0.125, status=FeatureStatus.INFERRED, source="business_info",
                )

        return FeatureValue(
            name="listing_status", raw_value="private",
            normalized_value=0.25, status=FeatureStatus.INFERRED, source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_industry_type(self, info: dict[str, Any]) -> FeatureValue:
        """Map industry to one of 8 industry_type buckets."""
        industry = str(info.get("industry", "") or info.get("main_business", ""))
        name = str(info.get("name", ""))
        text = industry + " " + name

        if not text.strip():
            return FeatureValue(
                name="industry_type", status=FeatureStatus.UNAVAILABLE, source="business_info",
            )

        norm_map = {
            "consumer_brand": 1.0,
            "consumer_internet": 1.0,
            "finance": 0.875,
            "media_game": 0.875,
            "pharma_retail": 0.75,
            "premium_consumer": 0.75,
            "education_tech": 0.625,
            "real_estate_hotel": 0.5,
            "heavy_industry": 0.125,
        }

        for itype, keywords in _INDUSTRY_MAP:
            if any(kw in text for kw in keywords):
                return FeatureValue(
                    name="industry_type", raw_value=itype,
                    normalized_value=norm_map[itype],
                    status=FeatureStatus.INFERRED, source="business_info",
                )

        return FeatureValue(
            name="industry_type", raw_value="real_estate_hotel",
            normalized_value=0.5, status=FeatureStatus.INFERRED, source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_annual_revenue(
        self, financial: dict[str, Any], business: dict[str, Any]
    ) -> FeatureValue:
        revenue = financial.get("estimated_revenue") or business.get("annual_revenue") or business.get("estimated_revenue")
        source = "financial_info" if financial.get("estimated_revenue") else "business_info"

        if revenue is None:
            return FeatureValue(name="annual_revenue", status=FeatureStatus.UNAVAILABLE, source="financial_info")

        try:
            revenue = float(revenue)
            status = FeatureStatus.AVAILABLE
        except (TypeError, ValueError):
            revenue = self._parse_chinese_revenue(str(revenue))
            if revenue is None:
                return FeatureValue(name="annual_revenue", status=FeatureStatus.UNAVAILABLE, source=source)
            status = FeatureStatus.INFERRED

        normalized = min(revenue / 100_000_000_000, 1.0)
        return FeatureValue(
            name="annual_revenue", raw_value=revenue,
            normalized_value=max(0.0, normalized),
            status=status, source=source,
        )

    # ------------------------------------------------------------------
    def _extract_region(self, info: dict[str, Any]) -> FeatureValue:
        region = str(info.get("region", "") or info.get("address", ""))
        if not region.strip():
            return FeatureValue(name="region", status=FeatureStatus.UNAVAILABLE, source="business_info")

        city = self._parse_city(region)
        if city in TIER1_CITIES:
            normalized = 1.0
        elif city in NEW_TIER1_CITIES:
            normalized = 0.75
        else:
            normalized = 0.25

        return FeatureValue(
            name="region", raw_value=city,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE, source="business_info",
        )

    # ------------------------------------------------------------------
    def _extract_employee_scale(self, info: dict[str, Any]) -> FeatureValue:
        scale = info.get("employee_scale")
        if scale is None:
            return FeatureValue(name="employee_scale", status=FeatureStatus.UNAVAILABLE, source="business_info")

        numeric = self._parse_employee_scale(scale)
        if numeric is None:
            return FeatureValue(name="employee_scale", status=FeatureStatus.UNAVAILABLE, source="business_info")

        normalized = min(max(numeric, 0) / 10000.0, 1.0)
        return FeatureValue(
            name="employee_scale", raw_value=numeric,
            normalized_value=normalized,
            status=FeatureStatus.AVAILABLE, source="business_info",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_employee_scale(scale: Any) -> int | None:
        if isinstance(scale, (int, float)):
            return int(scale)
        s = str(scale).replace(",", "").replace(" ", "")
        m = re.match(r"(\d+)\s*人?以上", s)
        if m:
            return int(m.group(1))
        m = re.match(r"(\d+)\s*[-~]\s*(\d+)", s)
        if m:
            return int(m.group(2))
        m = re.match(r"(\d+)\s*人?以下", s)
        if m:
            return int(m.group(1))
        m = re.match(r"(\d+)", s)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _parse_city(region: str) -> str:
        all_cities = TIER1_CITIES | NEW_TIER1_CITIES
        for city in all_cities:
            if city in region:
                return city
        return region

    @staticmethod
    def _parse_chinese_revenue(s: str) -> float | None:
        s = s.replace(",", "").replace(" ", "")
        m = re.match(r"([\d.]+)\s*亿", s)
        if m:
            return float(m.group(1)) * 100_000_000
        m = re.match(r"([\d.]+)\s*万", s)
        if m:
            return float(m.group(1)) * 10_000
        m = re.match(r"([\d.]+)", s)
        if m:
            return float(m.group(1))
        return None
