"""评分引擎 — 基于 YAML 配置对多维特征进行规则评分。

核心职责：
- 加载评分配置并对 SixDimensionFeatures 逐项评分
- 计算维度得分、加权综合得分（满分 100）
- 判定成单可能性等级（高/中/低）及跟进建议
- 提取 Top-3 影响因素（按加权贡献值降序）
- 标记数据不足维度和低置信度提示
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..models import (
    DimensionFeatures,
    DimensionScore,
    FeatureScore,
    FeatureStatus,
    FeatureValue,
    ScoreResult,
    SixDimensionFeatures,
)
from .config_loader import load_config

logger = logging.getLogger(__name__)

# Dimension name → attribute name on SixDimensionFeatures（顺序影响报告评分表展示顺序）
_DIMENSION_ATTR_MAP: dict[str, str] = {
    "basic_attributes": "basic_attributes",
    "business_relevance": "business_relevance",
    "public_behavior": "public_behavior",
    "tech_environment": "tech_environment",
    "marketing_intent": "marketing_intent",
    "copyright_risk": "copyright_risk",
    "decision_chain": "decision_chain",
}


# ---------------------------------------------------------------------------
# Rule matching helpers
# ---------------------------------------------------------------------------


def _parse_numeric(value: Any) -> float | None:
    """Try to interpret *value* as a number."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return None


def _match_condition(condition: str, raw_value: Any) -> bool:
    """Evaluate a single condition string against *raw_value*.

    Supported patterns:
    - Numeric comparisons: ">= 50000000", "< 1000000", "> 500", "== 0"
    - Range: ">= 3 and <= 10"
    - Set membership: "in [北京,上海,广州,深圳]"
    - String equality: "== true", "== false", "== custom", "== high", etc.
    - "other" as catch-all
    """
    condition = condition.strip()

    # Catch-all
    if condition == "other":
        return True

    # Range: ">= 3 and <= 10"
    range_match = re.match(
        r"^(>=?|<=?|==)\s*(-?[\d.]+)\s+and\s+(>=?|<=?|==)\s*(-?[\d.]+)$",
        condition,
    )
    if range_match:
        op1, val1, op2, val2 = range_match.groups()
        num = _parse_numeric(raw_value)
        if num is None:
            return False
        return _cmp(num, op1, float(val1)) and _cmp(num, op2, float(val2))

    # Set membership: "in [北京,上海,广州,深圳]"
    set_match = re.match(r"^in\s*\[(.+)]$", condition)
    if set_match:
        members = [m.strip() for m in set_match.group(1).split(",")]
        return str(raw_value).strip() in members

    # Simple comparison: ">= 50000000", "< 1000000", "== true"
    cmp_match = re.match(r"^(>=?|<=?|==|!=)\s*(.+)$", condition)
    if cmp_match:
        op, rhs = cmp_match.group(1), cmp_match.group(2).strip()
        # Try numeric comparison first
        num_lhs = _parse_numeric(raw_value)
        num_rhs = _parse_numeric(rhs)
        if num_lhs is not None and num_rhs is not None:
            return _cmp(num_lhs, op, num_rhs)
        # Fall back to string comparison
        lhs_str = str(raw_value).strip().lower()
        rhs_str = rhs.strip().lower()
        if op == "==":
            return lhs_str == rhs_str
        if op == "!=":
            return lhs_str != rhs_str
        return False

    return False


def _cmp(lhs: float, op: str, rhs: float) -> bool:
    if op == ">=":
        return lhs >= rhs
    if op == ">":
        return lhs > rhs
    if op == "<=":
        return lhs <= rhs
    if op == "<":
        return lhs < rhs
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    return False


# ---------------------------------------------------------------------------
# ScoringEngine
# ---------------------------------------------------------------------------


class ScoringEngine:
    """Rule-based scoring engine driven by YAML configuration."""

    def __init__(self, config_path: str) -> None:
        self._config: dict[str, Any] = load_config(config_path)

    # -- public API ----------------------------------------------------------

    def score(self, features: SixDimensionFeatures) -> ScoreResult:
        """Score all six dimensions and produce a complete ScoreResult."""
        dim_configs = self._config.get("dimensions", {})

        dimension_scores: list[DimensionScore] = []
        # Collect per-feature weighted contributions for Top-3 extraction
        all_feature_contributions: list[tuple[str, float]] = []

        for dim_name, attr_name in _DIMENSION_ATTR_MAP.items():
            dim_cfg = dim_configs.get(dim_name, {})
            dim_features: DimensionFeatures = getattr(features, attr_name)

            dim_score = self._score_dimension(dim_name, dim_cfg, dim_features)
            dimension_scores.append(dim_score)

            # Collect feature-level weighted contributions
            dim_weight = dim_cfg.get("weight", 0)
            dim_max = dim_cfg.get("max_score", 1)
            for fs in dim_score.feature_scores:
                contribution = (fs.score / dim_max) * dim_weight * 100 if dim_max else 0
                all_feature_contributions.append((fs.feature_name, contribution))

        # Total score (clamped to [0, 100])
        total_score = sum(ds.weighted_score for ds in dimension_scores)
        total_score = max(0.0, min(100.0, total_score))

        # Probability level & follow-up suggestion
        probability_level, follow_up_suggestion = self._determine_probability(total_score)

        # Top-3 factors by weighted contribution (descending)
        all_feature_contributions.sort(key=lambda x: x[1], reverse=True)
        top_factors = [name for name, _ in all_feature_contributions[:3]]

        # Low confidence check
        insufficient_count = sum(1 for ds in dimension_scores if ds.data_insufficient)
        threshold = self._config.get("missing_data", {}).get("low_confidence_threshold", 2)
        low_confidence = insufficient_count > threshold

        return ScoreResult(
            dimension_scores=dimension_scores,
            total_score=total_score,
            probability_level=probability_level,
            top_factors=top_factors,
            low_confidence=low_confidence,
            follow_up_suggestion=follow_up_suggestion,
        )

    def reload_config(self, config_path: str) -> None:
        """Hot-reload scoring configuration from a new YAML file."""
        self._config = load_config(config_path)

    # -- internal helpers ----------------------------------------------------

    def _score_dimension(
        self,
        dim_name: str,
        dim_cfg: dict[str, Any],
        dim_features: DimensionFeatures,
    ) -> DimensionScore:
        """Score a single dimension."""
        features_cfg = dim_cfg.get("features", {})
        weight = dim_cfg.get("weight", 0)
        dim_max_score = dim_cfg.get("max_score", 1)

        feature_scores: list[FeatureScore] = []
        raw_score_total = 0.0

        # Build a lookup from feature name → FeatureValue
        fv_map: dict[str, FeatureValue] = {fv.name: fv for fv in dim_features.features}

        for feat_name, feat_cfg in features_cfg.items():
            fv = fv_map.get(feat_name)
            feat_max = feat_cfg.get("max_score", 0)
            default = feat_cfg.get("default_score", 0)
            rules = feat_cfg.get("rules", [])

            score_val, reason = self._score_feature(fv, rules, default)
            # Clamp individual feature score to [0, feat_max]
            score_val = max(0.0, min(float(feat_max), float(score_val)))

            feature_scores.append(
                FeatureScore(
                    feature_name=feat_name,
                    score=score_val,
                    max_score=feat_max,
                    scoring_reason=reason,
                )
            )
            raw_score_total += score_val

        # Weighted score: (raw_score / max_score) * weight * 100
        if dim_max_score > 0:
            weighted_score = (raw_score_total / dim_max_score) * weight * 100
        else:
            weighted_score = 0.0

        # Data insufficient: ALL features in the dimension are UNAVAILABLE
        data_insufficient = self._is_data_insufficient(dim_features, features_cfg)

        return DimensionScore(
            dimension_name=dim_name,
            raw_score=raw_score_total,
            max_score=dim_max_score,
            weight=weight,
            weighted_score=weighted_score,
            feature_scores=feature_scores,
            data_insufficient=data_insufficient,
        )

    def _score_feature(
        self,
        fv: FeatureValue | None,
        rules: list[dict[str, Any]],
        default_score: float,
    ) -> tuple[float, str]:
        """Score a single feature. Returns (score, reason)."""
        # No feature value provided or UNAVAILABLE → use default
        if fv is None or fv.status == FeatureStatus.UNAVAILABLE:
            return float(default_score), "数据缺失，使用默认分"

        raw_value = fv.raw_value

        # Match rules in order, first match wins
        for rule in rules:
            condition = rule.get("condition", "")
            if _match_condition(condition, raw_value):
                return float(rule.get("score", 0)), f"匹配规则: {condition}"

        # No rule matched → use default
        return float(default_score), "无匹配规则，使用默认分"

    @staticmethod
    def _is_data_insufficient(
        dim_features: DimensionFeatures,
        features_cfg: dict[str, Any],
    ) -> bool:
        """A dimension is data-insufficient when ALL configured features
        have status UNAVAILABLE (or are missing from the input entirely)."""
        if not features_cfg:
            return False

        fv_map = {fv.name: fv for fv in dim_features.features}
        for feat_name in features_cfg:
            fv = fv_map.get(feat_name)
            if fv is not None and fv.status != FeatureStatus.UNAVAILABLE:
                return False
        return True

    def _determine_probability(self, total_score: float) -> tuple[str, str]:
        """Return (probability_level_label, follow_up_suggestion)."""
        levels = self._config.get("probability_levels", {})

        # Sort levels by min_score descending so we match highest first
        sorted_levels = sorted(
            levels.items(),
            key=lambda kv: kv[1].get("min_score", 0),
            reverse=True,
        )

        for _key, level_cfg in sorted_levels:
            if total_score >= level_cfg.get("min_score", 0):
                label = level_cfg.get("label", _key)
                suggestion = level_cfg.get("suggestion", "")
                return label, suggestion

        # Fallback (should not happen with a proper config)
        return "低", "暂缓跟进"
