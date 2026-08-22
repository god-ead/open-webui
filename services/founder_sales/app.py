"""OpenAI 兼容接入层 — 管理身份、checkpoint、MessageID 幂等和 SSE。"""

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from company_profile.application import (
    CompanyProfileConfig,
    CompanyProfileService,
)
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from founder_sales.assistant.config import Settings
from founder_sales.assistant.state import TurnRecord
from founder_sales.main_agent_graph import build_main_agent_graph
from founder_sales.shared.knowledge import KnowledgeService
from founder_sales.shared.qwen_main_agent import QwenMainAgent
from founder_sales.shared.qwen_web_search import QwenWebSearch
from founder_sales.tools import CompanyProfileTool


MODEL_ID = "founder-sales-assistant"
FAILURE_TEXT = "\n\n本轮回答失败，请重新发送问题。\n"
logger = logging.getLogger("uvicorn.error")
WEB_DEBUG = "[DEBUG-WEB-7f3a]"


class Message(BaseModel):
    """单条 Chat Completions 文本消息。"""

    role: str
    content: str


class ChatRequest(BaseModel):
    """当前支持的 Chat Completions 请求。"""

    model: str
    messages: list[Message]
    stream: bool = True


def _turn(value: Any) -> TurnRecord | None:
    """将 checkpoint 值转换为轮次记录，无法解析时视为不存在。"""

    if value is None:
        return None
    try:
        return value if isinstance(value, TurnRecord) else TurnRecord.model_validate(value)
    except Exception:
        return None


def _thread_id(user_id: str, conversation_id: str) -> str:
    """生成稳定的用户会话键；冒号禁用以避免不同身份组合发生碰撞。"""

    if not user_id or not conversation_id or ":" in user_id or ":" in conversation_id:
        raise HTTPException(status_code=422, detail="invalid identity headers")
    return f"founder-sales:{user_id}:{conversation_id}"


def _turn_values(turn: TurnRecord) -> dict[str, Any]:
    """同时维护最近轮次和按 MessageID 查询的历史轮次。"""

    return {"last_turn": turn, "turn_records": {turn.message_id: turn}}


@asynccontextmanager
async def lifespan(service: FastAPI):
    """启动 checkpoint、LLM、知识库和联网搜索运行时。"""

    checkpoint_path = Path(
        os.getenv("CHECKPOINT_DB_PATH", "./data/runtime/checkpoints.sqlite3")
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        await saver.setup()
        settings = Settings.from_env()
        client = httpx.AsyncClient(timeout=settings.business_timeout_seconds)
        try:
            knowledge = await asyncio.to_thread(KnowledgeService.load, settings)
            web_search = QwenWebSearch(settings, client)
            company_profile = CompanyProfileTool(
                CompanyProfileService(
                    CompanyProfileConfig(
                        llm_api_key=settings.company_profile_llm_api_key,
                        llm_base_url=settings.company_profile_llm_base_url,
                        llm_model=settings.company_profile_llm_model,
                        llm_timeout_seconds=(
                            settings.company_profile_llm_timeout_seconds
                        ),
                    )
                )
            )
            service.state.graph = build_main_agent_graph(
                saver,
                QwenMainAgent(
                    settings,
                    client,
                    web_search,
                    knowledge,
                    company_profile,
                ),
            )
            service.state.thread_locks = defaultdict(asyncio.Lock)
            service.state.inflight = set()
            service.state.inflight_lock = asyncio.Lock()
            yield
        finally:
            await client.aclose()


app = FastAPI(lifespan=lifespan)


def authorize(authorization: str | None) -> None:
    """校验外部调用方的 Bearer API Key。"""

    if authorization != f"Bearer {os.environ['FOUNDER_SALES_API_KEY']}":
        raise HTTPException(status_code=401, detail="invalid API key")


def _sse(
    completion_id: str,
    content: str = "",
    *,
    reasoning_content: str | None = None,
    finish: bool = False,
) -> str:
    """构造 OpenAI Chat Completions SSE chunk。"""

    if finish:
        delta = {}
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


@app.get("/healthz")
async def healthz():
    """报告 HTTP 服务和 checkpointer 已完成启动。"""

    return {"ok": True, "checkpointer": "ready"}


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    """返回 OpenAI 模型列表，使 OpenWebUI 能发现方正销售助手。"""

    authorize(authorization)
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(
    request: ChatRequest,
    authorization: str | None = Header(default=None),
    user_id: str = Header(alias="X-User-Id"),
    conversation_id: str = Header(alias="X-Conversation-Id"),
    message_id: str = Header(alias="Idempotency-Key"),
):
    """执行带历史和完整 MessageID 幂等的一轮流式对话。"""

    authorize(authorization)
    if (
        request.model != MODEL_ID
        or not request.messages
        or request.messages[-1].role != "user"
        or not request.messages[-1].content.strip()
        or not message_id.strip()
    ):
        raise HTTPException(status_code=422, detail="invalid request")

    thread_id = _thread_id(user_id.strip(), conversation_id.strip())
    message_id = message_id.strip()
    logger.info(
        "%s chat input thread_id=%s message_id=%s body=%s",
        WEB_DEBUG,
        thread_id,
        message_id,
        request.model_dump_json(),
    )
    config = {"configurable": {"thread_id": thread_id}}
    graph = app.state.graph
    inflight_key = (thread_id, message_id)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"

    # 同一进程内的重复在途请求立即拒绝，不等待首个请求完成后再次执行。
    async with app.state.inflight_lock:
        if inflight_key in app.state.inflight:
            raise HTTPException(status_code=409, detail="message is already in progress")
        app.state.inflight.add(inflight_key)

    async def events():
        """串行执行单个会话轮次，并将图事件转换为 SSE 数据流。"""

        success = False
        answer = ""
        lock = app.state.thread_locks[thread_id]
        try:
            async with lock:
                snapshot = await graph.aget_state(config)
                values = snapshot.values or {}
                records = values.get("turn_records", {})
                existing = _turn(records.get(message_id)) if isinstance(records, dict) else None

                # 已完成轮次直接回放；failed 和服务重启后遗留的 in_progress 允许重试。
                if existing is not None and existing.status == "completed":
                    logger.info(
                        "%s chat replay output=%s",
                        WEB_DEBUG,
                        existing.response_content,
                    )
                    if existing.response_content:
                        yield _sse(completion_id, existing.response_content)
                    yield _sse(completion_id, finish=True)
                    yield "data: [DONE]\n\n"
                    success = True
                    return

                yield _sse(
                    completion_id,
                    reasoning_content="正在分析用户需求\n",
                )

                in_progress = TurnRecord(message_id=message_id, status="in_progress")
                await graph.aupdate_state(
                    config,
                    {
                        "messages": [
                            HumanMessage(
                                id=message_id,
                                content=request.messages[-1].content.strip(),
                            )
                        ],
                        **_turn_values(in_progress),
                    },
                )

                async for event in graph.astream({}, config, stream_mode="custom"):
                    if isinstance(event, str):
                        answer += event
                        yield _sse(completion_id, event)
                    elif isinstance(event, dict) and event.get("type") == "status":
                        description = str(event.get("description", "")).strip()
                        if description:
                            yield _sse(
                                completion_id,
                                reasoning_content=description + "\n",
                            )

                terminal = await graph.aget_state(config)
                terminal_records = terminal.values.get("turn_records", {})
                completed = (
                    _turn(terminal_records.get(message_id))
                    if isinstance(terminal_records, dict)
                    else None
                )
                if completed is None or completed.status != "completed":
                    raise RuntimeError("completion checkpoint was not saved")
                logger.info(
                    "%s chat output thread_id=%s message_id=%s body=%s",
                    WEB_DEBUG,
                    thread_id,
                    message_id,
                    answer,
                )
                yield _sse(completion_id, finish=True)
                yield "data: [DONE]\n\n"
                success = True
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            logger.exception(
                "%s chat stream failed thread_id=%s message_id=%s answer_chars=%d",
                WEB_DEBUG,
                thread_id,
                message_id,
                len(answer),
            )
            yield _sse(completion_id, FAILURE_TEXT)
            yield _sse(completion_id, finish=True)
            yield "data: [DONE]\n\n"
        finally:
            if not success:
                failed = TurnRecord(message_id=message_id, status="failed")
                try:
                    await graph.aupdate_state(config, _turn_values(failed))
                except Exception:
                    pass
            async with app.state.inflight_lock:
                app.state.inflight.discard(inflight_key)

    return StreamingResponse(events(), media_type="text/event-stream")
