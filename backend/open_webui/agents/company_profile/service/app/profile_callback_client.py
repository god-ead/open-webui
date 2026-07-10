"""企业画像结果回调客户端。"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
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

    body_aes_key: str
    body_aes_iv: str
    token_aes_key: str
    token_aes_iv: str
    server_ip: str
    timeout_seconds: float | None
    retry_delay_seconds: float

    @classmethod
    def from_env(cls) -> "CallbackConfig":
        """从环境变量读取回调加密、服务器 IP、超时和重试间隔配置。"""
        return cls(
            body_aes_key=os.getenv("PROFILE_CALLBACK_BODY_AES_KEY", ""),
            body_aes_iv=os.getenv("PROFILE_CALLBACK_BODY_AES_IV", ""),
            token_aes_key=os.getenv("PROFILE_CALLBACK_TOKEN_AES_KEY", ""),
            token_aes_iv=os.getenv("PROFILE_CALLBACK_TOKEN_AES_IV", ""),
            server_ip=os.getenv("PROFILE_CALLBACK_SERVER_IP", ""),
            timeout_seconds=_parse_timeout(os.getenv("PROFILE_CALLBACK_TIMEOUT", "0")),
            retry_delay_seconds=max(
                0,
                float(os.getenv("PROFILE_CALLBACK_RETRY_DELAY_SECONDS", "2")),
            ),
        )

    def is_complete(self) -> bool:
        """检查发起回调所需的密钥、IV 和服务器 IP 是否齐全。"""
        return all(
            [
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


def _decrypt_aes_cbc_pkcs7_base64(ciphertext: str, key: str, iv: str) -> str:
    """使用 AES-128-CBC + PKCS7 解密 Base64 响应体。"""
    key_bytes = key.encode("utf-8")
    iv_bytes = iv.encode("utf-8")
    if len(key_bytes) != 16 or len(iv_bytes) != 16:
        raise ValueError("AES key and iv must be 16 bytes for AES-128-CBC")

    encrypted = base64.b64decode(ciphertext.strip(), validate=True)
    decryptor = Cipher(
        algorithms.AES(key_bytes),
        modes.CBC(iv_bytes),
    ).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8")


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
    callback_url: str,
    *,
    config: CallbackConfig | None = None,
) -> CallbackResult:
    """发送企业画像完成回调，失败时最多发送三次。"""
    try:
        config = config or CallbackConfig.from_env()
        if not callback_url or not config.is_complete():
            raise ValueError("callback config incomplete")

        # body 与 token 使用不同密钥分别加密；body 按要求不再包 JSON 外壳。
        body_plaintext = _json_dumps(
            {
                "task_id": task_id,
                "pdfurl": pdfurl
            }
        )
        encrypted_body = _aes_cbc_pkcs7_base64(
            body_plaintext,
            config.body_aes_key,
            config.body_aes_iv,
        )
    except Exception as exc:
        logger.warning(
            "企业画像回调配置异常 task_id=%s pdfurl=%s callback_url=%s error=%s",
            task_id,
            pdfurl,
            callback_url,
            exc,
        )
        return CallbackResult(ok=False, error=str(exc))

    last_result = CallbackResult(ok=False, error="callback failed")

    for attempt in range(1, 4):
        status_code = None
        response_body = ""
        response_json = None
        error = ""

        try:
            token_plaintext = _json_dumps(
                {
                    "IP": config.server_ip,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
            )
            encrypted_token = _aes_cbc_pkcs7_base64(
                token_plaintext,
                config.token_aes_key,
                config.token_aes_iv,
            )
            response = requests.post(
                callback_url,
                data=encrypted_body,
                headers={
                    "token": encrypted_token,
                    "Content-Type": "application/json",
                },
                timeout=config.timeout_seconds,
            )
            status_code = response.status_code
            response_body = response.text

            if not 200 <= status_code < 300:
                error = f"HTTP 状态码异常: {status_code}"
            else:
                plaintext = _decrypt_aes_cbc_pkcs7_base64(
                    response_body,
                    config.body_aes_key,
                    config.body_aes_iv,
                )
                response_json = json.loads(plaintext)
                if response_json.get("code") != 0:
                    error = f"回调业务状态码异常: {response_json.get('code')}"
        except Exception as exc:
            error = str(exc)

        ok = not error
        logger.info(
            "企业画像回调完成 task_id=%s pdfurl=%s callback_url=%s attempt=%s/3 "
            "http_status=%s business_code=%s ok=%s response_body=%s error=%s",
            task_id,
            pdfurl,
            callback_url,
            attempt,
            status_code,
            response_json.get("code") if isinstance(response_json, dict) else None,
            ok,
            response_body,
            error,
        )
        last_result = CallbackResult(
            ok=ok,
            status_code=status_code,
            response_body=response_body,
            response_json=response_json,
            error=error,
        )
        if ok:
            return last_result
        if attempt < 3:
            time.sleep(config.retry_delay_seconds * attempt)

    return last_result
