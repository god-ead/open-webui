# founder-sales 文件结构

## 目录树

```
founder-sales/
├── app.py                       # 应用入口：组装 LangGraphRuntime 与 OpenAI Bridge
├── langgraph_runtime.py         # LangGraph 会话、checkpoint、幂等和流事件
├── main_agent_graph.py          # LangGraph 单节点图（main_agent → END）
├── requirements.txt             # Python 依赖
├── Dockerfile                   # 镜像构建（Python 3.11 slim）
├── docker-compose.yaml          # 本地开发：单独构建并启动，:8050 对外
├── .gitlab-ci.yml               # CI：构建并推送镜像到 Harbor（main/test 分支规则）
├── .env                         # 本地运行配置（不入库）
├── .env.example                 # 配置样例（与 open-webui 根 .env.example 同步）
│
├── docs/                        # 文档
│   ├── README.md                #   项目说明（架构 / 部署 / 配置 / API 契约）
│   └── FILE_STRUCTURE.md        #   本文件
│
├── assistant/                   # 运行时契约
│   ├── __init__.py              #   对外 re-export：Settings / AssistantState
│   ├── config.py                #   纯环境变量配置（Settings.from_env）
│   ├── state.py                 #   LangGraph 状态：AssistantState（messages + title）
│   ├── qwen_main_agent.py       #   Qwen 主 Agent：工具调用循环 + fallback 静默降级
│   └── qwen_task_model.py       #   Qwen 轻量任务模型：标题等辅助任务
│
├── bridge/                      # 外部前端协议 Adapter
│   ├── __init__.py              #   re-export OpenAI router
│   └── openai_chat.py           #   Chat Completions 请求、身份 Header 与 SSE 转换
│
├── tools/                       # 主 Agent 的 Tool 实现
│   ├── __init__.py              #   re-export 当前启用的三个 Tool
│   ├── company_profile.py       #   企业画像 Tool Adapter
│   ├── knowledge.py             #   销售知识 RAG：FAISS 召回 + SQLite 取 chunk + 可选 reranker
│   ├── qwen_web_search.py       #   联网搜索 Tool：供应商调用、重试与结果整理
│   ├── contact_search.py        #   保留的联系方式搜索源码，当前未注册
│   └── information_organizer.py #   联系方式搜索的信息整理模块
│
├── prompts/
│   └── main_agent.md            # 主 Agent 系统提示词
│
└── .dev/                        # 本地研究/备份区（gitignore，不入库）
    └── backup/
        └── V0.2.0-legacy/       # V0.2.0 移除的 legacy 多节点图归档
```

## 模块职责

### 接入层

| 文件 | 职责 |
|---|---|
| [app.py](../app.py) | 生命周期装配 checkpoint、QwenMainAgent、QwenTaskModel、KnowledgeService 和 Tool，并将 `LangGraphRuntime` 注入 OpenAI Bridge |
| [langgraph_runtime.py](../langgraph_runtime.py) | `stream_turn()` interface；封装 LangGraph 执行、checkpoint、MessageID 幂等和会话锁，产出运行状态及原始图事件 |
| [bridge/openai_chat.py](../bridge/openai_chat.py) | `/healthz`、`/v1/models`、`/v1/chat/completions`；Bearer 与身份 Header 校验；OpenAI messages 到 LangGraph messages、LangGraph 流事件到 OpenAI SSE 的转换 |
| [main_agent_graph.py](../main_agent_graph.py) | 单节点 `main_agent` 图：转发过程事件，仅把最终回答写入 checkpoint |

### assistant/ — 运行时契约

| 文件 | 职责 |
|---|---|
| [config.py](../assistant/config.py) | `Settings` 数据类 + `from_env()`；全部 QWEN_*/RAG_*/鉴权/checkpoint 配置在此定义，模型身份（model_id/display_name）由代码持有 |
| [state.py](../assistant/state.py) | `AssistantState`（Conversation History + Conversation Title） |
| [qwen_main_agent.py](../assistant/qwen_main_agent.py) | `QwenMainAgent`：OpenAI 兼容 Chat Completions 流式调用；校验并执行 Tool；最多一轮 Tool Call 后组织最终回答；主模型失败且未产出增量时切换 fallback 模型 |
| [qwen_task_model.py](../assistant/qwen_task_model.py) | `QwenTaskModel`：独立执行标题等不属于主 Agent 的轻量模型任务 |

### tools/ — Tool 实现

| 文件 | 职责 |
|---|---|
| [company_profile.py](../tools/company_profile.py) | `CompanyProfileTool`：在线程中调用同步企业画像核心，并返回 Markdown 报告 |
| [knowledge.py](../tools/knowledge.py) | `KnowledgeService`：启动期硬加载 embedding 模型与 FAISS 索引（缺任一文件启动失败）；`search()` 按 FAISS 相似度召回，`RERANKER_MODEL_PATH` 非空时用 CrossEncoder 重排，否则按相似度直排取 top_k |
| [qwen_web_search.py](../tools/qwen_web_search.py) | `QwenWebSearch`：调用 `QWEN_SEARCH_MODEL` 联网搜索，规范化候选回答、来源和调用元数据，并对受控错误进行有限重试 |
| [contact_search.py](../tools/contact_search.py) | `ContactSearch`：保留的联系方式搜索实现；当前不导出、不注册给主 Agent |
| [information_organizer.py](../tools/information_organizer.py) | `QwenInformationOrganizer`：联系方式搜索使用的 JSON Schema 信息整理模块 |

### 配置与构建

| 文件 | 职责 |
|---|---|
| [Dockerfile](../Dockerfile) | Python 3.11 slim；复制完整 `services/founder_sales` 和共享 `packages/company_profile` 源码；HEALTHCHECK 探测 `/healthz` |
| [docker-compose.yaml](../docker-compose.yaml) | 本地开发：build 本地镜像 `founder-sales:v0.2`，挂载 `/data/app/founder-sales`，映射 `8050:8050`，强制 `RAG_DEVICE=cpu` |
| [.gitlab-ci.yml](../.gitlab-ci.yml) | `build_founder_sales` job：push 触发（main/test 分支 + 路径过滤）；`main` + 手动版本 → 版本号 + `latest`；`test` → SHA8 + 浮动 `test` tag |
| [.env.example](../.env.example) | 配置样例；与 `program/open-webui/.env.example` 的 Founder Sales 段保持同步 |

## 数据与模型依赖（宿主机 `/data/app/founder-sales`）

```
/data/app/founder-sales/
├── sales_rag.sqlite3        # 知识库 chunk（五本销售书籍）
├── sales_rag.faiss          # FAISS 向量索引（与 embedding 模型维度绑定）
├── model/embedding/         # embedding 模型（必需，启动硬加载）
└── model/rerank/            # rerank 模型（可选，RERANKER_MODEL_PATH 留空则跳过）
```

本地直跑时路径由 `.env` 的 `RAG_*` 指向；容器内指向挂载卷 `/app/data/runtime`。

## 归档区 `.dev/backup/V0.2.0-legacy/`

V0.2.0 移除的 legacy 多节点图完整归档，含：

- `graph.py` — 7 节点状态机图（plan_assistant → plan_sales_tools → search_knowledge → search_web → answer_sales + company_profile + visit_plan）
- `assistant/router.py`、`assistant/state_legacy.py` — 路由节点与 legacy 状态契约（RouteDecision/VisitDecision/VisitSheetContext 等）
- `shared/llm.py` — SalesLLM（kimi 共享客户端，stream + complete_json）
- `company_profile_agent/` — 企业画像子系统全套
- `agents/company_profile/` — 旧版企业画像（含 `tests/agents/company_profile/` 测试）
- `visit_plan/` — 拜访计划与准备表
- `prompts/` — legacy 各节点提示词（router / sales_answer / visit_* 等 11 份）

该目录被 `.gitignore` 排除，不参与镜像构建与 CI。
