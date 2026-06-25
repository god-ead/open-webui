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
│   └── verification/         # 集成验证工具
│
├── scripts/                  # 批量测试与验证脚本
├── scoring_config.yaml       # 评分权重配置文件
├── 评分逻辑.md               # 评分算法说明文档
│
├── docker-compose.yaml       # 主编排文件（基础设施 + 业务服务）
├── docker-compose.infra.yaml # 通用基础服务模板（postgres、redis、nginx 等）
├── docker-compose.dev.yaml   # 开发环境叠加文件（本地构建）
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

通过 API Gateway 提交任务 → Redis 队列 → Worker 消费 → 生成 PDF → 返回下载链接。

```
POST /api/task {"task_type":"profile","input":{"company_name":"..."}}
    ↓ API Gateway（写 DB + 推 Redis 队列）
    ↓ Worker main.py（brpop 消费）
    ↓ CompanyProfileHandler.handle()
    ↓ service.generate() → 分析
    ↓ ProfilePdfExporter.export() → PDF + 下载链接
    ↓ 通知回调 + WebSocket 推送
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
    │  ×3 副本     │ │ Server│ │             │
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
# 编辑 .env，填写 LLM_API_KEY 等

# 2. 生产部署（拉取预构建镜像）
docker compose -f docker-compose.yaml up -d

# 3. 开发部署（本地构建 + 热更新）
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
```

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
    ↓ _file_name() 安全处理（保留中文、过滤路径遍历字符）
文件名: {公司名}_{时间戳}_{8位UUID}.pdf
```

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | LLM API 密钥 | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://dashscope.aliyuncs.com/...` |
| `LLM_MODEL` | LLM 模型 | `qwen3.5-plus` |
| `LLM_TIMEOUT_SECONDS` | LLM 超时（秒） | `180` |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql://...` |
| `HANDLER_MODULES` | Handler 注册列表 | `handlers.company_profile:CompanyProfileHandler` |
| `PROFILE_PDF_DOWNLOAD_BASE_URL` | PDF 下载基础 URL | `http://127.0.0.1:8088/api/download` |
| `PROFILE_PDF_TEMP_TTL_HOURS` | PDF 临时文件保留（小时） | `24` |
| `PROFILE_PDF_BACKUP_TTL_DAYS` | PDF 备份保留（天） | `7` |

完整变量列表见 [.env.example](.env.example)。

## 依赖

- **LLM**: 通义千问（DashScope），或兼容 OpenAI 接口的模型
- **PDF**: WeasyPrint（HTML → PDF 渲染，需系统字体支持）
- **任务队列**: Redis（BRPOP 阻塞消费）
- **数据库**: PostgreSQL（任务状态持久化）
- **部署**: Docker Compose（nginx 反向代理 + 多副本网关 + Worker 集群）
