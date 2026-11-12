/* Журнал в админке: последние 10 минут, при прокрутке вверх подгружаются более ранние записи,
   поиск идёт по всему журналу. */
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

  const state = { before: "", skip: 0, hasMore: false, loading: false, request: 0, shown: 0 };

  function params(extra) {
    const p = new URLSearchParams({ q: qInput.value.trim(), level: levelSelect.value, ...extra });
    return "/admin/api/logs?" + p.toString();
  }

  // Подсветка найденного: строим узлы, а не HTML, чтобы текст журнала не исполнялся.
  function lineNode(entry, needle) {
    const div = document.createElement("div");
    div.className = "log-line lv-" + entry.level;
    const text = entry.text;
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

  function updateEdge() {
    more.hidden = !state.hasMore;
    edge.hidden = state.hasMore || state.shown === 0;
  }

  function describe() {
    const q = qInput.value.trim();
    const filtered = q || levelSelect.value;
    if (state.shown === 0) {
      status.textContent = filtered ? "Ничего не найдено." : "За последние 10 минут событий нет. Нажмите «Загрузить раньше», чтобы посмотреть предыдущие.";
      return;
    }
    const base = filtered ? `Найдено записей: ${state.shown}${state.hasMore ? "+" : ""}` : `Показано записей: ${state.shown}`;
    status.textContent = base + (state.hasMore ? ". Прокрутите вверх, чтобы загрузить более ранние." : ".");
  }

  async function fetchPage(extra) {
    const id = ++state.request;
    state.loading = true;
    try {
      const res = await fetch(params(extra), { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      return id === state.request ? data : null; // ответ на устаревший запрос (уже ищем другое)
    } finally {
      if (id === state.request) state.loading = false;
    }
  }

  async function reload() {
    status.textContent = "Загружаю…";
    let data;
    try {
      data = await fetchPage({});
    } catch (err) {
      status.textContent = "Не удалось загрузить журнал: " + err.message;
      return;
    }
    if (!data) return;
    const needle = qInput.value.trim().toLowerCase();
    list.replaceChildren(...data.entries.map((e) => lineNode(e, needle)));
    if (!data.entries.length) {
      const empty = document.createElement("div");
      empty.className = "log-empty";
      empty.textContent = qInput.value.trim() || levelSelect.value ? "Ничего не найдено" : "Нет событий за последние 10 минут";
      list.append(empty);
    }
    Object.assign(state, { before: data.before || "", skip: data.skip, hasMore: data.has_more, shown: data.entries.length });
    updateEdge();
    describe();
    userScrolling = false;
    box.scrollTop = box.scrollHeight; // самые свежие — внизу
  }

  async function loadOlder() {
    if (state.loading || !state.hasMore) return;
    more.textContent = "Загружаю…";
    let data;
    try {
      data = await fetchPage({ before: state.before, skip: String(state.skip) });
    } catch (err) {
      status.textContent = "Не удалось загрузить журнал: " + err.message;
      return;
    } finally {
      more.textContent = "Загрузить раньше";
    }
    if (!data) return;
    const empty = list.querySelector(".log-empty");
    if (empty && data.entries.length) empty.remove();
    // Добавляем сверху и сохраняем позицию, чтобы текст под глазами не прыгал.
    const fromBottom = box.scrollHeight - box.scrollTop;
    const needle = qInput.value.trim().toLowerCase();
    list.prepend(...data.entries.map((e) => lineNode(e, needle)));
    box.scrollTop = box.scrollHeight - fromBottom;
    Object.assign(state, { before: data.before || state.before, skip: data.skip, hasMore: data.has_more,
      shown: state.shown + data.entries.length });
    updateEdge();
    describe();
  }

  // Подгружаем только когда вверх листает человек, а не когда мы сами прокрутили окно вниз.
  let userScrolling = false;
  for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
    box.addEventListener(type, () => { userScrolling = true; }, { passive: true });
  }
  box.addEventListener("scroll", () => {
    if (userScrolling && box.scrollTop < 80) loadOlder();
  });
  more.addEventListener("click", loadOlder);
  document.getElementById("log-refresh").addEventListener("click", reload);
  levelSelect.addEventListener("change", reload);
  let timer = null;
  qInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(reload, 350);
  });
  qInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(timer);
      reload();
    }
  });

  reload();
})();
