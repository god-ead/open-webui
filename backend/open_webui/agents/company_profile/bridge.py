"""Backward-compatible bridge over the framework-independent application."""

from __future__ import annotations

from .adapters.openwebui import config_from_valves, format_configuration_error
from .application.match_policy import format_auto_selected_status
from .application.service import CompanyProfileService
from .lookalike.engine import MultiMatchResult
from .lookalike.models import AnalysisResult, CompanyMatch


class CompanyProfileBridge:
    def __init__(self, valves: object) -> None:
        self._config = config_from_valves(valves)
        self._service = CompanyProfileService(self._config)

    def get_configuration_error(self) -> str | None:
        return format_configuration_error(self._config)

    def analyze_company(self, company_name: str) -> AnalysisResult | MultiMatchResult:
        return self._service.analyze_company(company_name)

    def analyze_match(self, match: CompanyMatch) -> AnalysisResult:
        return self._service.analyze_match(match)

    def render_markdown(self, result: AnalysisResult) -> str:
        return self._service.render_markdown(result)

    @staticmethod
    def pick_best_match(matches: list[CompanyMatch]) -> CompanyMatch | None:
        return CompanyProfileService.pick_best_match(matches)

    @staticmethod
    def format_auto_selected_status(
        matches: list[CompanyMatch],
        selected_match: CompanyMatch,
    ) -> str:
        return format_auto_selected_status(matches, selected_match)

    @staticmethod
    def is_empty_result(result: AnalysisResult) -> bool:
        return CompanyProfileService.is_empty_result(result)
