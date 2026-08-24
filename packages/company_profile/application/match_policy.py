"""Deterministic company match selection policy."""

from __future__ import annotations

from ..lookalike.models import CompanyMatch


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
