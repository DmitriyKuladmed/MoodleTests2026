/* Запоминаем ФИО и кафедру в этом браузере, чтобы не вводить их для каждого теста. */
(() => {
  "use strict";
  const form = document.getElementById("new-test");
  if (!form) return;
  const fields = form.querySelectorAll("[data-remember]");
  const key = (input) => "moodle-tests:" + input.dataset.remember;
  fields.forEach((input) => {
    if (input.value) return;
    try {
      const saved = localStorage.getItem(key(input));
      if (saved) input.value = saved;
    } catch (_) { /* хранилище недоступно — просто не подставляем */ }
  });
  form.addEventListener("submit", () => {
    fields.forEach((input) => {
      try { localStorage.setItem(key(input), input.value.trim()); } catch (_) { /* ignore */ }
    });
  });
})();
