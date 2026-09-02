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
from openai import AsyncOpenAI

from founder_sales.assistant.config import Settings
from founder_sales.assistant.qwen_main_agent import (
    QwenMainAgent,
    qwen_openai_base_url,
)
from founder_sales.assistant.qwen_task_model import QwenTaskModel
from founder_sales.bridge import router as openai_router
from founder_sales.langgraph_runtime import LangGraphRuntime
from founder_sales.logging_config import setup_logging
from founder_sales.main_agent_graph import build_main_agent_graph
from founder_sales.tools import (
    CompanyProfileTool,
    KnowledgeService,
    QwenWebSearch,
    VisitPlanTool,
)

setup_logging()


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
        qwen_base_url = qwen_openai_base_url(settings.qwen_base_url)
        http_client = httpx.AsyncClient(
            timeout=settings.business_timeout_seconds
        )
        qwen_client = AsyncOpenAI(
            api_key=settings.qwen_api_key,
            base_url=qwen_base_url,
            timeout=settings.qwen_timeout_seconds,
            max_retries=0,
        )
        try:
            knowledge = await asyncio.to_thread(KnowledgeService.load, settings)
            web_search = QwenWebSearch(settings, http_client)
            visit_plan = VisitPlanTool(settings, qwen_client, knowledge)
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
            agent = QwenMainAgent(
                settings,
                qwen_client,
                web_search,
                knowledge,
                visit_plan,
                company_profile,
            )
            task_model = QwenTaskModel(settings, qwen_client)
            graph = build_main_agent_graph(saver, agent, task_model, settings)
            service.state.langgraph_runtime = LangGraphRuntime(graph)
            service.state.qwen_task_model = task_model
            yield
        finally:
            await qwen_client.close()
            await http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(openai_router)
