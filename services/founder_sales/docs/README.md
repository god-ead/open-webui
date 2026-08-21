# founder-sales — 方正销售助手智能体

基于 **FastAPI + LangGraph + Qwen 工具调用**的销售咨询智能体，对外提供 OpenAI 兼容的 `/v1/chat/completions` 接口，可被 Open WebUI 以"外部链接"方式接入。

## 核心架构

单 Agent 工具调用模式（V0.2.0 起，legacy 多节点图已移除）：

```
Open WebUI ── OpenAI 兼容 ──▶ app.py (FastAPI + SSE)
                                  │
                             LangGraph 单节点图 (main_agent_graph.py)
                                  │
                          QwenMainAgent（QWEN_AGENT_MODEL）
                          ├── web_search 工具（联网搜索）
                          │      └─ QwenWebSearch（QWEN_SEARCH_MODEL 生成查询词/整理结果）
                          └── search_sales_knowledge 工具（销售知识 RAG）
                                 └─ KnowledgeService（FAISS 召回 + SQLite 取 chunk，reranker 可选）
```

- **主模型**：`QWEN_AGENT_MODEL` 承担工具调用与最终回答；请求失败（未产出内容时）自动静默切换 `QWEN_FALLBACK_MODEL` 重试，仅记录日志。
- **会话与幂等**：SQLite checkpoint 持久化对话历史；`Idempotency-Key`（MessageID）保证同一消息重放不重复执行。
- **流式输出**：SSE 同时输出正文（`content`）与过程状态（`reasoning_content`，如"正在分析用户需求"）。

## 本地开发

```bash
cd /home/zhongjinyan/project/program/open-webui/backend/open_webui/agents/founder-sales
cp .env.example .env   # 填入 QWEN_API_KEY / FOUNDER_SALES_API_KEY 等
docker compose up -d --build
```

启动后服务监听 `:8050`。在本地 Open WebUI 中手动配置外部链接：

| 项 | 值 |
|---|---|
| API Base URL | `http://localhost:8050/v1` |
| API Key | `.env` 中 `FOUNDER_SALES_API_KEY` 的值 |

依赖数据（RAG 知识库、embedding 模型、checkpoint）由宿主机 `/data/app/founder-sales` 挂载（见 [docker-compose.yaml](../docker-compose.yaml)）。

## 远程部署（test 分支）

镜像由根仓库 CI 构建推送到 Harbor（`$REGISTRY_URL/sales-agents/founder-sales`），远程 compose 以浮动 tag 拉取：

- `test` 分支提交 → 推送 `:test` tag（远程测试环境固定引用）
- `main` 分支手动指定版本 → 推送版本号 + `:latest`

远程测试环境的双容器（open-webui + founder-sales）编排见 `program/open-webui/docker-compose.yaml`，本 agent 容器挂载 `/data/app/founder-sales:/app/data/runtime`。

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
| `QWEN_FALLBACK_MODEL` | `qwen3.5-plus` | 主模型不可用时的静默降级模型（留空则禁用） |
| `QWEN_SEARCH_MODEL` | `qwen3.5-plus` | web_search 工具内部的搜索子模型 |
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
| `POST /v1/chat/completions` | 单轮流式对话，SSE 返回 |

请求头：

- `Authorization: Bearer <FOUNDER_SALES_API_KEY>`（必需）
- `X-User-Id`（必需）
- `X-Conversation-Id`（必需）
- `Idempotency-Key`（必需）：本轮消息 ID；已完成轮次直接回放，`failed`/进程重启遗留的 `in_progress` 可重试

## 测试

```bash
pytest tests/          # 需要依赖环境（langgraph/httpx 等）
```

- `tests/shared/`：RAG 无 reranker 降级、Qwen 主 Agent、联网搜索

## 目录说明

见 [FILE_STRUCTURE.md](FILE_STRUCTURE.md)。

## 备注

- `.dev/backup/V0.2.0-legacy/`：V0.2.0 移除的 legacy 多节点图（graph.py、router、SalesLLM、企业画像、visit_plan、旧版 company_profile 等）归档区，被 `.gitignore` 排除，不入仓库。
- 系统环境缺依赖时（如 `ModuleNotFoundError: langgraph`），部分测试无法收集——为环境问题，非代码问题。
