"""Геолокация в журнале: страна и город по IP у входов, гостей и блокировок."""
from fastapi.testclient import TestClient

from app.geo import Geo, _months_back
from tests.test_activity import lines_with
from tests.test_app import admin, app, editor, login  # noqa: F401 — фикстуры

RECORDS = {
    "93.84.1.1": {"country": {"names": {"ru": "Беларусь", "en": "Belarus"}},
                  "city": {"names": {"en": "Minsk"}}},
    "77.88.8.8": {"country": {"names": {"ru": "Россия"}},
                  "city": {"names": {"en": "Moscow (Tsentralnyy administrativnyy okrug)"}}},
    "8.8.4.4": {"country": {"names": {"en": "United States"}}},
}


class FakeReader:
    def get(self, ip):
        return RECORDS.get(ip)


def with_fake_base(geo: Geo) -> Geo:
    geo._reader = FakeReader()
    geo._where.cache_clear()
    return geo


def test_where_names(tmp_path):
    geo = with_fake_base(Geo(tmp_path))
    assert geo.where("93.84.1.1") == "Беларусь, Minsk"
    assert geo.where("77.88.8.8") == "Россия, Moscow"  # уточнение района в скобках убираем
    assert geo.where("8.8.4.4") == "United States"
    assert geo.where("1.2.3.4") is None  # нет в базе
    assert geo.where("192.168.1.5") == "локальная сеть"
    assert geo.where("127.0.0.1") == "локальная сеть"
    assert geo.where("не адрес") is None and geo.where(None) is None


def test_no_base_no_geo(tmp_path):
    geo = Geo(tmp_path)
    assert geo.where("93.84.1.1") is None
    assert geo.where("10.0.0.1") == "локальная сеть"


def test_months_back():
    from datetime import date
    assert _months_back(date(2026, 1, 15), 2) == ["2026-01", "2025-12"]


def test_login_and_guest_lines_have_geo(app, editor):
    # у тестового клиента адрес «testclient» — не IP, поэтому подменяем поиск
    app.state.geo.where = lambda ip: "Беларусь, Minsk"
    TestClient(app).get("/login")
    guest = [l for l in lines_with(app, "http") if "role=гость" in l][-1]
    assert 'geo="Беларусь, Minsk"' in guest

    login(TestClient(app))
    assert 'geo="Беларусь, Minsk"' in lines_with(app, "auth.login")[-1]

    TestClient(app).post("/login", data={"login": "kafedra", "password": "неверный"})
    assert 'geo="Беларусь, Minsk"' in lines_with(app, "auth.fail")[-1]

    TestClient(app).get("/.env")
    assert 'geo="Беларусь, Minsk"' in lines_with(app, "security.ban")[-1]

    editor.get("/")
    own = [l for l in lines_with(app, "http") if "role=editor" in l and "path=/ " in l]
    assert own and 'geo="Беларусь, Minsk"' in own[-1]  # место пишем у всех запросов


def test_admin_ban_table_shows_where(app, admin):
    app.state.geo.where = lambda ip: "Нидерланды, Amsterdam"
    TestClient(app).get("/wp-login.php")
    page = admin.get("/admin").text
    assert "Откуда" in page and "Нидерланды, Amsterdam" in page


def test_no_geo_field_when_unknown(app):
    TestClient(app).get("/login")  # базы в тестах нет — поле не пишется вовсе
    guest = [l for l in lines_with(app, "http") if "role=гость" in l][-1]
    assert "geo=" not in guest
