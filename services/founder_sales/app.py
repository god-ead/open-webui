"""销售智能体应用入口 — 组装 LangGraph 运行时和 OpenAI Bridge。"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from company_profile.application import (
    CompanyProfileConfig,
    CompanyProfileService,
)
from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from founder_sales.assistant.config import Settings
from founder_sales.bridge import router as openai_router
from founder_sales.langgraph_runtime import LangGraphRuntime
from founder_sales.main_agent_graph import build_main_agent_graph
from founder_sales.assistant.qwen_main_agent import QwenMainAgent
from founder_sales.tools import CompanyProfileTool, KnowledgeService, QwenWebSearch


@asynccontextmanager
async def lifespan(service: FastAPI):
    """启动 checkpoint、LLM、知识库和联网搜索运行时。"""

    checkpoint_path = Path(
        os.getenv("CHECKPOINT_DB_PATH", "./data/runtime/checkpoints.sqlite3")
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        await saver.setup()
        settings = Settings.from_env()
        client = httpx.AsyncClient(timeout=settings.business_timeout_seconds)
        try:
            knowledge = await asyncio.to_thread(KnowledgeService.load, settings)
            web_search = QwenWebSearch(settings, client)
            company_profile = CompanyProfileTool(
                CompanyProfileService(
                    CompanyProfileConfig(
                        llm_api_key=settings.company_profile_llm_api_key,
                        llm_base_url=settings.company_profile_llm_base_url,
                        llm_model=settings.company_profile_llm_model,
                        llm_timeout_seconds=(
                            settings.company_profile_llm_timeout_seconds
                        ),
                    )
                )
            )
            graph = build_main_agent_graph(
                saver,
                QwenMainAgent(
                    settings,
                    client,
                    web_search,
                    knowledge,
                    company_profile,
                ),
            )
            service.state.langgraph_runtime = LangGraphRuntime(graph)
            yield
        finally:
            await client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(openai_router)
