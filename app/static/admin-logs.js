/* Журнал в админке: свежие события внизу, при прокрутке вверх подгружаются более ранние,
   раз в 30 секунд снизу дописываются новые. Поиск и фильтр действуют на весь журнал. */
(() => {
  "use strict";
  const box = document.getElementById("log-box");
  if (!box) return;
  const list = document.getElementById("log-list");
  const more = document.getElementById("log-more");
  const edge = document.getElementById("log-edge");
  const status = document.getElementById("log-status");
  const qInput = document.getElementById("log-q");
  const levelSelect = document.getElementById("log-level");
  const REFRESH_MS = 30_000;

  const state = {
    gen: 0, // номер «выборки»: меняется при поиске/фильтре, старые ответы отбрасываем
    before: "", skip: 0, hasMore: false, // курсор в прошлое
    newest: null, newestSkip: 0, // курсор для новых событий
    shown: 0, fresh: 0, updatedAt: null,
    loadingOlder: false, loadingNewer: false,
  };

  const query = () => qInput.value.trim();
  const filtered = () => Boolean(query() || levelSelect.value);

  async function getJSON(extra) {
    const p = new URLSearchParams({ q: query(), level: levelSelect.value, ...extra });
    const res = await fetch("/admin/api/logs?" + p, {
      headers: { "X-Requested-With": "fetch" }, credentials: "same-origin",
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  // Подсветка найденного: строим узлы, а не HTML, чтобы текст журнала не исполнялся.
  function lineNode(entry) {
    const div = document.createElement("div");
    div.className = "log-line lv-" + entry.level;
    const text = entry.text;
    const needle = query().toLowerCase();
    if (!needle) {
      div.textContent = text;
      return div;
    }
    const lower = text.toLowerCase();
    let at = 0;
    for (let i = lower.indexOf(needle); i !== -1; i = lower.indexOf(needle, at)) {
      div.append(text.slice(at, i));
      const mark = document.createElement("mark");
      mark.textContent = text.slice(i, i + needle.length);
      div.append(mark);
      at = i + needle.length;
    }
    div.append(text.slice(at));
    return div;
  }

  const atBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const time = (d) => d.toLocaleTimeString("ru-RU");

  function render() {
    more.hidden = !state.hasMore;
    edge.hidden = state.hasMore || state.shown === 0;
    const empty = list.querySelector(".log-empty");
    if (state.shown === 0 && !empty) {
      const div = document.createElement("div");
      div.className = "log-empty";
      div.textContent = filtered() ? "Ничего не найдено" : "Журнал пуст";
      list.append(div);
    } else if (state.shown > 0 && empty) {
      empty.remove();
    }
    const parts = [];
    parts.push(filtered() ? `Найдено записей: ${state.shown}${state.hasMore ? "+" : ""}` : `Показано записей: ${state.shown}`);
    if (state.hasMore) parts.push("прокрутите вверх, чтобы загрузить более ранние");
    if (state.fresh) parts.push(`новых внизу: ${state.fresh}`);
    if (state.updatedAt) parts.push(`обновлено в ${time(state.updatedAt)}, автообновление раз в 30 с`);
    status.textContent = parts.join(" · ");
  }

  async function reload() {
    const gen = ++state.gen;
    status.textContent = "Загружаю…";
    let data;
    try {
      data = await getJSON({});
    } catch (err) {
      if (gen === state.gen) status.textContent = "Не удалось загрузить журнал: " + err.message;
      return;
    }
    if (gen !== state.gen) return; // пока грузили, поменяли поиск
    list.replaceChildren(...data.entries.map(lineNode));
    Object.assign(state, {
      before: data.before || "", skip: data.skip, hasMore: data.has_more,
      newest: data.newest, newestSkip: data.newest_skip,
      shown: data.entries.length, fresh: 0, updatedAt: new Date(),
    });
    render();
    userScrolling = false;
    box.scrollTop = box.scrollHeight; // самые свежие — внизу
  }

  async function loadOlder() {
    if (state.loadingOlder || !state.hasMore) return;
    const gen = state.gen;
    state.loadingOlder = true;
    more.textContent = "Загружаю…";
    try {
      const data = await getJSON({ before: state.before, skip: String(state.skip) });
      if (gen !== state.gen) return;
      // Добавляем сверху и сохраняем позицию, чтобы текст под глазами не прыгал.
      const fromBottom = box.scrollHeight - box.scrollTop;
      list.prepend(...data.entries.map(lineNode));
      box.scrollTop = box.scrollHeight - fromBottom;
      Object.assign(state, { before: data.before || state.before, skip: data.skip, hasMore: data.has_more,
        shown: state.shown + data.entries.length });
      render();
    } catch (err) {
      status.textContent = "Не удалось загрузить журнал: " + err.message;
    } finally {
      state.loadingOlder = false;
      more.textContent = "Загрузить раньше";
    }
  }

  async function loadNewer() {
    if (state.loadingNewer || document.hidden) return;
    const gen = state.gen;
    state.loadingNewer = true;
    try {
      const data = await getJSON({ after: state.newest || "", after_skip: String(state.newestSkip) });
      if (gen !== state.gen) return;
      if (data.reset) { // новых слишком много — проще перезагрузить окно
        await reload();
        return;
      }
      const stick = atBottom();
      if (data.entries.length) {
        list.append(...data.entries.map(lineNode));
        state.shown += data.entries.length;
        if (!stick) state.fresh += data.entries.length;
      }
      Object.assign(state, { newest: data.newest, newestSkip: data.newest_skip, updatedAt: new Date() });
      if (stick) {
        state.fresh = 0;
        box.scrollTop = box.scrollHeight; // читали низ — показываем новое
      }
      render();
    } catch (_) {
      /* сеть моргнула — попробуем в следующий раз */
    } finally {
      state.loadingNewer = false;
    }
  }

  // Подгружаем старое только когда вверх листает человек, а не когда мы сами прокрутили окно вниз.
  let userScrolling = false;
  for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
    box.addEventListener(type, () => { userScrolling = true; }, { passive: true });
  }
  box.addEventListener("scroll", () => {
    if (userScrolling && box.scrollTop < 80) loadOlder();
    if (state.fresh && atBottom()) {
      state.fresh = 0;
      render();
    }
  });
  more.addEventListener("click", loadOlder);
  document.getElementById("log-refresh").addEventListener("click", reload);
  levelSelect.addEventListener("change", reload);
  let typing = null;
  qInput.addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(reload, 350);
  });
  qInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(typing);
      reload();
    }
  });

  setInterval(loadNewer, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadNewer(); });
  reload();
})();
