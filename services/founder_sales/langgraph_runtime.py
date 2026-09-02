"""LangGraph 运行时 — 管理会话、checkpoint 和流事件。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import BaseMessage

class LangGraphRuntime:
    """通过流式 interface 封装 LangGraph 执行和轮次状态。"""

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.active_runs: dict[str, asyncio.Task[None]] = {}
        self.active_lock = asyncio.Lock()

    async def stream_turn(
        self,
        thread_id: str,
        messages: Sequence[BaseMessage],
    ) -> AsyncIterator[Any]:
        """返回当前会话的新一轮 LangGraph 事件流。"""

        async with self.active_lock:
            previous = self.active_runs.get(thread_id)
            if previous is not None and not previous.done():
                previous.cancel()
                with suppress(asyncio.CancelledError):
                    await previous

            config = await self._last_complete_config(thread_id)
            queue: asyncio.Queue[Any] = asyncio.Queue()
            task = asyncio.create_task(
                self._produce(queue, config, messages),
                name=f"founder-sales:{thread_id}",
            )
            self.active_runs[thread_id] = task
        return self._events(queue, task)

    async def _last_complete_config(self, thread_id: str) -> dict[str, Any]:
        """忽略中断输入 checkpoint，返回最后一个完整轮次。"""

        base = {"configurable": {"thread_id": thread_id}}
        found = False
        async for snapshot in self.graph.aget_state_history(base):
            found = True
            messages = (snapshot.values or {}).get("messages") or []
            if not snapshot.next and (not messages or messages[-1].type == "ai"):
                return snapshot.config
        if found:
            raise RuntimeError("conversation has no complete checkpoint")
        await self.graph.ainvoke({"messages": []}, base)
        return (await self.graph.aget_state(base)).config

    async def _produce(
        self,
        queue: asyncio.Queue[Any],
        config: dict[str, Any],
        messages: Sequence[BaseMessage],
    ) -> None:
        """在可取消任务中执行图，并将事件送往当前 HTTP 响应。"""

        try:
            await queue.put({"type": "status", "description": "正在分析用户需求"})
            async for event in self.graph.astream(
                {"messages": list(messages)}, config, stream_mode="custom"
            ):
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(None)

    async def _events(
        self,
        queue: asyncio.Queue[Any],
        task: asyncio.Task[None],
    ) -> AsyncIterator[Any]:
        """消费后台事件；客户端断开时停止对应 run。"""

        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def title_context(self, thread_id: str) -> tuple[str | None, str, str]:
        """读取已保存标题和首轮完整问答。"""

        config = await self._last_complete_config(thread_id)
        snapshot = await self.graph.aget_state(config)
        values = snapshot.values or {}
        messages = values.get("messages") or []
        user_text = next(
            (str(message.content) for message in messages if message.type == "human"),
            "",
        )
        assistant_text = next(
            (str(message.content) for message in messages if message.type == "ai"),
            "",
        )
        return values.get("title"), user_text, assistant_text

    async def save_title(self, thread_id: str, title: str) -> None:
        """把首次生成的标题写入 Conversation Thread checkpoint。"""

        await self.graph.aupdate_state(
            {"configurable": {"thread_id": thread_id}}, {"title": title}
        )


__all__ = ["LangGraphRuntime"]
