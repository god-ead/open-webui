"""Qwen 主智能体图 — 滚动压缩会话上下文并转发运行过程事件。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from founder_sales.assistant.config import Settings
from founder_sales.assistant.state import AssistantState


_HISTORY_KEEP_MESSAGES = 5


class ConversationHistoryLimitError(RuntimeError):
    """最近保留消息无法压缩到单会话 token 上限。"""


def _messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """将 checkpoint 消息转换为 OpenAI 文本消息。"""

    roles = {"human": "user", "ai": "assistant", "system": "system"}
    result = []
    for message in messages:
        role = roles.get(message.type)
        if role and isinstance(message.content, str):
            result.append({"role": role, "content": message.content})
    return result


def _estimated_text_tokens(text: str) -> int:
    """近似计算中英文 token：非 ASCII 字符按一个、ASCII 字符按四个计。"""

    ascii_chars = sum(character.isascii() for character in text)
    return len(text) - ascii_chars + (ascii_chars + 3) // 4


def _history_tokens(messages: list[BaseMessage], summary: str | None) -> int:
    """估算会话摘要与文本消息的 token 数，不包含固定主提示词和 Tool schema。"""

    total = 0
    if summary:
        summary_message = f"<conversation_summary>{summary}</conversation_summary>"
        total = 4 + _estimated_text_tokens(summary_message)
    for message in messages:
        if isinstance(message.content, str):
            total += 4 + _estimated_text_tokens(message.content)
    return total


def _model_messages(
    messages: list[BaseMessage], summary: str | None
) -> list[BaseMessage]:
    """把独立摘要临时注入模型上下文，不写入 checkpoint 的 messages。"""

    if not summary:
        return messages
    return [
        SystemMessage(
            content=f"<conversation_summary>{summary}</conversation_summary>"
        ),
        *messages,
    ]


async def _prepare_history(
    state: AssistantState,
    task_model: Any,
    settings: Settings,
) -> tuple[list[BaseMessage], str | None]:
    """按消息数或 token 阈值滚动摘要，返回模型上下文与待保存的新摘要。"""

    messages = list(state["messages"])
    summary = state.get("conversation_summary")
    exceeds_limit = (
        len(messages) > settings.history_max_messages
        or _history_tokens(messages, summary) > settings.history_max_tokens
    )
    if not exceeds_limit:
        return _model_messages(messages, summary), None

    older = messages[:-_HISTORY_KEEP_MESSAGES]
    recent = messages[-_HISTORY_KEEP_MESSAGES:]
    if not older:
        raise ConversationHistoryLimitError(
            "conversation history exceeds token limit after trimming"
        )

    new_summary = await task_model.summarize_conversation(
        _messages(older), summary
    )
    if _history_tokens(recent, new_summary) > settings.history_max_tokens:
        raise ConversationHistoryLimitError(
            "conversation history exceeds token limit after summarization"
        )
    return _model_messages(recent, new_summary), new_summary


def build_main_agent_graph(
    checkpointer: Any,
    agent: Any,
    task_model: Any,
    settings: Settings,
):
    """构建带单会话滚动摘要的 Qwen 主智能体图。"""

    async def run_main_agent(state: AssistantState) -> dict[str, Any]:
        """转发主智能体的正文与处理状态，并保存最终回答。"""

        if not state["messages"]:
            return {}
        model_messages, new_summary = await _prepare_history(
            state, task_model, settings
        )
        writer = get_stream_writer()
        answer = ""
        async for event in agent.stream(_messages(model_messages)):
            if isinstance(event, str):
                answer += event
                writer(event)
            elif isinstance(event, dict):
                writer(event)

        if new_summary is None:
            return {"messages": [AIMessage(content=answer)]}
        # 摘要与回答均成功后再压缩完整 checkpoint，失败轮次由运行时回退。
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *state["messages"][-_HISTORY_KEEP_MESSAGES:],
                AIMessage(content=answer),
            ],
            "conversation_summary": new_summary,
        }

    builder = StateGraph(AssistantState)
    builder.add_node("main_agent", run_main_agent)
    builder.add_edge(START, "main_agent")
    builder.add_edge("main_agent", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["ConversationHistoryLimitError", "build_main_agent_graph"]
