"""Bridge layer between the pipe and the vendored lookalike runtime."""

from __future__ import annotations

from pathlib import Path

from .lookalike.collectors.kimi_collector import QwenCollector
from .lookalike.collectors.manager import CollectorManager
from .lookalike.collectors.marketing_channels import MarketingChannelsAdapter
from .lookalike.collectors.web_scraper import WebScraperAdapter
from .lookalike.engine import AnalysisEngine, MultiMatchResult
from .lookalike.models import AnalysisResult, CompanyMatch
from .lookalike.report import ReportGenerator
from .noop_storage import NoopStorage

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


class CompanyProfileBridge:
    def __init__(self, valves: object) -> None:
        self._valves = valves
        self._config_path = Path(__file__).resolve().parent / "scoring_config.yaml"
        self._reporter = ReportGenerator()

    def get_configuration_error(self) -> str | None:
        missing = [
            name
            for name in ("llm_api_key", "llm_base_url", "llm_model")
            if not getattr(self._valves, name, "")
        ]
        if not missing:
            return None

        joined = "、".join(missing)
        return (
            "企业分析 Pipe 尚未完成管理员配置："
            f"缺少 `{joined}`。"
            "请在 Pipe valves 中设置 llm_api_key、llm_base_url、llm_model。"
        )

    def analyze_company(self, company_name: str) -> AnalysisResult | MultiMatchResult:
        engine = self._build_engine()
        return engine.analyze(company_name, config_path=str(self._config_path))

    def analyze_match(self, match: CompanyMatch) -> AnalysisResult:
        engine = self._build_engine()
        return engine.analyze_by_id(match, config_path=str(self._config_path))

    def render_markdown(self, result: AnalysisResult) -> str:
        return self._reporter.generate_markdown(result)

    @staticmethod
    def pick_best_match(matches: list[CompanyMatch]) -> CompanyMatch | None:
        if not matches:
            return None

        return max(
            matches,
            key=lambda match: (
                match.confidence,
                len(match.company_name or ""),
                bool(match.legal_representative),
            ),
        )

    @staticmethod
    def format_auto_selected_status(
        matches: list[CompanyMatch],
        selected_match: CompanyMatch,
    ) -> str:
        ranked_matches = sorted(
            matches,
            key=lambda match: (
                match.confidence,
                len(match.company_name or ""),
                bool(match.legal_representative),
            ),
            reverse=True,
        )
        preview = "；".join(
            f"{match.company_name}（{match.confidence:.0%}）"
            for match in ranked_matches[:3]
        )
        return (
            "发现多个匹配企业，"
            f"已自动选择：{selected_match.company_name}（置信度: {selected_match.confidence:.0%}）。"
            f"候选包括：{preview}"
        )

    @staticmethod
    def is_empty_result(result: AnalysisResult) -> bool:
        if result.raw_data.company_id:
            return False

        if result.raw_data.sources:
            return False

        return not any(bool(getattr(result.raw_data, field_name)) for field_name in _RAW_DATA_FIELDS)

    def _build_engine(self) -> AnalysisEngine:
        kimi_collector = QwenCollector(
            api_key=self._valves.llm_api_key,
            base_url=self._valves.llm_base_url,
            model=self._valves.llm_model,
        )
        collectors = [kimi_collector, WebScraperAdapter(), MarketingChannelsAdapter()]
        return AnalysisEngine(
            collector_manager=CollectorManager(collectors),
            storage=NoopStorage(),
            config_path=str(self._config_path),
        )
