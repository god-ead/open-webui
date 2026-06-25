ARG BASE_IMAGE_REGISTRY
FROM ${BASE_IMAGE_REGISTRY}python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        libharfbuzz-subset0 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

# ── 依赖安装 ──
# service 核心依赖（Redis + PostgreSQL）及业务依赖
COPY service/requirements.txt service-requirements.txt
RUN python -m pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://pypi.mirrors.ustc.edu.cn/simple/ \
    -r service-requirements.txt

# 业务依赖
COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://pypi.mirrors.ustc.edu.cn/simple/ \
    -r requirements.txt

# ── 基础代码 ──
COPY service/main.py .
COPY service/app/ app/
COPY service/handlers/ handlers/
COPY service/file_server/ file_server/
COPY service/file_cleanup/ file_cleanup/

# ── 业务代码 ──
COPY __init__.py company_profile/
COPY application/ company_profile/application/
COPY lookalike/ company_profile/lookalike/
COPY noop_storage.py company_profile/
COPY scoring_config.yaml company_profile/

CMD ["python", "main.py"]
