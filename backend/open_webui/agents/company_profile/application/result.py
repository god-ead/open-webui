"""Result returned by the framework-independent application service."""

from __future__ import annotations

from dataclasses import dataclass

from ..lookalike.models import AnalysisResult, CompanyMatch


@dataclass(frozen=True)
class ProfileApplicationResult:
    company_name: str
    markdown: str
    report_id: str
    version: str
    analysis: AnalysisResult
    candidates: tuple[CompanyMatch, ...] = ()
    selected_match: CompanyMatch | None = None

    @property
    def auto_selected(self) -> bool:
        return len(self.candidates) > 1 and self.selected_match is not None
