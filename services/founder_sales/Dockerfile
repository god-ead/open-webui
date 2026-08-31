FROM docker.fzyun.io/python:3.11-slim-bookworm

ARG MINIO_ENDPOINT_URL="http://172.19.52.215:9001"
ARG MINIO_BUCKET="sales-agent"
ARG MINIO_SALES_RAG="knowledge/sales-rag/v1"
ARG MINIO_EMBED_MODEL="models/embedding/bge-small-zh-v1"

ARG SALES_RAG_PATH="/app/data/agent/knowledge"
ARG EMBED_MODEL_PATH="/app/data/agent/model/embedding"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_PORT=8050 \
    RAG_DEVICE=cpu

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY services/founder_sales/requirements.txt ./
COPY packages/company_profile/requirements.txt ./company-profile-requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        -r requirements.txt \
        -r company-profile-requirements.txt
RUN pip install --no-cache-dir awscli -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY services/founder_sales /app/founder_sales
COPY packages/company_profile /app/company_profile

# ===== 下载 MinIO 数据 =====
RUN --mount=type=secret,id=minio_access_key \
    --mount=type=secret,id=minio_secret_key \
    set -eu; \
    \
    # 临时载入 MinIO 密钥
    MINIO_ACCESS_KEY="$(cat /run/secrets/minio_access_key)"; \
    MINIO_SECRET_KEY="$(cat /run/secrets/minio_secret_key)"; \
    \
    echo "Key starts with: $(echo "$MINIO_ACCESS_KEY" | cut -c1-4)..."; \
    echo "Secret length: $(echo "$MINIO_SECRET_KEY" | wc -c)"; \
    \
    # 下载 minIO 数据
    mkdir -p "${SALES_RAG_PATH}"; \
    mkdir -p "${EMBED_MODEL_PATH}"; \
    export AWS_ACCESS_KEY_ID="${MINIO_ACCESS_KEY}"; \
    export AWS_SECRET_ACCESS_KEY="${MINIO_SECRET_KEY}"; \
    export AWS_DEFAULT_REGION="us-east-1"; \
    export AWS_EC2_METADATA_DISABLED=true; \
    \
    echo "=== 验证 AWS 凭证 ==="; \
    echo "Access Key: $(echo "$AWS_ACCESS_KEY_ID" | cut -c1-4)..."; \
    echo "Secret Key length: $(echo "$AWS_SECRET_ACCESS_KEY" | wc -c)"; \
    aws --version; \
    aws --endpoint-url "${MINIO_ENDPOINT_URL}" \
        s3 cp \
        "s3://${MINIO_BUCKET}/${MINIO_SALES_RAG}" \
        "${SALES_RAG_PATH}" \
        --recursive \
        --no-progress; \
    aws --endpoint-url "${MINIO_ENDPOINT_URL}" \
        s3 cp \
        "s3://${MINIO_BUCKET}/${MINIO_EMBED_MODEL}" \
        "${EMBED_MODEL_PATH}" \
        --recursive \
        --no-progress;

EXPOSE 8050

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=3)"]

CMD ["uvicorn", "founder_sales.app:app", "--host", "0.0.0.0", "--port", "8050"]
