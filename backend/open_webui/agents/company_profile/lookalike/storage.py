"""文件存储 — 将分析结果持久化到本地文件系统。

存储目录结构：
    data/reports/{report_id}/
        raw_data.json   — 原始采集数据
        features.json   — 提取的特征数据
        score.json      — 评分结果
        report.md       — Markdown 报告
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .models import (
    AnalysisResult,
    CommunicationSuggestion,
    DataSource,
    DimensionFeatures,
    DimensionScore,
    FeatureScore,
    FeatureStatus,
    FeatureValue,
    RawCompanyData,
    SalesStrategy,
    ScoreResult,
    SixDimensionFeatures,
)
from .report import ReportGenerator

logger = logging.getLogger(__name__)

# Default base directory for report storage
_DEFAULT_BASE_DIR = Path("data/reports")


class StorageError(Exception):
    """Raised on file I/O or permission errors during storage operations."""


@dataclass
class ReportSummary:
    """Lightweight summary returned by :meth:`FileStorage.list_reports`."""

    report_id: str
    company_name: str
    analyzed_at: datetime
    probability_level: str
    total_score: float
    path: str


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

class _Encoder(json.JSONEncoder):
    """Custom encoder that handles *datetime* and *Enum* values."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Enum):
            return o.value
        return super().default(o)



def _serialize(obj: Any) -> Any:
    """Convert a dataclass tree to a plain dict suitable for JSON."""
    return json.loads(json.dumps(asdict(obj), cls=_Encoder))


def _parse_datetime(value: Any) -> datetime:
    """Parse an ISO-format string back to *datetime*."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _build_data_source(d: dict) -> DataSource:
    return DataSource(
        source_name=d["source_name"],
        url=d["url"],
        collected_at=_parse_datetime(d["collected_at"]),
        field_name=d["field_name"],
    )


def _build_raw_company_data(d: dict) -> RawCompanyData:
    return RawCompanyData(
        company_id=d["company_id"],
        company_name=d["company_name"],
        business_info=d.get("business_info", {}),
        recruitment_info=d.get("recruitment_info", {}),
        market_activity=d.get("market_activity", {}),
        litigation_info=d.get("litigation_info", {}),
        tech_products=d.get("tech_products", {}),
        governance_info=d.get("governance_info", {}),
        contact_info=d.get("contact_info", {}),
        sources=[_build_data_source(s) for s in d.get("sources", [])],
        collected_at=_parse_datetime(d["collected_at"]),
    )


def _build_feature_value(d: dict) -> FeatureValue:
    return FeatureValue(
        name=d["name"],
        raw_value=d.get("raw_value"),
        normalized_value=d.get("normalized_value", 0.0),
        status=FeatureStatus(d.get("status", "unavailable")),
        source=d.get("source", ""),
    )


def _build_dimension_features(d: dict) -> DimensionFeatures:
    return DimensionFeatures(
        dimension_name=d["dimension_name"],
        features=[_build_feature_value(fv) for fv in d.get("features", [])],
    )


def _build_six_dimension_features(d: dict) -> SixDimensionFeatures:
    return SixDimensionFeatures(
        basic_attributes=_build_dimension_features(d["basic_attributes"]),
        business_relevance=_build_dimension_features(d["business_relevance"]),
        public_behavior=_build_dimension_features(d["public_behavior"]),
        copyright_risk=_build_dimension_features(d["copyright_risk"]),
        tech_environment=_build_dimension_features(d["tech_environment"]),
        decision_chain=_build_dimension_features(d["decision_chain"]),
    )


def _build_feature_score(d: dict) -> FeatureScore:
    return FeatureScore(
        feature_name=d["feature_name"],
        score=d["score"],
        max_score=d["max_score"],
        scoring_reason=d.get("scoring_reason", ""),
    )


def _build_dimension_score(d: dict) -> DimensionScore:
    return DimensionScore(
        dimension_name=d["dimension_name"],
        raw_score=d["raw_score"],
        max_score=d["max_score"],
        weight=d["weight"],
        weighted_score=d["weighted_score"],
        feature_scores=[_build_feature_score(fs) for fs in d.get("feature_scores", [])],
        data_insufficient=d.get("data_insufficient", False),
    )


def _build_score_result(d: dict) -> ScoreResult:
    return ScoreResult(
        dimension_scores=[_build_dimension_score(ds) for ds in d.get("dimension_scores", [])],
        total_score=d.get("total_score", 0.0),
        probability_level=d.get("probability_level", ""),
        top_factors=d.get("top_factors", []),
        low_confidence=d.get("low_confidence", False),
        follow_up_suggestion=d.get("follow_up_suggestion", ""),
    )


def _build_communication_suggestion(d: dict) -> CommunicationSuggestion:
    return CommunicationSuggestion(
        angle=d["angle"],
        talk_direction=d["talk_direction"],
        expected_effect=d["expected_effect"],
    )


def _build_sales_strategy(d: dict) -> SalesStrategy:
    return SalesStrategy(
        entry_point=d.get("entry_point", ""),
        risk_talk_direction=d.get("risk_talk_direction", ""),
        target_role=d.get("target_role", ""),
        product_direction=d.get("product_direction", ""),
        suggestions=[_build_communication_suggestion(s) for s in d.get("suggestions", [])],
        obstacle_note=d.get("obstacle_note", ""),
    )



# ---------------------------------------------------------------------------
# FileStorage
# ---------------------------------------------------------------------------

class FileStorage:
    """Persist and retrieve :class:`AnalysisResult` on the local filesystem."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        self._report_generator = ReportGenerator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_report(self, report_id: str, data: AnalysisResult) -> str:
        """Save *data* under ``data/reports/{report_id}/``.

        Returns the absolute path to the report directory.

        Raises :class:`StorageError` on permission or I/O errors.
        """
        report_dir = self._base_dir / report_id
        try:
            report_dir.mkdir(parents=True, exist_ok=True)

            # 1. raw_data.json
            self._write_json(report_dir / "raw_data.json", _serialize(data.raw_data))

            # 2. features.json — enrich source field with _source_url from raw_data
            features_data = _serialize(data.features)
            self._enrich_feature_sources(features_data, data.raw_data)
            self._write_json(report_dir / "features.json", features_data)

            # 3. score.json — includes strategy + metadata
            score_payload = {
                "report_id": data.report_id,
                "score_result": _serialize(data.score_result),
                "strategy": _serialize(data.strategy),
                "analyzed_at": data.analyzed_at.isoformat(),
                "company_name": data.raw_data.company_name,
            }
            self._write_json(report_dir / "score.json", score_payload)

            # 4. report.md
            md_content = self._report_generator.generate_markdown(data)
            self._write_text(report_dir / "report.md", md_content)

            logger.info("报告已保存: %s", report_dir)
            return str(report_dir.resolve())

        except PermissionError as exc:
            raise StorageError(
                f"文件写入权限不足，无法保存报告到 {report_dir}。请检查目录权限。"
            ) from exc
        except OSError as exc:
            raise StorageError(
                f"保存报告时发生 I/O 错误: {exc}"
            ) from exc

    def load_report(self, report_id: str) -> AnalysisResult:
        """Load a previously saved analysis result.

        Raises :class:`StorageError` if the report does not exist or files
        are corrupted.
        """
        report_dir = self._base_dir / report_id
        if not report_dir.is_dir():
            raise StorageError(f"报告不存在: {report_id}")

        try:
            raw_data_dict = self._read_json(report_dir / "raw_data.json")
            features_dict = self._read_json(report_dir / "features.json")
            score_dict = self._read_json(report_dir / "score.json")
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"加载报告 {report_id} 时出错: {exc}"
            ) from exc

        raw_data = _build_raw_company_data(raw_data_dict)
        features = _build_six_dimension_features(features_dict)
        score_result = _build_score_result(score_dict["score_result"])
        strategy = _build_sales_strategy(score_dict["strategy"])
        analyzed_at = _parse_datetime(score_dict["analyzed_at"])

        return AnalysisResult(
            report_id=score_dict.get("report_id", report_id),
            raw_data=raw_data,
            features=features,
            score_result=score_result,
            strategy=strategy,
            analyzed_at=analyzed_at,
        )

    def list_reports(self) -> list[ReportSummary]:
        """Return a summary for every saved report, sorted by *analyzed_at* descending."""
        summaries: list[ReportSummary] = []

        if not self._base_dir.is_dir():
            return summaries

        for child in sorted(self._base_dir.iterdir()):
            if not child.is_dir():
                continue
            score_file = child / "score.json"
            if not score_file.exists():
                continue
            try:
                score_dict = self._read_json(score_file)
                sr = score_dict.get("score_result", {})
                summaries.append(
                    ReportSummary(
                        report_id=score_dict.get("report_id", child.name),
                        company_name=score_dict.get("company_name", ""),
                        analyzed_at=_parse_datetime(score_dict["analyzed_at"]),
                        probability_level=sr.get("probability_level", ""),
                        total_score=sr.get("total_score", 0.0),
                        path=str(child.resolve()),
                    )
                )
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                logger.warning("跳过损坏的报告目录 %s: %s", child.name, exc)

        # Most recent first
        summaries.sort(key=lambda s: s.analyzed_at, reverse=True)
        return summaries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _enrich_feature_sources(features_data: dict, raw_data: RawCompanyData) -> None:
        """Replace generic source names (e.g. 'business_info') with actual URLs.

        Looks up _source_url stored in each raw_data section by Kimi collector.
        Falls back to matching DataSource entries from raw_data.sources.
        """
        # Build section_name → URL map from raw_data fields
        url_map: dict[str, str] = {}
        for section_name in ("business_info", "financial_info", "overseas_info",
                             "recruitment_info", "market_activity", "litigation_info",
                             "tech_products", "governance_info", "contact_info"):
            section = getattr(raw_data, section_name, None)
            if isinstance(section, dict):
                url = section.get("_source_url")
                if isinstance(url, str) and url.startswith("http"):
                    url_map[section_name] = url

        # Also build from DataSource entries
        for ds in raw_data.sources:
            if ds.url.startswith("http") and ds.field_name not in url_map:
                url_map[ds.field_name] = ds.url

        # Walk features and enrich source field
        for dim_data in features_data.values():
            if not isinstance(dim_data, dict):
                continue
            for feature in dim_data.get("features", []):
                if not isinstance(feature, dict):
                    continue
                current_source = feature.get("source", "")
                # If source is a section name, try to replace with URL
                if current_source in url_map:
                    feature["source_url"] = url_map[current_source]
                    # Keep original source as source_section
                    feature["source_section"] = current_source

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, cls=_Encoder),
            encoding="utf-8",
        )

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))
