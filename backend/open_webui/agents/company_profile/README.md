# Company Profile（企业画像）

基于 LLM 的企业公开信息分析系统，支持 OpenWebUI Pipe 即时查询和分布式任务队列异步生成两种模式，自动采集、提取、评分企业多维数据，输出结构化 Markdown 报告和 PDF 文件。

## 目录结构

```
company_profile/
├── pipe.py                   # OpenWebUI Pipe 入口
├── entry.py                  # Pipe 请求处理主逻辑（意图识别 → 分析 → 渲染）
├── bridge.py                 # 框架无关的桥接层，供外部调用
├── intent.py                 # LLM 意图识别（分析 / 未知）
├── function_shim.py          # OpenWebUI Function 兼容层
├── requirements.txt          # 业务依赖（weasyprint、beautifulsoup4 等）
│
├── adapters/                 # 平台适配层
│   └── openwebui.py          # OpenWebUI Valves → 内部配置转换
│
├── application/              # 应用层（配置、服务、错误处理）
│   ├── config.py             # CompanyProfileConfig 配置模型
│   ├── service.py            # CompanyProfileService 核心服务
│   ├── result.py             # ProfileApplicationResult 结果模型
│   ├── match_policy.py       # 多匹配结果自动选择策略
│   └── errors.py             # 错误类型定义
│
├── lookalike/                # 分析引擎（核心业务逻辑）
│   ├── engine.py             # 分析管道：采集→提取→评分→策略→报告
│   ├── models.py             # 数据模型（AnalysisResult、ScoreResult 等）
│   ├── report.py             # Markdown 报告渲染 + PDF 生成
│   ├── storage.py            # 文件存储（JSON 持久化）
│   ├── collectors/           # 企业公开信息采集器
│   ├── extractors/           # 结构化信息提取器
│   └── scoring/              # 多维评分引擎
│
├── service/                  # 分布式任务服务
│   ├── Dockerfile            # 服务镜像构建
│   ├── main.py               # Worker 主入口（Redis 消费 + 任务处理）
│   ├── app/
│   │   ├── db.py             # PostgreSQL 任务状态持久化
│   │   └── export_tasks.py   # 任务数据导出脚本
│   ├── handlers/             # 任务 Handler 注册与路由
│   │   ├── registry.py       # HandlerRegistry 注册中心
│   │   ├── company_profile.py # 企业画像生成 Handler
│   │   └── pdf_export.py     # PDF 导出器
│   ├── file_server/          # PDF 文件 HTTP 下载服务
│   │   ├── main.py           # FastAPI 应用（health + download）
│   │   └── downloads.py      # 安全下载（路径防护 + TTL 校验）
│   ├── file_cleanup/         # 过期 PDF 清理服务
│   │   ├── main.py           # 清理入口（定时循环）
│   │   └── cleanup.py        # 清理逻辑（基于文件 mtime + TTL）
│   ├── nginx/
│   │   └── api_upstream.conf # Nginx 反向代理配置
│   ├── verification/         # 集成验证工具
│   └── 服务接口与交互规范.md # 任务提交、状态查询与回调协议
│
├── scripts/                  # 批量测试与验证脚本
├── scoring_config.yaml       # 评分权重配置文件
├── 评分逻辑.md               # 评分算法说明文档
│
├── docker-compose.yaml       # 主编排文件（基础设施 + 业务服务）
├── docker-compose.infra.yaml # 通用基础服务模板（postgres、redis、nginx 等）
└── .env.example              # 环境变量模板
```

## 两种运行模式

### 模式一：OpenWebUI Pipe（同步，即时返回）

作为 OpenWebUI Pipe 插件运行，用户输入企业名称，LLM 实时分析并返回 Markdown 报告。

```
用户输入 "分析一下华为技术有限公司"
    ↓ intent.py (意图识别)
    ↓ CompanyProfileService.analyze_company()
    ↓ lookalike 引擎（采集 → 提取 → 评分 → 报告）
    ↓ 返回 Markdown 报告
```

### 模式二：分布式任务服务（异步，PDF 输出）

通过 API Gateway 提交任务 → Redis 队列 → Worker 消费 → 生成 PDF → 返回下载链接，并在 profile 任务成功后回调外部接口。

```
POST /api/task {"task_type":"profile","callback":"http://...","input":{"company_name":"..."}}
    ↓ API Gateway（写 DB + 推 Redis 队列）
    ↓ Worker main.py（brpop 消费）
    ↓ CompanyProfileHandler.handle()
    ↓ service.generate() → 分析
    ↓ ProfilePdfExporter.export() → PDF + 下载链接
    ↓ 写入任务结果 + result_queue 状态通知
    ↓ Worker 写入明文 callback_payload
    ↓ API Gateway 加密并调用企业画像完成回调接口
```

架构图：

```
                  ┌──────────────┐
                  │   Nginx:8088  │
                  └──────┬───────┘
           ┌─────────────┼─────────────┐
           │             │             │
    ┌──────▼──────┐ ┌───▼───┐ ┌──────▼──────┐
    │ API Gateway │ │ File  │ │   Monitor   │
    │  ×2 副本     │ │ Server│ │             │
    └──────┬──────┘ └───┬───┘ └─────────────┘
           │            │
    ┌──────▼──────┐     │
    │    Redis    │     │
    └──────┬──────┘     │
           │            │
    ┌──────▼──────┐     │
    │  Service-1  ├─────┤  读写 PDF 文件（共享卷）
    │  Service-2  │     │
    └──────┬──────┘     │
           │            │
    ┌──────▼──────┐ ┌───▼──────────┐
    │ PostgreSQL  │ │ File Cleanup │
    └─────────────┘ └──────────────┘
```

### 部署

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，至少设置 PUBLIC_HOST、LLM_API_KEY 和四项 CALLBACK_* AES 密钥/IV

# 2. 启动（按 .env 中的仓库和版本拉取预构建镜像）
docker compose -f docker-compose.yaml up -d

# 3. 查看服务状态
docker compose -f docker-compose.yaml ps
```

默认部署包含 2 个 API Gateway、2 个 Worker，以及单实例的 Nginx、Redis、PostgreSQL、Scheduler、Monitor、Backup、File Server 和 File Cleanup。`DATA_DIR` 统一控制 PDF、备份和日志的宿主机落盘目录；相对路径以 `docker-compose.yaml` 所在目录为基准。

任务提交、状态查询和加密回调的完整报文规范见 [service/服务接口与交互规范.md](service/服务接口与交互规范.md)。

## 核心流程

### 企业画像生成管道

```
公司名称
  → 意图识别（分析 / 未知）
  → 企业信息采集（公开数据源）
  → 结构化提取（基本信息、经营数据、风险信息等）
  → 六维评分（经营、财务、信用、创新、合规、稳定性）
  → 销售策略生成
  → Markdown 报告渲染
  → PDF 导出（可选）
```

### 文件名生成规则

```
report_id: {公司名}_{时间戳}_{8位UUID}
    ↓ _file_name() 安全处理（过滤路径遍历字符，公开 URL 去掉公司名）
文件名: {task_id前16位}_{时间戳}_{8位UUID}.pdf
```

示例：

```text
cff50e2f-12fd-44_20260709_215808_0ab8b83e.pdf
```

### PDF 保存与清理时机

异步 profile 任务生成 PDF 时会同步保存两份同名文件：

1. WeasyPrint 先将 PDF 写入临时目录 `PROFILE_PDF_TEMP_DIR`（默认 `${DATA_DIR}/pdf_temp`），供 File Server 对外下载。
2. 临时文件生成成功后，Worker 立即通过 `copy2` 将其复制到备份目录 `PROFILE_PDF_BACKUP_DIR`（默认 `${DATA_DIR}/pdf_backup`）。
3. 备份成功后，Worker 才返回下载地址、将任务标记为 `completed`，并由 API Gateway 发送成功回调。
4. 如果备份失败，Worker 会删除刚生成的临时文件并使任务进入 `failed`，不会发送成功回调。

临时文件和备份文件相互独立清理：临时文件默认保留 24 小时，备份文件默认保留 7 天。`file-cleanup` 每隔 `PROFILE_PDF_CLEANUP_INTERVAL_SECONDS`（默认 3600 秒）扫描一次，因此实际删除时间可能晚于 TTL，最多接近一个扫描周期。

### 企业画像完成回调

profile 任务成功生成 PDF 后，Worker 将明文回调数据写入结果队列，由 API Gateway 加密并调用任务请求中的 `callback` 地址：

| 内容 | 明文 | 密钥配置 |
|---|---|---|
| body | `{"task_id":"任务ID","pdfurl":"PDF下载地址"}` | `CALLBACK_BODY_AES_KEY` / `CALLBACK_BODY_AES_IV` |
| header `token` | `{"IP":"本服务IP","date":"YYYY-MM-DD HH:mm"}` | `CALLBACK_TOKEN_AES_KEY` / `CALLBACK_TOKEN_AES_IV` |

加密方式为 AES-128-CBC + PKCS7，输出 Base64。`CALLBACK_TIMEOUT=0` 表示不启用 HTTP 客户端超时限制。

对方返回的 `response_body` 使用 BODY KEY/IV 解密。只有 HTTP 状态码为 2xx、响应可解密为合法 JSON 且 `code=0`，才视为回调成功。例如：

```text
response_body=5XN73b6j8DnVfq6dUPNiYHuHyrwAYVXPd+mti8okrJk=
```

解密后：

```json
{"code":0,"msg":"操作成功"}
```

### 每日额度

`DAILY_VISIT_LIMIT` 控制每日最多处理任务数：

- `0` 或空表示不限额
- 正整数表示每日最多处理 N 个任务
- 额度 Redis key 按日期区分：`quota:used:{SERVICE_NAME}:{YYYY-MM-DD}`

额度不足时任务会放回队列，并进入两阶段睡眠：

| 阶段 | 策略 | 日志 |
|---|---|---|
| 一阶段 | `10s * 2^n`，直到达到 `DAILY_VISIT_LIMIT_SLEEP_SECONDS` | 每次打印 |
| 二阶段 | 固定 `DAILY_VISIT_LIMIT_SLEEP_SECONDS`，默认 300s | Redis 抢锁后每小时最多打印一次 |

二阶段日志锁只影响“额度不足导致的 sleep 提示”，其他日志不受影响。

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `SERVICE_NAME` | 容器名前缀、调度器定位标识及额度 key 组成部分 | `company-profile` |
| `COMPOSE_PROJECT_NAME` | Compose 项目隔离名称 | `company-profile` |
| `PUBLIC_HOST` | 对外可达 IP/域名，用于下载地址和回调 token | `127.0.0.1`（部署时须修改） |
| `NGINX_HOST_PORT` | Nginx 对外端口 | `8088` |
| `DATA_DIR` | PDF、数据库备份和日志的统一宿主机根目录 | `./data` |
| `LLM_API_KEY` | LLM API 密钥 | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://dashscope.aliyuncs.com/...` |
| `LLM_MODEL` | LLM 模型 | `qwen3.5-plus` |
| `LLM_TIMEOUT_SECONDS` | LLM 超时（秒） | `180` |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql://...` |
| `HANDLER_MODULES` | Handler 注册列表 | `handlers.company_profile:CompanyProfileHandler` |
| `ALLOWED_TASK_TYPES` | API Gateway 接受的任务类型 | `profile` |
| `CALLBACK_REQUIRED` | 提交任务时是否强制要求 callback | `true` |
| `DAILY_VISIT_LIMIT` | 每日访问次数限额，0/空表示不限 | `200` |
| `DAILY_VISIT_LIMIT_SLEEP_SECONDS` | 额度不足后二阶段检查间隔（秒） | `300` |
| `PROFILE_PDF_DOWNLOAD_BASE_URL` | PDF 下载基础 URL | `http://127.0.0.1:8088/api/download` |
| `PROFILE_PDF_TEMP_TTL_HOURS` | PDF 临时文件保留（小时） | `24` |
| `PROFILE_PDF_BACKUP_TTL_DAYS` | PDF 备份保留（天） | `7` |
| `PROFILE_PDF_CLEANUP_INTERVAL_SECONDS` | PDF 清理扫描间隔（秒） | `3600` |
| `PROFILE_PDF_TEMP_DIR` / `PROFILE_PDF_BACKUP_DIR` | PDF 宿主机目录 | `${DATA_DIR}/pdf_temp` / `${DATA_DIR}/pdf_backup` |
| `BACKUP_DIR` / `LOG_DIR` | 数据库备份与共享日志目录 | `${DATA_DIR}/backups` / `${DATA_DIR}/.log` |
| `CALLBACK_MODE` | API Gateway 回调模式，企业画像使用 `aes_cbc` | `aes_cbc` |
| `CALLBACK_BODY_AES_KEY` / `CALLBACK_BODY_AES_IV` | 回调 body AES key/IV | — |
| `CALLBACK_TOKEN_AES_KEY` / `CALLBACK_TOKEN_AES_IV` | 回调 token AES key/IV | — |
| `CALLBACK_SERVER_IP` | token 明文中的本服务 IP | — |
| `CALLBACK_TIMEOUT` | 回调超时，0 表示不限制 | `30` |
| `CALLBACK_MAX_RETRIES` | 最大重试次数 | `3` |
| `CALLBACK_RETRY_DELAY_SECONDS` | 回调失败后的重试间隔基数（秒） | `5` |

完整变量列表见 [.env.example](.env.example)。

## 依赖

- **LLM**: 通义千问（DashScope），或兼容 OpenAI 接口的模型
- **PDF**: WeasyPrint（HTML → PDF 渲染，需系统字体支持）
- **任务队列**: Redis（BRPOP 阻塞消费）
- **数据库**: PostgreSQL（任务状态持久化）
- **部署**: Docker Compose（nginx 反向代理 + 双副本网关 + Worker 集群）
