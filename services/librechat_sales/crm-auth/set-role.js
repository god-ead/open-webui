"use strict";

/**
 * CRM 平台角色运维命令 — 通过精确 CRM userId 设置 LibreChat ADMIN/USER。
 */

const path = require("node:path");
require("module-alias")({ base: path.resolve(__dirname, "..", "api") });
const mongoose = require("mongoose");
const { SystemRoles } = require("librechat-data-provider");
const { connectDb } = require("~/db/connect");

/** 通过 CRM userId 定位关联账户，并仅更新 LibreChat 平台角色。 */
async function main() {
  const [, , crmUserId, role] = process.argv;
  if (!crmUserId || ![SystemRoles.ADMIN, SystemRoles.USER].includes(role)) {
    throw new Error("Usage: node /app/crm-auth/set-role.js <CRM_USER_ID> <ADMIN|USER>");
  }

  await connectDb();
  const identity = await mongoose.connection
    .collection("crm_identities")
    .findOne({ crmUserId });
  if (!identity) {
    throw new Error("CRM identity not found");
  }

  const result = await mongoose.connection.collection("users").findOneAndUpdate(
    // 以身份映射中的 ObjectId 更新账户，禁止用姓名等非唯一字段授权。
    { _id: identity.libreChatUserId },
    { $set: { role, updatedAt: new Date() } },
    { returnDocument: "after" },
  );
  const user = result && (result.value || result);
  if (!user || !user._id) {
    throw new Error("Linked LibreChat user not found");
  }

  process.stdout.write(
    `${JSON.stringify({ crmUserId, libreChatUserId: String(user._id), role: user.role })}\n`,
  );
}

main()
  .then(() => mongoose.disconnect())
  .catch(async (error) => {
    // 无论成功失败都断开数据库；失败通过非零退出码交给运维工具判断。
    process.stderr.write(`CRM role update failed: ${error.message}\n`);
    await mongoose.disconnect().catch(() => undefined);
    process.exitCode = 1;
  });
