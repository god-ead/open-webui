"""Qwen 主智能体图 — 保存对话正文并转发运行过程事件。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from founder_sales.assistant.state import AssistantState


def _messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """将 checkpoint 消息转换为 OpenAI 文本消息。"""

    roles = {"human": "user", "ai": "assistant", "system": "system"}
    result = []
    for message in messages:
        role = roles.get(message.type)
        if role and isinstance(message.content, str):
            result.append({"role": role, "content": message.content})
    return result


def build_main_agent_graph(checkpointer: Any, agent: Any):
    """构建单节点 Qwen 主智能体图，旧多路由图由调用方继续保留。"""

    async def run_main_agent(state: AssistantState) -> dict[str, Any]:
        """转发主智能体的正文与处理状态，并保存最终回答。"""

        if not state["messages"]:
            return {}
        writer = get_stream_writer()
        answer = ""
        async for event in agent.stream(_messages(state["messages"])):
            if isinstance(event, str):
                answer += event
                writer(event)
            elif isinstance(event, dict):
                writer(event)
        return {"messages": [AIMessage(content=answer)]}

    builder = StateGraph(AssistantState)
    builder.add_node("main_agent", run_main_agent)
    builder.add_edge(START, "main_agent")
    builder.add_edge("main_agent", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_main_agent_graph"]
