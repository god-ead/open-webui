"""联系方式搜索 — 并行收集公开信息并整理为校验后的 JSON。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from founder_sales.tools.qwen_web_search import WebSource


logger = logging.getLogger("uvicorn.error")
CONTACT_LOG = "[CONTACT-SEARCH]"


CONTACT_QUERIES = {
    "官网": "{company} 官方网站 联系我们 电话 邮箱 地址",
    "天眼查": 'site:tianyancha.com "{company}" 联系电话 邮箱 地址',
    "招投标": '"{company}" 招标 投标 联系人 联系电话 邮箱',
}
_PUBLIC_ONLY = "仅限公开页面明确信息，不要推测"

DEFAULT_CONTACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "contacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "enum": ["官网", "天眼查", "招投标", "其他"],
                    },
                    "contact_name": {"type": ["string", "null"]},
                    "department": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "email": {
                        "type": ["string", "null"],
                        "format": "email",
                    },
                    "address": {"type": ["string", "null"]},
                },
                "required": [
                    "channel",
                    "contact_name",
                    "department",
                    "phone",
                    "email",
                    "address",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["company_name", "contacts"],
    "additionalProperties": False,
}


class ContactSearchResult(BaseModel):
    """联系方式搜索结果：整理数据、公开来源及分渠道错误。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any | None = None
    sources: list[WebSource] = Field(default_factory=list)
    channel_errors: dict[str, str] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class ContactSearch:
    """编排多渠道联网搜索，并将候选文本交给信息整理模块。"""

    def __init__(self, web_search: Any, organizer: Any) -> None:
        """注入联网搜索与信息整理模块。"""

        self.web_search = web_search
        self.organizer = organizer

    async def search(
        self,
        company_name: str,
        *,
        json_schema: Mapping[str, Any] | None = None,
    ) -> ContactSearchResult:
        """并行搜索公开联系方式，允许部分渠道失败后继续整理。"""

        company = company_name.strip()
        if not company:
            raise ValueError("company_name must not be empty")

        channel_queries = [
            (channel, f"{template.format(company=company)} {_PUBLIC_ONLY}")
            for channel, template in CONTACT_QUERIES.items()
        ]
        for channel, query in channel_queries:
            logger.info(
                "%s web request channel=%s query=%r",
                CONTACT_LOG,
                channel,
                query,
            )
        results = await asyncio.gather(
            *(
                self.web_search.search(query, max_sources=5)
                for _, query in channel_queries
            ),
            return_exceptions=True,
        )

        sections: list[str] = []
        sources: list[WebSource] = []
        seen_urls: set[str] = set()
        channel_errors: dict[str, str] = {}

        for (channel, _), result in zip(channel_queries, results):
            if isinstance(result, Exception):
                channel_errors[channel] = f"搜索失败：{type(result).__name__}"
                logger.warning(
                    "%s web response channel=%s error=%s",
                    CONTACT_LOG,
                    channel,
                    channel_errors[channel],
                )
                continue
            if result.error:
                channel_errors[channel] = result.error
                logger.warning(
                    "%s web response channel=%s error=%s",
                    CONTACT_LOG,
                    channel,
                    result.error,
                )
                continue
            answer = result.answer_text.strip()
            logger.info(
                "%s web response channel=%s answer_text=%r",
                CONTACT_LOG,
                channel,
                answer,
            )
            if not answer:
                channel_errors[channel] = "搜索结果缺少回答"
                continue

            sections.append(f"[渠道：{channel}]\n{answer}")
            for source in result.sources:
                if source.url in seen_urls:
                    continue
                seen_urls.add(source.url)
                sources.append(source)

        if not sections:
            return ContactSearchResult(
                success=False,
                channel_errors=channel_errors,
                error_code="CONTACT_SEARCH_FAILED",
                error_message="所有联系方式搜索渠道均失败",
            )

        information = f"目标企业：{company}\n\n" + "\n\n".join(sections)
        organized = await self.organizer.organize(
            information,
            DEFAULT_CONTACT_SCHEMA if json_schema is None else json_schema,
        )
        if not organized.success:
            return ContactSearchResult(
                success=False,
                sources=sources,
                channel_errors=channel_errors,
                error_code=organized.error_code,
                error_message=organized.error_message,
            )

        return ContactSearchResult(
            success=True,
            data=organized.data,
            sources=sources,
            channel_errors=channel_errors,
        )


__all__ = [
    "CONTACT_QUERIES",
    "DEFAULT_CONTACT_SCHEMA",
    "ContactSearch",
    "ContactSearchResult",
]
