"""Qwen 单主智能体 — 流式选择检索工具、执行并组织最终回答。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from openai import AsyncOpenAI

from founder_sales.assistant.config import Settings


logger = logging.getLogger("uvicorn.error")
AGENT_LOG = "[QWEN-MAIN-AGENT]"
# 固定北京时间：注入的当天日期需与用户所处时区一致
_BEIJING_TIMEZONE = timezone(timedelta(hours=8))
MAIN_AGENT_PROMPT = (Path(__file__).parents[1] / "prompts/main_agent.md").read_text(encoding="utf-8")


def _tool_schema(
    name: str,
    description: str,
    parameter_description: str,
    *,
    parameter_name: str = "query",
) -> dict[str, Any]:
    """构造只有一个必填文本参数的检索 Tool Schema。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    parameter_name: {
                        "type": "string",
                        "description": parameter_description,
                    }
                },
                "required": [parameter_name],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _tool_schema(
        "web_search",
        "查询当前、外部或时效性公开信息，返回未经网页校验的候选回答和来源；用户明确要求不联网时不得调用。",
        "补全主体、时间和指代后的自包含查询。",
        parameter_name="query",
    ),
    _tool_schema(
        "search_sales_knowledge",
        "检索五本销售培训书籍中的销售方法、沟通技巧、异议处理和谈判知识；不包含公司产品、制度、客户档案或其他内部资料。",
        "补全上下文后的自包含销售知识查询。",
        parameter_name="query",
    ),
    _tool_schema(
        "generate_visit_plan",
        "根据客户、拜访对象、目标和合作背景生成完整标准客户拜访计划；仅在用户明确要求完整计划时调用。",
        "包含客户、拜访对象及角色、拜访目标、历史合作和项目背景等已知信息的自包含拜访上下文。",
        parameter_name="visit_context",
    ),
    _tool_schema(
        "generate_company_profile",
        "为指定企业生成完整企业画像和营销价值分析；仅在用户明确要求企业画像、企业分析、客户画像或营销价值研判时调用。",
        "需要生成企业画像的完整企业名称。",
        parameter_name="company_name",
    ),
]


@dataclass
class _ToolCall:
    """合并单个流式 Tool Call 的碎片。"""
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True)
class _PreparedCall:
    """校验后的 Tool Call；error 非空时禁止执行。"""
    call: _ToolCall
    query: str
    error: str | None = None


def qwen_openai_base_url(base_url: str) -> str:
    """从任意 DashScope base URL 构造 OpenAI 兼容接口根地址。"""
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Qwen base URL is invalid")
    return f"{parsed.scheme}://{parsed.netloc}/compatible-mode/v1"


def _content_text(content: Any) -> str:
    """读取 OpenAI 兼容增量中的文本内容。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, Mapping)
    )


def _merge_fragment(current: str, fragment: Any) -> str:
    """合并 ID 或函数名碎片，并忽略供应商重复发送的完整值。"""
    if not isinstance(fragment, str) or not fragment:
        return current
    return current if fragment == current else current + fragment


def _merge_tool_calls(buffers: dict[int, _ToolCall], fragments: Any) -> None:
    """按 index 合并 OpenAI 流式 Tool Call。"""
    if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
        return
    for fragment in fragments:
        if not isinstance(fragment, Mapping):
            continue
        index = fragment.get("index")
        if not isinstance(index, int) or index < 0:
            continue
        call = buffers.setdefault(index, _ToolCall(index=index))
        call.call_id = _merge_fragment(call.call_id, fragment.get("id"))
        function = fragment.get("function") or {}
        if not isinstance(function, Mapping):
            continue
        call.name = _merge_fragment(call.name, function.get("name"))
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            call.arguments += arguments


def _tool_error(code: str, message: str) -> dict[str, Any]:
    """构造主模型可理解的受控 Tool 错误。"""
    return {"ok": False, "error": {"code": code, "message": message}}


def _tool_result_for_model(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """裁剪 Tool 结果，仅保留最终模型生成回答所需的信息。"""
    if result.get("ok") is False:
        return result
    if tool_name == "web_search":
        return {
            "query": result.get("query", ""),
            "answer_text": result.get("answer_text", ""),
            "error": result.get("error"),
        }
    if tool_name == "search_sales_knowledge":
        return {
            "query": result.get("query", ""),
            "hits": [
                {
                    "source_name": hit.get("source_name", ""),
                    "text": hit.get("text", ""),
                }
                for hit in result.get("hits") or []
                if isinstance(hit, Mapping)
            ],
            "error": result.get("error"),
        }
    if tool_name == "generate_visit_plan":
        return {
            key: result.get(key)
            for key in ("plan", "model", "used_fallback")
        }
    if tool_name == "generate_company_profile":
        return {
            key: result.get(key)
            for key in ("company_name", "markdown", "version")
        }
    return result


def _source_suffix(grouped_sources: list[tuple[str, list[dict[str, Any]]]]) -> str:
    """按查询分组生成确定性的文末来源列表；同 URL 跨组只保留首次出现。"""
    seen_urls: set[str] = set()
    blocks: list[str] = []
    for query, sources in grouped_sources:
        lines: list[str] = []
        for source in sources[:10]:
            url = str(source.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = str(source.get("title") or url).replace("[", "\\[").replace("]", "\\]")
            lines.append(f"  - [{title}]({url})")
        if lines:
            blocks.append(f"▸ 查询「{query}」\n" + "\n".join(lines))
    if not blocks:
        return ""
    return "\n\n本次联网搜索来源：\n" + "\n".join(blocks)


class QwenMainAgent:
    """运行单轮 Agent → Tool → Agent 循环，并记录可人工核查的 Tool 日志。"""

    def __init__(
        self,
        settings: Settings,
        client: AsyncOpenAI,
        web_search: Any,
        knowledge: Any,
        visit_plan: Any,
        company_profile: Any,
    ) -> None:
        """注入模型连接、检索、拜访计划和企业画像 Tool。"""
        self.settings = settings
        self.client = client
        self.web_search = web_search
        self.knowledge = knowledge
        self.visit_plan = visit_plan
        self.company_profile = company_profile

    async def _deltas(self, payload: dict[str, Any]) -> AsyncIterator[Mapping[str, Any]]:
        """通过 OpenAI SDK 调用 Qwen，并产出业务层使用的增量字典。"""
        stream = await self.client.chat.completions.create(**payload)
        async for chunk in stream:
            if chunk.choices:
                yield chunk.choices[0].delta.model_dump(exclude_none=True)

    async def _deltas_with_fallback(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[Mapping[str, Any]]:
        """依次请求候选模型；任一模型开始输出后不再继续降级。"""

        models = self.settings.qwen_model_candidates(payload["model"])
        for index, model in enumerate(models):
            started = False
            try:
                async for delta in self._deltas({**payload, "model": model}):
                    started = True
                    yield delta
                return
            except Exception as exc:
                if started or index == len(models) - 1:
                    raise
                logger.warning(
                    "%s model unavailable model=%s error=%s -> fallback=%s",
                    AGENT_LOG,
                    model,
                    exc,
                    models[index + 1],
                )

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        with_tools: bool,
    ) -> dict[str, Any]:
        """构造主模型请求；内置联网搜索参数始终不进入请求。"""
        payload: dict[str, Any] = {
            "model": self.settings.qwen_agent_model,
            "messages": messages,
            "stream": True,
        }
        if with_tools:
            payload.update(
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        return payload

    def _prepare_calls(self, calls: list[_ToolCall]) -> list[_PreparedCall]:
        """执行 Tool 名称和参数校验；单轮并行调用次数与重复不限。"""
        prepared = []
        parameters = {
            "web_search": "query",
            "search_sales_knowledge": "query",
            "generate_visit_plan": "visit_context",
            "generate_company_profile": "company_name",
        }
        for call in calls:
            call.call_id = call.call_id or f"call_{call.index}"
            error = None
            query = ""
            parameter_name = parameters.get(call.name)
            if parameter_name is None:
                error = f"未知 Tool：{call.name or 'empty'}"
            else:
                try:
                    arguments = json.loads(call.arguments)
                except json.JSONDecodeError:
                    arguments = None
                if not isinstance(arguments, dict) or set(arguments) != {parameter_name}:
                    error = f"Tool 参数必须是仅包含 {parameter_name} 的 JSON 对象"
                elif not isinstance(arguments[parameter_name], str) or not arguments[parameter_name].strip():
                    error = f"Tool {parameter_name} 不能为空"
                else:
                    query = arguments[parameter_name].strip()
            logger.info(
                "%s tool_call received call_id=%s tool=%s arguments=%s validation_error=%r",
                AGENT_LOG,
                call.call_id,
                call.name,
                call.arguments,
                error,
            )
            prepared.append(_PreparedCall(call=call, query=query, error=error))
        return prepared

    async def _execute(
        self,
        prepared: _PreparedCall,
        on_section: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """执行一个已校验 Tool，并记录完整结构化响应。"""
        call = prepared.call
        started = perf_counter()
        if prepared.error:
            result = _tool_error("invalid_tool_call", prepared.error)
            logger.warning(
                "%s tool_call rejected call_id=%s tool=%s reason=%s",
                AGENT_LOG,
                call.call_id,
                call.name,
                prepared.error,
            )
        else:
            logger.info(
                "%s tool start call_id=%s tool=%s query=%r",
                AGENT_LOG,
                call.call_id,
                call.name,
                prepared.query,
            )
            try:
                if call.name == "web_search":
                    value = await self.web_search.search(prepared.query, max_sources=15)
                    result = value.model_dump(mode="json")
                elif call.name == "search_sales_knowledge":
                    hits = await asyncio.to_thread(
                        self.knowledge.search,
                        prepared.query,
                    )
                    result = {
                        "query": prepared.query,
                        "hits": [asdict(hit) for hit in hits],
                        "error": None,
                    }
                elif call.name == "generate_visit_plan":
                    result = await self.visit_plan.generate(prepared.query)
                elif call.name == "generate_company_profile":
                    result = await self.company_profile.generate(
                        prepared.query,
                        on_section,
                    )
                else:
                    raise RuntimeError(f"unsupported prepared Tool: {call.name}")
            except Exception as exc:
                logger.exception(
                    "%s tool failed call_id=%s tool=%s",
                    AGENT_LOG,
                    call.call_id,
                    call.name,
                )
                result = _tool_error("tool_failed", f"{call.name} 暂时不可用")
        logger.info(
            "%s tool response call_id=%s tool=%s elapsed=%.4f body=%s",
            AGENT_LOG,
            call.call_id,
            call.name,
            perf_counter() - started,
            json.dumps(result, ensure_ascii=False),
        )
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str | dict[str, Any]]:
        """流式运行单轮主 Agent，最多执行一轮 Tool Call。"""
        # 注入固定时区当天日期，避免模型按知识截止时间补全查询中的年份
        conversation = [
            {
                "role": "system",
                "content": (
                    f"{MAIN_AGENT_PROMPT}\n\n"
                    f"今天是{datetime.now(_BEIJING_TIMEZONE):%Y年%m月%d日}，时间范围请据此计算。"
                ),
            },
            *messages,
        ]
        logger.info(
            "%s model request phase=decision model=%s messages=%d tools=%d",
            AGENT_LOG,
            self.settings.qwen_agent_model,
            len(conversation),
            len(TOOL_SCHEMAS),
        )
        calls: dict[int, _ToolCall] = {}
        direct_answer = ""
        async for delta in self._deltas_with_fallback(self._payload(conversation, with_tools=True)):
            reasoning = _content_text(delta.get("reasoning_content"))
            if reasoning:
                yield {"type": "reasoning", "content": reasoning}
            fragments = delta.get("tool_calls")
            if fragments:
                if direct_answer:
                    raise RuntimeError("Qwen mixed content with tool_calls")
                _merge_tool_calls(calls, fragments)
            content = _content_text(delta.get("content"))
            if content:
                if calls:
                    raise RuntimeError("Qwen mixed tool_calls with content")
                direct_answer += content
                yield content

        if not calls:
            logger.info("%s model response phase=direct body=%s", AGENT_LOG, direct_answer)
            return

        ordered_calls = [calls[index] for index in sorted(calls)]
        prepared = self._prepare_calls(ordered_calls)
        for item in prepared:
            yield {
                "type": "tool_start",
                "call_id": item.call.call_id,
                "name": item.call.name,
                "summary": _tool_summary(item, started=True),
            }
        section_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def execute_all() -> list[dict[str, Any]]:
            try:
                return await asyncio.gather(*(
                    self._execute(
                        item,
                        section_queue.put_nowait
                        if item.call.name == "generate_company_profile"
                        else None,
                    )
                    for item in prepared
                ))
            finally:
                section_queue.put_nowait(None)

        results_task = asyncio.create_task(execute_all())
        while True:
            section = await section_queue.get()
            if section is None:
                break
            yield section
        results = await results_task
        for item, result in zip(prepared, results):
            yield {
                "type": "tool_end",
                "call_id": item.call.call_id,
                "name": item.call.name,
                "summary": _tool_summary(item, started=False, result=result),
                "success": result.get("ok") is not False,
            }

        profile_reports = [
            (
                str(result.get("company_name") or item.query),
                result["markdown"],
            )
            for item, result in zip(prepared, results)
            if item.call.name == "generate_company_profile"
            and result.get("ok") is not False
            and isinstance(result.get("markdown"), str)
            and result["markdown"]
        ]
        only_successful_profiles = (
            bool(prepared)
            and len(profile_reports) == len(prepared)
            and all(
                item.call.name == "generate_company_profile"
                for item in prepared
            )
        )
        if only_successful_profiles:
            logger.info(
                "%s model response phase=profile_artifact body=%s",
                AGENT_LOG,
                "\n\n".join(markdown for _, markdown in profile_reports),
            )
            return

        assistant_calls = [
            {
                "id": item.call.call_id,
                "type": "function",
                "function": {
                    "name": item.call.name,
                    "arguments": item.call.arguments,
                },
            }
            for item in prepared
        ]
        model_results = [
            _tool_result_for_model(item.call.name, result)
            for item, result in zip(prepared, results)
        ]
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": item.call.call_id,
                "name": item.call.name,
                "content": json.dumps(result, ensure_ascii=False),
            }
            for item, result in zip(prepared, model_results)
        ]
        final_messages = [
            *conversation,
            {"role": "assistant", "content": None, "tool_calls": assistant_calls},
            *tool_messages,
        ]
        logger.info(
            "%s model request phase=final model=%s tool_messages=%d",
            AGENT_LOG,
            self.settings.qwen_agent_model,
            len(tool_messages),
        )
        final_answer = ""
        try:
            async for delta in self._deltas_with_fallback(
                self._payload(final_messages, with_tools=False)
            ):
                reasoning = _content_text(delta.get("reasoning_content"))
                if reasoning:
                    yield {"type": "reasoning", "content": reasoning}
                if delta.get("tool_calls"):
                    raise RuntimeError(
                        "Qwen requested a tool after the tool budget was closed"
                    )
                content = _content_text(delta.get("content"))
                if content:
                    final_answer += content
                    yield content
        except Exception:
            if not profile_reports:
                raise
            logger.exception(
                "%s supplemental model response failed; preserving profile artifact",
                AGENT_LOG,
            )
            notice = "\n\n补充分析未能完整生成。"
            final_answer += notice
            yield notice

        grouped_sources = [
            (item.query, result.get("sources") or [])
            for item, result in zip(prepared, results)
            if item.call.name == "web_search"
            and not result.get("error")
        ]
        suffix = _source_suffix(grouped_sources)
        if suffix:
            final_answer += suffix
            yield suffix

        web_errors = [
            result.get("error")
            for item, result in zip(prepared, results)
            if item.call.name == "web_search"
            and isinstance(result.get("error"), str)
            and result["error"]
        ]
        if web_errors:
            error_suffix = "\n\n---\n\n" + "\n".join(
                f"error：{error}" for error in web_errors
            )
            final_answer += error_suffix
            yield error_suffix
        logger.info("%s model response phase=final body=%s", AGENT_LOG, final_answer)


def _tool_summary(
    item: _PreparedCall,
    *,
    started: bool,
    result: Mapping[str, Any] | None = None,
) -> str:
    """生成不包含原始 Tool 返回值的前端过程摘要。"""

    labels = {
        "web_search": "联网搜索",
        "search_sales_knowledge": "销售知识库查询",
        "generate_visit_plan": "拜访计划准备",
        "generate_company_profile": "企业画像生成",
    }
    label = labels.get(item.call.name, "工具调用")
    if started:
        if item.call.name == "generate_company_profile":
            return (
                f"\n\n正在生成「{item.query}」的完整企业画像，通常需要 2–4 分钟；"
                "\n完成的报告章节将依次展示。"
            )
        return f"\n\n正在{label}：{item.query}" if item.query else f"正在{label}"
    if result is None or result.get("ok") is False:
        return f"{label}失败"
    if item.call.name == "web_search":
        count = len(result.get("sources") or [])
        return f"{label}完成：找到 {count} 条相关来源"
    if item.call.name == "search_sales_knowledge":
        count = len(result.get("hits") or [])
        return f"{label}完成：命中 {count} 条内容"
    return f"{label}完成"

__all__ = ["QwenMainAgent", "TOOL_SCHEMAS", "qwen_openai_base_url"]
