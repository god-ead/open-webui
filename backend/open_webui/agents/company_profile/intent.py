"""LLM-based intent understanding for the company profile trial pipe."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

_ANALYZE_SYSTEM_PROMPT = """你是企业名称提取助手。
你的唯一任务是判断用户是否想查询一家企业，并提取最可能的企业名称。

输出规则：
1. 只返回 JSON。
2. 如果用户明确想查询一家企业，返回：
   {"intent":"analyze","company_name":"企业名称"}
3. 如果用户没有给出可识别的企业名称，返回：
   {"intent":"unknown"}
4. 不要补充解释，不要输出 Markdown，不要编造企业全称。"""


class IntentInterpreter:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=2,
        )
        self._model = model

    def interpret(
        self,
        user_text: str,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(user_text)
        raw = self._complete(system_prompt=_ANALYZE_SYSTEM_PROMPT, prompt=prompt)
        return self._normalize_result(raw)

    def _build_prompt(
        self,
        user_text: str,
    ) -> str:
        return "\n".join(
            [
                f"用户输入：{user_text}",
                "",
                "请提取用户想查询的企业名称。",
            ]
        )

    def _complete(self, system_prompt: str, prompt: str) -> dict[str, Any]:
        extra_body: dict[str, Any] = {}
        if "kimi-k2" in self._model:
            extra_body["thinking"] = {"type": "disabled"}

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            extra_body=extra_body,
        )
        content = (response.choices[0].message.content or "").strip()
        return _extract_json_object(content)

    @staticmethod
    def _normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
        intent = str(raw.get("intent") or "").strip().lower()
        if intent == "analyze":
            company_name = str(raw.get("company_name") or "").strip()
            if company_name:
                return {"intent": "analyze", "company_name": company_name}
            return {"intent": "unknown"}

        return {"intent": "unknown"}


def _extract_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"intent": "unknown"}

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"intent": "unknown"}
