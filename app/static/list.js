/* Список тестов: поиск и переход по клику на строку. */
(() => {
  "use strict";
  const table = document.getElementById("tests-table");
  if (!table) return;

  table.addEventListener("click", (e) => {
    if (e.target.closest("a, button, form")) return;
    const row = e.target.closest("tr[data-href]");
    if (row) location.href = row.dataset.href;
  });

  const filter = document.getElementById("filter");
  const empty = document.getElementById("filter-empty");
  if (!filter) return;
  filter.addEventListener("input", () => {
    const needle = filter.value.trim().toLowerCase();
    let shown = 0;
    for (const row of table.tBodies[0].rows) {
      const match = !needle || row.textContent.toLowerCase().includes(needle);
      row.hidden = !match;
      if (match) shown += 1;
    }
    if (empty) empty.hidden = shown > 0;
  });
})();
