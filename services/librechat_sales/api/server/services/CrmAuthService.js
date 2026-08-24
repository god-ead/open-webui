"use strict";

/**
 * CRM 认证服务 — 原子消费 token，维护身份映射并解析 LibreChat 用户。
 */

const mongoose = require("mongoose");
const { SystemRoles } = require("librechat-data-provider");
const { createUser, findUser, getUserById, updateUser } = require("~/models");
const {
  CrmAuthError,
  buildCrmUserKeys,
  decryptAndValidateToken,
  loadCrmAuthConfig,
} = require("~/server/services/crmAuthCore");

const config = loadCrmAuthConfig();
let indexesPromise;

/** 返回认证网关专用集合，避免修改 LibreChat 上游数据模型。 */
function collections() {
  return {
    identities: mongoose.connection.collection("crm_identities"),
    tokenUses: mongoose.connection.collection("crm_token_uses"),
  };
}

/** 幂等创建 TTL 与一对一身份映射索引，并复用同一初始化 Promise。 */
function ensureIndexes() {
  if (!indexesPromise) {
    const { identities, tokenUses } = collections();
    indexesPromise = Promise.all([
      tokenUses.createIndex({ expiresAt: 1 }, { expireAfterSeconds: 0 }),
      identities.createIndex({ crmUserId: 1 }, { unique: true }),
      identities.createIndex({ libreChatUserId: 1 }, { unique: true }),
    ]);
  }
  return indexesPromise;
}

/** 以摘要唯一键原子消费 token；重复键即表示重放。 */
async function consumeToken(tokenDigest, payload, now) {
  const { tokenUses } = collections();
  // 多留一分钟覆盖 MongoDB TTL 清理边界；过期 token 仍会先被时效校验拒绝。
  const expiresAt = new Date((payload.timestamp + config.maxAgeSeconds + 60) * 1000);
  try {
    await tokenUses.insertOne({ _id: tokenDigest, createdAt: now, expiresAt });
  } catch (error) {
    if (error && error.code === 11000) {
      throw new CrmAuthError("CRM_AUTH_TOKEN_REPLAYED", error);
    }
    throw error;
  }
}

/** 将 CRM userId 稳定映射到单个 LibreChat 账户并刷新业务资料。 */
async function findOrCreateCrmUser(payload) {
  const { identities } = collections();
  const existingIdentity = await identities.findOne({ crmUserId: payload.userId });
  let user = existingIdentity
    ? await getUserById(existingIdentity.libreChatUserId)
    : null;

  if (!user) {
    // 根据CRM传入的userId查找确定性账户键。
    const keys = buildCrmUserKeys(payload.userId);
    user = await findUser({ email: keys.email });
    if (user && user.provider !== "crm") {
      throw new CrmAuthError("CRM_AUTH_ACCOUNT_FAILED");
    }

    if (!user) {
      try {
        user = await createUser(
          {
            ...keys,
            name: payload.userName,
            provider: "crm",
            role: SystemRoles.USER,
            emailVerified: true,
          },
          undefined,
          true,
          true,
        );
      } catch (error) {
        // 并发首次登录可能同时创建账户；重复键后读取胜出的 CRM 账户。
        if (!error || error.code !== 11000) {
          throw error;
        }
        user = await findUser({ email: keys.email });
        if (!user || user.provider !== "crm") {
          throw new CrmAuthError("CRM_AUTH_ACCOUNT_FAILED", error);
        }
      }
    }
  }

  /* 更新 LibreChat中 users.name */
  user = await updateUser(user._id, { name: payload.userName });
  if (!user) {
    throw new CrmAuthError("CRM_AUTH_ACCOUNT_FAILED");
  }

  const now = new Date();
  try {
    // CRM 业务角色只记录在身份表，不映射或覆盖 LibreChat 平台权限。
    await identities.updateOne(
      { crmUserId: payload.userId },
      {
        $set: {
          libreChatUserId: user._id,
          userName: payload.userName,
          agentName: payload.agentName,
          area: payload.area,
          roleId: payload.roleId,
          lastAuthenticatedAt: now,
          updatedAt: now,
        },
        $setOnInsert: { createdAt: now },
      },
      { upsert: true },
    );
  } catch (error) {
    // 唯一索引竞争仅在最终映射与当前账户一致时视为成功。
    if (!error || error.code !== 11000) {
      throw error;
    }
    const identity = await identities.findOne({ crmUserId: payload.userId });
    if (!identity || String(identity.libreChatUserId) !== String(user._id)) {
      throw new CrmAuthError("CRM_AUTH_ACCOUNT_FAILED", error);
    }
  }

  return user;
}

/** 按“验证 → 消费 → 解析账户”顺序完成认证，失败 token 不可再次使用。 */
async function authenticateCrmToken(token) {
  await ensureIndexes();
  const result = decryptAndValidateToken(token, config);
  await consumeToken(result.tokenDigest, result.payload, new Date());
  try {
    const user = await findOrCreateCrmUser(result.payload);
    return { user, payload: result.payload };
  } catch (error) {
    if (error instanceof CrmAuthError) {
      throw error;
    }
    throw new CrmAuthError("CRM_AUTH_ACCOUNT_FAILED", error);
  }
}

module.exports = {
  authenticateCrmToken,
  config,
};
