"""Защита от сканеров: ловушки, много 404, частые запросы. Вошедших не трогает."""
from fastapi.testclient import TestClient

from tests.test_activity import journal, lines_with
from tests.test_app import admin, app, editor, login  # noqa: F401 — фикстуры


def test_guest_404_is_info_with_guest_role(app):
    client = TestClient(app)
    assert client.get("/no-such-page").status_code == 404
    line = [l for l in lines_with(app, "http") if "/no-such-page" in l][0]
    assert " INFO " in line and "role=гость" in line


def test_trap_bans_guests_but_not_logged_in_users(app, editor):
    bot = TestClient(app)  # в тестах у всех клиентов один адрес — как общий адрес университета
    assert bot.get("/.env").status_code == 404
    ban, = lines_with(app, "security.ban")
    assert "reason=ловушка" in ban and "path=/.env" in ban

    r = bot.get("/")
    assert r.status_code == 403 and "войдите" in r.text
    assert bot.get("/login").status_code == 200  # вход открыт всегда
    assert editor.get("/").status_code == 200  # вошедший с того же адреса работает как обычно
    assert login(bot).status_code == 303 and bot.get("/").status_code == 200  # и после входа всё работает
    assert "path=/ " not in "\n".join(l for l in lines_with(app, "http") if "status=403" in l)  # отказы не шумят


def test_trap_in_query_and_wordpress_paths(app):
    assert TestClient(app).get("/?rest_route=/wp/v2/users/").status_code == 404
    assert "rest_route" in lines_with(app, "security.ban")[0]


def test_many_404_ban(app):
    bot = TestClient(app)
    for i in range(9):
        bot.get(f"/missing-{i}")
    assert any("reason=\"много 404\"" in l for l in lines_with(app, "security.ban"))
    assert bot.get("/").status_code == 403


def test_too_many_requests_ban(app):
    bot = TestClient(app)
    codes = [bot.get("/robots.txt").status_code for _ in range(61)]
    assert codes[-1] == 429 and set(codes[:-1]) == {200}
    assert any("слишком часто" in l for l in lines_with(app, "security.ban"))


def test_logged_in_users_are_never_counted(app, editor):
    for i in range(20):
        editor.get(f"/missing-{i}")
    editor.get("/.git/config")
    assert lines_with(app, "security.ban") == []
    assert editor.get("/").status_code == 200


def test_admin_sees_and_lifts_bans(app, admin):
    bot = TestClient(app)
    bot.get("/wp-admin/")
    page = admin.get("/admin").text
    assert "/wp-admin/" in page and "Разблокировать" in page
    ip = app.state.guard.active()[0][0]
    r = admin.post("/admin/security/unban", data={"ip": ip}, follow_redirects=False)
    assert r.status_code == 303
    assert bot.get("/", follow_redirects=False).status_code == 303  # снова обычный редирект на вход
    assert "security.unban" in journal(app)


def test_security_headers(app):
    r = TestClient(app).get("/login")
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in r.headers["permissions-policy"]


def test_all_requests_and_bans_record_browser(app, editor):
    bot = TestClient(app, headers={"User-Agent": "python-requests/2.31"})
    bot.get("/login")
    guest_line = [l for l in lines_with(app, "http") if "role=гость" in l][-1]
    assert "ua=python-requests/2.31" in guest_line
    bot.get("/info.php")
    assert "ua=python-requests/2.31" in lines_with(app, "security.ban")[0]
    editor.get("/")
    own = [l for l in lines_with(app, "http") if "role=editor" in l and "path=/ " in l]
    assert own and "ua=testclient" in own[-1]  # браузер пишем у всех запросов
