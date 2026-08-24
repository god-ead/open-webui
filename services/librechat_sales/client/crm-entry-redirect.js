/**
 * LibreChat 登录入口引导 — 正式环境关闭原生登录注册后返回 CRM 专用入口。
 */

(function () {
  "use strict";

  var checking = false;

  /** 判断当前路由是否为可选尾斜杠的登录页。 */
  function isLoginPath() {
    return /\/login\/?$/.test(window.location.pathname);
  }

  /** 查询公开配置，仅在登录与注册同时关闭时离开登录页。 */
  function checkRedirect() {
    if (checking || !isLoginPath()) {
      return;
    }
    checking = true;
    var configPath = window.location.pathname.replace(/\/login\/?$/, "/api/config");
    fetch(configPath, { credentials: "same-origin" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (config) {
        if (
          config &&
          config.emailLoginEnabled === false &&
          config.registrationEnabled === false
        ) {
          var entryPath = window.location.pathname.replace(/login\/?$/, "");
          window.location.replace(entryPath);
        }
      })
      .catch(function () {
        // 配置查询失败时保留当前页面，避免网络抖动造成重定向循环。
        return undefined;
      })
      .finally(function () {
        checking = false;
      });
  }

  ["pushState", "replaceState"].forEach(function (method) {
    // LibreChat 是 SPA；History API 改路由时不会触发 popstate，需要主动复查。
    var original = window.history[method];
    window.history[method] = function () {
      var result = original.apply(this, arguments);
      queueMicrotask(checkRedirect);
      return result;
    };
  });
  window.addEventListener("popstate", checkRedirect);
  checkRedirect();
})();
