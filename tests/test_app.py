import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
H = {"X-Requested-With": "fetch"}


@pytest.fixture
def app(tmp_path):
    settings = Settings(data_dir=tmp_path, secret_key="test-secret", editor_login="kafedra",
                        editor_password="editor-pass", admin_login="admin", admin_password="admin-pass")
    settings.login_max_failures = 3
    return create_app(settings)


def login(client, user="kafedra", password="editor-pass"):
    return client.post("/login", data={"login": user, "password": password, "next": "/"}, follow_redirects=False)


@pytest.fixture
def editor(app):
    client = TestClient(app)
    assert login(client).status_code == 303
    return client


@pytest.fixture
def admin(app):
    client = TestClient(app)
    assert login(client, "admin", "admin-pass").status_code == 303
    return client


def create(client, discipline="Физика", teacher="Иванов И. И.", **extra):
    r = client.post("/tests/new", data={"discipline": discipline, "teacher": teacher, "department": "", **extra},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_pages_require_login(app):
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/api/tests/1").status_code == 401
    assert client.get("/admin/export.zip", follow_redirects=False).status_code == 303
    assert "noindex" in client.get("/login").headers["x-robots-tag"]
    assert client.get("/robots.txt").text.startswith("User-agent: *\nDisallow: /")


@pytest.mark.parametrize("target", ["//evil.com", "/\\evil.com", "https://evil.com", "/\\/evil.com"])
def test_login_next_is_local_only(app, target):
    client = TestClient(app)
    r = client.post("/login", data={"login": "kafedra", "password": "editor-pass", "next": target},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_wrong_password_and_throttle(app):
    client = TestClient(app)
    for _ in range(3):
        r = login(client, password="nope")
        assert "Неверный логин или пароль" in r.text
    r = login(client)  # даже верный пароль не пускает во время блокировки
    assert "Слишком много неудачных попыток" in r.text


def test_create_save_and_conflict(editor):
    test_id = create(editor)
    data = editor.get(f"/api/tests/{test_id}").json()
    assert data["discipline"] == "Физика" and data["version"] == 1
    assert any(i["message"] == "В тесте нет ни одного вопроса" for i in data["issues"])

    body = {**data, "questions": [{"type": "truefalse", "text": "Земля круглая", "correct": True}]}
    r = editor.put(f"/api/tests/{test_id}", json=body, headers=H)
    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["issues"] == []

    stale = editor.put(f"/api/tests/{test_id}", json=body, headers=H)  # старая версия
    assert stale.status_code == 409

    saved = editor.get(f"/api/tests/{test_id}").json()
    assert saved["questions"][0]["text"] == "Земля круглая"


def test_api_requires_fetch_header(editor):
    test_id = create(editor)
    data = editor.get(f"/api/tests/{test_id}").json()
    assert editor.put(f"/api/tests/{test_id}", json=data).status_code == 403


def test_ready_status_rules(editor):
    test_id = create(editor)
    r = editor.post(f"/api/tests/{test_id}/status", json={"ready": True}, headers=H)
    assert r.status_code == 400  # нет вопросов
    data = editor.get(f"/api/tests/{test_id}").json()
    data["questions"] = [{"type": "truefalse", "text": "x"}]
    editor.put(f"/api/tests/{test_id}", json=data, headers=H)
    r = editor.post(f"/api/tests/{test_id}/status", json={"ready": True}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    # Сломали тест — он сам возвращается в черновики.
    data = editor.get(f"/api/tests/{test_id}").json()
    data["questions"] = [{"type": "single", "text": ""}]
    assert editor.put(f"/api/tests/{test_id}", json=data, headers=H).json()["status"] == "draft"


def test_duplicate_discipline_warning(editor):
    create(editor, "Химия")
    r = editor.post("/tests/new", data={"discipline": "химия", "teacher": "Петров"}, follow_redirects=False)
    assert r.status_code == 200 and "уже есть" in r.text
    create(editor, "химия", "Петров", confirm="1")


def test_image_upload_and_access(app, editor):
    test_id = create(editor)
    bad = editor.post(f"/api/tests/{test_id}/images", files={"file": ("a.txt", b"hello", "text/plain")}, headers=H)
    assert bad.status_code == 400
    r = editor.post(f"/api/tests/{test_id}/images", files={"file": ("a.png", PNG, "image/png")}, headers=H)
    image_id = r.json()["id"]
    assert editor.get(f"/images/{image_id}").content == PNG
    assert TestClient(app).get(f"/images/{image_id}").status_code == 401


def test_parse_endpoint(editor):
    r = editor.post("/api/parse", json={"text": "1. Вопрос\nа) да *\nб) нет"}, headers=H)
    assert r.json()["questions"][0]["options"][0]["correct"] is True


def test_editor_cannot_use_admin(editor):
    assert editor.get("/admin").status_code == 403
    assert editor.get("/admin/export.zip").status_code == 403


def test_admin_export_delete_restore(admin):
    test_id = create(admin)
    data = admin.get(f"/api/tests/{test_id}").json()
    img = admin.post(f"/api/tests/{test_id}/images", files={"file": ("a.png", PNG, "image/png")}, headers=H).json()["id"]
    data["questions"] = [{"type": "truefalse", "text": "x", "image": img}]
    admin.put(f"/api/tests/{test_id}", json=data, headers=H)

    xml = admin.get(f"/admin/export/{test_id}.xml")
    assert xml.status_code == 200 and b"@@PLUGINFILE@@" in xml.content

    z = zipfile.ZipFile(io.BytesIO(admin.get("/admin/export.zip").content))
    assert any(n.endswith("Физика.xml") for n in z.namelist())

    assert admin.delete(f"/api/tests/{test_id}", headers=H).status_code == 200
    assert admin.get(f"/api/tests/{test_id}").status_code == 404
    assert "Удалённые тесты" in admin.get("/admin").text
    admin.post(f"/admin/tests/{test_id}/restore", follow_redirects=False)
    assert admin.get(f"/api/tests/{test_id}").status_code == 200

    backup = admin.get("/admin/backup.db")
    assert backup.status_code == 200 and backup.content.startswith(b"SQLite format 3")


def test_password_change_logs_out_editors(app, admin):
    editor = TestClient(app)
    login(editor)
    assert editor.get("/", follow_redirects=False).status_code == 200
    r = admin.post("/admin/accounts", data={"account": "editor", "login": "kafedra", "password": "new-password-1"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert editor.get("/", follow_redirects=False).status_code == 303  # старая сессия больше не действует
    fresh = TestClient(app)
    assert login(fresh, password="editor-pass").status_code == 200  # старый пароль не подходит
    assert login(fresh, password="new-password-1").status_code == 303
    assert admin.get("/admin").status_code == 200  # админ остался в системе
