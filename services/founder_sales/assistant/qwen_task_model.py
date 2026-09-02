"""Qwen 轻量任务模型 — 生成会话标题与单会话滚动摘要。"""

from __future__ import annotations

from openai import AsyncOpenAI

from founder_sales.assistant.config import Settings


# 标题任务约束：限制输入、输出和等待时间，避免辅助任务占用主对话资源。
_TITLE_USER_MAX_CHARS = 600
_TITLE_ASSISTANT_MAX_CHARS = 400
_TITLE_MAX_TOKENS = 24
_TITLE_TIMEOUT_SECONDS = 10


class QwenTaskModel:
    """执行会话标题与单会话滚动摘要等内部轻量模型任务。"""

    def __init__(self, settings: Settings, client: AsyncOpenAI) -> None:
        self.settings = settings
        self.client = client

    async def generate_title(self, user_text: str, assistant_text: str) -> str:
        """使用内部轻量模型生成一次会话标题。"""

        user_excerpt = " ".join(user_text.split())[:_TITLE_USER_MAX_CHARS]
        assistant_excerpt = " ".join(assistant_text.split())[
            :_TITLE_ASSISTANT_MAX_CHARS
        ]
        dialogue = f"用户：{user_excerpt}"
        if assistant_excerpt:
            dialogue += f"\n助手：{assistant_excerpt}"
        response = await self.client.chat.completions.create(
            model=self.settings.qwen_task_model_lite,
            messages=[{
                "role": "user",
                "content": (
                    "请为以下对话生成一个不超过16个字符的中文标题，只输出标题纯文本。\n"
                    f"{dialogue}"
                ),
            }],
            stream=False,
            extra_body={"enable_thinking": False},
            max_tokens=_TITLE_MAX_TOKENS,
            timeout=_TITLE_TIMEOUT_SECONDS,
        )
        content = response.choices[0].message.content
        return str(content)

    async def summarize_conversation(
        self,
        messages: list[dict[str, str]],
        previous_summary: str | None = None,
    ) -> str:
        """合并已有摘要与较早消息，生成新的单会话内部摘要。"""

        roles = {"user": "用户", "assistant": "助手", "system": "系统"}
        dialogue = "\n".join(
            f"{roles.get(message['role'], message['role'])}：{message['content']}"
            for message in messages
        )
        existing = previous_summary.strip() if previous_summary else "无"
        response = await self.client.chat.completions.create(
            model=self.settings.qwen_task_model_lite,
            messages=[{
                "role": "user",
                "content": (
                    "请把以下单个会话的已有摘要与新增早期对话合并为一份内部摘要。\n"
                    "保留用户目标、已确认事实、约束、偏好、关键结论和未完成事项；"
                    "不要编造信息，不要复述寒暄，只输出摘要正文。\n\n"
                    f"<已有摘要>\n{existing}\n</已有摘要>\n\n"
                    f"<新增早期对话>\n{dialogue}\n</新增早期对话>"
                ),
            }],
            stream=False,
            extra_body={"enable_thinking": False},
            max_tokens=self.settings.history_summary_max_tokens,
            timeout=self.settings.qwen_timeout_seconds,
        )
        content = str(response.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError("conversation summarization returned empty content")
        return content


__all__ = ["QwenTaskModel"]
