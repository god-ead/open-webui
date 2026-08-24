# sales-agent-librechat 文件结构

## 目录树

```text
librechat_sales/
├── Dockerfile                         # 基于 LibreChat v0.8.7 构建定制镜像
├── README.md                          # 服务职责、配置、构建和运维说明
├── .dockerignore                      # 排除文档、测试和仓库管理文件
├── .gitlab-ci.yml                     # 构建并发布定制 LibreChat 镜像
├── api/server/
│   ├── controllers/auth/
│   │   └── CrmAuthController.js       # iframe 入口、token 兑换和会话签发
│   └── services/
│       ├── crmAuthCore.js             # 配置、AES-CBC 解密和载荷校验
│       └── CrmAuthService.js          # token 消费、身份映射和账户解析
├── client/
│   └── crm-entry-redirect.js          # 正式环境原生登录页入口引导
├── crm-auth/
│   └── set-role.js                    # CRM userId 对应平台角色运维命令
├── scripts/
│   └── patch-librechat.js             # LibreChat v0.8.7 构建产物定点补丁
├── tests/
│   ├── crmAuthCore.test.js            # token 配置、解密、时效和摘要契约
│   ├── crmEntryProtocol.test.js       # Widget 消息与 URL 传输协议契约
│   └── crmEntryRedirect.test.js       # 正式/开发环境登录入口行为
└── docs/
    └── FILE_STRUCTURE.md              # 本文件
```

## Module 职责

### 认证 interface

| 文件 | 职责 |
|---|---|
| `crmAuthCore.js` | 将不可信 token 转换为经过规范化的 CRM Identity；加载并冻结密钥、时效和 Origin 配置 |
| `CrmAuthService.js` | 在 MongoDB 原子消费 token，维护 CRM Identity 与 Agent Account 的一对一映射 |
| `CrmAuthController.js` | 提供 iframe HTML 和 `/api/auth/crm` 兑换 interface，成功后复用 LibreChat `setAuthTokens()` |

### 浏览器 interface

| 文件 | 职责 |
|---|---|
| `CrmAuthController.js` 内联入口脚本 | 接受 URL token 或父窗口 `{source:'fm-widget', type:'token'}` 消息；通知 `ready`/`token-expired` |
| `crm-entry-redirect.js` | 原生登录注册同时关闭时，将 `/login` 引导回 CRM 根入口 |

### 构建与运维

| 文件 | 职责 |
|---|---|
| `Dockerfile` | 把自维护代码覆盖到上游镜像，执行定点补丁后恢复 `node` 用户 |
| `patch-librechat.js` | 精确注册路由、注入入口脚本、修改用户名称展示并同步压缩资源 |
| `set-role.js` | 沿 `crmUserId → libreChatUserId` 映射设置 `ADMIN/USER`，不使用姓名授权 |
| `.gitlab-ci.yml` | 按分支和版本规则构建、标记并推送镜像 |

## 外部依赖

```text
deploy/sales_agent/docker-compose.yaml
  ├── 构建本服务 Dockerfile
  ├── 注入认证和 LibreChat 环境变量
  ├── 挂载 deploy/sales_agent/librechat.yaml
  └── 连接 librechat-mongodb 与 founder_sales
```

本服务不包含 `package.json`：运行依赖由 LibreChat 基础镜像提供，自维护脚本和测试只使用 Node.js 标准库。
