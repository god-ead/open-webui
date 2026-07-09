"""企业画像任务处理器 — 注册到 Worker 中消费 profile 类型任务"""

from __future__ import annotations

import os
import time
from typing import Any

from company_profile.application import CompanyProfileConfig, CompanyProfileService

from .pdf_export import ProfilePdfExporter


class CompanyProfileHandler:
    """企业画像生成 Handler：提取公司名 → LLM 生成分析 → 导出 PDF → 返回下载链接"""

    task_type = "profile"

    def __init__(
        self,
        service: CompanyProfileService,
        pdf_exporter: ProfilePdfExporter,
    ) -> None:
        self.service = service
        self.pdf_exporter = pdf_exporter

    @classmethod
    def from_env(cls) -> "CompanyProfileHandler":
        """从环境变量构建 Handler 实例（LLM 密钥、模型、超时等）"""
        config = CompanyProfileConfig(
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv(
                "LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            llm_model=os.getenv("LLM_MODEL", "qwen3.5-plus"),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
        )
        return cls(
            CompanyProfileService(config),
            ProfilePdfExporter.from_env(),
        )

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理企业画像生成任务，返回包含下载链接的统一响应"""
        company_name = str(payload.get("company_name", "")).strip()
        if not company_name:
            raise ValueError("input.company_name 不能为空")
        task_id = str(payload.get("task_id", ""))[:16]

        result = self.service.generate(company_name)
        download_url = self.pdf_exporter.export(result, task_id=task_id)
        return {
            "code": 0,
            "message": "success",
            "timestamp": time.strftime("%Y%m%d%H%M%S", time.localtime()),
            "data": {
                "profile": download_url,
                "version": result.version,
            },
        }
