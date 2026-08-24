"""企业画像 PDF 文件清理服务入口 — 每天在指定整点执行文件清理"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .cleanup import cleanup_once, get_cleanup_settings

CLEANUP_TIMEZONE = ZoneInfo("Asia/Shanghai")


def seconds_until_next_cleanup(
    cleanup_hour: int,
    now: datetime | None = None,
) -> float:
    """计算距离下一次清理的秒数"""
    current = now or datetime.now(CLEANUP_TIMEZONE)
    current = current.astimezone(CLEANUP_TIMEZONE)
    next_cleanup = current.replace(
        hour=cleanup_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if next_cleanup < current:
        next_cleanup += timedelta(days=1)
    return (next_cleanup - current).total_seconds()


def main() -> None:
    """加载配置后每天在指定整点执行清理"""
    settings = get_cleanup_settings()
    while True:
        time.sleep(seconds_until_next_cleanup(settings.cleanup_hour))
        cleanup_once(settings)


if __name__ == "__main__":
    from company_profile_service.common.logging_config import setup_logging
    setup_logging()
    main()
