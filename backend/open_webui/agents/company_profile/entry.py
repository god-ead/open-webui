"""Stable runtime entrypoint for the company profile trial pipe."""

from __future__ import annotations

import asyncio
import logging

from .bridge import CompanyProfileBridge
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

    bridge = CompanyProfileBridge(valves)
    config_error = bridge.get_configuration_error()
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
                f"正在调查 {intent['company_name']}，实际时间根据公司复杂度有所变化，预计需要4~7分钟",
                done=False,
            )
            result = await asyncio.to_thread(bridge.analyze_company, intent["company_name"])
            result_company_name = intent["company_name"]
            if hasattr(result, "matches"):
                await _emit_status(
                    event_emitter,
                    "发现多个匹配企业，正在自动选择最高置信度企业",
                    done=False,
                )
                selected_match = bridge.pick_best_match(result.matches)
                if selected_match is None:
                    await _emit_status(
                        event_emitter,
                        f"未找到 {intent['company_name']} 的可分析公开信息",
                        done=True,
                    )
                    return _NO_MATCH_MESSAGE
                await _emit_status(
                    event_emitter,
                    bridge.format_auto_selected_status(result.matches, selected_match),
                    done=False,
                )
                result = await asyncio.to_thread(bridge.analyze_match, selected_match)
                result_company_name = selected_match.company_name

            if bridge.is_empty_result(result):
                await _emit_status(
                    event_emitter,
                    f"未找到 {result_company_name} 的可分析公开信息",
                    done=True,
                )
                return _NO_MATCH_MESSAGE

            await _emit_status(
                event_emitter,
                f"正在整理 {result_company_name} 的画像与建议",
                done=False,
            )
            markdown = bridge.render_markdown(result)
            await _emit_status(
                event_emitter,
                f"已完成 {result_company_name} 企业画像",
                done=True,
            )
            return markdown

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
