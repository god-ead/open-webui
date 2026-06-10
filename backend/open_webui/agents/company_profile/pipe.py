"""Company profile trial pipe."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .adapters.openwebui import get_last_user_message
from .entry import run_company_profile_pipe


class Pipe:
    name = "company_profile"

    class Valves(BaseModel):
        llm_api_key: str = Field(default="", description="企业画像 LLM API Key")
        llm_base_url: str = Field(default="", description="企业画像 LLM Base URL")
        llm_model: str = Field(default="", description="企业画像 LLM Model")
        llm_timeout_seconds: int = Field(default=180, description="LLM 请求超时（秒）")

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def pipe(
        self,
        body: dict,
        __user__=None,
        __request__=None,
        __metadata__=None,
        __event_emitter__=None,
        __files__=None,
        __tools__=None,
    ) -> str:
        user_text = get_last_user_message(body.get("messages", [])) or ""
        return await run_company_profile_pipe(
            user_text=user_text,
            user=__user__,
            metadata=__metadata__,
            event_emitter=__event_emitter__,
            valves=self.valves,
        )
