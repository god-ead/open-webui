"""Translate Open WebUI Pipe valves into application configuration."""

from __future__ import annotations

from ..application.config import CompanyProfileConfig


def config_from_valves(valves: object) -> CompanyProfileConfig:
    return CompanyProfileConfig(
        llm_api_key=str(getattr(valves, "llm_api_key", "") or "").strip(),
        llm_base_url=str(getattr(valves, "llm_base_url", "") or "").strip(),
        llm_model=str(getattr(valves, "llm_model", "") or "").strip(),
        llm_timeout_seconds=int(
            getattr(valves, "llm_timeout_seconds", 180) or 180
        ),
    )


def format_configuration_error(config: CompanyProfileConfig) -> str | None:
    missing = config.missing_fields()
    if not missing:
        return None

    joined = "、".join(missing)
    return (
        "企业分析 Pipe 尚未完成管理员配置："
        f"缺少 `{joined}`。"
        "请在 Pipe valves 中设置 llm_api_key、llm_base_url、llm_model。"
    )


def get_last_user_message(messages: list[dict]) -> str | None:
    """Read OpenAI-style message content without importing Open WebUI internals."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue

        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    return item.get("text")
            return None
        return content
    return None
