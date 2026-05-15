# 初始化设备类型参数
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG USE_SLIM=false
ARG USE_PERMISSION_HARDENING=false
# CUDA 11 使用 cu117，CUDA 12 使用 cu121（默认）
ARG USE_CUDA_VER=cu128
# 可使用任意 sentence-transformers 模型
# 重要：如果切换嵌入模型（如 sentence-transformers/all-MiniLM-L6-v2）或切回原模型，将无法继续对 WebUI 中之前加载的文档使用 RAG Chat，必须重新生成嵌入。
ARG USE_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG USE_RERANKING_MODEL=""
ARG USE_AUXILIARY_EMBEDDING_MODEL=TaylorAI/bge-micro-v2

# 设置镜像源加速下载
ARG ALPINE_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/alpine
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
ARG HF_ENDPOINT=https://hf-mirror.com
ARG PYTORCH_CPU_INDEX_URL
ARG PYTORCH_CUDA_INDEX_URL
ARG NLTK_DATA_INDEX_URL


# Tiktoken 编码名称
ARG USE_TIKTOKEN_ENCODING_NAME="cl100k_base"

ARG BUILD_HASH=dev-build
ARG UID=0
ARG GID=0

######## WebUI 前端 ########
FROM --platform=$BUILDPLATFORM docker.fzyun.io/library/node:22-alpine3.20 AS build
ARG BUILD_HASH
ARG ALPINE_MIRROR
ARG NPM_REGISTRY
ARG BUILD_HASH

# 设置 Node.js 选项（用于避免堆内存限制导致 Allocation failed / JavaScript heap out of memory）
ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /app

# 在构建过程中保存 git 修订版本信息
RUN if [ -n "$ALPINE_MIRROR" ]; then \
    sed -i "s|https://dl-cdn.alpinelinux.org/alpine|$ALPINE_MIRROR|g" /etc/apk/repositories; \
    fi && \
    apk add --no-cache git

COPY package.json package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then \
    npm ci --force --registry "$NPM_REGISTRY"; \
    else \
    npm ci --force; \
    fi

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
RUN npm run build

######## WebUI 后端 ########
FROM docker.fzyun.io/library/python:3.11.14-slim-bookworm AS base

# 使用构建参数
ARG USE_CUDA
ARG USE_OLLAMA
ARG USE_CUDA_VER
ARG USE_SLIM
ARG USE_PERMISSION_HARDENING
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_AUXILIARY_EMBEDDING_MODEL
ARG UID
ARG GID

ARG APT_MIRROR
ARG PIP_MIRROR
ARG PYTORCH_CPU_INDEX_URL
ARG PYTORCH_CUDA_INDEX_URL
ARG HF_ENDPOINT
ARG NLTK_DATA_INDEX_URL

# Python 设置
ENV PYTHONUNBUFFERED=1

## Basis ##
ENV ENV=prod \
    PORT=8080 \
    # 将构建参数传递到构建环境中
    USE_OLLAMA_DOCKER=${USE_OLLAMA} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_SLIM_DOCKER=${USE_SLIM} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER} \
    USE_EMBEDDING_MODEL_DOCKER=${USE_EMBEDDING_MODEL} \
    USE_RERANKING_MODEL_DOCKER=${USE_RERANKING_MODEL} \
    USE_AUXILIARY_EMBEDDING_MODEL_DOCKER=${USE_AUXILIARY_EMBEDDING_MODEL}

## 基础 URL 配置 ##
ENV OLLAMA_BASE_URL="/ollama" \
    OPENAI_API_BASE_URL=""

## API 密钥与安全配置 ##
ENV OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false

#### 其他模型 #########################################################
## Whisper TTS 模型设置 ##
ENV WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models"

## RAG 嵌入模型设置 ##
ENV RAG_EMBEDDING_MODEL="$USE_EMBEDDING_MODEL_DOCKER" \
    RAG_RERANKING_MODEL="$USE_RERANKING_MODEL_DOCKER" \
    AUXILIARY_EMBEDDING_MODEL="$USE_AUXILIARY_EMBEDDING_MODEL_DOCKER" \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models"

## Tiktoken 模型设置 ##
ENV TIKTOKEN_ENCODING_NAME="cl100k_base" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken"

## Hugging Face 下载缓存 ##
ENV HF_HOME="/app/backend/data/cache/embedding/models"

## Torch 扩展 ##
# ENV TORCH_EXTENSIONS_DIR="/.cache/torch_extensions"

#### 其他模型 ##########################################################

WORKDIR /app/backend

ENV HOME=/root
# 如果不是 root，则创建用户和用户组
RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
    addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN mkdir -p $HOME/.cache/chroma
RUN echo -n 00000000-0000-0000-0000-000000000000 > $HOME/.cache/chroma/telemetry_user_id

# 确保用户对应用目录和 root 目录有访问权限
RUN chown -R $UID:$GID /app $HOME

# 安装通用系统依赖
RUN if [ -n "$APT_MIRROR" ]; then \
    sed -i "s|http://deb.debian.org/debian-security|$APT_MIRROR-security|g; \
            s|http://security.debian.org/debian-security|$APT_MIRROR-security|g; \
            s|http://deb.debian.org/debian|$APT_MIRROR|g; \
            s|https://deb.debian.org/debian-security|$APT_MIRROR-security|g; \
            s|https://security.debian.org/debian-security|$APT_MIRROR-security|g; \
            s|https://deb.debian.org/debian|$APT_MIRROR|g" \
        /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    git build-essential pandoc gcc netcat-openbsd curl jq \
    libmariadb-dev python3-dev ffmpeg libsm6 libxext6 zstd && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY --chown=$UID:$GID ./backend/requirements.txt ./requirements.txt

RUN set -e; \
    if [ -n "$PIP_MIRROR" ]; then export PIP_INDEX_URL="$PIP_MIRROR" UV_INDEX_URL="$PIP_MIRROR" UV_DEFAULT_INDEX="$PIP_MIRROR"; fi; \
    if [ -n "$HF_ENDPOINT" ]; then export HF_ENDPOINT; fi; \
    if [ -n "$NLTK_DATA_INDEX_URL" ]; then export NLTK_DATA_INDEX_URL; fi; \
    pip3 install --no-cache-dir uv; \
    if [ "$USE_CUDA" = "true" ]; then \
    # 如果启用 CUDA，Whisper 和嵌入模型会在首次使用时下载
    # 修复：固定 torch<=2.9.1，torch 2.10.0 的 aarch64 wheel 会在 ARM 设备（RPi 4 Cortex-A72）上触发 SIGILL，见 #21349
    torch_index_url="${PYTORCH_CUDA_INDEX_URL:-https://download.pytorch.org/whl/$USE_CUDA_DOCKER_VER}"; \
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url "$torch_index_url" --no-cache-dir; \
    uv pip install --system -r requirements.txt --no-cache-dir; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')"; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    python -c "import os, nltk; from nltk.downloader import Downloader; index=os.environ.get('NLTK_DATA_INDEX_URL'); (Downloader(server_index_url=index).download('punkt_tab') if index else nltk.download('punkt_tab'))"; \
    else \
    torch_index_url="${PYTORCH_CPU_INDEX_URL:-https://download.pytorch.org/whl/cpu}"; \
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url "$torch_index_url" --no-cache-dir; \
    uv pip install --system -r requirements.txt --no-cache-dir; \
    if [ "$USE_SLIM" != "true" ]; then \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')"; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    python -c "import os, nltk; from nltk.downloader import Downloader; index=os.environ.get('NLTK_DATA_INDEX_URL'); (Downloader(server_index_url=index).download('punkt_tab') if index else nltk.download('punkt_tab'))"; \
    fi; \
    fi; \
    playwright install chromium; \
    playwright install-deps chromium; \
    mkdir -p /app/backend/preload; \
    if [ -d /app/backend/data/cache ]; then cp -a /app/backend/data/cache /app/backend/preload/cache; fi; \
    mkdir -p /app/backend/data; chown -R $UID:$GID /app/backend/data/ /app/backend/preload/; \
    rm -rf /var/lib/apt/lists/*;

# 如果启用，则安装 Ollama
RUN if [ "$USE_OLLAMA" = "true" ]; then \
    date +%s > /tmp/ollama_build_hash && \
    echo "Cache broken at timestamp: `cat /tmp/ollama_build_hash`" && \
    curl -fsSL https://ollama.com/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# 从构建阶段复制嵌入权重
# RUN mkdir -p /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2
# COPY --from=build /app/onnx /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx

# 复制已构建的前端文件
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json

# 复制后端文件
COPY --chown=$UID:$GID ./backend .

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

# 为 OpenShift（任意 UID）提供最小化、原子化的权限加固：
# - /app 和 /root 归属组 0
# - 目录对组可写，并设置 SGID，使新文件继承 GID 0
RUN if [ "$USE_PERMISSION_HARDENING" = "true" ]; then \
    set -eux; \
    chgrp -R 0 /app /root || true; \
    chmod -R g+rwX /app /root || true; \
    find /app -type d -exec chmod g+s {} + || true; \
    find /root -type d -exec chmod g+s {} + || true; \
    fi

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD [ "bash", "start.sh"]
