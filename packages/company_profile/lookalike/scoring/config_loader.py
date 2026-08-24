"""YAML 评分配置加载器。

加载、校验和归一化评分配置文件。格式错误抛出 ConfigError，
缺少必要字段时使用内置默认配置兜底。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """评分配置文件格式或内容错误。"""


# ---------------------------------------------------------------------------
# 内置默认配置（兜底用）
# ---------------------------------------------------------------------------

_DEFAULT_FEATURE: dict[str, Any] = {
    "max_score": 1,
    "rules": [],
    "default_score": 0,
}

_DEFAULT_DIMENSION: dict[str, Any] = {
    "weight": 1.0 / 6,
    "max_score": 15,
    "features": {},
}

_DEFAULT_PROBABILITY_LEVELS: dict[str, Any] = {
    "high": {"min_score": 70, "label": "高", "suggestion": "优先跟进"},
    "medium": {"min_score": 40, "label": "中", "suggestion": "进一步调研"},
    "low": {"min_score": 0, "label": "低", "suggestion": "暂缓跟进"},
}

_DEFAULT_MISSING_DATA: dict[str, Any] = {
    "low_confidence_threshold": 2,
}

_REQUIRED_SECTIONS = ("dimensions", "probability_levels", "missing_data")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> dict[str, Any]:
    """加载并校验 YAML 评分配置文件。

    Parameters
    ----------
    path:
        YAML 配置文件路径。

    Returns
    -------
    dict
        校验并归一化后的配置字典。

    Raises
    ------
    ConfigError
        文件不存在、无法解析或结构不合法时抛出。
    """
    raw = _read_yaml(path)
    _validate_structure(raw)
    _validate_dimensions(raw["dimensions"])
    _normalize_weights(raw["dimensions"])
    return raw


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_yaml(path: str | Path) -> dict[str, Any]:
    """读取并解析 YAML 文件，返回字典。"""
    filepath = Path(path)
    if not filepath.exists():
        raise ConfigError(f"配置文件不存在: {filepath}")

    try:
        with open(filepath, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析错误: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("配置文件顶层必须是一个映射（dict）")

    return data


def _validate_structure(config: dict[str, Any]) -> None:
    """校验顶层必要字段，缺失时使用默认值兜底。"""
    if "dimensions" not in config:
        logger.warning("配置缺少 'dimensions' 字段，使用内置默认配置")
        config["dimensions"] = {}

    if "probability_levels" not in config:
        logger.warning("配置缺少 'probability_levels' 字段，使用内置默认配置")
        config["probability_levels"] = dict(_DEFAULT_PROBABILITY_LEVELS)

    if "missing_data" not in config:
        logger.warning("配置缺少 'missing_data' 字段，使用内置默认配置")
        config["missing_data"] = dict(_DEFAULT_MISSING_DATA)


def _validate_dimensions(dimensions: dict[str, Any]) -> None:
    """校验每个维度及其特征的结构，缺失字段用默认值补齐。"""
    if not isinstance(dimensions, dict):
        raise ConfigError("'dimensions' 必须是一个映射（dict）")

    for dim_name, dim_cfg in dimensions.items():
        if not isinstance(dim_cfg, dict):
            raise ConfigError(f"维度 '{dim_name}' 必须是一个映射（dict）")

        # 补齐维度级别缺失字段
        if "weight" not in dim_cfg:
            logger.warning("维度 '%s' 缺少 'weight'，使用默认值 %s", dim_name, _DEFAULT_DIMENSION["weight"])
            dim_cfg["weight"] = _DEFAULT_DIMENSION["weight"]

        if "max_score" not in dim_cfg:
            logger.warning("维度 '%s' 缺少 'max_score'，使用默认值 %s", dim_name, _DEFAULT_DIMENSION["max_score"])
            dim_cfg["max_score"] = _DEFAULT_DIMENSION["max_score"]

        if "features" not in dim_cfg:
            logger.warning("维度 '%s' 缺少 'features'，使用空特征集", dim_name)
            dim_cfg["features"] = {}

        _validate_features(dim_name, dim_cfg["features"])


def _validate_features(dim_name: str, features: dict[str, Any]) -> None:
    """校验维度内每个特征的结构，缺失字段用默认值补齐。"""
    if not isinstance(features, dict):
        raise ConfigError(f"维度 '{dim_name}' 的 'features' 必须是一个映射（dict）")

    for feat_name, feat_cfg in features.items():
        if not isinstance(feat_cfg, dict):
            raise ConfigError(f"特征 '{dim_name}.{feat_name}' 必须是一个映射（dict）")

        if "max_score" not in feat_cfg:
            logger.warning("特征 '%s.%s' 缺少 'max_score'，使用默认值 %s", dim_name, feat_name, _DEFAULT_FEATURE["max_score"])
            feat_cfg["max_score"] = _DEFAULT_FEATURE["max_score"]

        if "rules" not in feat_cfg:
            logger.warning("特征 '%s.%s' 缺少 'rules'，使用空规则列表", dim_name, feat_name)
            feat_cfg["rules"] = list(_DEFAULT_FEATURE["rules"])

        if "default_score" not in feat_cfg:
            logger.warning("特征 '%s.%s' 缺少 'default_score'，使用默认值 %s", dim_name, feat_name, _DEFAULT_FEATURE["default_score"])
            feat_cfg["default_score"] = _DEFAULT_FEATURE["default_score"]


def _normalize_weights(dimensions: dict[str, Any]) -> None:
    """如果维度权重之和不等于 1.0，自动归一化并记录警告。"""
    if not dimensions:
        return

    total = sum(dim.get("weight", 0) for dim in dimensions.values())

    if total == 0:
        logger.warning("所有维度权重之和为 0，无法归一化，将平均分配权重")
        avg = 1.0 / len(dimensions)
        for dim in dimensions.values():
            dim["weight"] = avg
        return

    if not _is_close(total, 1.0):
        logger.warning("维度权重之和为 %.4f（非 1.0），自动归一化", total)
        for dim in dimensions.values():
            dim["weight"] = dim["weight"] / total


def _is_close(a: float, b: float, rel_tol: float = 1e-9, abs_tol: float = 1e-9) -> bool:
    """判断两个浮点数是否足够接近。"""
    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)
