"""销售智能体日志配置 — 同时写入控制台和本地轮转文件。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = "/data/app/logs"
_DEFAULT_LOG_FILE = "founder-sales-agent"
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 14
_DEFAULT_FMT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"

_configured = False


def setup_logging(
    log_file: str = _DEFAULT_LOG_FILE,
    log_dir: str | None = None,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
    level: int = logging.INFO,
) -> None:
    """配置 root 和 Uvicorn logger，共享同一个轮转文件。"""
    global _configured
    if _configured:
        return
    _configured = True

    resolved_dir = Path(log_dir or os.getenv("LOG_DIR", _DEFAULT_LOG_DIR))
    resolved_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_DEFAULT_FMT, datefmt="%Y-%m-%d %H:%M:%S")
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        resolved_dir / f"{log_file}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Uvicorn logger 默认不向 root 传播，需显式复用文件 handler。
    logging.getLogger("uvicorn").addHandler(file_handler)
    logging.getLogger("uvicorn.access").addHandler(file_handler)
