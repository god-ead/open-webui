# Service Worker（分布式任务服务）

基于 Redis 队列 + PostgreSQL 的异步任务处理服务，将企业画像生成能力封装为可水平扩展的独立 Worker，支持任务提交 → 排队 → 处理 → 回调通知的完整生命周期管理。

## 架构概览

```
                    Nginx:8088
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    ▼                    ▼                    ▼
api-gateway-1       api-gateway-2       api-gateway-3
(8001)              (8002)              (8003)
    │                    │                    │
    └────────────────────┼────────────────────┘
                         │ 推入任务 / 查询状态
                         ▼
    ┌─────────────────────────────────────────┐
    │              Redis                      │
    │  task_queue ←── API Gateway 推入任务     │
    │  result_queue → API Gateway 消费回调     │
    └──────────────┬──────────────────────────┘
                   │ brpop 阻塞消费
    ┌──────────────┼──────────────────┐
    │              ▼                  │
    ▼                              ▼
service-1                     service-2
    │                              │
    └──────────┬───────────────────┘
               │ 读写状态 / 写结果
               ▼
    ┌──────────────────┐
    │   PostgreSQL      │
    │   tasks 表        │
    └──────────────────┘

    ┌──────────────────┐     ┌──────────────────┐
    │   file-server    │     │  file-cleanup    │
    │   :8010          │     │  (定时清理过期    │
    │   PDF 下载服务    │     │   PDF 文件)      │
    └──────────────────┘     └──────────────────┘
```

## 目录结构

```
service/
├── Dockerfile              # 服务镜像（python:3.11-slim + 中文字体 + 依赖）
├── main.py                 # Worker 主入口：心跳 + 消费循环 + 任务处理
├── app/
│   ├── db.py               # Database：PostgreSQL 任务状态管理
│   └── export_tasks.py     # 任务数据导出脚本（JSON）
├── handlers/
│   ├── __init__.py          # Handler 包入口
│   ├── registry.py          # HandlerRegistry：从环境变量动态加载 Handler
│   ├── company_profile.py   # CompanyProfileHandler：企业画像业务逻辑
│   └── pdf_export.py        # ProfilePdfExporter：PDF 生成 + 备份 + 下载链接
├── file_server/
│   ├── main.py              # FastAPI 应用：/health + /api/download/{file_name}
│   └── downloads.py         # 安全下载：路径遍历防护 + TTL 校验
├── file_cleanup/
│   ├── main.py              # 清理入口：定时循环执行
│   └── cleanup.py           # 清理逻辑：基于文件 mtime + TTL 删除过期文件
├── nginx/
│   └── api_upstream.conf    # Nginx 反向代理 + 负载均衡配置
└── verification/            # 集成验证工具
    ├── lib/                 # 测试客户端、报告对比
    ├── scenarios/           # 测试场景
    └── reports/             # 验证报告输出
```

## 核心模块

### 1. main.py — Worker 主循环

```
启动 → 注册服务 → 连接 DB → 启动心跳线程
  ↓
while running:
  brpop task_queue (阻塞 1s)
  ↓ 收到任务
  process_job():
    1. 检查任务状态（跳过已完成/失败的）
    2. mark_processing → dispatch Handler → mark_completed/failed
    3. rpush result_queue（触发回调 + WebSocket 通知）
```

**关键设计：**
- 心跳线程独立运行（每 5s，不受任务阻塞），向 Redis 上报存活
- 20 分钟超时保护（SIGALRM），超时自动标记失败
- 消费循环与心跳解耦，Worker 正在处理长任务时心跳不中断

### 2. HandlerRegistry — 插件式 Handler 路由

通过环境变量 `HANDLER_MODULES` 动态加载业务 Handler：

```bash
HANDLER_MODULES=handlers.company_profile:CompanyProfileHandler
```

- 格式：`module.path:ClassName`
- 每个 Handler 必须实现 `TaskHandler` 协议（`task_type` + `handle()`）
- 支持逗号分隔注册多个 Handler
- Handler 通过 `from_env()` 工厂方法从环境变量构建

### 3. CompanyProfileHandler — 企业画像任务处理

```
payload {"company_name": "..."}
  → 校验 company_name 非空
  → CompanyProfileService.generate() (调用 LLM 分析)
  → ProfilePdfExporter.export() (生成 PDF + 备份)
  → 返回 {"code":0, "data":{"profile":"下载链接", "version":"4.0"}}
```

### 4. ProfilePdfExporter — PDF 导出器

```
AnalysisResult
  → ReportGenerator.generate_pdf() (WeasyPrint 渲染)
  → 写入 temp_dir (临时目录)
  → copy2 到 backup_dir (备份)
  → 返回 download_base_url/{file_name}
```

文件名 `_file_name()` 保留中英文字符，过滤路径遍历字符，最长 120 字符。

备份失败时自动回滚（删除临时文件）再抛出异常。

### 5. file_server — PDF HTTP 下载服务

基于 FastAPI 的独立服务，挂载 `profile_pdf_temp` 卷（只读），提供安全的 PDF 下载。

**三层安全校验：**
1. 仅允许 `.pdf` 后缀的纯文件名（防目录遍历）
2. 绝对路径必须在 `root_dir` 目录树内
3. 超 TTL 返回 `410 Gone`

**路由：**
| 路径 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /api/download/{file_name}` | PDF 下载 |

### 6. file_cleanup — 过期文件清理

定时循环执行，基于文件 `st_mtime` 判断过期：

| 目录 | TTL 默认值 | 环境变量 |
|---|---|---|
| 临时目录 | 24 小时 | `PROFILE_PDF_TEMP_TTL_HOURS` |
| 备份目录 | 7 天 | `PROFILE_PDF_BACKUP_TTL_DAYS` |
| 清理间隔 | 3600 秒 | `PROFILE_PDF_CLEANUP_INTERVAL_SECONDS` |

### 7. nginx — 反向代理

[api_upstream.conf](nginx/api_upstream.conf) 配置：
- `/api/*` → `api_backend`（3 个 API Gateway，least_conn 负载均衡）
- `/api/download/*` → `file-server:8010`（PDF 下载直连）
- `/ws/*` → `api_backend`（WebSocket 长连接，3600s 超时）
- `/monitor/*` → `monitor:8089`（监控面板）
- `502/503/504` → 统一返回 JSON 错误 + `Retry-After`

## 部署

### 镜像构建

```bash
docker build -f service/Dockerfile -t company-profile-service:v0.1.0 .
```

镜像包含：Python 3.11 + 中文字体（Noto CJK）+ 业务依赖 + 核心代码。

### 容器启动

同一个镜像通过不同 `command` 启动不同角色：

| 容器 | command | 说明 |
|---|---|---|
| service-1/2 | `python main.py` | Worker 消费任务 |
| file-server | `uvicorn file_server.main:app --host 0.0.0.0 --port 8010` | PDF 下载服务 |
| file-cleanup | `python -m file_cleanup.main` | 文件清理服务 |

## 任务生命周期

```
1. POST /api/task {"task_type":"profile","input":{"company_name":"..."}}
   ├── API Gateway 生成 UUID → INSERT tasks (status=queued)
   └── RPUSH task_queue

2. Worker brpop 获取任务
   ├── UPDATE tasks (status=processing)
   ├── CompanyProfileHandler.handle()
   └── UPDATE tasks (status=completed, output=...)

3. RPUSH result_queue
   ├── API Gateway 消费回调
   ├── WebSocket 推送状态变更
   └── 超时/失败 → SCHEDULER 检测心跳 → 重新分配
```

## 扩容

Worker 是**无状态**的（配置来自环境变量，任务来自 Redis），需要扩容时直接增加 `docker-compose.yaml` 中 `service-X` 副本数（当前 2 个），修改容器名和 `SERVICE_ID` 即可。
