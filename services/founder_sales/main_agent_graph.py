"""Qwen 主智能体图 — 保持现有 checkpoint、幂等和自定义流事件契约。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from founder_sales.assistant.state import AssistantState, TurnRecord


def _messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """将 checkpoint 消息转换为 OpenAI 文本消息。"""

    roles = {"human": "user", "ai": "assistant", "system": "system"}
    result = []
    for message in messages:
        role = roles.get(message.type)
        if role and isinstance(message.content, str):
            result.append({"role": role, "content": message.content})
    return result


def _completed(state: AssistantState, answer: str) -> dict[str, Any]:
    """把主智能体回答与幂等完成状态原子写入 checkpoint。"""

    current = state.get("last_turn")
    turn = current if isinstance(current, TurnRecord) else TurnRecord.model_validate(current)
    completed = turn.model_copy(
        update={"status": "completed", "answer": answer, "response_content": answer}
    )
    return {
        "messages": [AIMessage(content=answer)],
        "last_turn": completed,
        "turn_records": {completed.message_id: completed},
    }


def build_main_agent_graph(checkpointer: Any, agent: Any):
    """构建单节点 Qwen 主智能体图，旧多路由图由调用方继续保留。"""

    async def run_main_agent(state: AssistantState) -> dict[str, Any]:
        """转发主智能体的正文与处理状态，并保存最终回答。"""

        writer = get_stream_writer()
        answer = ""
        async for event in agent.stream(_messages(state["messages"])):
            if isinstance(event, str):
                answer += event
                writer(event)
            elif isinstance(event, dict) and event.get("type") == "status":
                writer(event)
        return _completed(state, answer)

    builder = StateGraph(AssistantState)
    builder.add_node("main_agent", run_main_agent)
    builder.add_edge(START, "main_agent")
    builder.add_edge("main_agent", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_main_agent_graph"]
