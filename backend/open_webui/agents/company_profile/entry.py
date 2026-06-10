"""Stable runtime entrypoint for the company profile trial pipe."""

from __future__ import annotations

import asyncio
import logging

from .adapters.openwebui import config_from_valves, format_configuration_error
from .application.errors import CompanyNotFoundError
from .application.match_policy import format_auto_selected_status
from .application.service import CompanyProfileService
from .intent import IntentInterpreter

logger = logging.getLogger(__name__)

_EMPTY_INPUT_MESSAGE = (
    "请告诉我要分析的企业名称。"
)
_NO_MATCH_MESSAGE = "未找到匹配企业或未能获取有效公开信息，请补充更完整的企业全称后重试。"


async def _emit_status(
    event_emitter: callable | None,
    description: str,
    *,
    done: bool,
    hidden: bool = False,
) -> None:
    if not event_emitter:
        return

    try:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                    "hidden": hidden,
                },
            }
        )
    except Exception:
        logger.warning("发送企业画像状态事件失败：description=%r done=%s", description, done, exc_info=True)


async def run_company_profile_pipe(
    user_text: str,
    user: dict | None = None,
    metadata: dict | None = None,
    event_emitter: callable | None = None,
    valves: object | None = None,
) -> str:
    text = (user_text or "").strip()
    chat_id = str((metadata or {}).get("chat_id") or "")
    user_id = str((user or {}).get("id") or "")

    if not text:
        return _EMPTY_INPUT_MESSAGE

    if valves is None:
        return "企业分析 Pipe 尚未初始化 valves，请联系管理员。"

    config = config_from_valves(valves)
    config_error = format_configuration_error(config)
    if config_error:
        logger.warning("企业画像请求中止：配置校验失败，原因=%s", config_error)
        return config_error

    await _emit_status(
        event_emitter,
        f"正在识别目标企业：{text}",
        done=False,
    )

    try:
        interpreter = IntentInterpreter(
            api_key=valves.llm_api_key,
            base_url=valves.llm_base_url,
            model=valves.llm_model,
            timeout_seconds=int(getattr(valves, "llm_timeout_seconds", 120) or 120),
        )
        intent = await asyncio.to_thread(
            interpreter.interpret,
            text,
        )

        if intent["intent"] == "unknown":
            await _emit_status(
                event_emitter,
                "未识别到企业名称，请重新输入",
                done=True,
            )
            return _EMPTY_INPUT_MESSAGE

        if intent["intent"] == "analyze":
            await _emit_status(
                event_emitter,
                f"正在调查 {intent['company_name']}，通常需要4~7分钟，实际时间将根据公司复杂程度有所变化",
                done=False,
            )
            try:
                profile_result = await asyncio.to_thread(
                    CompanyProfileService(config).generate,
                    intent["company_name"],
                )
            except CompanyNotFoundError:
                await _emit_status(
                    event_emitter,
                    f"未找到 {intent['company_name']} 的可分析公开信息",
                    done=True,
                )
                return _NO_MATCH_MESSAGE

            if profile_result.auto_selected and profile_result.selected_match:
                await _emit_status(
                    event_emitter,
                    format_auto_selected_status(
                        list(profile_result.candidates),
                        profile_result.selected_match,
                    ),
                    done=False,
                )

            await _emit_status(
                event_emitter,
                f"已完成 {profile_result.company_name} 企业画像",
                done=True,
            )
            return profile_result.markdown

        await _emit_status(
            event_emitter,
            "企业画像请求未能识别，请重新输入",
            done=True,
        )
        return _EMPTY_INPUT_MESSAGE
    except Exception:
        logger.exception("企业画像执行异常：chat_id=%s user_id=%s 输入文本=%r", chat_id or "-", user_id or "-", text)
        await _emit_status(
            event_emitter,
            "企业画像执行失败，请稍后重试",
            done=True,
        )
        raise
