"""企业画像 PDF 下载模块 — 提供带路径遍历防护和 TTL 校验的文件下载"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


@dataclass(frozen=True)
class DownloadSettings:
    """文件下载配置：存储根目录和文件有效时长（小时）"""
    root_dir: Path
    ttl_hours: int


def _positive_int(value: str | None, default: int) -> int:
    """将环境变量解析为正整数，无效时回退默认值"""
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def get_download_settings() -> DownloadSettings:
    """从环境变量加载下载配置"""
    return DownloadSettings(
        root_dir=Path("/app/data/pdf-temp"),
        ttl_hours=_positive_int(os.getenv("PROFILE_PDF_TEMP_TTL_HOURS", "24"), 24),
    )


def build_download_response(
    file_name: str,
    settings: DownloadSettings | None = None,
) -> FileResponse:
    """构建安全的文件下载响应，包含三层安全校验

    1. 仅允许 .pdf 后缀的纯文件名（防目录遍历攻击）
    2. 解析后的绝对路径必须在 root_dir 目录树内
    3. 超过 TTL 的文件返回 410 Gone
    """
    resolved = settings or get_download_settings()

    # 安全校验：只允许纯文件名 + 仅 .pdf 后缀
    if Path(file_name).name != file_name or not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="文件不存在")

    # 路径遍历防护：确保最终路径在允许的根目录内
    root = resolved.root_dir.resolve()
    path = (root / file_name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    # TTL 过期校验
    ttl_seconds = resolved.ttl_hours * 60 * 60
    if time.time() - path.stat().st_mtime > ttl_seconds:
        raise HTTPException(status_code=410, detail="文件已过期")

    return FileResponse(path, media_type="application/pdf", filename=file_name)
