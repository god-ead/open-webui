"use strict";

/**
 * CRM 认证控制器 — 提供 iframe 入口并将 CRM token 兑换为 LibreChat 会话。
 */

const crypto = require("node:crypto");
const { logger } = require("@librechat/data-schemas");
const { setAuthTokens } = require("~/server/services/AuthService");
const { authenticateCrmToken, config } = require("~/server/services/CrmAuthService");
const { CrmAuthError } = require("~/server/services/crmAuthCore");

const GENERIC_MESSAGE = "认证失败，请关闭窗口并从 CRM 重新进入";
const INTERNAL_ERROR_CODES = new Set([
  "CRM_AUTH_ACCOUNT_FAILED",
  "CRM_AUTH_SESSION_FAILED",
]);

/** 生成不可逆短摘要，用于关联同一 CRM token 的重复请求。 */
function tokenFingerprint(token) {
  if (typeof token !== "string" || !token) {
    return null;
  }
  return crypto.createHash("sha256").update(token).digest("hex").slice(0, 12);
}

/** 返回请求经过代理后的地址视图，不将任一转发头视为可信身份。 */
function requestNetworkDetails(req) {
  return {
    ip: req.ip,
    ips: req.ips,
    remoteAddress: req.socket?.remoteAddress || null,
    forwardedFor: req.headers["x-forwarded-for"] || null,
  };
}

/** 判断指定 Cookie 是否随请求到达服务端，不读取或记录其值。 */
function hasCookie(req, name) {
  const cookie = req.headers.cookie;
  return (
    typeof cookie === "string" &&
    cookie.split(";").some((part) => part.trim().startsWith(`${name}=`))
  );
}

/** 提取认证 Cookie 的公开属性，排除 Cookie 值。 */
function authCookieAttributes(res) {
  const header = res.getHeader("set-cookie");
  const values = Array.isArray(header) ? header : header ? [header] : [];
  return values.flatMap((value) => {
    const [pair, ...attributes] = String(value)
      .split(";")
      .map((part) => part.trim());
    const name = pair.slice(0, pair.indexOf("="));
    if (name !== "refreshToken" && name !== "token_provider") {
      return [];
    }
    const findAttribute = (prefix) =>
      attributes.find((attribute) => attribute.toLowerCase().startsWith(prefix));
    return [
      {
        name,
        path: findAttribute("path=")?.slice(5) || null,
        sameSite: findAttribute("samesite=")?.slice(9) || null,
        secure: attributes.some((attribute) => attribute.toLowerCase() === "secure"),
        httpOnly: attributes.some((attribute) => attribute.toLowerCase() === "httponly"),
      },
    ];
  });
}

/** 在响应完成或连接提前关闭时只记录一次结果。 */
function logResponseCompletion(res, message, fields, startedAt) {
  let logged = false;
  const complete = (outcome) => {
    if (logged) {
      return;
    }
    logged = true;
    writeCrmAuthLog("info", message, {
      ...fields,
      outcome,
      statusCode: res.statusCode,
      contentType: res.getHeader("content-type") || null,
      durationMs: Date.now() - startedAt,
    });
  };
  res.once("finish", () => complete("finished"));
  res.once("close", () => complete("closed"));
}

/** 同时保留结构化字段与消息副本，兼容 LibreChat 文件日志字段过滤。 */
function writeCrmAuthLog(level, message, fields) {
  logger[level](`${message} ${JSON.stringify(fields)}`);
}

/** 解析 LibreChat 对外地址，统一生成同源兑换路径与跳转路径。 */
function getPublicLocation() {
  try {
    const clientUrl = new URL(process.env.DOMAIN_CLIENT);
    const pathname = clientUrl.pathname.replace(/\/$/, "");
    return {
      basePath: pathname === "/" ? "" : pathname,
      origin: clientUrl.origin,
    };
  } catch (error) {
    throw new Error("DOMAIN_CLIENT must be an absolute URL for CRM authentication", {
      cause: error,
    });
  }
}

const publicLocation = getPublicLocation();
const publicBasePath = publicLocation.basePath;

/** 转义内联脚本中的 JSON，防止配置值提前闭合 HTML 标签或实体。 */
function escapeHtmlJson(value) {
  return JSON.stringify(value).replace(/[<>&]/g, (character) => {
    return { "<": "\\u003c", ">": "\\u003e", "&": "\\u0026" }[character];
  });
}

/** 构建受 CSP nonce 保护的 iframe 认证入口页。 */
function renderEntryPage(nonce) {
  const origins = escapeHtmlJson(config.allowedOrigins);
  const exchangePath = escapeHtmlJson(`${publicBasePath}/api/auth/crm`);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>营销智能体认证</title>
  <style nonce="${nonce}">
    :root { color-scheme: light dark; font-family: system-ui, "Microsoft YaHei", sans-serif; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; background: #f7f8fa; color: #172033; }
    main { width: min(28rem, calc(100% - 3rem)); text-align: center; }
    .spinner { width: 2rem; height: 2rem; margin: 0 auto 1rem; border: .2rem solid #d8e2f0; border-top-color: #0b5cab; border-radius: 50%; animation: spin .8s linear infinite; }
    p { line-height: 1.6; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  </style>
</head>
<body>
  <main aria-live="polite">
    <div class="spinner" aria-hidden="true"></div>
    <p id="status">正在等待 CRM 身份认证…</p>
  </main>
  <script nonce="${nonce}">
    (() => {
      const allowedOrigins = ${origins};
      const exchangePath = ${exchangePath};
      const status = document.getElementById('status');
      const retryDelayMs = 100;
      const retryWindowMs = 1000;
      let started = false;
      let retryDeadline = 0;

      const fail = () => {
        status.textContent = '认证失败，请关闭窗口并从 CRM 重新进入';
        document.querySelector('.spinner').hidden = true;
      };

      const notifyParent = (message) => {
        // 仅向配置的 CRM Origin 广播协议消息，避免使用通配目标泄露认证状态。
        for (const origin of allowedOrigins) {
          window.parent.postMessage(message, origin);
        }
      };

      const requestNewToken = () => {
        // 每次失败后延迟请求新 token，并以首次失败为起点限制整个重试窗口。
        const now = performance.now();
        retryDeadline ||= now + retryWindowMs;
        if (now + retryDelayMs > retryDeadline) {
          fail();
          return;
        }

        status.textContent = '认证信息已失效，正在等待 CRM 重新认证…';
        window.setTimeout(() => {
          if (performance.now() > retryDeadline) {
            fail();
            return;
          }
          started = false;
          notifyParent({ source: 'fm-agent', type: 'token-expired' });
        }, retryDelayMs);
      };

      const takeUrlToken = () => {
        // 兼容 query/hash URL 传输，并立即清除地址栏中的敏感 token。
        const url = new URL(window.location.href);
        const hashParams = new URLSearchParams(url.hash.replace(/^#/, ''));
        const token = url.searchParams.get('token') || hashParams.get('token');
        if (!token) {
          return null;
        }

        url.searchParams.delete('token');
        hashParams.delete('token');
        url.hash = hashParams.toString() ? '#' + hashParams.toString() : '';
        window.history.replaceState(null, '', url.pathname + url.search + url.hash);
        return token;
      };

      const exchangeToken = async (value) => {
        // 单次入口只允许一个兑换请求在途，避免重复消费同一 token。
        if (started || typeof value !== 'string' || !value) {
          return;
        }

        started = true;
        let token = value;
        try {
          const response = await fetch(exchangePath, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
          });
          token = null;
          const result = await response.json().catch(() => null);
          if (response.status === 401) {
            requestNewToken();
            return;
          }
          if (!response.ok || !result || result.ok !== true || typeof result.redirect !== 'string') {
            fail();
            return;
          }
          window.location.replace(result.redirect);
        } catch (_) {
          token = null;
          fail();
        }
      };

      window.addEventListener('message', (event) => {
        // 白名单配置为 '*' 时仅校验父窗口引用与协议字段。
        if (
          started ||
          event.source !== window.parent ||
          (!allowedOrigins.includes('*') && !allowedOrigins.includes(event.origin))
        ) {
          return;
        }
        if (
          !event.data ||
          event.data.source !== 'fm-widget' ||
          event.data.type !== 'token' ||
          typeof event.data.token !== 'string'
        ) {
          return;
        }

        exchangeToken(event.data.token);
      });

      const urlToken = takeUrlToken();
      notifyParent({ source: 'fm-agent', type: 'ready' });
      if (urlToken) {
        exchangeToken(urlToken);
      }
    })();
  </script>
</body>
</html>`;
}

/** 返回允许指定 CRM 嵌入、禁止缓存且仅执行本页内联资源的入口页。 */
function crmEntryController(_req, res) {
  const nonce = crypto.randomBytes(18).toString("base64");
  const frameAncestors = config.allowedOrigins.join(" ");
  res.removeHeader("X-Frame-Options");
  res.set({
    "Cache-Control": "no-store",
    "Content-Security-Policy": `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; connect-src 'self'; frame-ancestors ${frameAncestors}`,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
  return res.type("html").send(renderEntryPage(nonce));
}

/** 对外隐藏认证细节，仅在显式调试配置下返回内部错误码。 */
function errorResponse(res, code, status = 401) {
  return res.status(status).json({
    ok: false,
    message: GENERIC_MESSAGE,
    ...(config.debugErrors ? { code } : {}),
  });
}

/** 在限流前记录 CRM token 兑换请求，确保 429 也可追踪。 */
function crmAuthRequestLogger(req, res, next) {
  const startedAt = Date.now();
  const context = {
    requestId: crypto.randomUUID(),
    tokenFingerprint: tokenFingerprint(req.body?.token),
  };
  req.crmAuthLog = context;
  writeCrmAuthLog("info", "[crmAuth] request received", {
    ...context,
    origin: req.headers.origin || null,
    ...requestNetworkDetails(req),
  });
  logResponseCompletion(res, "[crmAuth] request completed", context, startedAt);
  return next();
}

/** 记录 refresh 是否收到 CRM 登录签发的会话 Cookie。 */
function crmRefreshRequestLogger(req, res, next) {
  const startedAt = Date.now();
  const context = {
    requestId: crypto.randomUUID(),
    hasRefreshToken: hasCookie(req, "refreshToken"),
    hasTokenProvider: hasCookie(req, "token_provider"),
  };
  writeCrmAuthLog("info", "[crmAuth] refresh received", {
    ...context,
    origin: req.headers.origin || null,
    ...requestNetworkDetails(req),
  });
  logResponseCompletion(res, "[crmAuth] refresh completed", context, startedAt);
  return next();
}

/** 兑换 CRM token，解析本地账户并签发 LibreChat 原生会话。 */
async function crmAuthController(req, res) {
  const requestId = req.crmAuthLog?.requestId || crypto.randomUUID();
  const fingerprint =
    req.crmAuthLog?.tokenFingerprint || tokenFingerprint(req.body?.token);
  const startedAt = Date.now();
  try {
    if (req.headers.origin && req.headers.origin !== publicLocation.origin) {
      throw new CrmAuthError("CRM_AUTH_ORIGIN_REJECTED");
    }
    if (!req.body || typeof req.body.token !== "string") {
      throw new CrmAuthError("CRM_AUTH_INVALID_REQUEST");
    }
    const { user } = await authenticateCrmToken(req.body.token);
    req.user = user;
    try {
      // 复用 LibreChat 的 Cookie/JWT 会话生命周期，不另建网关会话体系。
      await setAuthTokens(user._id, res, null, req);
    } catch (error) {
      throw new CrmAuthError("CRM_AUTH_SESSION_FAILED", error);
    }
    writeCrmAuthLog("info", "[crmAuth] authentication succeeded", {
      requestId,
      tokenFingerprint: fingerprint,
      authCookies: authCookieAttributes(res),
      durationMs: Date.now() - startedAt,
    });
    return res.status(200).json({ ok: true, redirect: `${publicBasePath}/c/new` });
  } catch (error) {
    const expected = error instanceof CrmAuthError;
    const code = expected ? error.code : "CRM_AUTH_ACCOUNT_FAILED";
    const internal = !expected || INTERNAL_ERROR_CODES.has(code);
    writeCrmAuthLog(internal ? "error" : "warn", "[crmAuth] authentication failed", {
      requestId,
      tokenFingerprint: fingerprint,
      code,
      durationMs: Date.now() - startedAt,
    });
    return errorResponse(res, code, internal ? 500 : 401);
  }
}

/** 按环境配置启停原生账号密码登录，开发环境缺省保持可用。 */
function requireEmailLoginEnabled(_req, res, next) {
  const value = process.env.ALLOW_EMAIL_LOGIN;
  if (value == null || /^(1|true)$/i.test(value)) {
    return next();
  }
  return res.status(403).json({ message: "Email login is disabled" });
}

module.exports = {
  crmAuthController,
  crmAuthRequestLogger,
  crmEntryController,
  crmRefreshRequestLogger,
  requireEmailLoginEnabled,
};
