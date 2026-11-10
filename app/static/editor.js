/* Редактор теста: вопросы, автосохранение, картинки, вставка из Word. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const editorEl = $("#editor");
  if (!editorEl) return;

  const TEST_ID = editorEl.dataset.testId;
  const TYPES = JSON.parse($("#question-types").textContent);
  const initial = JSON.parse($("#initial-data").textContent);
  const LETTERS = "абвгдежзиклмнопрстуфхцчшщэюя";
  const MAX_IMAGE = 5 * 1024 * 1024;

  const ICONS = {
    up: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15V5M5.5 9.5 10 5l4.5 4.5"/></svg>',
    down: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 5v10M5.5 10.5 10 15l4.5-4.5"/></svg>',
    copy: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 4.5H5.5A1.5 1.5 0 0 0 4 6v7.5"/></svg>',
    trash: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h12M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6"/></svg>',
    x: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg>',
  };

  const state = {
    test: {
      discipline: initial.discipline,
      teacher: initial.teacher,
      department: initial.department,
      settings: initial.settings,
      questions: initial.questions,
    },
    version: initial.version,
    status: initial.status,
    issues: initial.issues,
    updated: initial.updated_label,
    changeId: 0,
    savedChangeId: 0,
    saving: false,
    conflict: false,
    timer: null,
    fresh: new Set(), // только что созданные вопросы: ошибки покажем, когда человек уйдёт с вопроса
  };

  // --- утилиты ------------------------------------------------------------
  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return Date.now().toString(16) + Math.random().toString(16).slice(2, 14);
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "html") node.innerHTML = value;
      else if (key === "value") node.value = value;
      else if (key === "checked") node.checked = !!value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function letter(i) {
    return i < LETTERS.length ? LETTERS[i] : String(i + 1);
  }

  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function blankQuestion(type) {
    return {
      id: uid(), type, text: "", image: null, points: 1, shuffle: true,
      options: [0, 1, 2, 3].map(() => ({ text: "", correct: false })),
      correct: true, answers: [""], case_sensitive: false, number: "", tolerance: "",
      pairs: [0, 1, 2].map(() => ({ left: "", right: "" })), distractors: [],
    };
  }

  function findQuestion(node) {
    const card = node.closest(".q");
    if (!card) return null;
    const index = state.test.questions.findIndex((q) => q.id === card.dataset.id);
    return index < 0 ? null : { q: state.test.questions[index], index, card };
  }

  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight + 2, 640) + "px";
  }

  async function api(method, url, body) {
    const opts = { method, headers: { "X-Requested-With": "fetch" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts);
  }

  async function errorText(res, fallback) {
    try {
      const data = await res.json();
      if (typeof data.detail === "string") return data.detail;
    } catch (_) { /* ignore */ }
    return fallback;
  }

  // --- уведомления --------------------------------------------------------
  const toastEl = $("#toast");
  let toastTimer = null;
  function toast(text, actionLabel, action) {
    $("#toast-text").textContent = text;
    const btn = $("#toast-action");
    btn.hidden = !actionLabel;
    btn.textContent = actionLabel || "";
    btn.onclick = () => { hideToast(); if (action) action(); };
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, actionLabel ? 8000 : 4000);
  }
  function hideToast() { toastEl.classList.remove("show"); }

  const bannerEl = $("#banner");
  function banner(nodes) {
    bannerEl.replaceChildren(...nodes);
    bannerEl.classList.add("show");
  }
  function hideBanner() { bannerEl.classList.remove("show"); }

  // --- сохранение ---------------------------------------------------------
  const saveStatusEl = $("#save-status");
  const saveTimeEl = $("#save-time");
  function setSaveStatus(kind, text) {
    const labels = {
      saved: "Все изменения сохранены",
      dirty: "Есть несохранённые изменения",
      saving: "Сохраняю…",
      error: "Не удалось сохранить. Повторю через несколько секунд",
    };
    saveStatusEl.dataset.state = kind;
    saveStatusEl.textContent = text || labels[kind];
    saveTimeEl.textContent = state.updated ? "Последнее сохранение: " + state.updated : "";
  }

  function markDirty() {
    state.changeId += 1;
    if (!state.saving) setSaveStatus("dirty");
    clearTimeout(state.timer);
    state.timer = setTimeout(save, 700);
  }

  function scheduleSave(delay) {
    clearTimeout(state.timer);
    state.timer = setTimeout(save, delay);
  }

  async function save() {
    if (state.conflict || state.saving) return;
    if (state.changeId === state.savedChangeId) return;
    state.saving = true;
    const changeId = state.changeId;
    setSaveStatus("saving");
    let retryIn = 0;
    try {
      const res = await api("PUT", `/api/tests/${TEST_ID}`, { version: state.version, ...state.test });
      if (res.status === 409) {
        showConflict();
        return;
      }
      if (res.status === 401) {
        showSessionExpired();
        retryIn = 5000;
        setSaveStatus("error", "Не сохранено: нужно войти заново");
        return;
      }
      if (!res.ok) {
        retryIn = 5000;
        setSaveStatus("error", await errorText(res, "Не удалось сохранить. Повторю через несколько секунд"));
        return;
      }
      const data = await res.json();
      state.version = data.version;
      state.status = data.status;
      state.issues = data.issues;
      state.updated = data.updated_at;
      state.savedChangeId = changeId;
      hideBanner();
      applyIssues();
      renderSide();
      setSaveStatus(state.changeId === changeId ? "saved" : "dirty");
    } catch (_) {
      retryIn = 5000;
      setSaveStatus("error", "Нет связи с сервером. Повторю через несколько секунд");
    } finally {
      state.saving = false;
      if (!state.conflict) {
        if (retryIn) scheduleSave(retryIn);
        else if (state.changeId !== state.savedChangeId) scheduleSave(300);
      }
    }
  }

  async function flush() {
    clearTimeout(state.timer);
    for (let i = 0; i < 20 && state.saving; i++) await new Promise((r) => setTimeout(r, 150));
    await save();
    return state.changeId === state.savedChangeId;
  }

  function showSessionExpired() {
    banner([
      "Сессия истекла. ",
      el("a", { href: `/login?next=/tests/${TEST_ID}`, target: "_blank", rel: "noopener" }, "Войдите заново в новой вкладке"),
      " — изменения на этой странице не пропадут и сохранятся, как только вы войдёте.",
    ]);
  }

  function showConflict() {
    state.conflict = true;
    setSaveStatus("error", "Не сохранено: тест изменили в другом месте");
    const overwrite = el("button", { type: "button", class: "btn btn-small" }, "Сохранить мою версию");
    overwrite.addEventListener("click", async () => {
      const res = await api("GET", `/api/tests/${TEST_ID}`);
      if (!res.ok) return;
      const fresh = await res.json();
      state.version = fresh.version;
      state.conflict = false;
      hideBanner();
      state.changeId += 1;
      save();
    });
    banner([
      "Этот тест одновременно изменили в другой вкладке или на другом компьютере. ",
      el("a", { href: location.pathname }, "Обновите страницу"),
      ", чтобы увидеть последнюю версию, или ",
      overwrite,
    ]);
  }

  window.addEventListener("beforeunload", (e) => {
    if (state.changeId !== state.savedChangeId) {
      save();
      e.preventDefault();
      e.returnValue = "";
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") save();
  });

  // --- шапка теста --------------------------------------------------------
  function initHead() {
    document.querySelectorAll("[data-field]").forEach((input) => {
      input.value = state.test[input.dataset.field] || "";
      input.addEventListener("input", () => {
        state.test[input.dataset.field] = input.value;
        markDirty();
      });
    });
    document.querySelectorAll("[data-setting]").forEach((input) => {
      const key = input.dataset.setting;
      const value = state.test.settings[key];
      if (input.type === "checkbox") input.checked = !!value;
      else input.value = value === null || value === undefined ? "" : value;
      if (input.tagName === "TEXTAREA") autoGrow(input);
      input.addEventListener(input.type === "checkbox" ? "change" : "input", () => {
        let v;
        if (input.type === "checkbox") v = input.checked;
        else if (input.type === "number") v = input.value === "" ? null : Number(input.value);
        else v = input.value;
        state.test.settings[key] = v;
        if (input.tagName === "TEXTAREA") autoGrow(input);
        markDirty();
      });
    });
    $("#expand-settings").addEventListener("click", () => {
      const details = $("#settings");
      details.open = true;
      details.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  // --- карточка вопроса ---------------------------------------------------
  const listEl = $("#questions");

  function iconButton(role, icon, label, extra = {}) {
    return el("button", { type: "button", class: "icon-btn" + (extra.danger ? " danger" : ""), "data-role": role,
      "aria-label": label, title: label, html: ICONS[icon], disabled: extra.disabled });
  }

  function removeButton(role, index, label) {
    return el("button", { type: "button", class: "icon-btn danger", "data-role": role, "data-index": index,
      "aria-label": label, title: label, html: ICONS.x });
  }

  function renderChoice(q) {
    const single = q.type === "single";
    const wrap = el("div", { class: "q-answers" });
    wrap.append(el("div", { class: "answers-hint" },
      single ? "Нажмите на кружок с буквой, чтобы отметить правильный ответ."
        : "Отметьте все правильные ответы. Неверно выбранные варианты снижают балл."));
    const group = el("div", { role: single ? "radiogroup" : "group", "aria-label": "Варианты ответа" });
    q.options.forEach((opt, i) => {
      group.append(el("div", { class: "opt" + (opt.correct ? " correct" : "") },
        el("button", {
          type: "button", class: "bubble" + (single ? "" : " square"), "data-role": "opt-correct", "data-index": i,
          role: single ? "radio" : "checkbox", "aria-checked": opt.correct ? "true" : "false",
          "aria-label": `Вариант ${letter(i)} правильный`, title: "Отметить правильным",
        }, letter(i)),
        el("input", { type: "text", "data-role": "opt-text", "data-index": i, value: opt.text,
          placeholder: `Вариант ${letter(i)}`, "aria-label": `Текст варианта ${letter(i)}`, maxlength: 20000 }),
        removeButton("opt-remove", i, `Удалить вариант ${letter(i)}`),
      ));
    });
    wrap.append(group, el("div", { class: "answers-footer" },
      el("button", { type: "button", class: "add-row", "data-role": "opt-add" }, "+ Добавить вариант"),
      el("label", { class: "check" }, el("input", { type: "checkbox", "data-role": "shuffle", checked: q.shuffle }),
        "Перемешивать варианты"),
    ));
    return wrap;
  }

  function renderTrueFalse(q) {
    const choice = (value, label) => el("button", {
      type: "button", class: "tf-choice", role: "radio", "data-role": "tf", "data-value": value ? "1" : "0",
      "aria-checked": q.correct === value ? "true" : "false",
    }, el("span", { class: "bubble", "aria-hidden": "true" }), label);
    return el("div", { class: "q-answers" },
      el("div", { class: "answers-hint" }, "Какое утверждение правильное?"),
      el("div", { class: "tf", role: "radiogroup", "aria-label": "Правильный ответ" },
        choice(true, "Верно"), choice(false, "Неверно")));
  }

  function renderShort(q) {
    const wrap = el("div", { class: "q-answers" },
      el("div", { class: "answers-hint" }, "Студент вводит ответ сам. Укажите все варианты написания, которые нужно засчитать."));
    const list = el("div", { class: "row-list" });
    q.answers.forEach((a, i) => {
      list.append(el("div", { class: "row" },
        el("input", { type: "text", "data-role": "ans-text", "data-index": i, value: a, maxlength: 1000,
          placeholder: i === 0 ? "Правильный ответ" : "Ещё вариант написания", "aria-label": `Правильный ответ ${i + 1}` }),
        removeButton("ans-remove", i, "Удалить вариант ответа")));
    });
    wrap.append(list, el("div", { class: "answers-footer" },
      el("button", { type: "button", class: "add-row", "data-role": "ans-add" }, "+ Ещё вариант написания"),
      el("label", { class: "check" }, el("input", { type: "checkbox", "data-role": "case", checked: q.case_sensitive }),
        "Учитывать заглавные и строчные буквы")));
    return wrap;
  }

  function renderNumerical(q) {
    return el("div", { class: "q-answers" },
      el("div", { class: "numeric" },
        el("label", { class: "field" }, el("span", {}, "Правильный ответ"),
          el("input", { type: "text", inputmode: "decimal", "data-role": "number", value: q.number, placeholder: "Например, 9,8" })),
        el("label", { class: "field" }, el("span", {}, "Допустимая погрешность ±"),
          el("input", { type: "text", inputmode: "decimal", "data-role": "tolerance", value: q.tolerance, placeholder: "0" }))),
      el("div", { class: "hint" }, "Например, ответ 9,8 с погрешностью 0,1 засчитает всё от 9,7 до 9,9."));
  }

  function renderMatching(q) {
    const wrap = el("div", { class: "q-answers" },
      el("div", { class: "answers-hint" }, "Студент сопоставляет каждому пункту слева ответ справа. Ответы будут перемешаны."),
      el("div", { class: "pair-head", "aria-hidden": "true" }, el("span", {}, "Пункт"), el("span"), el("span", {}, "Его ответ"), el("span")));
    q.pairs.forEach((p, i) => {
      wrap.append(el("div", { class: "pair" },
        el("input", { type: "text", "data-role": "pair-left", "data-index": i, value: p.left, maxlength: 20000,
          placeholder: `Пункт ${i + 1}`, "aria-label": `Пункт ${i + 1}` }),
        el("span", { class: "dash", "aria-hidden": "true" }, "—"),
        el("input", { type: "text", "data-role": "pair-right", "data-index": i, value: p.right, maxlength: 20000,
          placeholder: "Ответ", "aria-label": `Ответ к пункту ${i + 1}` }),
        removeButton("pair-remove", i, `Удалить пару ${i + 1}`)));
    });
    wrap.append(el("div", { class: "answers-footer" },
      el("button", { type: "button", class: "add-row", "data-role": "pair-add" }, "+ Добавить пару")));
    const extra = el("div", { class: "sub-block" },
      el("div", { class: "answers-hint" }, "Лишние ответы — необязательно. Это варианты справа, у которых нет пары: так сложнее угадать."));
    const list = el("div", { class: "row-list" });
    q.distractors.forEach((d, i) => {
      list.append(el("div", { class: "row" },
        el("input", { type: "text", "data-role": "distr-text", "data-index": i, value: d, maxlength: 20000,
          placeholder: "Лишний ответ", "aria-label": `Лишний ответ ${i + 1}` }),
        removeButton("distr-remove", i, "Удалить лишний ответ")));
    });
    extra.append(list, el("button", { type: "button", class: "add-row", "data-role": "distr-add" }, "+ Лишний ответ"));
    wrap.append(extra);
    return wrap;
  }

  function renderImage(q) {
    const box = el("div", { class: "q-image" });
    if (q.image) {
      box.append(
        el("img", { src: `/images/${q.image}`, alt: "Картинка к вопросу" }),
        el("button", { type: "button", class: "add-row", "data-role": "image-remove" }, "Убрать картинку"));
    } else {
      const input = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", "data-role": "image-file" });
      box.append(el("label", { class: "file-label" }, input, "+ Добавить картинку"),
        el("span", { class: "hint" }, "или вставьте её в текст вопроса через Ctrl+V"));
    }
    return box;
  }

  function renderAnswers(q) {
    switch (q.type) {
      case "single":
      case "multiple": return renderChoice(q);
      case "truefalse": return renderTrueFalse(q);
      case "short": return renderShort(q);
      case "numerical": return renderNumerical(q);
      case "matching": return renderMatching(q);
      default: return el("div");
    }
  }

  function renderQuestion(q, index) {
    const total = state.test.questions.length;
    const select = el("select", { "data-role": "type", "aria-label": "Тип вопроса" });
    for (const [key, label] of Object.entries(TYPES)) {
      const option = el("option", { value: key }, label);
      if (key === q.type) option.selected = true;
      select.append(option);
    }
    const text = el("textarea", { class: "q-text", "data-role": "text", rows: 3, maxlength: 20000,
      placeholder: "Текст вопроса", "aria-label": `Текст вопроса ${index + 1}` });
    text.value = q.text;

    const card = el("li", { class: "q", id: `q-${q.id}`, dataset: { id: q.id } },
      el("div", { class: "q-margin" }, el("span", { class: "q-num", "aria-hidden": "true" }, String(index + 1))),
      el("div", { class: "q-body" },
        el("h3", { class: "visually-hidden" }, `Вопрос ${index + 1}`),
        el("div", { class: "q-top" },
          select,
          el("label", { class: "q-points" }, "Баллов",
            el("input", { type: "number", min: "0.5", max: "100", step: "0.5", "data-role": "points", value: q.points,
              "aria-label": "Баллов за вопрос" })),
          el("div", { class: "q-actions" },
            iconButton("move-up", "up", "Переместить выше", { disabled: index === 0 }),
            iconButton("move-down", "down", "Переместить ниже", { disabled: index === total - 1 }),
            iconButton("copy", "copy", "Сделать копию вопроса"),
            iconButton("delete", "trash", "Удалить вопрос", { danger: true }))),
        text,
        renderImage(q),
        renderAnswers(q),
        el("ul", { class: "issues" })));
    requestAnimationFrame(() => autoGrow(text));
    return card;
  }

  function renderAll() {
    listEl.replaceChildren(...state.test.questions.map((q, i) => renderQuestion(q, i)));
    applyIssues();
    renderSide();
  }

  function rerender(q, focusSelector) {
    const index = state.test.questions.indexOf(q);
    const old = document.getElementById(`q-${q.id}`);
    const card = renderQuestion(q, index);
    if (old) old.replaceWith(card);
    applyIssues();
    if (focusSelector) {
      const target = card.querySelector(focusSelector);
      if (target) {
        target.focus();
        if (target.setSelectionRange && typeof target.value === "string") {
          const end = target.value.length;
          target.setSelectionRange(end, end);
        }
      }
    }
    return card;
  }

  function flashCard(id) {
    const card = document.getElementById(`q-${id}`);
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
  }

  // --- ошибки и боковая панель -------------------------------------------
  function visibleIssues() {
    return state.issues.filter((i) => !i.question || !state.fresh.has(i.question));
  }

  function applyIssues() {
    const byQuestion = new Map();
    const testLevel = [];
    for (const issue of visibleIssues()) {
      if (issue.question) {
        if (!byQuestion.has(issue.question)) byQuestion.set(issue.question, []);
        byQuestion.get(issue.question).push(issue);
      } else if (!issue.message.startsWith("В тесте нет")) {
        testLevel.push(issue);
      }
    }
    for (const card of listEl.children) {
      const issues = byQuestion.get(card.dataset.id) || [];
      card.classList.toggle("has-error", issues.some((i) => i.level === "error"));
      card.querySelector(".issues").replaceChildren(
        ...issues.map((i) => el("li", { class: i.level }, i.message)));
    }
    $("#test-issues").replaceChildren(...testLevel.map((i) => el("li", { class: i.level }, i.message)));
  }

  function renderSide() {
    const qs = state.test.questions;
    $("#stat-questions").textContent = qs.length;
    const points = qs.reduce((sum, q) => sum + (Number(q.points) || 0), 0);
    $("#stat-points").textContent = Number.isInteger(points) ? points : points.toFixed(1).replace(".", ",");

    const indexOf = new Map(qs.map((q, i) => [q.id, i]));
    const issues = visibleIssues();
    const errors = issues.filter((i) => i.level === "error");
    const warnings = issues.filter((i) => i.level === "warning");
    const box = $("#problems");
    if (!qs.length) {
      box.replaceChildren(el("p", { class: "hint" }, "Добавьте первый вопрос кнопками под формой."));
    } else if (!issues.length) {
      box.replaceChildren(el("p", { class: "problems-ok" }, "Ошибок нет"));
    } else {
      const item = (i) => {
        const btn = el("button", { type: "button" },
          i.question ? el("span", { class: "q-ref" }, `№${(indexOf.get(i.question) ?? 0) + 1}`) : null, i.message);
        btn.addEventListener("click", () => {
          if (i.question) flashCard(i.question);
          else $(".test-head").scrollIntoView({ behavior: "smooth" });
        });
        return el("li", {}, btn);
      };
      const nodes = [];
      if (errors.length) {
        nodes.push(el("h3", {}, `${errors.length} ${plural(errors.length, "ошибка", "ошибки", "ошибок")}`),
          el("ul", {}, errors.map(item)));
      }
      if (warnings.length) {
        nodes.push(el("h3", { class: "warn-title" }, `${warnings.length} ${plural(warnings.length, "замечание", "замечания", "замечаний")}`),
          el("ul", {}, warnings.map(item)));
      }
      box.replaceChildren(el("div", { class: "problems" }, nodes));
    }

    const readyBox = $("#ready-box");
    const hasErrors = state.issues.some((i) => i.level === "error");
    if (state.status === "ready") {
      const back = el("button", { type: "button", class: "btn" }, "Вернуть в черновики");
      back.addEventListener("click", () => setReady(false));
      readyBox.replaceChildren(
        el("div", { class: "ready-note" }, "Тест отмечен готовым — администратор загрузит его в Moodle. Править его по-прежнему можно."),
        back);
    } else {
      const ready = el("button", { type: "button", class: "btn btn-primary", disabled: hasErrors || !qs.length }, "Отметить тест готовым");
      ready.addEventListener("click", () => setReady(true));
      let hint = "Когда закончите, отметьте тест готовым, чтобы администратор это увидел.";
      if (!qs.length) hint = "Добавьте хотя бы один вопрос.";
      else if (hasErrors) hint = "Сначала исправьте ошибки — список выше.";
      readyBox.replaceChildren(ready, el("p", { class: "hint" }, hint));
    }
  }

  async function setReady(ready) {
    if (!(await flush())) {
      toast("Не удалось сохранить изменения — попробуйте ещё раз");
      return;
    }
    const res = await api("POST", `/api/tests/${TEST_ID}/status`, { ready });
    if (!res.ok) {
      toast(await errorText(res, "Не получилось изменить состояние теста"));
      return;
    }
    const data = await res.json();
    state.status = data.status;
    state.version = data.version;
    state.updated = data.updated_at;
    setSaveStatus("saved");
    renderSide();
    toast(ready ? "Тест отмечен готовым" : "Тест возвращён в черновики");
  }

  // --- действия с вопросами ----------------------------------------------
  function addQuestions(questions, { fresh = true, focus = true } = {}) {
    for (const q of questions) {
      state.test.questions.push(q);
      if (fresh) state.fresh.add(q.id);
    }
    renderAll();
    markDirty();
    const first = questions[0];
    if (first) {
      flashCard(first.id);
      if (focus) {
        const t = document.querySelector(`#q-${first.id} .q-text`);
        if (t) t.focus({ preventScroll: true });
      }
    }
  }

  function moveQuestion(index, delta) {
    const qs = state.test.questions;
    const target = index + delta;
    if (target < 0 || target >= qs.length) return;
    [qs[index], qs[target]] = [qs[target], qs[index]];
    const id = qs[target].id;
    renderAll();
    markDirty();
    const btn = document.querySelector(`#q-${id} [data-role="${delta < 0 ? "move-up" : "move-down"}"]`);
    const card = document.getElementById(`q-${id}`);
    if (card) card.scrollIntoView({ block: "nearest" });
    if (btn && !btn.disabled) btn.focus();
  }

  function deleteQuestion(index) {
    const [removed] = state.test.questions.splice(index, 1);
    state.fresh.delete(removed.id);
    renderAll();
    markDirty();
    toast(`Вопрос ${index + 1} удалён`, "Вернуть", () => {
      state.test.questions.splice(Math.min(index, state.test.questions.length), 0, removed);
      renderAll();
      markDirty();
      flashCard(removed.id);
    });
  }

  function copyQuestion(index) {
    const copy = JSON.parse(JSON.stringify(state.test.questions[index]));
    copy.id = uid();
    state.test.questions.splice(index + 1, 0, copy);
    renderAll();
    markDirty();
    flashCard(copy.id);
  }

  async function uploadImage(q, file) {
    if (!file || !/^image\/(png|jpeg|gif|webp)$/.test(file.type)) {
      toast("Подходят картинки PNG, JPEG, GIF или WebP");
      return;
    }
    if (file.size > MAX_IMAGE) {
      toast("Картинка больше 5 МБ — уменьшите её и попробуйте снова");
      return;
    }
    const card = document.getElementById(`q-${q.id}`);
    const box = card && card.querySelector(".q-image");
    if (box) box.replaceChildren(el("span", { class: "uploading" }, "Загружаю картинку…"));
    const form = new FormData();
    form.append("file", file, file.name || "image.png");
    try {
      const res = await fetch(`/api/tests/${TEST_ID}/images`, {
        method: "POST", body: form, headers: { "X-Requested-With": "fetch" },
      });
      if (!res.ok) {
        toast(await errorText(res, "Не удалось загрузить картинку"));
        rerender(q);
        return;
      }
      const data = await res.json();
      q.image = data.id;
      rerender(q);
      markDirty();
    } catch (_) {
      toast("Нет связи с сервером — картинка не загружена");
      rerender(q);
    }
  }

  // --- обработка событий в списке вопросов --------------------------------
  listEl.addEventListener("input", (e) => {
    const t = e.target;
    const found = findQuestion(t);
    if (!found) return;
    const { q } = found;
    const i = Number(t.dataset.index);
    switch (t.dataset.role) {
      case "text": q.text = t.value; autoGrow(t); break;
      case "points": q.points = t.value === "" ? 0 : Number(t.value); break;
      case "opt-text": q.options[i].text = t.value; break;
      case "ans-text": q.answers[i] = t.value; break;
      case "number": q.number = t.value; break;
      case "tolerance": q.tolerance = t.value; break;
      case "pair-left": q.pairs[i].left = t.value; break;
      case "pair-right": q.pairs[i].right = t.value; break;
      case "distr-text": q.distractors[i] = t.value; break;
      default: return;
    }
    markDirty();
  });

  listEl.addEventListener("change", (e) => {
    const t = e.target;
    const found = findQuestion(t);
    if (!found) return;
    const { q } = found;
    switch (t.dataset.role) {
      case "type": {
        const old = q.type;
        q.type = t.value;
        // Переход «один ответ» ↔ «несколько»: лишние отметки снимаем, чтобы не было ошибки.
        if (q.type === "single" && old === "multiple") {
          let seen = false;
          for (const o of q.options) { if (o.correct && !seen) seen = true; else o.correct = false; }
        }
        rerender(q, '[data-role="type"]');
        break;
      }
      case "shuffle": q.shuffle = t.checked; break;
      case "case": q.case_sensitive = t.checked; break;
      case "image-file": uploadImage(q, t.files[0]); return;
      default: return;
    }
    markDirty();
  });

  listEl.addEventListener("click", (e) => {
    const t = e.target.closest("[data-role]");
    if (!t || t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA") return;
    const found = findQuestion(t);
    if (!found) return;
    const { q, index } = found;
    const i = Number(t.dataset.index);
    switch (t.dataset.role) {
      case "move-up": moveQuestion(index, -1); return;
      case "move-down": moveQuestion(index, 1); return;
      case "copy": copyQuestion(index); return;
      case "delete": deleteQuestion(index); return;
      case "image-remove": q.image = null; rerender(q, ".file-label"); break;
      case "opt-correct":
        if (q.type === "single") q.options.forEach((o, k) => { o.correct = k === i; });
        else q.options[i].correct = !q.options[i].correct;
        rerender(q, `[data-role="opt-correct"][data-index="${i}"]`);
        break;
      case "opt-add":
        q.options.push({ text: "", correct: false });
        rerender(q, `[data-role="opt-text"][data-index="${q.options.length - 1}"]`);
        break;
      case "opt-remove":
        q.options.splice(i, 1);
        rerender(q, `[data-role="opt-text"][data-index="${Math.max(0, i - 1)}"]`);
        break;
      case "tf":
        q.correct = t.dataset.value === "1";
        rerender(q, `[data-role="tf"][data-value="${t.dataset.value}"]`);
        break;
      case "ans-add":
        q.answers.push("");
        rerender(q, `[data-role="ans-text"][data-index="${q.answers.length - 1}"]`);
        break;
      case "ans-remove":
        q.answers.splice(i, 1);
        if (!q.answers.length) q.answers.push("");
        rerender(q, `[data-role="ans-text"][data-index="${Math.max(0, i - 1)}"]`);
        break;
      case "pair-add":
        q.pairs.push({ left: "", right: "" });
        rerender(q, `[data-role="pair-left"][data-index="${q.pairs.length - 1}"]`);
        break;
      case "pair-remove":
        q.pairs.splice(i, 1);
        rerender(q, `[data-role="pair-left"][data-index="${Math.max(0, i - 1)}"]`);
        break;
      case "distr-add":
        q.distractors.push("");
        rerender(q, `[data-role="distr-text"][data-index="${q.distractors.length - 1}"]`);
        break;
      case "distr-remove":
        q.distractors.splice(i, 1);
        rerender(q, `[data-role="distr-text"][data-index="${Math.max(0, i - 1)}"]`);
        break;
      default: return;
    }
    markDirty();
  });

  // Enter в варианте — новый вариант ниже; Backspace в пустом — удалить и вернуться к предыдущему.
  listEl.addEventListener("keydown", (e) => {
    const t = e.target;
    const lists = { "opt-text": "options", "ans-text": "answers", "distr-text": "distractors", "pair-right": "pairs" };
    const key = lists[t.dataset.role];
    if (!key || e.isComposing) return;
    const found = findQuestion(t);
    if (!found) return;
    const { q } = found;
    const i = Number(t.dataset.index);
    const blank = { options: () => ({ text: "", correct: false }), answers: () => "", distractors: () => "",
      pairs: () => ({ left: "", right: "" }) }[key];
    const focusRole = key === "pairs" ? "pair-left" : t.dataset.role;
    if (e.key === "Enter") {
      e.preventDefault();
      q[key].splice(i + 1, 0, blank());
      rerender(q, `[data-role="${focusRole}"][data-index="${i + 1}"]`);
      markDirty();
    } else if (e.key === "Backspace" && t.value === "" && key !== "pairs" && q[key].length > 1) {
      e.preventDefault();
      q[key].splice(i, 1);
      rerender(q, `[data-role="${t.dataset.role}"][data-index="${Math.max(0, i - 1)}"]`);
      markDirty();
    }
  });

  // Вставка картинки из буфера (скриншот) прямо в текст вопроса.
  listEl.addEventListener("paste", (e) => {
    const t = e.target;
    if (t.dataset.role !== "text" || !e.clipboardData) return;
    // Word кладёт в буфер и текст, и картинку-снимок текста: если есть текст — вставляем текст.
    if (e.clipboardData.getData("text/plain")) return;
    const file = Array.from(e.clipboardData.files || []).find((f) => f.type.startsWith("image/"));
    if (!file) return;
    e.preventDefault();
    const found = findQuestion(t);
    if (found) uploadImage(found.q, file);
  });

  listEl.addEventListener("dragover", (e) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault();
  });
  listEl.addEventListener("drop", (e) => {
    const file = e.dataTransfer && Array.from(e.dataTransfer.files).find((f) => f.type.startsWith("image/"));
    if (!file) return;
    const found = findQuestion(e.target);
    if (!found) return;
    e.preventDefault();
    uploadImage(found.q, file);
  });

  // Ушли с нового вопроса — теперь можно показывать его ошибки.
  listEl.addEventListener("focusout", (e) => {
    const card = e.target.closest(".q");
    if (!card || !state.fresh.has(card.dataset.id)) return;
    if (e.relatedTarget && card.contains(e.relatedTarget)) return;
    setTimeout(() => {
      if (card.isConnected && card.contains(document.activeElement)) return;
      state.fresh.delete(card.dataset.id);
      applyIssues();
      renderSide();
    }, 0);
  });

  // --- кнопки «добавить» ---------------------------------------------------
  document.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => addQuestions([blankQuestion(btn.dataset.add)]));
  });

  // --- удаление теста ------------------------------------------------------
  $("#delete-test").addEventListener("click", async () => {
    const name = state.test.discipline || "без названия";
    if (!confirm(`Удалить тест «${name}»? Администратор сможет его восстановить.`)) return;
    await flush();
    const res = await api("DELETE", `/api/tests/${TEST_ID}`);
    if (res.ok) {
      state.savedChangeId = state.changeId;
      location.href = "/";
    } else {
      toast(await errorText(res, "Не удалось удалить тест"));
    }
  });

  // --- вставка текстом -----------------------------------------------------
  const dialog = $("#paste-dialog");
  const pasteText = $("#paste-text");
  const pastePreview = $("#paste-preview");
  const pasteExample = pastePreview.innerHTML;
  const pasteAdd = $("#paste-add");
  let parsed = null;
  let parseTimer = null;
  let parseSeq = 0;

  $("#open-paste").addEventListener("click", () => {
    dialog.showModal();
    pasteText.focus();
  });
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));

  pasteText.addEventListener("input", () => {
    clearTimeout(parseTimer);
    parseTimer = setTimeout(parsePaste, 400);
  });

  async function parsePaste() {
    const text = pasteText.value;
    const seq = ++parseSeq;
    if (!text.trim()) {
      parsed = null;
      pastePreview.innerHTML = pasteExample;
      updatePasteButton();
      return;
    }
    let data;
    try {
      const res = await api("POST", "/api/parse", { text });
      if (!res.ok) {
        pastePreview.replaceChildren(el("p", { class: "alert alert-error" }, await errorText(res, "Не удалось разобрать текст")));
        return;
      }
      data = await res.json();
    } catch (_) {
      pastePreview.replaceChildren(el("p", { class: "alert alert-error" }, "Нет связи с сервером"));
      return;
    }
    if (seq !== parseSeq) return;
    parsed = data;
    renderPastePreview();
    updatePasteButton();
  }

  function renderPastePreview() {
    const qs = parsed.questions;
    if (!qs.length) {
      pastePreview.replaceChildren(el("p", { class: "hint" },
        "Вопросы не найдены. Пронумеруйте вопросы (1., 2., …) или разделите их пустой строкой."));
      return;
    }
    const warn = new Map();
    for (const w of parsed.warnings) {
      if (!warn.has(w.index)) warn.set(w.index, []);
      warn.get(w.index).push(w.message);
    }
    const items = qs.map((q, idx) => {
      const li = el("li", {}, el("div", {}, q.text || "(без текста)"),
        el("div", { class: "hint" }, TYPES[q.type]));
      if (q.type === "single" || q.type === "multiple") {
        li.append(el("ul", { class: "opts" }, q.options.map((o, k) =>
          el("li", { class: o.correct ? "ok" : "" }, `${letter(k)}) ${o.text}${o.correct ? "  ✓" : ""}`))));
      } else if (q.type === "short") {
        li.append(el("ul", { class: "opts" }, el("li", { class: "ok" }, "Ответ: " + q.answers.join("; "))));
      } else if (q.type === "numerical") {
        li.append(el("ul", { class: "opts" }, el("li", { class: "ok" }, "Ответ: " + q.number)));
      }
      for (const m of warn.get(idx) || []) li.append(el("div", { class: "warn" }, m));
      return li;
    });
    const count = qs.length;
    const warned = warn.size;
    pastePreview.replaceChildren(
      el("p", {}, el("strong", {}, `Найдено ${count} ${plural(count, "вопрос", "вопроса", "вопросов")}`),
        warned ? ` — у ${warned} ${plural(warned, "есть замечание", "есть замечания", "есть замечания")}, их можно поправить после добавления` : ""),
      el("ol", {}, items));
  }

  function updatePasteButton() {
    const n = parsed ? parsed.questions.length : 0;
    pasteAdd.disabled = n === 0;
    pasteAdd.textContent = n ? `Добавить ${n} ${plural(n, "вопрос", "вопроса", "вопросов")}` : "Добавить вопросы";
  }

  pasteAdd.addEventListener("click", () => {
    if (!parsed || !parsed.questions.length) return;
    const qs = parsed.questions;
    dialog.close();
    addQuestions(qs, { fresh: false, focus: false });
    toast(`Добавлено ${qs.length} ${plural(qs.length, "вопрос", "вопроса", "вопросов")}`);
    pasteText.value = "";
    parsed = null;
    pastePreview.innerHTML = pasteExample;
    updatePasteButton();
  });

  // --- старт ---------------------------------------------------------------
  initHead();
  renderAll();
  setSaveStatus("saved");
})();
