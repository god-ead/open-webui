"use strict";

/**
 * LibreChat v0.8.7 定点补丁 — 注册 CRM 路由、封锁本地登录并加载入口引导，并统一用户名称展示。
 */

const fs = require("node:fs");
const zlib = require("node:zlib");

/** 精确替换唯一目标；上游版本漂移或重复命中时中止镜像构建。 */
function replaceOnce(file, before, after) {
  const source = fs.readFileSync(file, "utf8");
  const first = source.indexOf(before);
  if (first === -1 || source.indexOf(before, first + before.length) !== -1) {
    throw new Error(`Expected exactly one patch target in ${file}`);
  }
  fs.writeFileSync(file, source.replace(before, after));
}

const authRoute = "/app/api/server/routes/auth.js";

// 注入 CRM 控制器依赖与专用入口、token 兑换路由。
replaceOnce(
  authRoute,
  "const { loginController } = require('~/server/controllers/auth/LoginController');",
  `const { loginController } = require('~/server/controllers/auth/LoginController');
const {
  crmAuthController,
  crmEntryController,
  requireEmailLoginEnabled,
} = require('~/server/controllers/auth/CrmAuthController');`,
);
replaceOnce(
  authRoute,
  "//Local\nrouter.post('/logout', middleware.requireJwtAuth, logoutController);",
  `//Local
router.get('/crm/entry', crmEntryController);
router.post('/crm', middleware.loginLimiter, crmAuthController);
router.post('/logout', middleware.requireJwtAuth, logoutController);`,
);
replaceOnce(
  authRoute,
  "  middleware.checkBan,\n  ldapAuth ? middleware.requireLdapAuth : middleware.requireLocalAuth,",
  "  middleware.checkBan,\n  requireEmailLoginEnabled,\n  ldapAuth ? middleware.requireLdapAuth : middleware.requireLocalAuth,",
);

const clientIndex = "/app/client/dist/index.html";

// 在 LibreChat SPA 启动前加载正式环境登录入口引导。
replaceOnce(
  clientIndex,
  '    <script type="module" crossorigin src="./assets/index.BHS-ABOD.js"></script>',
  `    <script src="./crm-entry-redirect.js"></script>
    <script type="module" crossorigin src="./assets/index.BHS-ABOD.js"></script>`,
);

/* 统一“账号设置”中的账号显示，按 name->username 的顺序显示，虚拟邮箱仅用于账号定位 */
const accountSettingsAsset = "/app/client/dist/assets/AccountSettings.BOCFxK9P.js";

replaceOnce(
  accountSettingsAsset,
  "children:n?.email??t(`com_nav_user`)}",
  "children:n?.name??n?.username??t(`com_nav_user`)}",
);

// 同步预压缩资源，避免静态服务器按 Accept-Encoding 返回未修改的旧代码。
const accountSettingsSource = fs.readFileSync(accountSettingsAsset);
fs.writeFileSync(`${accountSettingsAsset}.gz`, zlib.gzipSync(accountSettingsSource));
fs.writeFileSync(`${accountSettingsAsset}.br`, zlib.brotliCompressSync(accountSettingsSource));
