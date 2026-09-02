#!/bin/sh
set -eu

ensure_node_writable() {
    path="$1"
    mkdir -p "$path"

    if ! su-exec node:node test -w "$path"; then
        chown -R node:node "$path"
    fi
}

# bind mount 首次由 Docker 创建时归 root 所有，启动前仅修正不可写目录。
ensure_node_writable /app/uploads
ensure_node_writable /data/app/logs

exec su-exec node:node /usr/local/bin/docker-entrypoint.sh "$@"
