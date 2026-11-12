"""Собирает docs/Инструкция по тестам.pdf из manual.html (нужен playwright с Chromium)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "Инструкция по тестам.pdf"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto((HERE / "manual.html").as_uri())
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.pdf(
        path=str(OUT),
        format="A4",
        print_background=True,
        prefer_css_page_size=True,
        display_header_footer=True,
        header_template="<span></span>",
        footer_template=(
            '<div style="width:100%;font-size:8px;color:#8a93a3;text-align:center;font-family:Arial">'
            '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'
        ),
    )
    browser.close()
print(OUT)
