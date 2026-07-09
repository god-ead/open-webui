"""企业画像结果回调客户端。"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


logger = logging.getLogger("company_profile.profile_callback")


@dataclass(frozen=True)
class CallbackConfig:
    """回调接口配置，全部从环境变量注入，避免在业务逻辑中硬编码密钥。"""

    url: str
    body_aes_key: str
    body_aes_iv: str
    token_aes_key: str
    token_aes_iv: str
    server_ip: str
    timeout_seconds: float | None

    @classmethod
    def from_env(cls) -> "CallbackConfig":
        """从环境变量读取回调配置；未配置时由调用方决定是否跳过回调。"""
        return cls(
            url=os.getenv("PROFILE_CALLBACK_URL", ""),
            body_aes_key=os.getenv("PROFILE_CALLBACK_BODY_AES_KEY", ""),
            body_aes_iv=os.getenv("PROFILE_CALLBACK_BODY_AES_IV", ""),
            token_aes_key=os.getenv("PROFILE_CALLBACK_TOKEN_AES_KEY", ""),
            token_aes_iv=os.getenv("PROFILE_CALLBACK_TOKEN_AES_IV", ""),
            server_ip=os.getenv("PROFILE_CALLBACK_SERVER_IP", ""),
            timeout_seconds=_parse_timeout(os.getenv("PROFILE_CALLBACK_TIMEOUT", "0")),
        )

    def is_complete(self) -> bool:
        """检查发起回调所需的地址、密钥、IV 和服务器 IP 是否齐全。"""
        return all(
            [
                self.url,
                self.body_aes_key,
                self.body_aes_iv,
                self.token_aes_key,
                self.token_aes_iv,
                self.server_ip,
            ]
        )


@dataclass(frozen=True)
class CallbackResult:
    """回调执行结果；响应无法解析为 JSON 时保留原始 body 即可。"""

    ok: bool
    status_code: int | None = None
    response_body: str = ""
    response_json: Any = None
    error: str = ""


def _aes_cbc_pkcs7_base64(plaintext: str, key: str, iv: str) -> str:
    """按对方接口要求执行 AES-128-CBC + PKCS7 加密，并输出 Base64 字符串。"""
    key_bytes = key.encode("utf-8")
    iv_bytes = iv.encode("utf-8")
    if len(key_bytes) != 16 or len(iv_bytes) != 16:
        raise ValueError("AES key and iv must be 16 bytes for AES-128-CBC")

    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(key_bytes),
        modes.CBC(iv_bytes),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def _json_dumps(data: dict[str, str]) -> str:
    """生成紧凑 JSON，保证加密前明文结构稳定。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _parse_timeout(value: str) -> float | None:
    """PROFILE_CALLBACK_TIMEOUT=0 表示不启用 requests 客户端超时限制。"""
    timeout = float(value)
    if timeout == 0:
        return None
    return timeout


def callback_profile_result(
    task_id: str,
    pdfurl: str,
    *,
    config: CallbackConfig | None = None,
) -> CallbackResult:
    """发送企业画像完成回调；任何异常都转成结果返回，不向主流程抛出。"""
    config = config or CallbackConfig.from_env()
    if not config.is_complete():
        logger.warning(
            "企业画像回调配置不完整，跳过回调 task_id=%s pdfurl=%s callback_url=%s",
            task_id,
            pdfurl,
            config.url,
        )
        return CallbackResult(ok=False, error="callback config incomplete")

    # body 与 token 使用不同密钥分别加密；body 按要求不再包 JSON 外壳。
    body_plaintext = _json_dumps(
        {
            "task_id": task_id,
            "pdfurl": pdfurl
        }
    )
    token_plaintext = _json_dumps(
        {
            "IP": config.server_ip,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    )

    try:
        encrypted_body = _aes_cbc_pkcs7_base64(
            body_plaintext,
            config.body_aes_key,
            config.body_aes_iv,
        )
        encrypted_token = _aes_cbc_pkcs7_base64(
            token_plaintext,
            config.token_aes_key,
            config.token_aes_iv,
        )
        # 对方要求 body 为 raw Base64 字符串，header 名固定为 token。
        response = requests.post(
            config.url,
            data=encrypted_body,
            headers={
                "token": encrypted_token,
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
        )
    except Exception as exc:
        logger.exception(
            "企业画像回调异常 task_id=%s pdfurl=%s callback_url=%s error=%s",
            task_id,
            pdfurl,
            config.url,
            exc,
        )
        return CallbackResult(ok=False, error=str(exc))

    response_json = None
    try:
        response_json = response.json()
    except ValueError:
        # 响应解密规则尚未确认，非明文 JSON 时只保留原始响应体。
        pass

    ok = 200 <= response.status_code < 300
    logger.info(
        "企业画像回调完成 task_id=%s pdfurl=%s callback_url=%s http_status=%s ok=%s response_body=%s",
        task_id,
        pdfurl,
        config.url,
        response.status_code,
        ok,
        response.text,
    )
    return CallbackResult(
        ok=ok,
        status_code=response.status_code,
        response_body=response.text,
        response_json=response_json,
    )
