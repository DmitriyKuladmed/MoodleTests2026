import base64
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from app.moodle_xml import build_test_xml, export_zip, snap_fraction, text_to_html
from app.questions import normalize_question, normalize_settings

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def make_test(questions, **kw):
    return {
        "id": kw.get("id", 1),
        "discipline": kw.get("discipline", "Физика"),
        "teacher": "Иванов И. И.",
        "department": "Кафедра физики",
        "settings": normalize_settings({"time_limit": 30, "questions_per_attempt": None}),
        "questions": [normalize_question(q) for q in questions],
        "status": "draft",
        "version": 1,
        "created_at": "2026-11-10T10:00:00+00:00",
        "updated_at": "2026-11-10T10:00:00+00:00",
        "deleted_at": None,
    }


def parse(test, get_image=None):
    root = ET.fromstring(build_test_xml(test, get_image))
    assert root.tag == "quiz"
    return root.findall("question")


def answers(q):
    return [(a.get("fraction"), a.findtext("text")) for a in q.findall("answer")]


def test_single_choice():
    t = make_test([{
        "type": "single", "text": "2 + 2 = ?",
        "options": [{"text": "3"}, {"text": "4", "correct": True}, {"text": ""}, {"text": "5"}],
    }])
    (q,) = parse(t)
    assert q.get("type") == "multichoice"
    assert q.findtext("single") == "true"
    assert q.find("questiontext").get("format") == "html"
    assert q.findtext("questiontext/text") == "<p>2 + 2 = ?</p>"
    # пустой вариант пропущен
    assert answers(q) == [("0", "3"), ("100", "4"), ("0", "5")]
    assert q.findtext("name/text").startswith("001 ")


def test_multiple_choice_fractions_sum_to_100_and_penalise_wrong():
    t = make_test([{
        "type": "multiple", "text": "Простые числа",
        "options": [{"text": "2", "correct": True}, {"text": "3", "correct": True},
                    {"text": "5", "correct": True}, {"text": "4"}, {"text": "6"}],
    }])
    (q,) = parse(t)
    assert q.findtext("single") == "false"
    fr = [float(f) for f, _ in answers(q)]
    assert fr[:3] == [33.33333] * 3
    assert fr[3:] == [-50.0, -50.0]


def test_snap_fraction_uses_moodle_list():
    assert snap_fraction(100 / 7) == 14.28571
    assert snap_fraction(-100 / 12) == -10.0
    assert snap_fraction(100 / 3) == 33.33333


def test_truefalse():
    t = make_test([{"type": "truefalse", "text": "Земля круглая", "correct": False}])
    (q,) = parse(t)
    assert q.get("type") == "truefalse"
    assert answers(q) == [("0", "true"), ("100", "false")]


def test_shortanswer_and_case():
    t = make_test([{"type": "short", "text": "Столица РБ", "answers": ["Минск", " г. Минск ", ""],
                    "case_sensitive": True}])
    (q,) = parse(t)
    assert q.get("type") == "shortanswer"
    assert q.findtext("usecase") == "1"
    assert answers(q) == [("100", "Минск"), ("100", "г. Минск")]


def test_numerical_with_comma_and_tolerance():
    t = make_test([{"type": "numerical", "text": "Пи", "number": "3,14", "tolerance": "0,01"}])
    (q,) = parse(t)
    (a,) = q.findall("answer")
    assert a.findtext("text") == "3.14"
    assert a.findtext("tolerance") == "0.01"


def test_matching_with_distractor():
    t = make_test([{
        "type": "matching", "text": "Сопоставьте",
        "pairs": [{"left": "H2O", "right": "Вода"}, {"left": "NaCl", "right": "Соль"},
                  {"left": "", "right": ""}],
        "distractors": ["Сахар"],
    }])
    (q,) = parse(t)
    subs = q.findall("subquestion")
    assert [(s.findtext("text"), s.findtext("answer/text")) for s in subs] == [
        ("<p>H2O</p>", "Вода"), ("<p>NaCl</p>", "Соль"), ("", "Сахар")]


def test_image_is_embedded_and_only_from_same_test():
    images = {"img1": {"id": "img1aaaaaaaa", "test_id": 1, "mime": "image/png", "data": PNG}}
    t = make_test([{"type": "truefalse", "text": "Что на картинке?", "image": "img1"}])
    (q,) = parse(t, lambda image_id: images.get(image_id))
    f = q.find("questiontext/file")
    assert f is not None and f.get("encoding") == "base64"
    assert base64.b64decode(f.text) == PNG
    assert f"@@PLUGINFILE@@/{f.get('name')}" in q.findtext("questiontext/text")
    (q2,) = parse(t, lambda image_id: None)
    assert q2.find("questiontext/file") is None


def test_html_is_escaped():
    assert text_to_html("a < b & c\nстрока 2\n\nабзац") == "<p>a &lt; b &amp; c<br>строка 2</p><p>абзац</p>"
    t = make_test([{"type": "single", "text": "<script>", "options": [
        {"text": "<b>x</b>", "correct": True}, {"text": "y"}]}])
    (q,) = parse(t)
    assert q.findtext("questiontext/text") == "<p>&lt;script&gt;</p>"
    assert answers(q)[0][1] == "&lt;b&gt;x&lt;/b&gt;"


def test_points():
    t = make_test([{"type": "truefalse", "text": "x", "points": 2.5}])
    (q,) = parse(t)
    assert float(q.findtext("defaultgrade")) == 2.5


def test_export_zip_contents():
    good = make_test([{"type": "truefalse", "text": "x"}], id=1, discipline="Химия")
    bad = make_test([{"type": "single", "text": "y"}], id=2, discipline="Био/логия")
    data = export_zip([good, bad])
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert "001 Химия.xml" in names
    assert "с ошибками/002 Био логия.xml" in names
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest[0]["time_limit_minutes"] == 30
    assert manifest[1]["errors"]
    assert zf.read("tests.csv").decode("utf-8").startswith("﻿ID;")


def test_text_is_cdata_and_survives_special_chars():
    t = make_test([{"type": "single", "text": "a & & b ]]> c\x0bновая строка", "options": [
        {"text": "x < y", "correct": True}, {"text": "1 -- 2"}]}], discipline="Тест -- двойной дефис")
    raw = build_test_xml(t).decode("utf-8")
    assert "<![CDATA[<p>a &amp; &amp; b ]]&gt; c<br>новая строка</p>]]>" in raw
    # В названии вопроса текст как есть: «]]>» разбивается на две CDATA-секции.
    assert "]]]]><![CDATA[>" in raw
    assert "\x0b" not in raw
    assert "--" not in raw.split("<question")[0].replace("<!--", "").replace("-->", "")
    (q,) = ET.fromstring(raw.encode("utf-8")).findall("question")
    assert q.findtext("questiontext/text") == "<p>a &amp; &amp; b ]]&gt; c<br>новая строка</p>"
    assert "]]>" in q.findtext("name/text")
