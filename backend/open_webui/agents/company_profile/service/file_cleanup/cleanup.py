"""企业画像 PDF 文件清理模块 — 根据 TTL 策略清理过期文件"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CleanupSettings:
    """文件清理配置：临时目录、备份目录及各自的 TTL 和清理间隔"""
    temp_dir: Path
    backup_dir: Path
    temp_ttl_hours: int
    backup_ttl_days: int
    interval_seconds: int


def _positive_int(value: str | None, default: int) -> int:
    """将环境变量解析为正整数，无效时回退默认值"""
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def get_cleanup_settings() -> CleanupSettings:
    """从环境变量加载清理配置"""
    return CleanupSettings(
        temp_dir=Path(os.getenv("PROFILE_PDF_TEMP_DIR", "/app/profile-pdf-temp")),
        backup_dir=Path(os.getenv("PROFILE_PDF_BACKUP_DIR", "/app/profile-pdf-backup")),
        temp_ttl_hours=_positive_int(os.getenv("PROFILE_PDF_TEMP_TTL_HOURS", "24"), 24),
        backup_ttl_days=_positive_int(os.getenv("PROFILE_PDF_BACKUP_TTL_DAYS", "7"), 7),
        interval_seconds=_positive_int(
            os.getenv("PROFILE_PDF_CLEANUP_INTERVAL_SECONDS", "3600"),
            3600,
        ),
    )


def cleanup_once(settings: CleanupSettings, now: float | None = None) -> list[str]:
    """执行一次清理：依次清理临时目录和备份目录中超过 TTL 的文件

    now 参数用于测试注入，为 None 时默认使用当前时间。
    """
    current_time = time.time() if now is None else now
    removed: list[str] = []
    removed.extend(
        _cleanup_dir(
            settings.temp_dir,
            settings.temp_ttl_hours * 60 * 60,
            current_time,
        )
    )
    removed.extend(
        _cleanup_dir(
            settings.backup_dir,
            settings.backup_ttl_days * 24 * 60 * 60,
            current_time,
        )
    )
    return removed


def _cleanup_dir(root: Path, ttl_seconds: int, now: float) -> list[str]:
    """遍历目录，删除修改时间超过 ttl_seconds 的文件

    目录不存在时直接返回空列表，删除失败仅打印日志不中断流程。
    """
    if not root.exists():
        return []

    removed: list[str] = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime <= ttl_seconds:
                continue
            path.unlink()
            removed.append(str(path))
        except OSError as exc:
            print(f"[file-cleanup] 删除失败 {path}: {exc}")
    return removed
