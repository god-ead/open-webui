"""Framework-independent orchestration for company profile generation."""

from __future__ import annotations

from pathlib import Path

from .config import CompanyProfileConfig
from .errors import CompanyNotFoundError, CompanyProfileConfigurationError
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
            raise CompanyNotFoundError(f"未找到 {normalized_name} 的有效公开信息")

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
