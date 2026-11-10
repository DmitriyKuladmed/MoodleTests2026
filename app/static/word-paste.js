/* Превращает HTML из буфера обмена (Word, Google Docs) в текст, где строки,
   выделенные жирным, подчёркиванием, цветом или заливкой, помечены « *».
   Так преподаватель может просто скопировать тест, где верные ответы уже выделены. */
(() => {
  "use strict";

  const BLOCK = new Set(["P", "DIV", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "TR", "TD", "TH",
    "BLOCKQUOTE", "PRE", "SECTION", "ARTICLE", "UL", "OL", "TABLE"]);
  const SKIP = new Set(["STYLE", "SCRIPT", "HEAD", "TITLE", "META", "LINK", "XML", "O:P"]);
  const NEUTRAL_COLORS = /^(black|windowtext|auto|inherit|initial|currentcolor|#0{3}|#0{6}|rgb\(0,\s*0,\s*0\)|#1f1f1f|#000000ff)$/i;
  const NEUTRAL_BG = /^(transparent|white|none|auto|inherit|initial|#fff|#ffffff|rgb\(255,\s*255,\s*255\)|rgba\(0,\s*0,\s*0,\s*0\))$/i;
  const LABEL = /^\s*(?:\d{1,3}\s*[.)]|[a-zа-яё]\s*[.)])\s*/i;

  function styleOf(el) {
    const out = {};
    const raw = el.getAttribute && el.getAttribute("data-word-style");
    if (!raw) return out;
    for (const part of raw.split(";")) {
      const i = part.indexOf(":");
      if (i < 0) continue;
      out[part.slice(0, i).trim().toLowerCase()] = part.slice(i + 1).trim().toLowerCase();
    }
    return out;
  }

  function firstColor(value) {
    return (value || "").split(/\s+(?![^(]*\))/)[0] || "";
  }

  // Выделен ли текст внутри элемента (смотрим сам элемент и его родителей до блока).
  function isMarked(el, stopAt) {
    for (let node = el; node && node !== stopAt; node = node.parentElement) {
      const tag = node.tagName;
      if (tag === "B" || tag === "STRONG" || tag === "U" || tag === "MARK") return true;
      if (tag === "FONT") {
        const c = node.getAttribute("color");
        if (c && !NEUTRAL_COLORS.test(c.trim())) return true;
      }
      const s = styleOf(node);
      const weight = s["font-weight"];
      if (weight === "normal" || weight === "400") return false;
      if (weight && (weight === "bold" || weight === "bolder" || Number(weight) >= 600)) return true;
      if ((s["text-decoration"] || s["text-decoration-line"] || "").includes("underline")) return true;
      if (s["text-underline"] && s["text-underline"] !== "none") return true;
      if (s["mso-highlight"] && s["mso-highlight"] !== "none") return true;
      const bg = firstColor(s["background"] || s["background-color"]);
      if (bg && !NEUTRAL_BG.test(bg)) return true;
      const color = s["color"];
      if (color && !NEUTRAL_COLORS.test(color)) return true;
      if (BLOCK.has(tag)) break;
    }
    return false;
  }

  function listPrefix(li) {
    const list = li.parentElement;
    if (!list || (list.tagName !== "OL" && list.tagName !== "UL")) return "";
    let depth = 0;
    for (let n = list; n; n = n.parentElement) if (n.tagName === "OL" || n.tagName === "UL") depth += 1;
    const items = Array.from(list.children).filter((c) => c.tagName === "LI");
    const start = Number(list.getAttribute("start")) || 1;
    const index = items.indexOf(li);
    if (depth === 1 && list.tagName === "OL") return `${start + index}. `;
    if (depth >= 2) return `${"абвгдежзиклмнопрстуфхцчшщэюя"[index] || index + 1}) `;
    return "";
  }

  function htmlToMarkedText(html) {
    // Стили читаем как текст, применять их не нужно: так браузер не ругается на CSP страницы.
    const safe = html
      .replace(/<style[\s\S]*?<\/style>/gi, "")
      .replace(/(<[^>]*?\s)style\s*=/gi, "$1data-word-style=");
    const doc = new DOMParser().parseFromString(safe, "text/html");
    const lines = [];
    let current = null;

    function startLine() {
      if (!current) current = { parts: [], marked: 0, total: 0 };
    }
    function endLine() {
      if (!current) return;
      lines.push(current);
      current = null;
    }
    function addText(text, marked) {
      // Переводы строк и пробелы между тегами — не текст документа.
      if (!current && /^[ \t\r\n]*$/.test(text)) return;
      startLine();
      current.parts.push(text);
      const letters = text.replace(/\s| /g, "").length;
      current.total += letters;
      if (marked) current.marked += letters;
    }

    function walk(node, block) {
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          if (child.nodeValue) addText(child.nodeValue, isMarked(child.parentElement, block.parentElement));
          continue;
        }
        if (child.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = child.tagName;
        if (SKIP.has(tag)) continue;
        if (tag === "BR") { endLine(); continue; }
        if (BLOCK.has(tag)) {
          endLine();
          const before = lines.length;
          if (tag === "LI") {
            const prefix = listPrefix(child);
            if (prefix && !LABEL.test(child.textContent)) addText(prefix, false);
          }
          walk(child, child);
          endLine();
          // Пустой абзац в Word — это пустая строка (ею разделяют вопросы без номеров).
          if (lines.length === before && (tag === "P" || tag === "DIV")) lines.push({ parts: [], marked: 0, total: 0 });
        } else {
          walk(child, block);
        }
      }
    }
    walk(doc.body, doc.body);
    endLine();

    const result = lines.map((line) => {
      const text = line.parts.join("").replace(/ /g, " ").replace(/[ \t]+/g, " ").trim();
      // Метку «а)»/«1.» не считаем: важно, выделен ли сам текст ответа.
      const label = (text.match(LABEL) || [""])[0].replace(/\s/g, "").length;
      const total = Math.max(0, line.total - label);
      const marked = total > 0 && line.marked - Math.min(label, line.marked) >= total * 0.6;
      return { text, marked };
    });

    // Если выделено почти всё (например, весь документ жирный) — выделение ничего не значит.
    const nonEmpty = result.filter((r) => r.text);
    const markedCount = nonEmpty.filter((r) => r.marked).length;
    const meaningful = nonEmpty.length > 0 && markedCount / nonEmpty.length <= 0.6;

    const out = [];
    for (const r of result) {
      if (!r.text) {
        if (out.length && out[out.length - 1] !== "") out.push("");
        continue;
      }
      out.push(meaningful && r.marked && !/\*\s*$/.test(r.text) ? r.text + " *" : r.text);
    }
    return out.join("\n").trim() + "\n";
  }

  window.WordPaste = { htmlToMarkedText };
})();
