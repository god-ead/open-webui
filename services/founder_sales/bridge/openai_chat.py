"""OpenAI Chat Completions Bridge — 转换 LangGraph 消息、事件和 SSE。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

from founder_sales.langgraph_runtime import LangGraphRuntime


MODEL_ID = "founder-sales-assistant"
TASK_MODEL_ID = "task-model-lite"
logger = logging.getLogger("uvicorn.error")
WEB_DEBUG = "[DEBUG-WEB]"
router = APIRouter()


class Message(BaseModel):
    """单条 Chat Completions 文本消息。"""

    role: str
    content: str


class ChatRequest(BaseModel):
    """当前支持的 Chat Completions 请求。"""

    model: str
    messages: list[Message]
    stream: bool = True


def authorize(authorization: str | None) -> None:
    """校验外部调用方的 Bearer API Key。"""

    if authorization != f"Bearer {os.environ['FOUNDER_SALES_API_KEY']}":
        raise HTTPException(status_code=401, detail="invalid API key")


def _thread_id(user_id: str, conversation_id: str) -> str:
    """生成稳定的用户会话键；冒号禁用以避免不同身份组合发生碰撞。"""

    if not user_id or not conversation_id or ":" in user_id or ":" in conversation_id:
        raise HTTPException(status_code=422, detail="invalid identity headers")
    return f"founder-sales:{user_id}:{conversation_id}"


def _convert_messages_to_langgraph(
    messages: list[Message],
) -> list[BaseMessage]:
    """将最后一条 OpenAI 用户消息转换为 LangGraph message。"""

    # 历史消息已由 checkpoint 持有，续接时只写入当前用户消息。
    return [HumanMessage(content=messages[-1].content.strip())]


def _sse(
    completion_id: str,
    content: str = "",
    *,
    role: str | None = None,
    reasoning_content: str | None = None,
    finish: bool = False,
) -> str:
    """构造 OpenAI Chat Completions SSE chunk。"""

    if finish:
        delta = {}
    elif role is not None:
        delta = {"role": role}
    elif reasoning_content is not None:
        delta = {"reasoning_content": reasoning_content}
    else:
        delta = {"content": content}

    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": "stop" if finish else None,
            }
        ],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _openai_events(
    events: AsyncIterator[Any],
    completion_id: str,
) -> AsyncIterator[str]:
    """将 LangGraph 流事件转换为 OpenAI SSE，并保证流正常结束。"""

    yield _sse(completion_id, role="assistant")
    try:
        async for event in events:
            if isinstance(event, str):
                yield _sse(completion_id, event)
            elif isinstance(event, dict):
                process = _process_text(event)
                if process:
                    yield _sse(
                        completion_id,
                        reasoning_content=process,
                    )
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        logger.exception("%s streaming chat failed", WEB_DEBUG)
        yield _sse(
            completion_id,
            reasoning_content="本轮回答失败，请重新发送问题。\n",
        )
    yield _sse(completion_id, finish=True)
    yield "data: [DONE]\n\n"


def _process_text(event: dict[str, Any]) -> str:
    """把内部过程事件转换成 LibreChat 可展示的简短文本。"""

    event_type = event.get("type")
    if event_type == "reasoning":
        return str(event.get("content") or "")
    if event_type == "status":
        text = str(event.get("description") or "").strip()
    elif event_type in {"tool_start", "tool_end"}:
        text = str(event.get("summary") or "").strip()
    else:
        return ""
    return text + "\n" if text else ""


def _completion(
    completion_id: str, content: str, model: str = MODEL_ID
) -> dict[str, Any]:
    """构造标准非流式 Chat Completions 响应。"""

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    }


def _normalize_title(value: str, fallback: str) -> str:
    """把模型输出规整为最多 16 字符的单行纯文本标题。"""

    title = " ".join(value.strip().strip("\"'“”").split())
    return (title or " ".join(fallback.split()))[:16]


@router.get("/healthz")
async def healthz():
    """报告 HTTP 服务和 checkpointer 已完成启动。"""

    return {"ok": True, "checkpointer": "ready"}


@router.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    """返回 OpenAI 模型列表，使前端能发现方正销售助手。"""

    authorize(authorization)
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@router.post("/v1/chat/completions")
async def chat(
    request: ChatRequest,
    http_request: Request,
    authorization: str | None = Header(default=None),
    user_id: str = Header(alias="X-User-Id"),
    conversation_id: str = Header(alias="X-Conversation-Id"),
):
    """将 OpenAI Chat Completions 请求适配为智能体轮次。"""

    authorize(authorization)
    if request.model not in {MODEL_ID, TASK_MODEL_ID} or not request.messages:
        raise HTTPException(status_code=422, detail="invalid request")
    if request.model == MODEL_ID and (
        request.messages[-1].role != "user"
        or not request.messages[-1].content.strip()
    ):
        raise HTTPException(status_code=422, detail="invalid request")

    thread_id = _thread_id(user_id.strip(), conversation_id.strip())
    logger.info(
        "%s chat input thread_id=%s body=%s",
        WEB_DEBUG,
        thread_id,
        request.model_dump_json(),
    )
    runtime: LangGraphRuntime = http_request.app.state.langgraph_runtime
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if request.model == TASK_MODEL_ID:
        if request.stream:
            raise HTTPException(status_code=422, detail="task-model-lite requires stream=false")
        title, user_text, assistant_text = await runtime.title_context(thread_id)
        if not user_text:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        if not assistant_text:
            raise HTTPException(status_code=409, detail="conversation_not_ready")
        if not title:
            try:
                generated = await http_request.app.state.qwen_task_model.generate_title(
                    user_text, assistant_text
                )
            except Exception:
                logger.exception("%s title generation failed thread_id=%s", WEB_DEBUG, thread_id)
                generated = ""
            title = _normalize_title(generated, user_text)
            await runtime.save_title(thread_id, title)
        return _completion(completion_id, title, TASK_MODEL_ID)

    langgraph_messages = _convert_messages_to_langgraph(request.messages)
    events = await runtime.stream_turn(thread_id, langgraph_messages)

    if not request.stream:
        answer = ""
        try:
            async for event in events:
                if isinstance(event, str):
                    answer += event
        except Exception:
            logger.exception("%s non-stream chat failed thread_id=%s", WEB_DEBUG, thread_id)
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "智能体暂时不可用", "type": "server_error"}},
            )
        return _completion(completion_id, answer)
    return StreamingResponse(
        _openai_events(events, completion_id),
        media_type="text/event-stream",
    )


__all__ = ["ChatRequest", "Message", "router"]
