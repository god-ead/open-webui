"""LangGraph 图共享的状态与 turn 记录。"""

from __future__ import annotations

from typing import Annotated, Literal, NotRequired

from langgraph.graph import MessagesState
from pydantic import BaseModel


class TurnRecord(BaseModel):
    """单轮对话记录：消息 ID、状态与最终回复内容。"""

    message_id: str
    status: Literal["in_progress", "completed", "failed"]
    answer: str = ""
    response_content: str = ""


def merge_turn_records(
    current: dict[str, TurnRecord] | None,
    updates: dict[str, TurnRecord] | None,
) -> dict[str, TurnRecord]:
    """合并 turn_records 字典，更新覆盖已有键。"""

    return {**(current or {}), **(updates or {})}


class AssistantState(MessagesState):
    """根图与子图共享的 LangGraph 状态。"""
    last_turn: NotRequired[TurnRecord | None]
    turn_records: NotRequired[
        Annotated[dict[str, TurnRecord], merge_turn_records]
    ]
