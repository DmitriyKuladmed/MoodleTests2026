/* Сообщает серверу об ошибках в браузере, чтобы они попали в журнал администратора.
   window.reportProblem(kind, data) — для своих событий (например, сбой сохранения). */
(() => {
  "use strict";
  const MAX_PER_PAGE = 20;
  let sent = 0;
  const seen = new Set();

  function send(kind, data) {
    const key = kind + "|" + (data.message || "") + "|" + (data.source || "") + "|" + (data.line || "");
    if (sent >= MAX_PER_PAGE || seen.has(key)) return;
    seen.add(key);
    sent += 1;
    const body = JSON.stringify({ kind, page: location.pathname + location.search, ...data });
    try {
      fetch("/api/client-log", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
        body,
        keepalive: true,
        credentials: "same-origin",
      }).catch(() => { /* сервер недоступен — сообщить некуда */ });
    } catch (_) { /* очень старый браузер */ }
  }

  window.reportProblem = (kind, data = {}) => send(kind, data);

  window.addEventListener("error", (e) => {
    // Ошибка загрузки картинки или скрипта приходит без message.
    if (!e.message && e.target && e.target !== window) {
      send("resource", { message: "Не загрузился " + (e.target.tagName || "ресурс").toLowerCase(),
        source: e.target.src || e.target.href || "" });
      return;
    }
    send("js_error", {
      message: e.message,
      source: e.filename,
      line: e.lineno,
      col: e.colno,
      stack: e.error && e.error.stack ? String(e.error.stack).slice(0, 1500) : "",
    });
  }, true);

  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    send("js_promise", {
      message: r && r.message ? r.message : String(r),
      stack: r && r.stack ? String(r.stack).slice(0, 1500) : "",
    });
  });
})();
