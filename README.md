# 方正营销智能体与企业画像服务

本仓库包含两套独立系统：面向销售人员的对话式营销智能体，以及面向系统集成的企业画像异步任务服务。二者共同使用 `packages/company_profile` 的核心源码，但拥有各自的 Compose、网络、配置、数据和生命周期。

## 系统架构

### 营销智能体

```text
浏览器
  → Nginx（/sales-agent）
  → LibreChat
  → OpenAI Chat Completions Bridge
  → LangGraph Runtime
  → QwenMainAgent
      ├── 联网搜索 Tool
      ├── 销售知识库 Tool
      └── 企业画像 Tool
```

Nginx 通过 `/sales-agent` 暴露 LibreChat 页面、接口和流式连接。LibreChat 通过 OpenAI 兼容接口调用智能体；Bridge 负责鉴权和协议转换；`LangGraphRuntime` 管理 checkpoint、会话锁与消息幂等；主 Agent 负责选择 Tool 和生成回答。

当前启用联网搜索、销售知识库和企业画像三个 Tool。联系方式搜索源码仍保留在 `tools`，但没有导出或注册给主 Agent。

### 独立企业画像服务

```text
调用方
  → Nginx
  → API Gateway × 2
  → Redis 任务队列
  → Company Profile Worker × 2
  → PostgreSQL
  → PDF File Server / Callback
```

该服务接收 `profile` 异步任务，由 Worker 生成画像和 PDF，并通过任务状态、下载地址及加密回调交付。PostgreSQL 保存任务状态，Redis 承担队列、心跳和每日额度控制；默认还包含 Scheduler、Monitor、Backup 和 File Cleanup。

## 目录说明

```text
open-webui/
├── services/
│   ├── founder_sales/             # 营销智能体、LangGraph、Bridge 与 Tool
│   └── company_profile_service/   # 企业画像 Worker、Handler、PDF 与文件服务
├── packages/
│   └── company_profile/           # 两套镜像共享的企业画像核心源码
└── deploy/
    ├── sales_agent/               # LibreChat + Nginx + MongoDB + 智能体
    └── company_profile/           # 企业画像完整分布式部署
```

`services` 保存应用，`packages` 保存共享业务实现，`deploy` 保存部署配置。两个 Dockerfile 都以仓库根目录为上下文，将共享源码直接复制进镜像，不要求发布 Python package。

## 环境要求

- Docker Engine 与 Docker Compose V2；企业画像部署需要 Compose 2.23.1 或更高版本。
- 可访问所配置的镜像仓库和 LLM 服务。
- 营销智能体需准备知识库、FAISS 索引和 embedding 模型。
- 从 `.env.example` 创建 `.env` 并填写密钥；不要提交 `.env`。

## 启动营销智能体

```bash
cd deploy/sales_agent
cp .env.example .env
# 编辑 .env

docker compose --env-file .env config
docker compose --env-file .env up -d --build
docker compose ps
```

至少需要检查以下配置：

- `PUBLIC_ORIGIN` 与 `PUBLIC_BASE_PATH`：浏览器入口及子路径，末尾不要带 `/`。
- `FOUNDER_SALES_API_KEY`：LibreChat 调用智能体的 Bearer Key。
- `QWEN_API_KEY` 与 `COMPANY_PROFILE_LLM_API_KEY`：智能体和画像 Tool 的 LLM 密钥。
- `JWT_SECRET`、`JWT_REFRESH_SECRET`、`CREDS_KEY`、`CREDS_IV`：LibreChat 必需密钥。
- `FOUNDER_SALES_DATA_DIR`：checkpoint、知识库和模型根目录。

默认入口为：

```text
http://服务器地址:3030/sales-agent/
```

常用检查命令：

```bash
docker compose logs -f nginx librechat sales-agent
docker compose exec sales-agent \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8050/healthz').read().decode())"
```

MongoDB 只供 LibreChat 使用，无需发布宿主机端口；Nginx 是唯一浏览器入口。

## 启动独立企业画像服务

```bash
cd deploy/company_profile
cp .env.example .env
# 编辑 .env

docker compose --env-file .env -f docker-compose.yaml config
docker compose --env-file .env -f docker-compose.yaml up -d --build
docker compose --env-file .env -f docker-compose.yaml ps
```

至少设置 `PUBLIC_HOST`、`LLM_API_KEY`，以及 Callback Body、Callback Token 的 AES Key 和 IV。默认 Nginx 端口为 `8088`，提供任务、PDF 下载、WebSocket 和监控路由。生产环境还应确认镜像仓库、`DATA_DIR`、下载地址、回调超时和每日额度。

两个画像入口相互独立：异步服务通过 Redis 队列生成 PDF；智能体 Tool 直接调用核心源码并返回 Markdown，不依赖独立服务、Redis 或 API Gateway。

## 数据与发布

营销智能体通过 `FOUNDER_SALES_DATA_DIR` 持久化 checkpoint、知识库和模型；LibreChat 分别持久化 MongoDB、上传与日志。企业画像服务用 `DATA_DIR` 管理 PDF、备份和日志，并用 Compose volume 保存数据库数据。

修改 `services` 或共享源码后需重建对应镜像。新镜像不会自动替换运行中的容器，部署时应执行 `docker compose up -d --build`。两套部署不要复用项目名或数据目录。

## 验证与排障

修改 Python 代码后可先执行：

```bash
python -m compileall services
python -m compileall packages/company_profile
```

部署时先用 `docker compose config` 检查配置，再查看 `docker compose ps` 和日志。营销智能体重点检查子路径、`/healthz`、RAG 路径和 LLM 权限；画像服务重点检查 Redis、PostgreSQL、Worker、PDF 权限和回调密钥。

## 详细文档

- [营销智能体说明](services/founder_sales/docs/README.md)
- [营销智能体文件结构](services/founder_sales/docs/FILE_STRUCTURE.md)
- [企业画像核心说明](packages/company_profile/README.md)
- [企业画像评分逻辑](packages/company_profile/评分逻辑.md)
- [企业画像任务服务说明](services/company_profile_service/README.md)
- [企业画像接口与交互规范](services/company_profile_service/服务接口与交互规范.md)
