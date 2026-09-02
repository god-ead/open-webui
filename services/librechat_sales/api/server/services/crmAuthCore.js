"use strict";

/**
 * CRM token 核心逻辑 — 校验配置、解密 AES-CBC token 并验证载荷时效。
 */

const crypto = require("node:crypto");
const { TextDecoder } = require("node:util");

const DEFAULT_MAX_AGE_SECONDS = 10;
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

/** 携带稳定错误码的认证异常，底层原因仅用于服务端诊断。 */
class CrmAuthError extends Error {
  constructor(code, cause) {
    super(code, cause ? { cause } : undefined);
    this.name = "CrmAuthError";
    this.code = code;
  }
}

/** 解析去重后的 Origin 白名单，拒绝路径、非 HTTP(S) 地址和空配置。 */
function parseOrigins(value) {
  if (!value) {
    throw new Error("CRM_ALLOWED_ORIGINS is required");
  }

  const origins = [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
  if (origins.length === 0) {
    throw new Error("CRM_ALLOWED_ORIGINS must contain at least one origin");
  }

  for (const origin of origins) {
    let parsed;
    try {
      parsed = new URL(origin);
    } catch (error) {
      throw new Error(`CRM_ALLOWED_ORIGINS contains an invalid origin: ${origin}`, {
        cause: error,
      });
    }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin !== origin) {
      throw new Error(`CRM_ALLOWED_ORIGINS must contain origins without paths: ${origin}`);
    }
  }

  return origins;
}

/** 解析 token 有效秒数；未配置时采用十秒的短时默认值。 */
function parseMaxAge(value) {
  const raw = value == null || value === "" ? String(DEFAULT_MAX_AGE_SECONDS) : value;
  if (!/^\d+$/.test(raw)) {
    throw new Error("CRM_TOKEN_MAX_AGE_SECONDS must be a positive integer");
  }
  const maxAgeSeconds = Number(raw);
  if (!Number.isSafeInteger(maxAgeSeconds) || maxAgeSeconds <= 0) {
    throw new Error("CRM_TOKEN_MAX_AGE_SECONDS must be a positive integer");
  }
  return maxAgeSeconds;
}

/** 加载并冻结认证配置，启动时尽早拒绝无效密钥、IV 和 Origin。 */
function loadCrmAuthConfig(env = process.env) {
  const key = Buffer.from(env.CRM_AES_KEY || "", "utf8");
  const iv = Buffer.from(env.CRM_AES_IV || "", "utf8");

  if (key.length !== 16) {
    throw new Error("CRM_AES_KEY must be 16 bytes when UTF-8 encoded");
  }
  if (iv.length !== 16) {
    throw new Error("CRM_AES_IV must be 16 bytes when UTF-8 encoded");
  }

  return Object.freeze({
    key,
    iv,
    maxAgeSeconds: parseMaxAge(env.CRM_TOKEN_MAX_AGE_SECONDS),
    allowedOrigins: Object.freeze(parseOrigins(env.CRM_ALLOWED_ORIGINS)),
    debugErrors: /^(1|true)$/i.test(env.CRM_AUTH_DEBUG_ERRORS || "false"),
  });
}

/** 规范化 URL 编码的 Base64 token，并校验 AES-CBC 密文块边界。 */
function decodeCiphertext(token) {
  if (typeof token !== "string" || token.length === 0 || token.length > 16_384) {
    throw new CrmAuthError("CRM_AUTH_INVALID_REQUEST");
  }

  let encoded;
  try {
    encoded = decodeURIComponent(token);
  } catch (error) {
    throw new CrmAuthError("CRM_AUTH_DECRYPT_FAILED", error);
  }

  if (!encoded || encoded.length % 4 !== 0 || !BASE64_PATTERN.test(encoded)) {
    throw new CrmAuthError("CRM_AUTH_DECRYPT_FAILED");
  }

  const ciphertext = Buffer.from(encoded, "base64");
  if (ciphertext.length === 0 || ciphertext.length % 16 !== 0) {
    throw new CrmAuthError("CRM_AUTH_DECRYPT_FAILED");
  }
  return ciphertext;
}

/** 读取并裁剪载荷字符串，统一执行必填与长度约束。 */
function assertString(payload, field, { allowEmpty = false, maxLength }) {
  if (typeof payload[field] !== "string") {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }
  const value = payload[field].trim();
  if ((!allowEmpty && value.length === 0) || value.length > maxLength) {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }
  return value;
}

/** 校验 CRM 载荷契约与双向时钟偏差，返回规范化的只读数据。 */
function validatePayload(payload, nowSeconds, maxAgeSeconds) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }

  const timestampValue = payload.timestamp;
  if (typeof timestampValue !== "string" || !/^\d+$/.test(timestampValue)) {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }
  const timestamp = Number(timestampValue);
  if (!Number.isSafeInteger(timestamp)) {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }
  if (Math.abs(nowSeconds - timestamp) > maxAgeSeconds) {
    throw new CrmAuthError("CRM_AUTH_TOKEN_EXPIRED");
  }
  if (!Number.isInteger(payload.roleId)) {
    throw new CrmAuthError("CRM_AUTH_INVALID_PAYLOAD");
  }

  return Object.freeze({
    userId: assertString(payload, "userId", { maxLength: 128 }),
    userName: assertString(payload, "userName", { maxLength: 200 }),
    agentName: assertString(payload, "agentName", { allowEmpty: true, maxLength: 200 }),
    area: assertString(payload, "area", { allowEmpty: true, maxLength: 200 }),
    roleId: payload.roleId,
    timestamp,
  });
}

/** 解密 AES-CBC token、解析严格 UTF-8 JSON，并生成防重放摘要。 */
function decryptAndValidateToken(token, config, nowSeconds = Math.floor(Date.now() / 1000)) {
  const ciphertext = decodeCiphertext(token);
  let plaintext;
  try {
    const algorithm = `aes-128-cbc`;
    const decipher = crypto.createDecipheriv(algorithm, config.key, config.iv);
    plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  } catch (error) {
    throw new CrmAuthError("CRM_AUTH_DECRYPT_FAILED", error);
  }

  let payload;
  try {
    const json = new TextDecoder("utf-8", { fatal: true }).decode(plaintext);
    payload = JSON.parse(json);
  } catch (error) {
    throw new CrmAuthError("CRM_AUTH_DECRYPT_FAILED", error);
  }

  return {
    payload: validatePayload(payload, nowSeconds, config.maxAgeSeconds),
    tokenDigest: crypto.createHash("sha256").update(ciphertext).digest("hex"),
  };
}

/** 从 CRM userId 生成稳定且不暴露原值的 LibreChat 虚拟账户键。 */
function buildCrmUserKeys(crmUserId) {
  const digest = crypto.createHash("sha256").update(crmUserId, "utf8").digest("hex");
  return {
    email: `crm-${digest}@users.invalid`,
    username: `crm_${digest.slice(0, 32)}`,
  };
}

module.exports = {
  CrmAuthError,
  buildCrmUserKeys,
  decodeCiphertext,
  decryptAndValidateToken,
  loadCrmAuthConfig,
  validatePayload,
};
