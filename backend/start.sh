#!/usr/bin/env bash

# 获取当前脚本所在目录。
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
# 切换到后端目录，确保后续访问 .webui_secret_key、模块导入和相对文件路径时行为一致。
cd "$SCRIPT_DIR" || exit

# 按需安装 Playwright 浏览器依赖。
# 只有当 WEB_LOADER_ENGINE=playwright 且没有提供远程 PLAYWRIGHT_WS_URL 时，
# 才在当前环境中安装 Chromium 及其系统依赖。
if [[ "${WEB_LOADER_ENGINE,,}" == "playwright" ]]; then
    if [[ -z "${PLAYWRIGHT_WS_URL}" ]]; then
        echo "Installing Playwright browsers..."
        playwright install chromium
        playwright install-deps chromium
    fi

    # Playwright 抓取链路会依赖 NLTK 的 punkt_tab 数据，这里一并补齐。
    python -c "import nltk; nltk.download('punkt_tab')"
fi

# 优先使用外部传入的密钥文件路径；否则使用当前目录下默认的 .webui_secret_key。
if [ -n "${WEBUI_SECRET_KEY_FILE}" ]; then
    KEY_FILE="${WEBUI_SECRET_KEY_FILE}"
else
    KEY_FILE=".webui_secret_key"
fi

# 设置 Web 服务监听端口和监听地址。
# 未显式传入时，默认监听 8080 端口和 0.0.0.0。
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

# 如果既没有传入 WEBUI_SECRET_KEY，也没有传入 WEBUI_JWT_SECRET_KEY，
# 则尝试从本地密钥文件中读取；若文件不存在则自动生成一个。
# 这样可以保证服务重启后仍可复用同一个密钥，避免会话/签名不一致。
if test "$WEBUI_SECRET_KEY $WEBUI_JWT_SECRET_KEY" = " "; then
  echo "Loading WEBUI_SECRET_KEY from file, not provided as an environment variable."

  if ! [ -e "$KEY_FILE" ]; then
    echo "Generating WEBUI_SECRET_KEY"
    # 当用户没有主动提供密钥时，生成一个随机值并保存到文件中，供后续启动复用。
    echo $(head -c 12 /dev/random | base64) > "$KEY_FILE"
  fi

  echo "Loading WEBUI_SECRET_KEY from $KEY_FILE"
  WEBUI_SECRET_KEY=$(cat "$KEY_FILE")
fi

# 如果当前镜像启用了内置 Ollama，则在后台启动 ollama serve。
# 这样 Open WebUI 容器内就同时具备 WebUI 服务和 Ollama 推理服务能力。
if [[ "${USE_OLLAMA_DOCKER,,}" == "true" ]]; then
    echo "USE_OLLAMA is set to true, starting ollama serve."
    ollama serve &
fi

# 如果启用了 CUDA，则把 torch/cudnn 相关动态库目录追加到 LD_LIBRARY_PATH。
# 这样 Python 在运行推理相关组件时能找到所需的 GPU 动态链接库。
if [[ "${USE_CUDA_DOCKER,,}" == "true" ]]; then
  echo "CUDA is enabled, appending LD_LIBRARY_PATH to include torch/cudnn & cublas libraries."
  export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/usr/local/lib/python3.11/site-packages/torch/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib"
fi

# 如果检测到运行在 Hugging Face Space 环境中，则执行对应的初始化逻辑。
# 这里主要做两件事：
# 1. 如配置了管理员账号，则先临时启动服务并自动创建管理员用户；
# 2. 设置 WEBUI_URL，适配 Space 的访问域名。
if [ -n "$SPACE_ID" ]; then
  echo "Configuring for HuggingFace Space deployment"
  if [ -n "$ADMIN_USER_EMAIL" ] && [ -n "$ADMIN_USER_PASSWORD" ]; then
    echo "Admin user configured, creating"

    # 先临时启动 WebUI，等待健康检查通过后，再调用注册接口创建管理员账户。
    WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY" uvicorn open_webui.main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" &
    webui_pid=$!
    echo "Waiting for webui to start..."
    while ! curl -s "http://localhost:${PORT}/health" > /dev/null; do
      sleep 1
    done

    # 通过注册接口创建管理员用户。
    echo "Creating admin user..."
    curl \
      -X POST "http://localhost:${PORT}/api/v1/auths/signup" \
      -H "accept: application/json" \
      -H "Content-Type: application/json" \
      -d "{ \"email\": \"${ADMIN_USER_EMAIL}\", \"password\": \"${ADMIN_USER_PASSWORD}\", \"name\": \"Admin\" }"

    # 管理员创建完成后，关闭临时启动的 uvicorn，后续再以正式方式启动。
    echo "Shutting down webui..."
    kill $webui_pid
  fi

  export WEBUI_URL=${SPACE_HOST}
fi

# 优先寻找 python3，找不到时退回到 python。
PYTHON_CMD=$(command -v python3 || command -v python)

# uvicorn worker 数量默认是 1，可通过环境变量覆盖。
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

# 如果脚本启动时附带了额外参数，则直接把这些参数传给 uvicorn；
# 否则默认仅传入 --workers "$UVICORN_WORKERS"。
# 这样既支持默认启动，也支持外部通过参数灵活覆盖 uvicorn 行为。
if [ "$#" -gt 0 ]; then
    ARGS=("$@")
else
    ARGS=(--workers "$UVICORN_WORKERS")
fi

# 最终以前台方式启动 uvicorn。
# 这里使用 exec 替换当前 shell 进程，使容器主进程就是 uvicorn，
# 便于 Docker/Kubernetes 正确接管信号、日志与退出状态。
# 同时把 WEBUI_SECRET_KEY 注入到当前启动命令的环境中。
WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY" exec "$PYTHON_CMD" -m uvicorn open_webui.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
    "${ARGS[@]}"
