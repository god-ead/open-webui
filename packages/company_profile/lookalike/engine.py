"""分析引擎 — 串联采集→提取→评分→策略→报告完整管道。

核心职责：
- 协调 CollectorManager、ExtractorManager、ScoringEngine、
  StrategyGenerator、ReportGenerator、FileStorage 的完整流程
- 处理多匹配结果的用户确认流程
- 管理分析过程中的错误和降级策略
- 支持使用新配置重新评分（rescore）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .collectors.manager import CollectorManager
from .extractors.manager import ExtractorManager
from .models import (
    AnalysisResult,
    CompanyMatch,
    RawCompanyData,
    SalesStrategy,
    ScoreResult,
    SixDimensionFeatures,
)
from .report import ReportGenerator
from .scoring.engine import ScoringEngine
from .storage import FileStorage
from .strategy import StrategyGenerator

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = "scoring_config.yaml"


@dataclass
class MultiMatchResult:
    """Returned when a company name search yields multiple matches.

    The caller (e.g. CLI) should present the list to the user and then
    call :meth:`AnalysisEngine.analyze_by_id` with the chosen company_id.
    """

    matches: list[CompanyMatch] = field(default_factory=list)


class AnalysisEngine:
    """Core orchestrator that ties together all pipeline components."""

    def __init__(
        self,
        collector_manager: CollectorManager,
        config_path: str = _DEFAULT_CONFIG_PATH,
        storage: FileStorage | None = None,
    ) -> None:
        self._collector = collector_manager
        self._extractor = ExtractorManager()
        self._scorer = ScoringEngine(config_path)
        self._strategy = StrategyGenerator()
        self._reporter = ReportGenerator()
        self._storage = storage or FileStorage()
        self._config_path = config_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        company_name: str,
        config_path: str | None = None,
    ) -> AnalysisResult | MultiMatchResult:
        """Run the full analysis pipeline for *company_name*.

        Returns:
            - :class:`MultiMatchResult` if the search yields multiple
              matches (caller should let the user pick one, then call
              :meth:`analyze_by_id`).
            - :class:`AnalysisResult` on success (including the case
              where exactly one match is found).

        If *config_path* is provided it overrides the default scoring
        configuration for this run.
        """
        if config_path:
            self._scorer.reload_config(config_path)
            self._config_path = config_path

        # Step 1: Search
        matches = self._safe_search(company_name)

        if not matches:
            logger.warning("未找到匹配企业: %s", company_name)
            # Return an empty result with a hint
            return self._empty_result(company_name)

        if len(matches) > 1:
            return MultiMatchResult(matches=matches)

        # Exactly one match — proceed with full pipeline
        return self._run_pipeline(matches[0])

    def analyze_by_id(
        self,
        match: CompanyMatch,
        config_path: str | None = None,
    ) -> AnalysisResult:
        """Run the full pipeline for a specific :class:`CompanyMatch`.

        Typically called after the user selects from a
        :class:`MultiMatchResult`.
        """
        if config_path:
            self._scorer.reload_config(config_path)
            self._config_path = config_path

        return self._run_pipeline(match)

    def rescore(
        self,
        report_id: str,
        config_path: str | None = None,
    ) -> AnalysisResult:
        """Reload an existing report and re-score with a new config.

        Steps:
        1. Load saved raw_data + features from storage.
        2. Reload scoring config (if provided).
        3. Re-score features.
        4. Re-generate strategy.
        5. Save updated report (new report_id).
        """
        existing = self._storage.load_report(report_id)

        effective_config = config_path or self._config_path
        self._scorer.reload_config(effective_config)
        self._config_path = effective_config

        # Re-score
        score_result = self._safe_score(existing.features)

        # Re-generate strategy
        strategy = self._safe_strategy(existing.features, score_result)

        new_report_id = _generate_report_id(existing.raw_data.company_name)
        result = AnalysisResult(
            report_id=new_report_id,
            raw_data=existing.raw_data,
            features=existing.features,
            score_result=score_result,
            strategy=strategy,
            analyzed_at=datetime.now(),
        )

        self._safe_save(new_report_id, result)
        return result

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _run_pipeline(self, match: CompanyMatch) -> AnalysisResult:
        """Execute the full collect → extract → score → strategy → save pipeline."""

        # Step 2: Collect
        raw_data = self._safe_collect(match.company_id)
        if not raw_data.company_name:
            raw_data.company_name = match.company_name

        # Generate report_id with company name
        report_id = _generate_report_id(raw_data.company_name)

        # Step 3: Extract
        features = self._safe_extract(raw_data)

        # Step 4: Score
        score_result = self._safe_score(features)

        # Step 5: Strategy
        strategy = self._safe_strategy(features, score_result)

        # Assemble result
        result = AnalysisResult(
            report_id=report_id,
            raw_data=raw_data,
            features=features,
            score_result=score_result,
            strategy=strategy,
            analyzed_at=datetime.now(),
        )

        # Step 6: Save
        self._safe_save(report_id, result)

        return result

    # ------------------------------------------------------------------
    # Safe wrappers (error handling / degradation)
    # ------------------------------------------------------------------

    def _safe_search(self, company_name: str) -> list[CompanyMatch]:
        """Search with error handling — returns empty list on failure."""
        try:
            return self._collector.search(company_name)
        except Exception:
            logger.error("搜索阶段异常", exc_info=True)
            return []

    def _safe_collect(self, company_id: str) -> RawCompanyData:
        """Collect with error handling — returns empty data on failure."""
        try:
            return self._collector.collect(company_id)
        except Exception:
            logger.error("数据采集阶段异常", exc_info=True)
            return RawCompanyData(company_id=company_id, company_name="")

    def _safe_extract(self, raw_data: RawCompanyData) -> SixDimensionFeatures:
        """Extract with error handling — returns default features on failure."""
        try:
            return self._extractor.extract_all(raw_data)
        except Exception:
            logger.error("特征提取阶段异常", exc_info=True)
            return SixDimensionFeatures()

    def _safe_score(self, features: SixDimensionFeatures) -> ScoreResult:
        """Score with error handling — returns default result on failure."""
        try:
            return self._scorer.score(features)
        except Exception:
            logger.error("评分阶段异常", exc_info=True)
            return ScoreResult()

    def _safe_strategy(
        self,
        features: SixDimensionFeatures,
        score_result: ScoreResult,
    ) -> SalesStrategy:
        """Generate strategy with error handling — returns default on failure."""
        try:
            return self._strategy.generate(features, score_result)
        except Exception:
            logger.error("策略生成阶段异常", exc_info=True)
            return SalesStrategy()

    def _safe_save(self, report_id: str, result: AnalysisResult) -> None:
        """Save report with error handling — logs but does not raise."""
        try:
            self._storage.save_report(report_id, result)
        except Exception:
            logger.error("报告保存阶段异常", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(company_name: str) -> AnalysisResult:
        """Build a minimal AnalysisResult for the no-match case."""
        return AnalysisResult(
            report_id=_generate_report_id(company_name),
            raw_data=RawCompanyData(company_id="", company_name=company_name),
            features=SixDimensionFeatures(),
            score_result=ScoreResult(),
            strategy=SalesStrategy(),
            analyzed_at=datetime.now(),
        )


def _generate_report_id(company_name: str = "") -> str:
    """Generate a unique report ID: company_name + timestamp + short UUID.

    If company_name is provided, it is sanitized and prepended.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    if company_name:
        # Sanitize: keep Chinese chars, alphanumeric, remove path-unsafe chars
        import re
        safe_name = re.sub(r'[\\/:*?"<>|（）\(\)\s]+', '_', company_name)
        safe_name = safe_name.strip('_')
        # Truncate to avoid overly long folder names
        if len(safe_name) > 50:
            safe_name = safe_name[:50]
        return f"{safe_name}_{ts}_{short_uuid}"
    return f"{ts}_{short_uuid}"
