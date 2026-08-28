"""Qwen 轻量任务模型。"""

from __future__ import annotations

from openai import AsyncOpenAI

from founder_sales.assistant.config import Settings


# 标题任务约束：限制输入、输出和等待时间，避免辅助任务占用主对话资源。
_TITLE_USER_MAX_CHARS = 600
_TITLE_ASSISTANT_MAX_CHARS = 400
_TITLE_MAX_TOKENS = 24
_TITLE_TIMEOUT_SECONDS = 10


class QwenTaskModel:
    """执行不属于主 Agent 的轻量模型任务。"""

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


__all__ = ["QwenTaskModel"]
