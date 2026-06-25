"""企业画像 PDF 文件清理服务入口 — 以固定间隔循环执行文件清理"""

from __future__ import annotations

import time

from .cleanup import cleanup_once, get_cleanup_settings


def main() -> None:
    """加载配置后无限循环执行清理，间隔由配置控制"""
    settings = get_cleanup_settings()
    while True:
        cleanup_once(settings)
        time.sleep(settings.interval_seconds)


if __name__ == "__main__":
    main()
