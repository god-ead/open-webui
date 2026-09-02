"""企业画像 Tool — 异步调用共享的同步核心服务。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from company_profile.application import CompanyProfileService


class CompanyProfileTool:
    """生成企业画像并返回主智能体所需的最小结果。"""

    def __init__(self, service: CompanyProfileService) -> None:
        self.service = service

    async def generate(
        self,
        company_name: str,
        on_section: Callable[[str], None] | None = None,
    ) -> dict[str, str]:
        """在线程中生成画像，并将完成章节投递回事件循环。"""

        if not isinstance(company_name, str):
            raise ValueError("company_name 必须是字符串")
        normalized_name = company_name.strip()
        if not normalized_name:
            raise ValueError("company_name 不能为空")
        if len(normalized_name) > 120:
            raise ValueError("company_name 长度不能超过 120 个字符")

        loop = asyncio.get_running_loop()

        def publish(section: str) -> None:
            if on_section is not None:
                loop.call_soon_threadsafe(on_section, section)

        result = await asyncio.to_thread(
            self.service.generate,
            normalized_name,
            publish if on_section is not None else None,
        )
        await asyncio.sleep(0)
        return {
            "company_name": result.company_name,
            "markdown": result.markdown,
            "version": result.version,
        }
