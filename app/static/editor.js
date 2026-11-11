/* Редактор теста: вопросы, автосохранение, картинки, вставка из Word, «Готово». */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  // Если снизу не хватает места, меню открывается вверх.
  document.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("dropdown") || !d.open) return;
    const menu = d.querySelector(".menu");
    if (!menu) return;
    menu.classList.remove("menu-up");
    const r = menu.getBoundingClientRect();
    const above = d.getBoundingClientRect().top;
    if (r.bottom > window.innerHeight - 8 && above > window.innerHeight - r.top) menu.classList.add("menu-up");
  }, true);

  const editorEl = $("#editor");
  if (!editorEl) return;

  const TEST_ID = editorEl.dataset.testId;
  const initial = JSON.parse($("#initial-data").textContent);
  const LETTERS = "абвгдежзиклмнопрстуфхцчшщэюя";
  const MAX_IMAGE = 5 * 1024 * 1024;

  const TYPE_LABEL = {
    truefalse: "Верно или неверно",
    short: "Ответ словом",
    numerical: "Ответ числом",
    matching: "Соответствие",
  };
  const TYPE_CHOICES = [["choice", "Выбор ответа"], ...Object.entries(TYPE_LABEL)];

  const ICONS = {
    up: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15V5M5.5 9.5 10 5l4.5 4.5"/></svg>',
    down: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 5v10M5.5 10.5 10 15l4.5-4.5"/></svg>',
    dots: '<svg viewBox="0 0 20 20" fill="currentColor"><circle cx="4.5" cy="10" r="1.6"/><circle cx="10" cy="10" r="1.6"/><circle cx="15.5" cy="10" r="1.6"/></svg>',
    trash: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h12M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6"/></svg>',
    x: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg>',
    check: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>',
    image: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><rect x="3" y="4" width="14" height="12" rx="2"/><circle cx="7.5" cy="8.5" r="1.5"/><path d="M3.5 14l4-4 3 3 2-2 4 4"/></svg>',
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
    changeId: 0,
    savedChangeId: 0,
    saving: false,
    conflict: false,
    timer: null,
    errorCursor: -1,
    fresh: new Set(), // новые вопросы: ошибки покажем, когда человек уйдёт с вопроса
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
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const letter = (i) => (i < LETTERS.length ? LETTERS[i] : String(i + 1));
  const isChoice = (q) => q.type === "single" || q.type === "multiple";

  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function fmtNumber(n) {
    return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10).replace(".", ",");
  }

  function blankQuestion(type) {
    return {
      id: uid(), type, text: "", image: null, points: 1, shuffle: true,
      options: [0, 1, 2, 3].map(() => ({ text: "", correct: false })),
      correct: true, answers: [""], case_sensitive: false, number: "", tolerance: "",
      pairs: [0, 1, 2].map(() => ({ left: "", right: "" })), distractors: [],
    };
  }

  // Один верный — обычный выбор, несколько — выбор нескольких. Человеку не надо об этом думать.
  function syncChoiceType(q) {
    if (!isChoice(q)) return;
    q.type = q.options.filter((o) => o.correct).length > 1 ? "multiple" : "single";
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
    toastTimer = setTimeout(hideToast, actionLabel ? 8000 : 3500);
  }
  function hideToast() { toastEl.classList.remove("show"); }

  const bannerEl = $("#banner");
  function banner(nodes) {
    bannerEl.replaceChildren(...nodes);
    bannerEl.classList.add("show");
  }
  function hideBanner() { bannerEl.classList.remove("show"); }

  // --- сохранение ---------------------------------------------------------
  const saveEl = $("#save-status");
  function setSave(kind, text) {
    const labels = { saved: "Сохранено", saving: "Сохраняю…", error: "Нет связи, повторяю…" };
    saveEl.dataset.state = kind;
    saveEl.textContent = text || labels[kind];
  }

  function markDirty() {
    state.changeId += 1;
    setSave("saving");
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
    setSave("saving");
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
        setSave("error", "Нужно войти заново");
        return;
      }
      if (!res.ok) {
        retryIn = 5000;
        setSave("error", await errorText(res, "Не сохранилось, повторяю…"));
        return;
      }
      const data = await res.json();
      state.version = data.version;
      state.status = data.status;
      state.issues = data.issues;
      state.savedChangeId = changeId;
      hideBanner();
      applyIssues();
      setSave(state.changeId === changeId ? "saved" : "saving");
    } catch (_) {
      retryIn = 5000;
      setSave("error");
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
    for (let i = 0; i < 30 && state.saving; i++) await new Promise((r) => setTimeout(r, 150));
    await save();
    return state.changeId === state.savedChangeId;
  }

  function showSessionExpired() {
    banner([
      "Вы вышли из системы. ",
      el("a", { href: `/login?next=/tests/${TEST_ID}`, target: "_blank", rel: "noopener" }, "Войдите снова"),
      " в новой вкладке — всё, что вы ввели здесь, сохранится.",
    ]);
  }

  function showConflict() {
    state.conflict = true;
    setSave("error", "Не сохранено");
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
      "Этот тест сейчас правят ещё в одном окне. ",
      el("a", { href: location.pathname }, "Обновите страницу"),
      " или ",
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
  }

  // --- карточка вопроса ---------------------------------------------------
  const listEl = $("#questions");

  function iconButton(role, icon, label, extra = {}) {
    return el("button", {
      type: "button", class: "icon-btn" + (extra.danger ? " danger" : ""), "data-role": role,
      "aria-label": label, title: label, html: ICONS[icon], disabled: extra.disabled,
    });
  }

  function removeButton(role, index, label) {
    return el("button", { type: "button", class: "icon-btn remove", "data-role": role, "data-index": index,
      "aria-label": label, title: label, html: ICONS.x });
  }

  function addRowButton(role, label) {
    return el("button", { type: "button", class: "add-row", "data-role": role }, "+ " + label);
  }

  function renderChoice(q) {
    const wrap = el("div", { class: "answers" });
    q.options.forEach((o, i) => {
      wrap.append(el("div", { class: "opt" + (o.correct ? " is-correct" : "") },
        el("span", { class: "opt-letter", "aria-hidden": "true" }, letter(i) + ")"),
        el("input", { type: "text", "data-role": "opt-text", "data-index": i, value: o.text, maxlength: 20000,
          placeholder: "Вариант ответа", "aria-label": `Вариант ${letter(i)}` }),
        el("button", { type: "button", class: "mark", "data-role": "opt-correct", "data-index": i,
          "aria-pressed": o.correct ? "true" : "false", title: o.correct ? "Снять отметку" : "Отметить верным" },
          el("span", { class: "mark-box", "aria-hidden": "true", html: ICONS.check }), "Верный"),
        removeButton("opt-remove", i, `Удалить вариант ${letter(i)}`)));
    });
    const footer = el("div", { class: "answers-foot" }, addRowButton("opt-add", "Ещё вариант"));
    if (q.type === "multiple") footer.append(el("span", { class: "note" }, "Несколько верных — студент выбирает все"));
    wrap.append(footer);
    return wrap;
  }

  function renderTrueFalse(q) {
    const seg = (value, label) => el("button", {
      type: "button", class: "seg-btn", "data-role": "tf", "data-value": value ? "1" : "0",
      role: "radio", "aria-checked": q.correct === value ? "true" : "false",
    }, label);
    return el("div", { class: "answers" },
      el("div", { class: "inline-field" }, el("span", { class: "label" }, "Верный ответ:"),
        el("div", { class: "seg", role: "radiogroup", "aria-label": "Верный ответ" }, seg(true, "Верно"), seg(false, "Неверно"))));
  }

  function renderShort(q) {
    const wrap = el("div", { class: "answers" });
    q.answers.forEach((a, i) => {
      wrap.append(el("div", { class: "row" },
        el("input", { type: "text", "data-role": "ans-text", "data-index": i, value: a, maxlength: 1000,
          placeholder: i === 0 ? "Правильный ответ" : "Другое написание", "aria-label": `Правильный ответ ${i + 1}` }),
        removeButton("ans-remove", i, "Удалить")));
    });
    wrap.append(el("div", { class: "answers-foot" }, addRowButton("ans-add", "Другое написание ответа")));
    return wrap;
  }

  function renderNumerical(q) {
    return el("div", { class: "answers" },
      el("div", { class: "inline-field" },
        el("label", { class: "inline" }, el("span", { class: "label" }, "Ответ"),
          el("input", { type: "text", inputmode: "decimal", "data-role": "number", value: q.number, placeholder: "9,8", class: "num-input" })),
        el("label", { class: "inline" }, el("span", { class: "label" }, "±"),
          el("input", { type: "text", inputmode: "decimal", "data-role": "tolerance", value: q.tolerance, placeholder: "0", class: "num-input",
            title: "Допустимая погрешность" }))));
  }

  function renderMatching(q) {
    const wrap = el("div", { class: "answers" },
      el("div", { class: "pair pair-head", "aria-hidden": "true" }, el("span", {}, "Слева"), el("span", { class: "dash" }), el("span", {}, "Справа (ответ)"), el("span")));
    q.pairs.forEach((p, i) => {
      wrap.append(el("div", { class: "pair" },
        el("input", { type: "text", "data-role": "pair-left", "data-index": i, value: p.left, maxlength: 20000,
          placeholder: "Например, «Сила»", "aria-label": `Пара ${i + 1}, слева` }),
        el("span", { class: "dash", "aria-hidden": "true" }, "—"),
        el("input", { type: "text", "data-role": "pair-right", "data-index": i, value: p.right, maxlength: 20000,
          placeholder: "«Ньютон»", "aria-label": `Пара ${i + 1}, ответ` }),
        removeButton("pair-remove", i, `Удалить пару ${i + 1}`)));
    });
    q.distractors.forEach((d, i) => {
      wrap.append(el("div", { class: "pair" },
        el("span", { class: "extra-label" }, "Лишний ответ"),
        el("span", { class: "dash", "aria-hidden": "true" }),
        el("input", { type: "text", "data-role": "distr-text", "data-index": i, value: d, maxlength: 20000,
          placeholder: "Ответ без пары", "aria-label": `Лишний ответ ${i + 1}` }),
        removeButton("distr-remove", i, "Удалить лишний ответ")));
    });
    wrap.append(el("div", { class: "answers-foot" }, addRowButton("pair-add", "Ещё пара"),
      el("button", { type: "button", class: "add-row add-row-muted", "data-role": "distr-add",
        title: "Ответ справа, к которому нет пары — так сложнее угадать" }, "+ Лишний ответ")));
    return wrap;
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

  function renderImage(q) {
    if (q.image) {
      return el("div", { class: "q-image" },
        el("img", { src: `/images/${q.image}`, alt: "Картинка к вопросу" }),
        el("button", { type: "button", class: "add-row add-row-muted", "data-role": "image-remove" }, "Убрать картинку"));
    }
    return null;
  }

  function moreMenu(q) {
    const typeSelect = el("select", { "data-role": "type" });
    for (const [key, label] of TYPE_CHOICES) {
      const option = el("option", { value: key }, label);
      if (key === q.type || (key === "choice" && isChoice(q))) option.selected = true;
      typeSelect.append(option);
    }
    return el("details", { class: "dropdown more" },
      el("summary", { class: "icon-btn", "aria-label": "Ещё действия", title: "Ещё", html: ICONS.dots }),
      el("div", { class: "menu menu-right" },
        el("button", { type: "button", class: "menu-item", "data-role": "copy" }, el("b", {}, "Сделать копию")),
        el("label", { class: "menu-field" }, el("span", {}, "Тип вопроса"), typeSelect),
        el("label", { class: "menu-field" }, el("span", {}, "Баллы за вопрос"),
          el("input", { type: "number", min: "0.5", max: "100", step: "0.5", "data-role": "points", value: q.points })),
        isChoice(q) ? el("label", { class: "check menu-check" },
          el("input", { type: "checkbox", "data-role": "shuffle", checked: q.shuffle }), "Перемешивать варианты") : null,
        q.type === "short" ? el("label", { class: "check menu-check" },
          el("input", { type: "checkbox", "data-role": "case", checked: q.case_sensitive }), "Учитывать заглавные буквы") : null));
  }

  function renderQuestion(q, index) {
    const total = state.test.questions.length;
    const text = el("textarea", { class: "q-text", "data-role": "text", rows: 2, maxlength: 20000,
      placeholder: "Текст вопроса", "aria-label": `Текст вопроса ${index + 1}` });
    text.value = q.text;
    const chips = [];
    if (!isChoice(q)) chips.push(el("span", { class: "chip" }, TYPE_LABEL[q.type]));
    if (Number(q.points) !== 1) {
      const p = Number(q.points) || 0;
      chips.push(el("span", { class: "chip" }, `${fmtNumber(p)} ${plural(Math.ceil(p), "балл", "балла", "баллов")}`));
    }
    const imageInput = el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp",
      "data-role": "image-file", class: "visually-hidden", tabindex: "-1" });
    const card = el("li", { class: "q", id: `q-${q.id}`, dataset: { id: q.id } },
      el("div", { class: "q-head" },
        el("h3", { class: "q-title" }, `Вопрос ${index + 1}`),
        chips,
        el("div", { class: "q-tools" },
          q.image ? null : el("button", { type: "button", class: "icon-btn", "data-role": "image-pick",
            "aria-label": "Добавить картинку", title: "Добавить картинку", html: ICONS.image }),
          iconButton("move-up", "up", "Выше", { disabled: index === 0 }),
          iconButton("move-down", "down", "Ниже", { disabled: index === total - 1 }),
          moreMenu(q),
          iconButton("delete", "trash", "Удалить вопрос", { danger: true }))),
      text,
      imageInput,
      renderImage(q),
      renderAnswers(q),
      el("ul", { class: "issues" }));
    requestAnimationFrame(() => autoGrow(text));
    return card;
  }

  function renderAll() {
    listEl.replaceChildren(...state.test.questions.map((q, i) => renderQuestion(q, i)));
    const empty = state.test.questions.length === 0;
    $("#add-bar").classList.toggle("is-empty", empty);
    $("#finish").hidden = empty;
    applyIssues();
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
        if (target.setSelectionRange && typeof target.value === "string" && target.type === "text") {
          const end = target.value.length;
          target.setSelectionRange(end, end);
        }
      }
    }
    return card;
  }

  function flashCard(card) {
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
  }

  // --- ошибки и верхняя панель -------------------------------------------
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
      } else if (issue.message !== "Добавьте вопросы") {
        testLevel.push(issue);
      }
    }
    for (const card of listEl.children) {
      const issues = byQuestion.get(card.dataset.id) || [];
      card.classList.toggle("has-error", issues.some((i) => i.level === "error"));
      card.querySelector(".issues").replaceChildren(...issues.map((i) => el("li", { class: i.level }, i.message)));
    }
    $("#test-issues").replaceChildren(...testLevel.map((i) => el("li", { class: i.level }, i.message)));
    $(".test-head").classList.toggle("has-error", testLevel.some((i) => i.level === "error"));
    renderBar();
  }

  function errorTargets() {
    const targets = [];
    if ($(".test-head").classList.contains("has-error")) targets.push($(".test-head"));
    targets.push(...listEl.querySelectorAll(".q.has-error"));
    return targets;
  }

  function goToNextError() {
    const targets = errorTargets();
    if (!targets.length) return false;
    state.errorCursor = (state.errorCursor + 1) % targets.length;
    const target = targets[state.errorCursor];
    flashCard(target);
    const field = target.querySelector(".q-text, .title-input");
    if (field && target.classList.contains("q") && !field.value) field.focus({ preventScroll: true });
    return true;
  }

  function renderBar() {
    const n = errorTargets().length;
    const errorsBtn = $("#bar-errors");
    errorsBtn.hidden = n === 0;
    errorsBtn.textContent = n ? `Исправить: ${n}` : "";
    errorsBtn.title = "Показать, что нужно исправить";
    const ready = state.status === "ready";
    $("#bar-ready").hidden = !ready;
    $("#done-top").hidden = ready;
    const bottom = $("#done-bottom");
    bottom.textContent = ready ? "✓ Тест готов" : "Готово — тест заполнен";
    bottom.classList.toggle("btn-done", !ready);
  }

  // --- «Готово» -----------------------------------------------------------
  async function finish() {
    if (!state.test.questions.length) {
      toast("Сначала добавьте вопросы");
      return;
    }
    state.fresh.clear();
    applyIssues();
    if (!(await flush())) {
      toast("Не получилось сохранить. Проверьте интернет и нажмите ещё раз");
      return;
    }
    if (state.issues.some((i) => i.level === "error")) {
      applyIssues();
      state.errorCursor = -1;
      goToNextError();
      toast("Исправьте то, что отмечено красным");
      return;
    }
    const res = await api("POST", `/api/tests/${TEST_ID}/status`, { ready: true });
    if (!res.ok) {
      toast(await errorText(res, "Не получилось. Попробуйте ещё раз"));
      return;
    }
    const data = await res.json();
    state.status = data.status;
    state.version = data.version;
    renderBar();
    $("#done-dialog").showModal();
  }
  $("#done-top").addEventListener("click", finish);
  $("#done-bottom").addEventListener("click", finish);
  $("#bar-errors").addEventListener("click", goToNextError);

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
      const card = document.getElementById(`q-${first.id}`);
      flashCard(card);
      if (focus && card) card.querySelector(".q-text").focus({ preventScroll: true });
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
    const card = document.getElementById(`q-${id}`);
    if (card) card.scrollIntoView({ block: "nearest" });
    const btn = card && card.querySelector(`[data-role="${delta < 0 ? "move-up" : "move-down"}"]`);
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
      flashCard(document.getElementById(`q-${removed.id}`));
    });
  }

  function copyQuestion(index) {
    const copy = JSON.parse(JSON.stringify(state.test.questions[index]));
    copy.id = uid();
    state.test.questions.splice(index + 1, 0, copy);
    renderAll();
    markDirty();
    flashCard(document.getElementById(`q-${copy.id}`));
    toast("Копия добавлена ниже");
  }

  async function uploadImage(q, file) {
    if (!file || !/^image\/(png|jpeg|gif|webp)$/.test(file.type)) {
      toast("Нужна картинка: PNG, JPG или GIF");
      return;
    }
    if (file.size > MAX_IMAGE) {
      toast("Картинка слишком большая (больше 5 МБ)");
      return;
    }
    toast("Загружаю картинку…");
    const form = new FormData();
    form.append("file", file, file.name || "image.png");
    try {
      const res = await fetch(`/api/tests/${TEST_ID}/images`, {
        method: "POST", body: form, headers: { "X-Requested-With": "fetch" },
      });
      if (!res.ok) {
        toast(await errorText(res, "Не удалось загрузить картинку"));
        return;
      }
      const data = await res.json();
      q.image = data.id;
      rerender(q);
      markDirty();
      hideToast();
    } catch (_) {
      toast("Нет связи — картинка не загрузилась");
    }
  }

  // --- события в списке вопросов -----------------------------------------
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
      case "type":
        if (t.value === "choice") {
          q.type = "single";
          syncChoiceType(q);
        } else {
          q.type = t.value;
        }
        rerender(q);
        break;
      case "points": rerender(q); break;
      case "shuffle": q.shuffle = t.checked; break;
      case "case": q.case_sensitive = t.checked; break;
      case "image-file": uploadImage(q, t.files[0]); t.value = ""; return;
      default: return;
    }
    markDirty();
  });

  listEl.addEventListener("click", (e) => {
    const t = e.target.closest("[data-role]");
    if (!t || t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA") return;
    const found = findQuestion(t);
    if (!found) return;
    const { q, index, card } = found;
    const i = Number(t.dataset.index);
    switch (t.dataset.role) {
      case "move-up": moveQuestion(index, -1); return;
      case "move-down": moveQuestion(index, 1); return;
      case "copy": copyQuestion(index); return;
      case "delete": deleteQuestion(index); return;
      case "image-pick": card.querySelector('[data-role="image-file"]').click(); return;
      case "image-remove": q.image = null; rerender(q); break;
      case "opt-correct":
        q.options[i].correct = !q.options[i].correct;
        syncChoiceType(q);
        rerender(q, `[data-role="opt-correct"][data-index="${i}"]`);
        break;
      case "opt-add":
        q.options.push({ text: "", correct: false });
        rerender(q, `[data-role="opt-text"][data-index="${q.options.length - 1}"]`);
        break;
      case "opt-remove":
        q.options.splice(i, 1);
        syncChoiceType(q);
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
        rerender(q, '[data-role="distr-add"]');
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
      syncChoiceType(q);
      rerender(q, `[data-role="${t.dataset.role}"][data-index="${Math.max(0, i - 1)}"]`);
      markDirty();
    }
  });

  // Картинка из буфера (скриншот) прямо в текст вопроса.
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
    const id = card.dataset.id;
    setTimeout(() => {
      // Карточку могли перерисовать (Enter, «Верный») — смотрим на актуальную.
      const current = document.getElementById(`q-${id}`);
      if (current && current.contains(document.activeElement)) return;
      state.fresh.delete(id);
      applyIssues();
    }, 0);
  });

  // Выпадающие меню закрываются кликом мимо и клавишей Esc.
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.dropdown[open]").forEach((d) => {
      if (!d.contains(e.target)) d.open = false;
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details.dropdown[open]").forEach((d) => {
      d.open = false;
      d.querySelector("summary").focus();
    });
  });

  // --- кнопки «добавить» ---------------------------------------------------
  document.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const menu = btn.closest("details");
      if (menu) menu.open = false;
      addQuestions([blankQuestion(btn.dataset.add)]);
    });
  });

  // --- удаление теста ------------------------------------------------------
  $("#delete-test").addEventListener("click", async () => {
    const name = state.test.discipline || "без названия";
    if (!confirm(`Удалить тест «${name}»?`)) return;
    await flush();
    const res = await api("DELETE", `/api/tests/${TEST_ID}`);
    if (res.ok) {
      state.savedChangeId = state.changeId;
      location.href = "/";
    } else {
      toast(await errorText(res, "Не удалось удалить тест"));
    }
  });

  document.querySelectorAll("dialog [data-close]").forEach((b) =>
    b.addEventListener("click", () => b.closest("dialog").close()));

  // --- вставка из Word ----------------------------------------------------
  const dialog = $("#paste-dialog");
  const pasteText = $("#paste-text");
  const pastePreview = $("#paste-preview");
  const pasteAdd = $("#paste-add");
  let parsed = null;
  let parseTimer = null;
  let parseSeq = 0;

  function resetPaste() {
    pasteText.value = "";
    parsed = null;
    pastePreview.replaceChildren(el("p", { class: "paste-empty" }, "Здесь появятся найденные вопросы"));
    updatePasteButton();
  }

  $("#open-paste").addEventListener("click", () => {
    if (!pasteText.value) resetPaste();
    dialog.showModal();
    pasteText.focus();
  });

  pasteText.addEventListener("input", () => {
    clearTimeout(parseTimer);
    parseTimer = setTimeout(parsePaste, 350);
  });

  // Вставка из Word: верные ответы, выделенные жирным/цветом/подчёркиванием, помечаем «*».
  pasteText.addEventListener("paste", (e) => {
    const html = e.clipboardData && e.clipboardData.getData("text/html");
    if (!html || !window.WordPaste) return;
    const text = window.WordPaste.htmlToMarkedText(html);
    if (!text.trim()) return;
    e.preventDefault();
    const start = pasteText.selectionStart;
    const end = pasteText.selectionEnd;
    pasteText.value = pasteText.value.slice(0, start) + text + pasteText.value.slice(end);
    pasteText.selectionStart = pasteText.selectionEnd = start + text.length;
    clearTimeout(parseTimer);
    parsePaste();
  });

  async function parsePaste() {
    const text = pasteText.value;
    const seq = ++parseSeq;
    if (!text.trim()) {
      resetPaste();
      return;
    }
    let data;
    try {
      const res = await api("POST", "/api/parse", { text });
      if (!res.ok) {
        pastePreview.replaceChildren(el("p", { class: "issues-inline error" }, await errorText(res, "Не удалось разобрать текст")));
        return;
      }
      data = await res.json();
    } catch (_) {
      pastePreview.replaceChildren(el("p", { class: "issues-inline error" }, "Нет связи с сервером"));
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
      pastePreview.replaceChildren(el("p", { class: "paste-empty" },
        "Вопросы не найдены. Пронумеруйте их (1., 2., …) или разделите пустой строкой."));
      return;
    }
    const warn = new Map();
    for (const w of parsed.warnings) {
      if (!warn.has(w.index)) warn.set(w.index, []);
      warn.get(w.index).push(w.message);
    }
    const noAnswer = qs.filter((q, i) => (warn.get(i) || []).includes("Нет верного ответа")).length;
    const items = qs.map((q, idx) => {
      const li = el("li", {}, el("div", { class: "pq-text" }, q.text || "(без текста)"));
      if (isChoice(q)) {
        li.append(el("ul", { class: "opts" }, q.options.map((o, k) =>
          el("li", { class: o.correct ? "ok" : "" }, `${letter(k)}) ${o.text}`, o.correct ? el("span", { class: "tick" }, " ✓") : null))));
      } else if (q.type === "short") {
        li.append(el("ul", { class: "opts" }, el("li", { class: "ok" }, "Ответ: " + q.answers.join("; "))));
      } else if (q.type === "numerical") {
        li.append(el("ul", { class: "opts" }, el("li", { class: "ok" }, "Ответ: " + q.number)));
      }
      for (const m of warn.get(idx) || []) li.append(el("div", { class: "warn" }, m));
      return li;
    });
    const head = el("p", { class: "paste-count" },
      el("strong", {}, `Найдено ${qs.length} ${plural(qs.length, "вопрос", "вопроса", "вопросов")}`));
    if (noAnswer) head.append(el("span", { class: "warn" }, ` · без верного ответа: ${noAnswer} — отметите потом`));
    pastePreview.replaceChildren(head, el("ol", {}, items));
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
    resetPaste();
  });

  // --- старт ---------------------------------------------------------------
  initHead();
  renderAll();
  setSave("saved");
  if (!state.test.discipline) $(".title-input").focus();
})();
