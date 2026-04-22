#!/usr/bin/env bash
set -euo pipefail

# 基础参数：按需只在此处修改。
IMAGE=openwebui-product:0.1.4
NAME=open-web
EX_PORT=3031
DATA_VOLUME=open-webui_open-webui
DATA_DIR=/app/backend/data
ENV_FILE=./.env.runtime

docker info >/dev/null 2>&1 && DOCKER=(docker) || DOCKER=(sudo docker)

# 删除同名旧容器，然后用 compose 构建出的镜像创建测试容器
"${DOCKER[@]}" rm -f "$NAME" 2>/dev/null || true
"${DOCKER[@]}" run -d --name "$NAME" \
  -p 0.0.0.0:${EX_PORT}:8080 \
  --env-file "$ENV_FILE" \
  -v "${DATA_VOLUME}:${DATA_DIR}" \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  "$IMAGE"

# 健康检查：确认 Open WebUI 服务已可访问。
for i in {1..30}; do
  if curl -fsS "http://localhost:${EX_PORT}/health" >/dev/null 2>&1; then
    echo "Open WebUI 已启动: http://localhost:${EX_PORT}"
    exit 0
  fi
  sleep 1
done
echo "容器已创建但健康检查未就绪，查看日志: ${DOCKER[*]} logs -f $NAME"
