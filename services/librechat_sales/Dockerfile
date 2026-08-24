ARG LIBRECHAT_BASE_IMAGE=registry.librechat.ai/danny-avila/librechat:v0.8.7
FROM ${LIBRECHAT_BASE_IMAGE}

# 以 root 写入上游镜像目录；补丁完成后恢复 LibreChat 的非特权运行用户。
USER root

# 仅覆盖 CRM 认证网关自维护文件，保留基础镜像中的其余 LibreChat 代码。
COPY api/server/services/crmAuthCore.js /app/api/server/services/crmAuthCore.js
COPY api/server/services/CrmAuthService.js /app/api/server/services/CrmAuthService.js
COPY api/server/controllers/auth/CrmAuthController.js /app/api/server/controllers/auth/CrmAuthController.js
COPY crm-auth/set-role.js /app/crm-auth/set-role.js
COPY client/crm-entry-redirect.js /app/client/dist/crm-entry-redirect.js
COPY scripts/patch-librechat.js /tmp/patch-librechat.js

# 构建期定点接入 CRM 路由与入口脚本；目标版本不匹配时直接令镜像构建失败。
RUN node /tmp/patch-librechat.js && rm /tmp/patch-librechat.js

USER node
