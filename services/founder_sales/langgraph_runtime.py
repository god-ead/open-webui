"""LangGraph 运行时 — 管理会话、checkpoint、幂等和流事件。"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import BaseMessage

from founder_sales.assistant.state import TurnRecord


logger = logging.getLogger("uvicorn.error")
WEB_DEBUG = "[DEBUG-WEB]"


class TurnInProgressError(Exception):
    """同一会话中的相同消息仍在执行。"""


def _turn(value: Any) -> TurnRecord | None:
    """将 checkpoint 值转换为轮次记录，无法解析时视为不存在。"""

    if value is None:
        return None
    try:
        return value if isinstance(value, TurnRecord) else TurnRecord.model_validate(value)
    except Exception:
        return None


def _turn_values(turn: TurnRecord) -> dict[str, Any]:
    """同时维护最近轮次和按 MessageID 查询的历史轮次。"""

    return {"last_turn": turn, "turn_records": {turn.message_id: turn}}


class LangGraphRuntime:
    """通过流式 interface 封装 LangGraph 执行和轮次状态。"""

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.thread_locks = defaultdict(asyncio.Lock)
        self.inflight: set[tuple[str, str]] = set()
        self.inflight_lock = asyncio.Lock()

    async def stream_turn(
        self,
        thread_id: str,
        message_id: str,
        messages: Sequence[BaseMessage],
    ) -> AsyncIterator[Any]:
        """预占消息并返回 LangGraph 流；重复的在途消息立即拒绝。"""

        inflight_key = (thread_id, message_id)
        async with self.inflight_lock:
            if inflight_key in self.inflight:
                raise TurnInProgressError
            self.inflight.add(inflight_key)
        return self._events(thread_id, message_id, messages, inflight_key)

    async def _events(
        self,
        thread_id: str,
        message_id: str,
        messages: Sequence[BaseMessage],
        inflight_key: tuple[str, str],
    ) -> AsyncIterator[Any]:
        """串行执行轮次，原样产出图事件并维护 checkpoint 终态。"""

        success = False
        answer = ""
        config = {"configurable": {"thread_id": thread_id}}
        lock = self.thread_locks[thread_id]
        try:
            async with lock:
                snapshot = await self.graph.aget_state(config)
                values = snapshot.values or {}
                records = values.get("turn_records", {})
                existing = (
                    _turn(records.get(message_id))
                    if isinstance(records, dict)
                    else None
                )

                # 已完成轮次直接回放；failed 和服务重启后遗留的 in_progress 允许重试。
                if existing is not None and existing.status == "completed":
                    logger.info(
                        "%s chat replay output=%s",
                        WEB_DEBUG,
                        existing.response_content,
                    )
                    if existing.response_content:
                        yield existing.response_content
                    success = True
                    return

                yield {"type": "status", "description": "正在分析用户需求"}

                in_progress = TurnRecord(message_id=message_id, status="in_progress")
                await self.graph.aupdate_state(
                    config,
                    {
                        "messages": list(messages),
                        **_turn_values(in_progress),
                    },
                )

                async for event in self.graph.astream(
                    {}, config, stream_mode="custom"
                ):
                    if isinstance(event, str):
                        answer += event
                    yield event

                terminal = await self.graph.aget_state(config)
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
            raise
        finally:
            if not success:
                failed = TurnRecord(message_id=message_id, status="failed")
                try:
                    await self.graph.aupdate_state(config, _turn_values(failed))
                except Exception:
                    pass
            async with self.inflight_lock:
                self.inflight.discard(inflight_key)


__all__ = ["LangGraphRuntime", "TurnInProgressError"]
