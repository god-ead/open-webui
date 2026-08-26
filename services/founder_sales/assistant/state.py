"""LangGraph 图共享的会话状态。"""

from __future__ import annotations

from typing import NotRequired

from langgraph.graph import MessagesState
class AssistantState(MessagesState):
    """根图与子图共享的 LangGraph 状态。"""

    title: NotRequired[str]
