ARG LIBRECHAT_BASE_IMAGE=registry.librechat.ai/danny-avila/librechat:v0.8.7
FROM ${LIBRECHAT_BASE_IMAGE}

# 构建阶段以 root 写入上游目录；运行时由入口脚本降权。
USER root

# 仅覆盖 CRM 认证网关自维护文件，保留基础镜像中的其余 LibreChat 代码。
COPY api/server/services/crmAuthCore.js /app/api/server/services/crmAuthCore.js
COPY api/server/services/CrmAuthService.js /app/api/server/services/CrmAuthService.js
COPY api/server/controllers/auth/CrmAuthController.js /app/api/server/controllers/auth/CrmAuthController.js
COPY crm-auth/set-role.js /app/crm-auth/set-role.js
COPY crm-auth/init-admin.js /app/crm-auth/init-admin.js
COPY crm-auth/init-admin.sh /app/crm-auth/init-admin.sh
COPY client/crm-entry-redirect.js /app/client/dist/crm-entry-redirect.js
COPY scripts/patch-librechat.js /tmp/patch-librechat.js
COPY docker-entrypoint-wrapper.sh /usr/local/bin/founder-sales-librechat-entrypoint.sh

# 构建期定点接入 CRM 路由，并保留 root → node 的最小降权工具。
RUN apk add --no-cache su-exec \
    && node /tmp/patch-librechat.js \
    && chmod 755 /app/crm-auth/init-admin.sh \
        /usr/local/bin/founder-sales-librechat-entrypoint.sh \
    && rm /tmp/patch-librechat.js

# 入口只负责修正 bind mount 权限，业务进程随即降权为 node。
USER root
ENTRYPOINT ["founder-sales-librechat-entrypoint.sh"]
CMD ["npm", "run", "backend"]
