"""Framework-independent orchestration for company profile generation."""

from __future__ import annotations

from pathlib import Path

from .config import CompanyProfileConfig
from .errors import (
    CompanyNotFoundError,
    CompanyProfileConfigurationError,
    LLMServiceError,
)
from .match_policy import pick_best_match
from .result import ProfileApplicationResult
from ..lookalike.collectors.kimi_collector import QwenCollector
from ..lookalike.collectors.manager import CollectorManager
from ..lookalike.collectors.marketing_channels import MarketingChannelsAdapter
from ..lookalike.collectors.web_scraper import WebScraperAdapter
from ..lookalike.engine import AnalysisEngine, MultiMatchResult
from ..lookalike.models import AnalysisResult, CompanyMatch
from ..lookalike.report import ReportGenerator
from ..lookalike.scoring.config_loader import load_config
from ..noop_storage import NoopStorage

_RAW_DATA_FIELDS = (
    "business_info",
    "recruitment_info",
    "market_activity",
    "litigation_info",
    "tech_products",
    "governance_info",
    "financial_info",
    "overseas_info",
    "contact_info",
)


class CompanyProfileService:
    """Generate company profiles without depending on Open WebUI runtime objects."""

    def __init__(
        self,
        config: CompanyProfileConfig,
        scoring_config_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self.scoring_config_path = Path(scoring_config_path or self._default_config_path())
        self._reporter = ReportGenerator()

    def generate(self, company_name: str) -> ProfileApplicationResult:
        self._validate_configuration()
        normalized_name = (company_name or "").strip()
        if not normalized_name:
            raise CompanyNotFoundError("company_name 不能为空")

        candidates: tuple[CompanyMatch, ...] = ()
        selected_match: CompanyMatch | None = None
        result = self.analyze_company(normalized_name)

        if isinstance(result, MultiMatchResult):
            candidates = tuple(result.matches)
            selected_match = self.pick_best_match(result.matches)
            if selected_match is None:
                raise CompanyNotFoundError(f"未找到 {normalized_name} 的匹配企业")
            result = self.analyze_match(selected_match)

        if self.is_empty_result(result):
            if self._is_llm_collection_failed(result):
                raise LLMServiceError(
                    f"大模型调用异常：企业 {normalized_name} 信息采集失败，"
                    f"请检查 LLM API 配置或稍后重试"
                )
            raise CompanyNotFoundError(f"未找到 {normalized_name} 的有效公开信息")

        # 即使 sources 非空（含 collection_failed 标记），
        # 若所有数据字段均为空，仍应视为 LLM 采集失败
        if self._is_llm_collection_failed(result) and self._has_no_meaningful_data(result):
            raise LLMServiceError(
                f"大模型调用异常：企业 {normalized_name} 信息采集失败，"
                f"请检查 LLM API 配置或稍后重试"
            )

        markdown = self.render_markdown(result)
        scoring_config = load_config(str(self.scoring_config_path))
        return ProfileApplicationResult(
            company_name=result.raw_data.company_name or normalized_name,
            markdown=markdown,
            report_id=result.report_id,
            version=str(scoring_config.get("version", "")),
            analysis=result,
            candidates=candidates,
            selected_match=selected_match,
        )

    def analyze_company(self, company_name: str) -> AnalysisResult | MultiMatchResult:
        self._validate_configuration()
        return self._build_engine().analyze(
            company_name,
            config_path=str(self.scoring_config_path),
        )

    def analyze_match(self, match: CompanyMatch) -> AnalysisResult:
        self._validate_configuration()
        return self._build_engine().analyze_by_id(
            match,
            config_path=str(self.scoring_config_path),
        )

    def render_markdown(self, result: AnalysisResult) -> str:
        return self._reporter.generate_markdown(result)

    @staticmethod
    def pick_best_match(matches: list[CompanyMatch]) -> CompanyMatch | None:
        return pick_best_match(matches)

    @staticmethod
    def is_empty_result(result: AnalysisResult) -> bool:
        if result.raw_data.company_id or result.raw_data.sources:
            return False
        return not any(
            bool(getattr(result.raw_data, field_name))
            for field_name in _RAW_DATA_FIELDS
        )

    @staticmethod
    def _has_no_meaningful_data(result: AnalysisResult) -> bool:
        """检查是否所有数据采集字段均为空。

        与 :meth:`is_empty_result` 不同，此方法**忽略** sources 列表
        和 company_id —— 仅检查实际采集的业务数据字段。
        LLM 采集失败时 sources 中会有 ``collection_failed`` 标记，
        导致 :meth:`is_empty_result` 返回 False；
        此时需要用本方法判断是否确实毫无可用数据。
        """
        return not any(
            bool(getattr(result.raw_data, field_name))
            for field_name in _RAW_DATA_FIELDS
        )

    @staticmethod
    def _is_llm_collection_failed(result: AnalysisResult) -> bool:
        """检测 LLM（Qwen）采集器是否明确报告采集失败。

        当所有 Qwen 搜索轮次均失败时，QwenCollector 会在 sources 中
        写入 ``field_name == "collection_failed"`` 的状态标记。
        若存在此标记则说明 LLM API 本身出错（网络/超时/服务端错误），
        而非"企业不存在"，应返回 ``code=1``。
        """
        for source in result.raw_data.sources:
            if (
                source.source_name == "qwen_web_search"
                and source.field_name == "collection_failed"
            ):
                return True
        return False

    def _validate_configuration(self) -> None:
        missing = self.config.missing_fields()
        if missing:
            raise CompanyProfileConfigurationError(
                f"缺少企业画像配置: {', '.join(missing)}"
            )

    def _build_engine(self) -> AnalysisEngine:
        qwen_collector = QwenCollector(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url,
            model=self.config.llm_model,
            timeout_seconds=self.config.llm_timeout_seconds,
        )
        collectors = [
            qwen_collector,
            WebScraperAdapter(),
            MarketingChannelsAdapter(),
        ]
        return AnalysisEngine(
            collector_manager=CollectorManager(collectors),
            storage=NoopStorage(),
            config_path=str(self.scoring_config_path),
        )

    @staticmethod
    def _default_config_path() -> Path:
        return Path(__file__).resolve().parents[1] / "scoring_config.yaml"
