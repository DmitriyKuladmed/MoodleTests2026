/* Глазик в поле пароля: показать или скрыть, что введено. */
(() => {
  "use strict";
  const input = document.getElementById("password");
  const button = document.getElementById("reveal-password");
  if (!input || !button) return;

  const EYE = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M1.8 10S4.8 4.5 10 4.5 18.2 10 18.2 10 15.2 15.5 10 15.5 1.8 10 1.8 10Z"/><circle cx="10" cy="10" r="2.6"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8.2 4.7A8 8 0 0 1 10 4.5c5.2 0 8.2 5.5 8.2 5.5a14 14 0 0 1-2.3 2.9M5.4 6.1C3 7.6 1.8 10 1.8 10s3 5.5 8.2 5.5c1.4 0 2.6-.4 3.6-1M8.2 8.2a2.6 2.6 0 0 0 3.6 3.6M3 3l14 14"/></svg>';

  function render(shown) {
    input.type = shown ? "text" : "password";
    button.innerHTML = shown ? EYE_OFF : EYE;
    const label = shown ? "Скрыть пароль" : "Показать пароль";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.setAttribute("aria-pressed", shown ? "true" : "false");
  }

  button.addEventListener("click", () => {
    const start = input.selectionStart, end = input.selectionEnd;
    render(input.type === "password");
    input.focus();
    try { input.setSelectionRange(start, end); } catch (_) { /* не у всех полей есть курсор */ }
  });
  // Перед отправкой прячем пароль обратно, чтобы браузер правильно предложил его сохранить.
  input.form.addEventListener("submit", () => render(false));

  render(false);
  button.hidden = false;
})();
