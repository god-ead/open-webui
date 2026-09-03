"use strict";

/**
 * LibreChat v0.8.7 定点补丁 — 注册 CRM 路由、封锁本地登录并加载入口引导，并统一用户名称展示。
 */

const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const zlib = require("node:zlib");

/** 精确替换唯一目标；上游版本漂移或重复命中时中止镜像构建。 */
function replaceOnce(file, before, after) {
  replaceExpected(file, before, after, 1);
}

function replaceExpected(file, before, after, expectedCount) {
  const source = fs.readFileSync(file, "utf8");
  const count = source.split(before).length - 1;
  if (count !== expectedCount) {
    throw new Error(`Expected ${expectedCount} patch targets in ${file}, found ${count}`);
  }
  fs.writeFileSync(file, source.split(before).join(after));
}

/** 返回唯一正则匹配；上游资源关系漂移时中止构建。 */
function matchOnce(source, pattern, label) {
  const matches = [...source.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one ${label}, found ${matches.length}`);
  }
  return matches[0];
}

/** 从入口 chunk 中解析唯一资源名，并校验当前引用次数。 */
function findReferencedAsset(source, pattern, label, expectedReferences) {
  const matches = [...source.matchAll(pattern)];
  const names = [...new Set(matches.map((match) => match[0]))];
  if (names.length !== 1 || matches.length !== expectedReferences) {
    throw new Error(
      `Expected one ${label} with ${expectedReferences} references, ` +
        `found ${names.length} assets and ${matches.length} references`,
    );
  }
  return names[0];
}

/** 同步预压缩资源，避免静态服务器返回修改前的旧内容。 */
function writeCompressedVariants(file) {
  const source = fs.readFileSync(file);
  fs.writeFileSync(`${file}.gz`, zlib.gzipSync(source));
  fs.writeFileSync(`${file}.br`, zlib.brotliCompressSync(source));
}

const authRoute = "/app/api/server/routes/auth.js";
const authService = "/app/api/server/services/AuthService.js";

// 注入 CRM 控制器依赖与专用入口、token 兑换路由。
replaceOnce(
  authRoute,
  "const { loginController } = require('~/server/controllers/auth/LoginController');",
  `const { loginController } = require('~/server/controllers/auth/LoginController');
const {
  crmAuthController,
  crmAuthRequestLogger,
  crmEntryController,
  crmRefreshRequestLogger,
  requireEmailLoginEnabled,
} = require('~/server/controllers/auth/CrmAuthController');`,
);
replaceOnce(
  authRoute,
  "//Local\nrouter.post('/logout', middleware.requireJwtAuth, logoutController);",
  `//Local
router.get('/crm/entry', crmEntryController);
router.post('/crm', crmAuthRequestLogger, middleware.loginLimiter, crmAuthController);
router.post('/logout', middleware.requireJwtAuth, logoutController);`,
);
replaceOnce(
  authRoute,
  "router.post('/refresh', refreshController);",
  "router.post('/refresh', crmRefreshRequestLogger, refreshController);",
);
replaceOnce(
  authRoute,
  "  middleware.checkBan,\n  ldapAuth ? middleware.requireLdapAuth : middleware.requireLocalAuth,",
  "  middleware.checkBan,\n  requireEmailLoginEnabled,\n  ldapAuth ? middleware.requireLdapAuth : middleware.requireLocalAuth,",
);

// HTTPS iframe 允许跨站携带会话 Cookie；本地 HTTP 回退到 Lax。
replaceOnce(
  authService,
  `    res.cookie('refreshToken', refreshToken, {
      expires: new Date(refreshTokenExpires),
      httpOnly: true,
      secure: shouldUseSecureCookie(),
      sameSite: 'strict',
    });
    res.cookie('token_provider', 'librechat', {
      expires: new Date(refreshTokenExpires),
      httpOnly: true,
      secure: shouldUseSecureCookie(),
      sameSite: 'strict',
    });`,
  `    res.cookie('refreshToken', refreshToken, {
      expires: new Date(refreshTokenExpires),
      httpOnly: true,
      secure: shouldUseSecureCookie(),
      sameSite: shouldUseSecureCookie() ? 'none' : 'lax',
    });
    res.cookie('token_provider', 'librechat', {
      expires: new Date(refreshTokenExpires),
      httpOnly: true,
      secure: shouldUseSecureCookie(),
      sameSite: shouldUseSecureCookie() ? 'none' : 'lax',
    });`,
);

const clientRoot = "/app/client/dist";
const clientIndex = path.join(clientRoot, "index.html");
const assetsRoot = path.join(clientRoot, "assets");
const indexSource = fs.readFileSync(clientIndex, "utf8");
const clientEntryTag = matchOnce(
  indexSource,
  /<script\b(?=[^>]*\btype=["']module["'])(?=[^>]*\bsrc=["']\.\/assets\/(index\.[A-Za-z0-9_-]+\.js)["'])[^>]*><\/script>/g,
  "client module entry",
);
const clientEntryName = clientEntryTag[1];
const clientEntryAsset = path.join(assetsRoot, clientEntryName);

// 在 LibreChat SPA 启动前加载正式环境登录入口引导。
replaceOnce(
  clientIndex,
  clientEntryTag[0],
  `<script src="./crm-entry-redirect.js"></script>\n    ${clientEntryTag[0]}`,
);

/* 统一“账号设置”中的账号显示，按 name->username 的顺序显示，虚拟邮箱仅用于账号定位 */
const clientEntrySource = fs.readFileSync(clientEntryAsset, "utf8");
const accountSettingsName = findReferencedAsset(
  clientEntrySource,
  /AccountSettings\.[A-Za-z0-9_-]+\.js/g,
  "AccountSettings asset",
  2,
);
const accountSettingsAsset = path.join(assetsRoot, accountSettingsName);
const hooksName = findReferencedAsset(
  clientEntrySource,
  /hooks\.[A-Za-z0-9_-]+\.js/g,
  "hooks asset",
  3,
);
const hooksAsset = path.join(assetsRoot, hooksName);

replaceOnce(
  accountSettingsAsset,
  "children:n?.email??t(`com_nav_user`)}",
  "children:n?.name??n?.username??t(`com_nav_user`)}",
);
replaceOnce(
  accountSettingsAsset,
  '(0,X.jsx)(wi,{helpAndFaqURL:s?.helpAndFaqURL,termsOfServiceURL:s?.interface?.termsOfService?.externalUrl,privacyPolicyURL:s?.interface?.privacyPolicy?.externalUrl,onShowShortcuts:()=>m(!0)}),(0,X.jsxs)(x,{onClick:()=>p(!0),className:`select-item text-sm`,children:[(0,X.jsx)(oe,{className:`icon-md`,"aria-hidden":`true`}),t(`com_nav_my_files`)]}),',
  "",
);
replaceOnce(
  accountSettingsAsset,
  '(0,X.jsxs)(x,{onClick:()=>u(!0),className:`select-item text-sm`,"data-testid":`nav-settings`,children:[(0,X.jsx)(tn,{className:`icon-md`,"aria-hidden":`true`}),t(`com_nav_settings`)]}),',
  "",
);
replaceOnce(
  accountSettingsAsset,
  '(0,X.jsx)(Ne,{}),(0,X.jsxs)(x,{onClick:()=>i(),className:`select-item text-sm`,children:[(0,X.jsx)(d,{className:`icon-md`,"aria-hidden":`true`}),t(`com_nav_log_out`)]})',
  "",
);

// 所有用户进入会话时默认展开思考过程，不沿用本地折叠偏好。
replaceExpected(hooksAsset, "useState)(dt(sW))", "useState)(!0)", 2);

// 纯中文姓名使用末尾两个字生成头像，英文及混合名称沿用上游规则。
replaceOnce(
  hooksAsset,
  "seed:r,fontFamily:[`Verdana`]",
  "seed:/^\\p{Script=Han}+$/u.test(r)?Array.from(r).slice(-2).join(``):r,fontFamily:[`Verdana`]",
);

// 当前思考内容仍在流式输出时，在面板末尾显示旋转状态。
replaceOnce(
  hooksAsset,
  'dW=(0,J.memo)(({children:e})=>(0,Y.jsx)(`div`,{className:`relative rounded-lg border border-border-light bg-surface-secondary p-3 pb-8 text-text-secondary`,children:(0,Y.jsx)(`p`,{className:Z(`whitespace-pre-wrap leading-[26px]`,dt(lW)),children:e})}))',
  'dW=(0,J.memo)(({children:e,isRunning:t=!1})=>(0,Y.jsx)(`div`,{className:`relative rounded-lg border border-border-light bg-surface-secondary p-3 pb-8 text-text-secondary`,children:(0,Y.jsxs)(`p`,{className:Z(`whitespace-pre-wrap leading-[26px]`,dt(lW)),children:[e,t&&(0,Y.jsxs)(`svg`,{viewBox:`0 0 24 24`,fill:`none`,className:`ml-2 inline-block h-[18px] w-[18px] align-middle`,style:{animation:`spin 0.8s linear infinite`,transformOrigin:`center`},"aria-hidden":`true`,children:[(0,Y.jsx)(`circle`,{cx:`12`,cy:`12`,r:`9`,stroke:`currentColor`,strokeWidth:`3`,opacity:`0.2`}),(0,Y.jsx)(`circle`,{cx:`12`,cy:`12`,r:`9`,stroke:`currentColor`,strokeWidth:`3`,strokeLinecap:`round`,strokeDasharray:`38 19`,opacity:`0.9`})]})]})}))',
);
replaceOnce(
  hooksAsset,
  "dW,{children:m}",
  "dW,{isRunning:b&&t,children:m}",
);

// 文件名保留上游 hash，通过内容摘要促使 Workbox 刷新原地修改的资源。
const patchedAccountSettings = fs.readFileSync(accountSettingsAsset);
const accountSettingsRevision = crypto
  .createHash("sha256")
  .update(patchedAccountSettings)
  .digest("hex");
const patchedHooks = fs.readFileSync(hooksAsset);
const hooksRevision = crypto.createHash("sha256").update(patchedHooks).digest("hex");
const serviceWorker = path.join(clientRoot, "sw.js");
replaceOnce(
  serviceWorker,
  `{url:"assets/${accountSettingsName}",revision:null}`,
  `{url:"assets/${accountSettingsName}",revision:"${accountSettingsRevision}"}`,
);
replaceOnce(
  serviceWorker,
  `{url:"assets/${hooksName}",revision:null}`,
  `{url:"assets/${hooksName}",revision:"${hooksRevision}"}`,
);

writeCompressedVariants(accountSettingsAsset);
writeCompressedVariants(hooksAsset);
