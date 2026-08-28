"""销售助手的纯环境变量运行时配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    """读取字符串环境变量，未设置时返回默认值。"""

    value = os.getenv(name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，未设置或空串时返回默认值。"""

    value = os.getenv(name)
    return default if value in (None, "") else int(value)


@dataclass(frozen=True)
class Settings:
    """单个 LangGraph worker 的配置。

    对外暴露的模型身份由代码持有，本运行时刻意不读取 OpenWebUI 的模型环境变量。
    """

    app_bind_host: str = "0.0.0.0"
    app_port: int = 8050
    langgraph_api_key: str = ""
    checkpoint_db_path: str = "/app/data/runtime/checkpoints.sqlite3"
    business_timeout_seconds: int = 360

    qwen_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    qwen_api_key: str = ""
    qwen_search_model: str = "qwen3.5-plus"
    qwen_search_retry_count: int = 2
    qwen_agent_model: str = "qwen3.7-plus"
    qwen_visit_model: str = "qwen3.7-plus"
    qwen_fallback_model: str = "qwen3.5-plus"
    qwen_task_model_lite: str = ""
    qwen_timeout_seconds: int = 60

    company_profile_llm_api_key: str = ""
    company_profile_llm_base_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    company_profile_llm_model: str = "qwen3.5-plus"
    company_profile_llm_timeout_seconds: int = 180

    rag_store_path: str = "/app/data/knowledge/sales_rag.sqlite3"
    faiss_index_path: str = "/app/data/knowledge/sales_rag.faiss"
    embedding_model_path: str = "/app/data/model/embedding"
    reranker_model_path: str = ""
    rag_device: str = "cpu"
    rag_candidate_k: int = 50
    rag_top_k: int = 5

    model_id: str = field(init=False, default="founder-sales-assistant")
    display_name: str = field(init=False, default="方正销售助手")

    @classmethod
    def from_env(cls) -> Settings:
        """从文档约定的环境变量构建配置。"""
        settings = cls(
            app_bind_host=_env_str("APP_BIND_HOST", cls.app_bind_host),
            app_port=_env_int("APP_PORT", cls.app_port),
            langgraph_api_key=_env_str(
                "LANGGRAPH_API_KEY", cls.langgraph_api_key
            ),
            checkpoint_db_path=_env_str("CHECKPOINT_DB_PATH", cls.checkpoint_db_path),
            business_timeout_seconds=_env_int(
                "BUSINESS_TIMEOUT_SECONDS", cls.business_timeout_seconds
            ),
            qwen_base_url=_env_str("QWEN_BASE_URL", cls.qwen_base_url),
            qwen_api_key=_env_str("QWEN_API_KEY", cls.qwen_api_key),
            qwen_search_model=_env_str(
                "QWEN_SEARCH_MODEL", cls.qwen_search_model
            ),
            qwen_search_retry_count=_env_int(
                "QWEN_SEARCH_RETRY_COUNT", cls.qwen_search_retry_count
            ),
            qwen_agent_model=_env_str(
                "QWEN_AGENT_MODEL", cls.qwen_agent_model
            ),
            qwen_visit_model=_env_str(
                "QWEN_VISIT_MODEL", cls.qwen_visit_model
            ),
            qwen_fallback_model=_env_str(
                "QWEN_FALLBACK_MODEL", cls.qwen_fallback_model
            ),
            qwen_task_model_lite=_env_str("QWEN_TASK_MODEL_LITE", "").strip(),
            qwen_timeout_seconds=_env_int(
                "QWEN_TIMEOUT_SECONDS", cls.qwen_timeout_seconds
            ),
            company_profile_llm_api_key=_env_str(
                "COMPANY_PROFILE_LLM_API_KEY",
                cls.company_profile_llm_api_key,
            ),
            company_profile_llm_base_url=_env_str(
                "COMPANY_PROFILE_LLM_BASE_URL",
                cls.company_profile_llm_base_url,
            ),
            company_profile_llm_model=_env_str(
                "COMPANY_PROFILE_LLM_MODEL",
                cls.company_profile_llm_model,
            ),
            company_profile_llm_timeout_seconds=_env_int(
                "COMPANY_PROFILE_LLM_TIMEOUT_SECONDS",
                cls.company_profile_llm_timeout_seconds,
            ),
            rag_store_path=_env_str("RAG_STORE_PATH", cls.rag_store_path),
            faiss_index_path=_env_str("FAISS_INDEX_PATH", cls.faiss_index_path),
            embedding_model_path=_env_str(
                "EMBEDDING_MODEL_PATH", cls.embedding_model_path
            ),
            reranker_model_path=_env_str(
                "RERANKER_MODEL_PATH", cls.reranker_model_path
            ),
            rag_device=_env_str("RAG_DEVICE", cls.rag_device),
            rag_candidate_k=_env_int("RAG_CANDIDATE_K", cls.rag_candidate_k),
            rag_top_k=_env_int("RAG_TOP_K", cls.rag_top_k),
        )
        if not settings.qwen_task_model_lite:
            raise ValueError("QWEN_TASK_MODEL_LITE is required")
        return settings
