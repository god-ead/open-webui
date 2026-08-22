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
# service 核心依赖（Redis + PostgreSQL）
COPY services/company_profile_service/requirements.txt service-requirements.txt
COPY packages/company_profile/requirements.txt company-profile-requirements.txt
RUN python -m pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://pypi.mirrors.ustc.edu.cn/simple/ \
    -r service-requirements.txt \
    -r company-profile-requirements.txt

# 企业画像共享源码
COPY packages/company_profile /app/company_profile

# ── 服务代码 ──
COPY services/company_profile_service /app/company_profile_service

CMD ["python", "-m", "company_profile_service.main"]
