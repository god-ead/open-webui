# founder-sales — 方正销售助手智能体

基于 **FastAPI + LangGraph + Qwen 工具调用**的销售咨询智能体，对外提供 OpenAI 兼容的 `/v1/chat/completions` 接口，可被 Open WebUI 以"外部链接"方式接入。

## 核心架构

单 Agent 工具调用模式（V0.2.0 起，legacy 多节点图已移除）：

```
Open WebUI ── OpenAI 兼容 ──▶ bridge/openai_chat.py
                                  │ LangGraph messages / events
                             LangGraphRuntime（checkpoint + 幂等）
                                  │
                             LangGraph 单节点图 (main_agent_graph.py)
                                  │
                          QwenMainAgent（QWEN_AGENT_MODEL）
                          ├── web_search 工具（联网搜索）
                          │      └─ QwenWebSearch（QWEN_SEARCH_MODEL 重试后依次降级）
                          ├── search_sales_knowledge 工具（销售知识 RAG）
                          │      └─ KnowledgeService（FAISS 召回 + SQLite 取 chunk，reranker 可选）
                          └── generate_visit_plan 工具（标准拜访计划）
                                 ├─ KnowledgeService（复用销售知识检索）
                                 └─ QWEN_VISIT_MODEL（失败时依次使用 fallback 模型）
```

- **主模型**：`QWEN_AGENT_MODEL` 承担工具调用与最终回答；请求失败（未产出内容时）按 `QWEN_FALLBACK_MODEL` 的配置顺序静默降级，仅记录日志。
- **会话**：SQLite checkpoint 独立持久化完整对话历史；每次请求都在当前 Conversation Thread 上启动新 run。
- **流式输出**：SSE 同时输出正文（`content`）与过程状态（`reasoning_content`，如"正在分析用户需求"）。

## 本地开发

```bash
cd /home/zhongjinyan/project/program/open-webui/deploy/sales_agent
cp .env.example .env   # 填入 QWEN_API_KEY / FOUNDER_SALES_API_KEY 等
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build
```

启动后服务监听 `:8050`。在本地 Open WebUI 中手动配置外部链接：

| 项 | 值 |
|---|---|
| API Base URL | `http://localhost:8050/v1` |
| API Key | `.env` 中 `FOUNDER_SALES_API_KEY` 的值 |

依赖数据（RAG 知识库、embedding 模型、checkpoint）由 `FOUNDER_SALES_DATA_DIR` 指定的宿主机目录挂载（见 [`deploy/sales_agent/docker-compose.yaml`](../../../deploy/sales_agent/docker-compose.yaml)）。

## 远程部署（main/test 分支）

镜像由根仓库 CI 构建推送到 Harbor（`$REGISTRY_URL/sales-agents/founder-sales`），远程 Compose 从 Harbor 拉取运维在 `.env` 中选定的 tag：

- `test` 分支提交 → 推送 `:test` tag（远程测试环境固定引用）
- `main` 分支手动指定版本 → 推送版本号 + `:latest`

远程环境编排见 `deploy/sales_agent/docker-compose.yaml`；基础 Compose 不包含 `build`，部署时只拉取镜像。

## 配置项

`QWEN_*` / `RAG_*` / 鉴权等配置在 **agent `.env` 与 open-webui 根 `.env` 各存一份**，两份需保持同步（部署与本地开发各用一份）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `FOUNDER_SALES_API_KEY` | — | 外部调用鉴权 Bearer Key（缺失时启动失败） |
| `APP_PORT` | 8050 | 服务端口 |
| `CHECKPOINT_DB_PATH` | `./data/runtime/checkpoints.sqlite3` | 会话 checkpoint 路径 |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/api/v1` | DashScope API 地址 |
| `QWEN_API_KEY` | — | DashScope API Key |
| `QWEN_AGENT_MODEL` | `qwen3.7-plus` | 主 Agent 工具调用模型 |
| `QWEN_VISIT_MODEL` | `qwen3.7-plus` | 拜访计划 Tool 使用的专用 Qwen 模型 |
| `QWEN_FALLBACK_MODEL` | `qwen3.5-plus` | 主 Agent、拜访计划和联网搜索共用的有序 fallback 模型；多个模型用英文逗号分隔，留空则禁用 |
| `QWEN_TASK_MODEL_LITE` | 必填 | 标题等轻量任务使用的 Qwen 模型 |
| `QWEN_SEARCH_MODEL` | `qwen3.5-plus` | web_search 工具内部的搜索子模型 |
| `QWEN_SEARCH_RETRY_COUNT` | 2 | 联网搜索主模型返回受控错误时的额外重试次数；耗尽后依次尝试 fallback 模型 |
| `QWEN_TIMEOUT_SECONDS` | 60 | 模型请求超时 |
| `BUSINESS_TIMEOUT_SECONDS` | 360 | httpx 客户端总超时 |
| `RAG_STORE_PATH` | `data/sales_rag.sqlite3` | 知识库 chunk 存储 |
| `FAISS_INDEX_PATH` | `data/sales_rag.faiss` | FAISS 向量索引 |
| `EMBEDDING_MODEL_PATH` | `data/model/embedding` | embedding 模型目录（启动硬加载，缺失即启动失败） |
| `RERANKER_MODEL_PATH` | 空 | rerank 模型目录（留空跳过重排，按 FAISS 相似度直排） |
| `RAG_DEVICE` | cpu | 推理设备 |
| `RAG_CANDIDATE_K` / `RAG_TOP_K` | 50 / 5 | 召回候选数 / 最终返回数 |

## API 契约

| 端点 | 说明 |
|---|---|
| `GET /healthz` | 健康检查（Docker HEALTHCHECK 使用） |
| `GET /v1/models` | 返回模型列表，模型 ID 为 `founder-sales-assistant` |
| `POST /v1/chat/completions` | 主模型支持流式/非流式回答；内部 `task-model-lite` 生成并保存标题 |

请求头：

- `Authorization: Bearer <FOUNDER_SALES_API_KEY>`（必需）
- `X-User-Id`（必需）
- `X-Conversation-Id`（必需）

## 目录说明

见 [FILE_STRUCTURE.md](FILE_STRUCTURE.md)。

## 备注

- `.dev/backup/V0.2.0-legacy/`：V0.2.0 移除的 legacy 多节点图（graph.py、router、SalesLLM、企业画像、visit_plan、旧版 company_profile 等）归档区，被 `.gitignore` 排除，不入仓库。
- 系统环境缺依赖时（如 `ModuleNotFoundError: langgraph`），部分测试无法收集——为环境问题，非代码问题。
