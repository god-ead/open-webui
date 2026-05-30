#!/usr/bin/env bash

set -euo pipefail

# 用途说明：
# 1. 按固定时间间隔备份本地 OpenWebUI 数据目录。
# 2. 备份时先在本地打包为带密码的 zip，再通过 SSH 管道直接传到远端。
# 3. 远端文件以时间戳命名为 .zip，先写入 .tmp，成功后再改名。
# 4. 远端最多保留 MAX_REMOTE_BACKUPS 个备份，超出时删除最旧文件。
# 5. 运行日志会同时输出到终端和 LOG_FILE。
#
# 使用前提：
# 1. 远端 SSH、端口、账号、目标目录配置正确。
# 2. 本地 SOURCE_DIR 必须存在。
# 3. 远端 REMOTE_BASE_DIR 必须已存在，脚本不会自动创建该目录。

# 按需修改以下配置。
INTERVAL_HOURS=24                                           # 定时备份
SOURCE_DIR="/data/app/open-webui"                           # 需备份的文件夹
ZIP_PASSWORD="pass-saleschat_bot-word"                      # 压缩包密码设置
REMOTE_HOST="172.19.45.60"
REMOTE_PORT=65534
REMOTE_USER="${USER}"                                       # 远程用户名称
REMOTE_BASE_DIR="/home/data/zhongjy/backup/sales-chatbot"   # 远程备份路径
MAX_REMOTE_BACKUPS=10                                       # 备份数量
LOG_FILE="./backup_data.log"                                # log记录

# SSH 主连接保持时间略长于备份周期，避免下次备份前连接提前失效。
CONTROL_PERSIST_HOURS=$((INTERVAL_HOURS + 2))

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

# 将脚本输出同时写入日志文件。
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

if ! [[ "$INTERVAL_HOURS" =~ ^[1-9][0-9]*$ ]]; then
    log "错误：INTERVAL_HOURS 必须是正整数。"
    exit 1
fi

if ! [[ "$MAX_REMOTE_BACKUPS" =~ ^[1-9][0-9]*$ ]]; then
    log "错误：MAX_REMOTE_BACKUPS 必须是正整数。"
    exit 1
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
    log "错误：源目录不存在：$SOURCE_DIR"
    exit 1
fi

if [[ -z "$ZIP_PASSWORD" ]]; then
    log "错误：ZIP_PASSWORD 不能为空。"
    exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
    log "错误：未找到 ssh 命令。"
    exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
    log "错误：未找到 zip 命令。"
    exit 1
fi

# 远端登录地址，默认沿用当前本机用户。
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
# 主连接控制文件放到当前用户的 .ssh 目录下，避免临时文件散落。
CONTROL_DIR="${HOME}/.ssh/controlmasters"
mkdir -p "$CONTROL_DIR"
chmod 700 "$CONTROL_DIR"
CONTROL_PATH="${CONTROL_DIR}/backup-openwebui-${REMOTE_USER}-${REMOTE_HOST}-${REMOTE_PORT}.sock"
# 用于记录当前脚本运行期间已知的远端备份文件列表。
BACKUP_FILES=()

cleanup() {
    # 脚本退出时主动关闭 SSH 主连接，并删除本地控制 socket。
    ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" -O exit "$REMOTE" >/dev/null 2>&1 || true
    rm -f "$CONTROL_PATH"
}

handle_interrupt() {
    log "收到中断信号，脚本退出。"
    exit 1
}

trap cleanup EXIT
trap handle_interrupt INT TERM

# 启动时先验证 SSH 密码，最多重试 3 次。
CONNECTED=0
for attempt in 1 2 3; do
    log "请输入远端服务器密码（第 ${attempt} 次，最多 3 次）。"
    if ssh -p "$REMOTE_PORT" \
        -o ControlMaster=yes \
        -o ControlPath="$CONTROL_PATH" \
        -o ControlPersist="${CONTROL_PERSIST_HOURS}h" \
        -o ServerAliveInterval=60 \
        -fN "$REMOTE"
    then
        CONNECTED=1
        break
    fi
    log "错误：密码验证失败或 SSH 连接失败。"
done

if [[ "$CONNECTED" -ne 1 ]]; then
    log "错误：连续 3 次验证失败，脚本退出。"
    exit 1
fi

# 远端基础目录必须预先存在，只允许在其中写备份文件。
if ! ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" "[ -d '$REMOTE_BASE_DIR' ]"; then
    log "错误：远端目标路径不存在：$REMOTE_BASE_DIR"
    exit 1
fi

# 查找远端备份目录下的 .zip 文件，并排序
while IFS= read -r remote_file; do
    [[ -n "$remote_file" ]] && BACKUP_FILES+=("$remote_file")
done < <(
    ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" \
        "find '$REMOTE_BASE_DIR' -maxdepth 1 -type f -name '*.zip' | sort"
)

# 如果远端已有备份超过上限，启动时先删除最旧的文件。
while (( ${#BACKUP_FILES[@]} > MAX_REMOTE_BACKUPS )); do
    log "删除最旧备份：${BACKUP_FILES[0]}"
    ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" "rm -f '${BACKUP_FILES[0]}'"
    BACKUP_FILES=("${BACKUP_FILES[@]:1}")
done

log "备份任务已启动，执行间隔：${INTERVAL_HOURS} 小时。"

while true; do
    # 每次备份都生成一个新的时间戳 zip 文件名。
    TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
    REMOTE_FILE="${REMOTE_BASE_DIR}/${TIMESTAMP}.zip"
    REMOTE_TMP_FILE="${REMOTE_FILE}.tmp"

    # 通过已建立的 SSH 复用连接执行后续命令，不再重复输入密码。
    if ! ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" "exit" >/dev/null 2>&1; then
        log "错误：无法连接远端 SSH：${REMOTE_HOST}:${REMOTE_PORT}"
        exit 1
    fi

    # 本地打包为带密码的 zip 后通过管道传输到远端。
    # 远端先写入 .tmp，只有完整接收成功后才改名为正式备份文件。
    log "开始打包并传输：$SOURCE_DIR -> $REMOTE:$REMOTE_FILE"
    (
        cd "$(dirname "$SOURCE_DIR")"
        zip -r -q -P "$ZIP_PASSWORD" - "$(basename "$SOURCE_DIR")"
    ) \
        | dd bs=50M status=progress \
        | ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" \
            "tmp='$REMOTE_TMP_FILE'; final='$REMOTE_FILE'; rm -f \"\$tmp\"; if cat > \"\$tmp\"; then mv \"\$tmp\" \"\$final\"; else rm -f \"\$tmp\"; exit 1; fi"

    # 本次备份成功后更新列表；如果超过上限，继续删除最旧文件。
    BACKUP_FILES+=("$REMOTE_FILE")
    while (( ${#BACKUP_FILES[@]} > MAX_REMOTE_BACKUPS )); do
        log "删除最旧备份：${BACKUP_FILES[0]}"
        ssh -p "$REMOTE_PORT" -S "$CONTROL_PATH" "$REMOTE" "rm -f '${BACKUP_FILES[0]}'"
        BACKUP_FILES=("${BACKUP_FILES[@]:1}")
    done

    log "备份完成：$REMOTE_FILE"
    log "休眠 ${INTERVAL_HOURS} 小时后继续。"
    sleep "${INTERVAL_HOURS}h"
done
