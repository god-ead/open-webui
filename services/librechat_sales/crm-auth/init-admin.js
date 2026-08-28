"use strict";

/**
 * LibreChat 本地管理员初始化 — 按 Email 幂等创建或提升账号，拒绝修改外部身份。
 */

const path = require("node:path");
require("module-alias")({ base: path.resolve(__dirname, "..", "api") });
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const { SystemRoles } = require("librechat-data-provider");
const { connectDb } = require("~/db/connect");
const { registerUser } = require("~/server/services/AuthService");

const MISSING_USER_EXIT_CODE = 10;

function loadEmail() {
  const email = String(process.env.LIBRECHAT_ADMIN_EMAIL || "")
    .trim()
    .toLowerCase();
  if (!email || !email.includes("@")) {
    throw new Error("LIBRECHAT_ADMIN_EMAIL must be a valid email address");
  }
  return email;
}

function assertLocalUser(user, email) {
  if (user && user.provider !== "local") {
    throw new Error(`Refusing to promote non-local account: ${email}`);
  }
}

async function findUserByEmail(email) {
  return mongoose.connection.collection("users").findOne({ email });
}

async function createLocalUser(email) {
  const password = String(process.env.LIBRECHAT_ADMIN_PASSWORD || "");
  if (!password) {
    throw new Error("LIBRECHAT_ADMIN_PASSWORD is required to create a new admin");
  }

  const defaultName = email.split("@", 1)[0];
  const result = await registerUser(
    {
      email,
      password,
      name: defaultName,
      username: defaultName,
      confirm_password: password,
    },
    { emailVerified: true },
  );
  if (result.status !== 200) {
    throw new Error(result.message || "Failed to create LibreChat admin account");
  }
}

async function main() {
  const email = loadEmail();
  await connectDb();

  let user = await findUserByEmail(email);
  assertLocalUser(user, email);

  if (process.argv.includes("--check")) {
    if (!user) {
      return MISSING_USER_EXIT_CODE;
    }
    return 0;
  }

  if (!user) {
    await createLocalUser(email);
    user = await findUserByEmail(email);
    assertLocalUser(user, email);
  }
  if (!user) {
    throw new Error(`LibreChat admin account was not created: ${email}`);
  }

  // 角色更新与密码相互独立，重复执行不会覆盖已有本地账号的密码。
  await mongoose.connection.collection("users").updateOne(
    { _id: user._id, provider: "local" },
    { $set: { role: SystemRoles.ADMIN, updatedAt: new Date() } },
  );

  const updatedUser = await findUserByEmail(email);
  if (!updatedUser || updatedUser.provider !== "local" || updatedUser.role !== SystemRoles.ADMIN) {
    throw new Error(`LibreChat admin role verification failed: ${email}`);
  }

  process.stdout.write(`${JSON.stringify({ email, role: updatedUser.role })}\n`);
  return 0;
}

async function run() {
  let exitCode;
  try {
    exitCode = await main();
  } catch (error) {
    process.stderr.write(`LibreChat admin initialization failed: ${error.message}\n`);
    exitCode = 1;
  }
  await mongoose.disconnect().catch(() => undefined);
  process.exit(exitCode);
}

run();
