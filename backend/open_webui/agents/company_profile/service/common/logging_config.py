"""共享日志配置 — 所有服务统一输出到同一个日志文件，5MB 轮转"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = ".log"
_DEFAULT_LOG_FILE = "service-deploy"
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_DEFAULT_BACKUP_COUNT = 5
_DEFAULT_FMT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"

_configured = False


def _ensure_root_configured(
    log_dir: str,
    log_file: str,
    max_bytes: int,
    backup_count: int,
    level: int,
    fmt: str,
) -> None:
    """配置 root logger（控制台 + 共享 RotatingFileHandler），幂等。"""
    global _configured
    if _configured:
        return
    _configured = True

    resolved_dir = Path(log_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    # PDF 生成时字体裁剪过程中，仅保留其警告和错误日志。
    logging.getLogger("fontTools.subset").setLevel(logging.WARNING)
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # 控制台
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 共享日志文件
    file_handler = RotatingFileHandler(
        str(resolved_dir / f"{log_file}.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    root.info(
        "日志已配置: %s/%s.log (max=%dMB, backups=%d)",
        resolved_dir, log_file, max_bytes // (1024 * 1024), backup_count,
    )


def setup_logging(
    log_file: str = _DEFAULT_LOG_FILE,
    log_dir: str | None = None,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
    fmt: str = _DEFAULT_FMT,
) -> None:
    """配置统一日志：所有服务输出到 ``{log_dir}/{log_file}.log``。

    首次调用时在 root logger 上挂载控制台 + RotatingFileHandler，
    后续调用幂等。日志按服务名 `[deploy.xxx]` 区分来源。

    单文件超过 *max_bytes*（默认 5MB）时自动轮转，上限约 30MB
    （1 活跃 + 5 归档 × 5MB）。
    """
    _ensure_root_configured(
        log_dir=log_dir or os.getenv("LOG_DIR", _DEFAULT_LOG_DIR),
        log_file=log_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
        level=level,
        fmt=fmt,
    )
