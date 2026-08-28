"""拜访计划 Tool — 检索销售知识并调用专用 Qwen 生成完整计划。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from founder_sales.assistant.config import Settings


logger = logging.getLogger("uvicorn.error")
VISIT_PLAN_LOG = "[VISIT-PLAN]"
VISIT_PLAN_PROMPT = (
    Path(__file__).parents[1] / "prompts/visit_plan.md"
).read_text(encoding="utf-8")


def _messages(visit_context: str, hits: list[Any]) -> list[dict[str, str]]:
    """将拜访上下文和销售知识命中组织为专用模型输入。"""

    knowledge = "\n\n".join(
        f"来源：{hit.source_name}\n{hit.text}" for hit in hits
    )
    if not knowledge:
        knowledge = "当前未检索到足够知识库依据。"
    return [
        {"role": "system", "content": VISIT_PLAN_PROMPT},
        {
            "role": "user",
            "content": (
                "请根据以下拜访上下文和销售知识参考，生成完整的标准版客户拜访计划。"
                "客户事实只能来自拜访上下文；销售知识仅用于方法和沟通策略。\n\n"
                f"<VISIT_CONTEXT>\n{visit_context}\n</VISIT_CONTEXT>\n\n"
                f"<SALES_KNOWLEDGE>\n{knowledge}\n</SALES_KNOWLEDGE>"
            ),
        },
    ]


class VisitPlanTool:
    """生成拜访计划：销售知识检索 → 专用模型生成 → 失败模型降级。"""

    def __init__(
        self,
        settings: Settings,
        client: AsyncOpenAI,
        knowledge: Any,
    ) -> None:
        """注入共享 Qwen 客户端和已加载的销售知识检索。"""

        self.settings = settings
        self.client = client
        self.knowledge = knowledge

    async def _complete(
        self,
        model: str,
        messages: list[dict[str, str]],
    ) -> str:
        """流式读取指定模型并合并为非空文本计划。"""

        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        parts: list[str] = []
        async for chunk in stream:
            choices = getattr(chunk, "choices", None)
            delta = getattr(choices[0], "delta", None) if choices else None
            content = getattr(delta, "content", None)
            if isinstance(content, str):
                parts.append(content)
        plan = "".join(parts).strip()
        if not plan:
            raise ValueError("拜访计划模型响应缺少文本内容")
        return plan

    async def generate(self, visit_context: str) -> dict[str, Any]:
        """基于自包含拜访上下文生成完整计划，主模型失败时降级一次。"""

        if not isinstance(visit_context, str):
            raise ValueError("visit_context 必须是字符串")
        normalized_context = visit_context.strip()
        if not normalized_context:
            raise ValueError("visit_context 不能为空")

        hits = await asyncio.to_thread(
            self.knowledge.search,
            normalized_context,
        )
        messages = _messages(normalized_context, hits)
        primary_model = self.settings.qwen_visit_model.strip()
        fallback_model = self.settings.qwen_fallback_model.strip()
        try:
            plan = await self._complete(primary_model, messages)
            model = primary_model
            used_fallback = False
        except Exception as exc:
            if not fallback_model or fallback_model == primary_model:
                raise
            logger.warning(
                "%s model unavailable model=%s error=%s -> fallback=%s",
                VISIT_PLAN_LOG,
                primary_model,
                exc,
                fallback_model,
            )
            plan = await self._complete(fallback_model, messages)
            model = fallback_model
            used_fallback = True

        return {
            "visit_context": normalized_context,
            "plan": plan,
            "model": model,
            "used_fallback": used_fallback,
            "knowledge_sources": list(
                dict.fromkeys(
                    str(hit.source_name)
                    for hit in hits
                    if str(hit.source_name).strip()
                )
            ),
        }


__all__ = ["VisitPlanTool"]
