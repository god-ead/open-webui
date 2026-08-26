# sales-agent-librechat — 销售智能体定制前端与认证网关

基于 LibreChat v0.8.7 基础镜像构建的定制服务，为销售智能体提供浏览器界面、CRM iframe 入口、CRM Launch Token 认证、Agent Account 映射及 LibreChat 原生会话。服务源码位于本目录；Compose、Nginx、运行配置和持久化目录由 `deploy/sales_agent` 管理。

## 职责边界

```text
CRM Widget
  → CRM 专用入口页
  → AES-CBC token 解密与时效校验
  → 单次消费 token
  → 查找或创建 LibreChat Agent Account
  → 签发 LibreChat 原生会话
  → LibreChat 调用 founder_sales
```

本服务负责“镜像中运行什么”，`deploy/sales_agent` 负责“镜像如何运行”：

- 本目录：认证代码、浏览器脚本、构建期补丁、运维脚本和测试。
- `deploy/sales_agent/librechat.yaml`：LibreChat 调用 `founder_sales` 的 endpoint 配置。
- `deploy/sales_agent/docker-compose.yaml`：环境变量、MongoDB、网络、挂载和健康检查。
- `deploy/sales_agent/nginx/`：`/sales-agent` 对外子路径和反向代理。
- `deploy/sales_agent/docs/`：认证网关设计、ADR 与运行手册。

## 镜像定制方式

Dockerfile 不维护 LibreChat 完整源码，而是在固定的 v0.8.7 基础镜像上复制少量自维护文件，并执行 `scripts/patch-librechat.js`：

1. 注册 CRM 入口和 token 兑换路由。
2. 按环境配置关闭原生 Email 登录接口。
3. 在前端入口加载正式环境登录引导。
4. 将用户菜单顶部从虚拟 Email 改为 `name → username`。
5. 同步修改后的 Gzip/Brotli 预压缩资源。

补丁要求目标文本恰好出现一次。LibreChat 升级导致构建产物变化时，镜像构建会失败，必须重新核对上游代码后更新补丁。

## CRM 认证数据

解密后的 CRM Identity 载荷：

```json
{
  "userId": "18e49ec5-1b02-e711-80c2-1866dae852f1",
  "userName": "黄萍萍",
  "agentName": "",
  "area": "",
  "roleId": 2,
  "timestamp": "1787214251"
}
```

MongoDB 使用两个网关专用集合：

- `crm_token_uses`：以密文 SHA-256 摘要作为 `_id` 原子消费 token，TTL 到期后清理。
- `crm_identities`：以唯一 `crmUserId` 将 CRM Identity 一对一映射到 `users._id`。

`userName` 保存到 `crm_identities.userName`，并同步为 LibreChat `users.name`。`roleId` 只记录 CRM 业务角色，不自动授予 LibreChat 平台权限。

## 主要配置

配置由 `deploy/sales_agent/.env` 注入：

| 变量 | 说明 |
|---|---|
| `LIBRECHAT_BASE_IMAGE` | 本地或 CI 构建时使用的固定上游 LibreChat 基础镜像 |
| `CRM_AES_KEY` / `CRM_AES_IV` | CRM AES-CBC token 解密参数 |
| `CRM_TOKEN_MAX_AGE_SECONDS` | token 最大时钟偏差，默认 10 秒 |
| `CRM_ALLOWED_ORIGINS` | 允许嵌入入口页并发送消息的 CRM Origin |
| `CRM_AUTH_DEBUG_ERRORS` | 是否向响应附带内部错误码；正式环境应为 `false` |
| `ALLOW_EMAIL_LOGIN` | 是否启用原生 Email 登录 |
| `ALLOW_REGISTRATION` | 是否启用原生注册 |
| `MONGO_URI` | LibreChat 与认证映射使用的 MongoDB |
| `JWT_SECRET` / `JWT_REFRESH_SECRET` | LibreChat 原生会话密钥 |
| `CREDS_KEY` / `CREDS_IV` | LibreChat 凭据加密配置 |

开发环境可将 Email 登录和注册同时设为 `true`；正式环境必须同时设为 `false`，用户只从 CRM 进入。

## 本地构建与运行

完整环境从部署目录启动：

```bash
cd deploy/sales_agent
cp .env.example .env
# 填写密钥和服务配置
docker compose config
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build
```

只构建本服务镜像：

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.dev.yaml \
  build librechat
```

## 管理员角色

CRM `roleId` 与 LibreChat `ADMIN/USER` 相互独立。运维人员通过精确 CRM userId 设置平台角色：

```bash
docker compose exec librechat \
  node /app/crm-auth/set-role.js <CRM_USER_ID> ADMIN
```

恢复普通用户时将 `ADMIN` 改为 `USER`。

## 文件说明

详见 [docs/FILE_STRUCTURE.md](docs/FILE_STRUCTURE.md)。认证数据链路、错误语义和部署步骤见 [`deploy/sales_agent/docs`](../../deploy/sales_agent/docs)。
