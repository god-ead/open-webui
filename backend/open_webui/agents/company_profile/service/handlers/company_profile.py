"""Queue handler for company profile generation."""

from __future__ import annotations

import os
import time
from typing import Any

from company_profile.application import CompanyProfileConfig, CompanyProfileService


class CompanyProfileHandler:
    task_type = "profile"

    def __init__(self, service: CompanyProfileService) -> None:
        self.service = service

    @classmethod
    def from_env(cls) -> "CompanyProfileHandler":
        config = CompanyProfileConfig(
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv(
                "LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            llm_model=os.getenv("LLM_MODEL", "qwen3.5-plus"),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
        )
        return cls(CompanyProfileService(config))

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_name = str(payload.get("company_name", "")).strip()
        if not company_name:
            raise ValueError("input.company_name 不能为空")

        result = self.service.generate(company_name)
        return {
            "code": 0,
            "message": "生成该公司企业画像成功",
            "timestamp": int(time.time() * 1000),
            "data": {
                "profile": result.markdown,
                "version": result.version,
            },
        }
