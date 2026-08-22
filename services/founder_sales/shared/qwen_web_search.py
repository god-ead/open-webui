"""Qwen 联网搜索 — 规范化供应商回答、来源和调用元数据。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from founder_sales.assistant.config import Settings


logger = logging.getLogger("uvicorn.error")
WEB_DEBUG = "[DEBUG-WEB]"
_ANSWER_INSTRUCTION = (
    "请执行联网搜索后回答。回答正文不要包含URL、引用角标或来源列表；"
    "来源将由调用程序统一处理。"
)


class WebDecision(BaseModel):
    """联网搜索决策：是否需要搜索及搜索 query。"""

    model_config = ConfigDict(extra="forbid")

    need_search: bool
    query: str


class WebSource(BaseModel):
    """供应商本次搜索返回的候选网页。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    url: str


class WebTokenUsage(BaseModel):
    """联网搜索消耗的文本 Token。"""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class WebContext(BaseModel):
    """联网搜索结果：候选回答、来源、用量和可选错误。"""

    model_config = ConfigDict(extra="forbid")

    query: str
    answer_text: str = ""
    sources: list[WebSource] = Field(default_factory=list)
    search_count: int = 0
    token_usage: WebTokenUsage = Field(default_factory=WebTokenUsage)
    error: str | None = None
    supplier_code: str | None = None
    supplier_message: str | None = None
    request_id: str | None = None


def _json_messages(
    current_input: str,
    messages: list[dict[str, Any]],
    knowledge_hits: list[Any],
) -> list[dict[str, str]]:
    """构造联网搜索决策的系统+用户消息，含输入、历史与知识命中上下文。"""

    context = {
        "current_input": current_input,
        "history": _jsonable(messages),
        "knowledge": _jsonable(knowledge_hits),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是销售助手的联网决策器。根据当前输入、对话历史和内部知识命中，"
                "仅返回 JSON：{\"need_search\":true/false,\"query\":\"...\"}。"
                "需要最新公开信息、具体客户公开动态或内部知识不足时搜索；"
                "私有事实不可猜测。"
            ),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def _jsonable(value: Any) -> Any:
    """递归把任意值转为 JSON 可序列化结构，兼容 Pydantic/dataclass/普通对象。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    attrs = {}
    for key in ("role", "content", "text", "source_name", "chunk_id", "score"):
        if hasattr(value, key):
            attrs[key] = _jsonable(getattr(value, key))
    return attrs if attrs else str(value)


async def decide_web_search(
    current_input: str,
    messages: list[dict[str, Any]],
    knowledge_hits: list[Any],
    llm: Any,
) -> WebDecision:
    """对当前输入做一次模型调用，返回校验后的联网搜索决策。"""

    result = await llm.complete_json(
        _json_messages(current_input, messages, knowledge_hits),
        WebDecision,
    )
    return result if isinstance(result, WebDecision) else WebDecision.model_validate(result)


def _source_from_search_result(result: Any) -> WebSource | None:
    """从供应商元数据提取合法的绝对 HTTP(S) 来源。"""

    if not isinstance(result, Mapping):
        return None
    url = result.get("url")
    if not isinstance(url, str):
        return None
    url = url.strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    title = result.get("title") or result.get("site_name")
    return WebSource(
        title=str(title).strip() if title else None,
        url=url,
    )


def _content_text(content: Any) -> str:
    """读取 Multimodal content 中的文本增量。"""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping)
    )


def _integer(value: Any) -> int:
    """只接受供应商返回的非负整数计数。"""

    return value if isinstance(value, int) and value >= 0 else 0


async def web_search(
    query: str,
    *,
    client: QwenWebSearch,
    max_sources: int = 10,
) -> WebContext:
    """执行一次 agent 搜索并规范化供应商结果。"""

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")
    if max_sources < 1:
        raise ValueError("max_sources must be positive")

    request = {
        "model": client.settings.qwen_search_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": f"{normalized_query}\n\n{_ANSWER_INSTRUCTION}"}
                    ],
                }
            ]
        },
        "parameters": {
            "enable_search": True,
            "search_options": {
                "search_strategy": "agent",
                "enable_source": True,
            },
            "incremental_output": True,
        },
    }
    answer = ""
    sources: list[WebSource] = []
    seen_urls: set[str] = set()
    raw_source_count = 0
    usage: Mapping[str, Any] = {}
    search_count = 0
    supplier_error: str | None = None
    supplier_code: str | None = None
    supplier_message: str | None = None
    request_id: str | None = None
    event_count = 0
    started = perf_counter()

    try:
        logger.info(
            "%s qwen search start model=%s strategy=agent",
            WEB_DEBUG,
            client.settings.qwen_search_model,
        )
        async with client.client.stream(
            "POST",
            client.url,
            headers=client.headers,
            json=request,
            timeout=client.settings.qwen_timeout_seconds,
        ) as response:
            if not response.is_success:
                await response.aread()
                error = f"Qwen HTTP {response.status_code}"
                logger.warning("%s qwen search failed error=%s", WEB_DEBUG, error)
                return WebContext(query=normalized_query, error=error)

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if not value or value == "[DONE]":
                    continue
                try:
                    event = json.loads(value)
                except json.JSONDecodeError:
                    return WebContext(
                        query=normalized_query,
                        error="Qwen SSE 响应无法解析",
                    )
                if not isinstance(event, Mapping):
                    continue
                event_count += 1

                code = event.get("code")
                message = event.get("message")
                event_request_id = event.get("request_id")
                if isinstance(event_request_id, str) and event_request_id:
                    request_id = event_request_id
                if code or message:
                    supplier_code = str(code) if code else None
                    supplier_message = str(message) if message else None
                    supplier_error = (
                        f"Qwen 供应商错误：{supplier_code or supplier_message}"
                    )

                output = event.get("output")
                if isinstance(output, Mapping):
                    choices = output.get("choices") or []
                    if choices and isinstance(choices[0], Mapping):
                        choice_message = choices[0].get("message") or {}
                        if isinstance(choice_message, Mapping):
                            text = _content_text(choice_message.get("content"))
                            answer = text if text.startswith(answer) else answer + text

                    search_info = output.get("search_info") or {}
                    search_results = (
                        search_info.get("search_results") or []
                        if isinstance(search_info, Mapping)
                        else []
                    )
                    if isinstance(search_results, list):
                        raw_source_count = max(raw_source_count, len(search_results))
                        for result in search_results:
                            source = _source_from_search_result(result)
                            if source is None or source.url in seen_urls:
                                continue
                            seen_urls.add(source.url)
                            if len(sources) < max_sources:
                                sources.append(source)

                event_usage = event.get("usage")
                if isinstance(event_usage, Mapping):
                    usage = event_usage
                    plugins = usage.get("plugins") or {}
                    search_usage = (
                        plugins.get("search") or {}
                        if isinstance(plugins, Mapping)
                        else {}
                    )
                    if isinstance(search_usage, Mapping):
                        search_count = max(
                            search_count,
                            _integer(search_usage.get("count")),
                        )
    except httpx.HTTPError as exc:
        error = f"Qwen 请求失败：{type(exc).__name__}"
        logger.warning("%s qwen search failed error=%s", WEB_DEBUG, error)
        return WebContext(query=normalized_query, error=error)

    token_usage = WebTokenUsage(
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        total_tokens=_integer(usage.get("total_tokens")),
    )
    error = supplier_error
    if error is None and event_count == 0:
        error = "Qwen SSE 响应为空"
    elif error is None and not answer.strip():
        error = "Qwen 响应缺少回答"
    elif error is None and search_count == 0:
        error = "Qwen 未执行联网搜索"
    elif error is None and not sources:
        error = "Qwen 联网搜索未返回合法来源"

    elapsed_seconds = perf_counter() - started
    logger.info(
        "%s qwen search finish elapsed=%.4f search_count=%d "
        "tokens=%d raw_sources=%d legal_sources=%d displayed_sources=%d error=%r",
        WEB_DEBUG,
        elapsed_seconds,
        search_count,
        token_usage.total_tokens,
        raw_source_count,
        len(seen_urls),
        len(sources),
        error,
    )
    return WebContext(
        query=normalized_query,
        answer_text=answer,
        sources=sources,
        search_count=search_count,
        token_usage=token_usage,
        error=error,
        supplier_code=supplier_code,
        supplier_message=supplier_message,
        request_id=request_id,
    )


class QwenWebSearch:
    """配置 Qwen 搜索连接，并保持现有图的调用接口。"""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        """注入配置与异步客户端，并预置鉴权请求头。"""

        self.settings = settings
        self.client = client
        self.headers = {
            "Authorization": f"Bearer {settings.qwen_api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "enable",
        }
        self.url = _generation_url(settings.qwen_base_url)

    async def search(self, query: str, *, max_sources: int = 10) -> WebContext:
        """执行联网搜索；内容检查失败且无正文时按配置有限重试。"""

        total_attempts = max(0, self.settings.qwen_search_retry_count) + 1
        for attempt in range(1, total_attempts + 1):
            result = await web_search(query, client=self, max_sources=max_sources)
            retryable = (
                result.supplier_code == "DataInspectionFailed"
                and not result.answer_text.strip()
            )
            if not retryable:
                return result

            will_retry = attempt < total_attempts
            logger.warning(
                "%s qwen search data inspection failed attempt=%d/%d "
                "error=%r message=%r request_id=%r will_retry=%s",
                WEB_DEBUG,
                attempt,
                total_attempts,
                result.error,
                result.supplier_message,
                result.request_id,
                will_retry,
            )
            if not will_retry:
                return result

        raise RuntimeError("unreachable search retry state")


def _generation_url(base_url: str) -> str:
    """由兼容或原生 base URL 生成 Multimodal Generation 地址。"""

    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Qwen base URL is invalid")
    return (
        f"{parsed.scheme}://{parsed.netloc}"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )


__all__ = [
    "QwenWebSearch",
    "WebContext",
    "WebDecision",
    "WebSource",
    "WebTokenUsage",
    "decide_web_search",
    "web_search",
]
