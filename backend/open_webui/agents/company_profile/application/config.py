"""Configuration values required by the company profile application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyProfileConfig:
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int = 180

    def missing_fields(self) -> tuple[str, ...]:
        values = {
            "llm_api_key": self.llm_api_key,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
        }
        return tuple(name for name, value in values.items() if not value)
