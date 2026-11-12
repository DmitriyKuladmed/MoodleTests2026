"""Журнал действий: что попадает в журнал, а что — нет."""
from fastapi.testclient import TestClient

from tests.test_app import H, admin, app, create, editor, login  # noqa: F401 — фикстуры


def journal(app) -> str:
    from app import activity
    for handler in activity.log.handlers:
        handler.flush()
    path = app.state.log_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def lines_with(app, name: str) -> list[str]:
    return [line for line in journal(app).splitlines() if f" {name} " in line]


def test_actions_are_logged_without_secrets(app, editor):
    test_id = create(editor)
    test = editor.get(f"/api/tests/{test_id}").json()
    body = {**test, "questions": [{"type": "single", "text": "Секретный текст вопроса",
                                   "options": [{"text": "да", "correct": True}, {"text": "нет"}]}]}
    assert editor.put(f"/api/tests/{test_id}", json=body, headers=H).status_code == 200
    assert editor.post(f"/api/tests/{test_id}/status", json={"ready": True}, headers=H).status_code == 200

    login_line, = lines_with(app, "auth.login")
    assert "role=editor" in login_line and "sid=" in login_line and "sid=-" not in login_line
    assert f"test={test_id}" in lines_with(app, "test.create")[0]
    save, = lines_with(app, "test.save")
    assert "questions=1" in save and "version=1->2" in save
    assert "status=ready" in lines_with(app, "test.status")[0]
    text = journal(app)
    assert "editor-pass" not in text  # пароль
    assert "Секретный текст вопроса" not in text  # содержимое теста


def test_failed_login_logged_without_password(app):
    client = TestClient(app)
    assert login(client, "kafedra", "wrong-pass-123").status_code == 200
    fail, = lines_with(app, "auth.fail")
    assert "login=kafedra" in fail
    assert "wrong-pass-123" not in journal(app)


def test_conflict_and_http_errors_are_logged(app, editor):
    test_id = create(editor)
    test = editor.get(f"/api/tests/{test_id}").json()
    assert editor.put(f"/api/tests/{test_id}", json=test, headers=H).status_code == 200
    assert editor.put(f"/api/tests/{test_id}", json=test, headers=H).status_code == 409  # старая версия
    conflict, = lines_with(app, "test.conflict")
    assert "client_version=1" in conflict and "server_version=2" in conflict

    assert editor.get("/api/tests/999").status_code == 404
    missing = [line for line in lines_with(app, "http") if "/api/tests/999" in line]
    assert missing and "WARNING" in missing[0] and "status=404" in missing[0] and "Тест не найден" in missing[0]


def test_server_error_is_logged_with_code(app, editor, monkeypatch):
    test_id = create(editor)

    def boom(_test_id):
        raise RuntimeError("сломалась база")

    monkeypatch.setattr(app.state.db, "get_test", boom)
    r = editor.get(f"/api/tests/{test_id}")
    assert r.status_code == 500
    rid = r.headers["x-request-id"]
    assert rid in r.json()["detail"]  # код ошибки виден пользователю
    text = journal(app)
    assert f"rid={rid}" in text and "server.error" in text
    assert "RuntimeError: сломалась база" in text  # трассировка


def test_client_errors_are_logged_and_limited(app, editor):
    report = {"kind": "js_error", "message": "x is undefined", "source": "/static/editor.js", "line": 10}
    assert editor.post("/api/client-log", json=report).status_code == 403  # без служебного заголовка
    assert editor.post("/api/client-log", json=report, headers=H).status_code == 204
    line, = lines_with(app, "client.js_error")
    assert "x is undefined" in line and "role=editor" in line

    for _ in range(40):
        editor.post("/api/client-log", json={"kind": "spam", "message": "!"}, headers=H)
    assert len(lines_with(app, "client.spam")) < 40  # поток ошибок не засоряет журнал


def test_journal_is_admin_only(app, editor, admin):
    assert editor.get("/admin/logs").status_code == 403
    r = admin.get("/admin/logs")
    assert r.status_code == 200 and "auth.login" in r.text
    r = admin.get("/admin/logs?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]


# --- просмотр журнала в админке ---------------------------------------------

def write_log(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_search_pages_back_without_gaps_or_repeats(tmp_path):
    from app import activity
    log = tmp_path / "app.log"
    # 25 событий, по 5 в одну секунду — курсор должен корректно делить одинаковые секунды.
    lines = [f"2026-11-12 10:00:{i // 5:02d} INFO    test.save          n={i}" for i in range(25)]
    write_log(log, lines)
    seen, page = [], activity.search(log, limit=7)
    while True:
        seen = [e["text"] for e in page["entries"]] + seen
        if not page["has_more"]:
            break
        page = activity.search(log, before=page["before"], skip=page["skip"], limit=7)
    assert seen == lines


def test_search_recent_window_filters_and_tracebacks(tmp_path):
    from app import activity
    log = tmp_path / "app.log"
    write_log(log, [
        "2026-11-12 09:00:00 INFO    auth.login         sid=old",
        "2026-11-12 10:00:00 ERROR   server.error       rid=abc123",
        "Traceback (most recent call last):",
        "RuntimeError: сломалась база",
        "2026-11-12 10:05:00 WARNING client.js_error    sid=new",
        "2026-11-12 10:06:00 INFO    test.save          test=7",
    ])
    recent = activity.search(log, since="2026-11-12 09:56:00")
    assert [e["ts"][-8:] for e in recent["entries"]] == ["10:00:00", "10:05:00", "10:06:00"]
    assert recent["has_more"]  # есть событие старше окна
    assert "RuntimeError" in recent["entries"][0]["text"]  # трассировка не теряется

    older = activity.search(log, before=recent["before"], skip=recent["skip"])
    assert [e["text"][-7:] for e in older["entries"]] == ["sid=old"] and not older["has_more"]

    assert len(activity.search(log, query="СЛОМАЛАСЬ")["entries"]) == 1  # без учёта регистра
    assert [e["level"] for e in activity.search(log, level="WARNING")["entries"]] == ["ERROR", "WARNING"]
    assert [e["level"] for e in activity.search(log, level="ERROR")["entries"]] == ["ERROR"]


def test_journal_api_is_admin_only_and_searches(app, editor, admin):
    create(editor, discipline="Химия")
    assert editor.get("/admin/api/logs").status_code == 403
    recent = admin.get("/admin/api/logs").json()
    assert any("test.create" in e["text"] for e in recent["entries"])
    found = admin.get("/admin/api/logs", params={"q": "химия"}).json()["entries"]
    assert found and all("Химия" in e["text"] for e in found)
    views = [line for line in journal(app).splitlines() if "/admin/api/logs" in line]
    assert views and all("status=403" in line for line in views)  # отказ пишется, сам просмотр — нет
