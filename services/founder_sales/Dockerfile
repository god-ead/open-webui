FROM docker.fzyun.io/python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_PORT=8050 \
    CHECKPOINT_DB_PATH=./data/runtime/checkpoints.sqlite3 \
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

COPY services/founder_sales /app/founder_sales
COPY packages/company_profile /app/company_profile

RUN mkdir -p ./data/runtime

EXPOSE 8050

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=3)"]

CMD ["uvicorn", "founder_sales.app:app", "--host", "0.0.0.0", "--port", "8050"]
