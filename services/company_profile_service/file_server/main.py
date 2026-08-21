"""企业画像 PDF 文件服务器入口 — 基于 FastAPI 提供 PDF 下载和健康检查"""

from __future__ import annotations

from fastapi import FastAPI

from .downloads import build_download_response

app = FastAPI(title="company-profile-file-server")


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查接口"""
    return {"status": "ok"}


@app.get("/api/download/{file_name}")
def download_file(file_name: str):
    """PDF 文件下载接口，会经过安全校验和 TTL 检查"""
    return build_download_response(file_name)
