"""Decision chain feature extractor."""

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

# Governance structure mapping from enterprise type keywords
GOVERNANCE_MAP = {
    "外资": "foreign",
    "合资": "foreign",
    "集团": "group_subsidiary",
    "子公司": "group_subsidiary",
    "股份有限": "joint_stock",
    "股份": "joint_stock",
    "有限责任": "limited",
    "有限": "limited",
    "个体": "individual",
    "个人独资": "individual",
}

VALID_GOVERNANCE = {"foreign", "group_subsidiary", "joint_stock", "limited", "individual", "private_small"}


class DecisionChainExtractor(BaseExtractor):
    """Extracts decision chain features from governance and business info."""

    def extract(self, raw_data: RawCompanyData) -> DimensionFeatures:
        governance = raw_data.governance_info or {}
        business = raw_data.business_info or {}
        gov_feature = self._extract_governance_structure(governance, business)
        employee_feature = self._get_employee_scale(business)
        features = [
            gov_feature,
            self._infer_decision_chain_length(gov_feature, employee_feature),
            self._extract_procurement_system(governance),
            self._extract_compliance_awareness(governance, business),
        ]
        return DimensionFeatures(
            dimension_name="decision_chain", features=features
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _get_employee_scale(business: dict[str, Any]) -> int | None:
        scale = business.get("employee_scale")
        if scale is None:
            return None
        if isinstance(scale, (int, float)):
            return int(scale)
        import re
        s = str(scale).replace(",", "").replace(" ", "")
        # "1000人以上" → 1000
        m = re.match(r"(\d+)\s*人?以上", s)
        if m:
            return int(m.group(1))
        # "500-999人" → take upper bound
        m = re.match(r"(\d+)\s*[-~]\s*(\d+)", s)
        if m:
            return int(m.group(2))
        # "10人以下" → 10
        m = re.match(r"(\d+)\s*人?以下", s)
        if m:
            return int(m.group(1))
        m = re.match(r"(\d+)", s)
        if m:
            return int(m.group(1))
        return None

    # ------------------------------------------------------------------
    def _extract_governance_structure(
        self, governance: dict[str, Any], business: dict[str, Any]
    ) -> FeatureValue:
        # Try direct governance_structure or governance_type field first
        structure = governance.get("governance_structure") or governance.get("governance_type")
        if structure and str(structure) in VALID_GOVERNANCE:
            return FeatureValue(
                name="governance_structure",
                raw_value=str(structure),
                normalized_value=self._governance_norm(str(structure)),
                status=FeatureStatus.AVAILABLE,
                source="governance_info",
            )
        # Infer from enterprise_type in business_info
        enterprise_type = business.get("enterprise_type") or business.get("company_type") or ""
        enterprise_type = str(enterprise_type)
        for keyword, gov_type in GOVERNANCE_MAP.items():
            if keyword in enterprise_type:
                return FeatureValue(
                    name="governance_structure",
                    raw_value=gov_type,
                    normalized_value=self._governance_norm(gov_type),
                    status=FeatureStatus.INFERRED,
                    source="business_info",
                )
        if not enterprise_type:
            return FeatureValue(
                name="governance_structure",
                status=FeatureStatus.UNAVAILABLE,
                source="governance_info",
            )
        return FeatureValue(
            name="governance_structure",
            raw_value="limited",
            normalized_value=self._governance_norm("limited"),
            status=FeatureStatus.INFERRED,
            source="business_info",
        )

    @staticmethod
    def _governance_norm(gov_type: str) -> float:
        norms = {
            "foreign": 1.0,
            "group_subsidiary": 0.8,
            "joint_stock": 0.6,
            "limited": 0.4,
            "private_small": 0.3,
            "individual": 0.2,
        }
        return norms.get(gov_type, 0.4)

    # ------------------------------------------------------------------
    def _infer_decision_chain_length(
        self, gov_feature: FeatureValue, employee_scale: int | None
    ) -> FeatureValue:
        gov = gov_feature.raw_value if gov_feature.status != FeatureStatus.UNAVAILABLE else None
        if gov is None and employee_scale is None:
            return FeatureValue(
                name="decision_chain_length",
                status=FeatureStatus.UNAVAILABLE,
                source="governance_info",
            )
        # Infer chain length from governance structure and employee scale
        length = self._compute_chain_length(gov, employee_scale)
        norm_map = {"short": 1.0, "medium": 0.6, "long": 0.2}
        return FeatureValue(
            name="decision_chain_length",
            raw_value=length,
            normalized_value=norm_map[length],
            status=FeatureStatus.INFERRED,
            source="governance_info",
        )

    @staticmethod
    def _compute_chain_length(gov: str | None, scale: int | None) -> str:
        # Long chain: large companies or complex governance
        if gov in ("foreign", "group_subsidiary"):
            if scale is not None and scale < 50:
                return "medium"
            return "long"
        if gov == "joint_stock":
            if scale is not None and scale > 500:
                return "long"
            return "medium"
        # Individual or private_small
        if gov in ("individual", "private_small"):
            return "short"
        # "limited" or unknown governance
        if scale is not None:
            if scale > 500:
                return "long"
            if scale >= 50:
                return "medium"
            return "short"
        return "medium"

    # ------------------------------------------------------------------
    def _extract_procurement_system(self, governance: dict[str, Any]) -> FeatureValue:
        procurement = governance.get("procurement_system") or governance.get("has_procurement_system")
        if procurement is None:
            return FeatureValue(
                name="procurement_system",
                status=FeatureStatus.UNAVAILABLE,
                source="governance_info",
            )
        is_true = str(procurement).lower() in ("true", "1", "yes")
        return FeatureValue(
            name="procurement_system",
            raw_value="true" if is_true else "false",
            normalized_value=1.0 if is_true else 0.0,
            status=FeatureStatus.AVAILABLE,
            source="governance_info",
        )

    # ------------------------------------------------------------------
    def _extract_compliance_awareness(
        self, governance: dict[str, Any], business: dict[str, Any]
    ) -> FeatureValue:
        awareness = governance.get("compliance_awareness")
        if awareness and str(awareness) in ("high", "medium", "low"):
            val = str(awareness)
            norm_map = {"high": 1.0, "medium": 0.6, "low": 0.2}
            return FeatureValue(
                name="compliance_awareness",
                raw_value=val,
                normalized_value=norm_map[val],
                status=FeatureStatus.AVAILABLE,
                source="governance_info",
            )
        # Infer from governance structure
        enterprise_type = str(business.get("enterprise_type", "") or business.get("company_type", ""))
        if "外资" in enterprise_type or "上市" in enterprise_type:
            return FeatureValue(
                name="compliance_awareness",
                raw_value="high",
                normalized_value=1.0,
                status=FeatureStatus.INFERRED,
                source="business_info",
            )
        if enterprise_type:
            return FeatureValue(
                name="compliance_awareness",
                raw_value="medium",
                normalized_value=0.6,
                status=FeatureStatus.INFERRED,
                source="business_info",
            )
        return FeatureValue(
            name="compliance_awareness",
            status=FeatureStatus.UNAVAILABLE,
            source="governance_info",
        )
