"""Собирает docs/Инструкция по тестам.pdf из manual.html.

Нужны playwright (Chromium) и PyMuPDF. Сборка в два прохода: сначала узнаём,
на какой странице оказался каждый раздел, затем вписываем номера в содержание.
"""
import re
from pathlib import Path

import fitz  # PyMuPDF
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "Инструкция по тестам.pdf"

BUILD_TOC = """
() => {
  const toc = document.getElementById("toc");
  toc.replaceChildren();
  const heads = [...document.querySelectorAll("section[data-toc] > h2, h3[data-toc]")];
  return heads.map((h, i) => {
    h.id = "sec-" + (i + 1);
    const li = document.createElement("li");
    if (h.tagName === "H3") li.className = "sub";
    li.innerHTML = '<a href="#' + h.id + '"><span class="t"></span><span class="dots"></span><span class="pg">00</span></a>';
    li.querySelector(".t").textContent = h.textContent.trim();
    toc.append(li);
    return [h.tagName === "H3" ? 2 : 1, h.textContent.trim()];
  });
}
"""

SET_PAGES = """
(pages) => document.querySelectorAll("#toc .pg").forEach((el, i) => { el.textContent = pages[i]; })
"""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_pages(pdf: bytes, titles: list[str]) -> list[int]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    texts = [norm(page.get_text()) for page in doc]
    # Страницы 1–2 — титул и содержание, разделы ищем после них.
    pages, start = [], 2
    for title in titles:
        for i in range(start, len(texts)):
            if norm(title) in texts[i]:
                pages.append(i + 1)
                start = i
                break
        else:
            raise SystemExit(f"Не нашёл раздел в PDF: {title}")
    return pages


def render(page) -> bytes:
    return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)


FONT = Path("C:/Windows/Fonts/times.ttf")
HEADER = "Инструкция по подготовке тестов для аккредитации"


def stamp(pdf: bytes, outline: list) -> bytes:
    """Колонтитул и номер страницы на всех листах, кроме титульного, и закладки PDF."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    doc.set_toc([[1, "Содержание", 2]] + outline)
    for i, pg in enumerate(doc):
        if i == 0:
            continue
        w, h = pg.rect.width, pg.rect.height
        left, right = 25 / 25.4 * 72, w - 15 / 25.4 * 72  # поля страницы, как в @page
        kw = dict(fontname="tnr", fontfile=str(FONT), color=(0.33, 0.33, 0.33))
        pg.insert_textbox(fitz.Rect(left, 22, right, 40), HEADER, fontsize=9, align=fitz.TEXT_ALIGN_RIGHT, **kw)
        pg.draw_line(fitz.Point(left, 40), fitz.Point(right, 40), color=(0.6, 0.6, 0.6), width=0.4)
        pg.insert_textbox(fitz.Rect(left, h - 40, right, h - 22), str(i + 1), fontsize=10,
                          align=fitz.TEXT_ALIGN_CENTER, **{**kw, "color": (0, 0, 0)})
    return doc.tobytes(garbage=3, deflate=True)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto((HERE / "manual.html").as_uri())
    page.wait_for_load_state("networkidle")
    entries = page.evaluate(BUILD_TOC)
    titles = [title for _, title in entries]
    pages = find_pages(render(page), titles)
    page.evaluate(SET_PAGES, pages)
    final = render(page)
    assert find_pages(final, titles) == pages, "номера страниц сдвинулись после заполнения содержания"
    OUT.write_bytes(stamp(final, [[lvl, title, pg] for (lvl, title), pg in zip(entries, pages)]))
    browser.close()
print(OUT)
