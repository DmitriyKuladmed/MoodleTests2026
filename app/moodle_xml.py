"""Выгрузка теста в формат Moodle XML (Банк вопросов → Импорт → «Формат Moodle XML»).

Файл не содержит категории: вопросы попадают в категорию, выбранную при импорте.
"""
import base64
import csv
import html
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Callable

from .questions import (
    QUESTION_TYPES,
    complete_pairs,
    filled,
    filled_options,
    has_errors,
    parse_number,
    validate_test,
)

# Допустимые в Moodle оценки ответов, в процентах (question_bank::fraction_options_full).
MOODLE_FRACTIONS = [
    100.0, 90.0, 83.33333, 80.0, 75.0, 70.0, 66.66667, 60.0, 50.0, 40.0,
    33.33333, 30.0, 25.0, 20.0, 16.66667, 14.28571, 12.5, 11.11111, 10.0, 5.0, 0.0,
]

IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

ImageGetter = Callable[[str], dict | None]


def snap_fraction(percent: float) -> float:
    """Ближайшая допустимая в Moodle оценка (с сохранением знака)."""
    sign = -1 if percent < 0 else 1
    best = min(MOODLE_FRACTIONS, key=lambda f: abs(f - abs(percent)))
    return sign * best


def fmt_fraction(percent: float) -> str:
    text = f"{percent:.5f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def text_to_html(text: str) -> str:
    """Обычный текст → HTML: экранирование, абзацы и переносы строк."""
    text = text.strip()
    if not text:
        return ""
    paragraphs = re.split(r"\n\s*\n", text)
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>" for p in paragraphs
    )


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def _text_block(parent: ET.Element, tag: str, value: str, fmt: str = "html") -> ET.Element:
    el = _sub(parent, tag, format=fmt)
    _sub(el, "text", value)
    return el


def _answer(parent: ET.Element, fraction: float, value: str, fmt: str = "html", **extra) -> ET.Element:
    ans = _sub(parent, "answer", fraction=fmt_fraction(fraction), format=fmt)
    _sub(ans, "text", value)
    fb = _sub(ans, "feedback", format="html")
    _sub(fb, "text", "")
    for tag, val in extra.items():
        _sub(ans, tag, val)
    return ans


def _question_name(index: int, q: dict) -> str:
    base = " ".join(q["text"].split())
    if len(base) > 80:
        base = base[:77].rstrip() + "…"
    return f"{index:03d} {base}" if base else f"{index:03d} Вопрос с картинкой"


def _combined_feedback(qel: ET.Element) -> None:
    for tag in ("correctfeedback", "partiallycorrectfeedback", "incorrectfeedback"):
        _text_block(qel, tag, "")


def question_element(index: int, q: dict, get_image: ImageGetter | None) -> ET.Element:
    moodle_type = {
        "single": "multichoice",
        "multiple": "multichoice",
        "truefalse": "truefalse",
        "short": "shortanswer",
        "numerical": "numerical",
        "matching": "matching",
    }[q["type"]]
    qel = ET.Element("question", {"type": moodle_type})
    name = _sub(qel, "name")
    _sub(name, "text", _question_name(index, q))

    body = text_to_html(q["text"])
    image = get_image(q["image"]) if (q["image"] and get_image) else None
    qtext = _sub(qel, "questiontext", format="html")
    if image:
        ext = IMAGE_EXT.get(image["mime"], "png")
        filename = f"q{index:03d}_{image['id'][:8]}.{ext}"
        body += f'<p><img src="@@PLUGINFILE@@/{filename}" alt="" class="img-fluid"></p>'
        _sub(qtext, "text", body)
        _sub(qtext, "file", base64.b64encode(image["data"]).decode("ascii"),
             name=filename, path="/", encoding="base64")
    else:
        _sub(qtext, "text", body)

    _text_block(qel, "generalfeedback", "")
    _sub(qel, "defaultgrade", f"{q['points']:.7f}")
    _sub(qel, "penalty", "1.0000000" if q["type"] == "truefalse" else "0.3333333")
    _sub(qel, "hidden", "0")
    _sub(qel, "idnumber", "")

    t = q["type"]
    if t in ("single", "multiple"):
        opts = filled_options(q)
        _sub(qel, "single", "true" if t == "single" else "false")
        _sub(qel, "shuffleanswers", "true" if q["shuffle"] else "false")
        _sub(qel, "answernumbering", "abc")
        # «Выберите один ответ» / «Выберите один или несколько ответов» — студенту полезно.
        _sub(qel, "showstandardinstruction", "1")
        _combined_feedback(qel)
        if t == "single":
            for o in opts:
                _answer(qel, 100.0 if o["correct"] else 0.0, html.escape(o["text"].strip()))
        else:
            n_correct = sum(1 for o in opts if o["correct"])
            n_wrong = len(opts) - n_correct
            plus = snap_fraction(100.0 / n_correct) if n_correct else 0.0
            minus = snap_fraction(-100.0 / n_wrong) if n_wrong else 0.0
            _sub(qel, "shownumcorrect")
            for o in opts:
                _answer(qel, plus if o["correct"] else minus, html.escape(o["text"].strip()))
    elif t == "truefalse":
        _answer(qel, 100.0 if q["correct"] else 0.0, "true", fmt="moodle_auto_format")
        _answer(qel, 0.0 if q["correct"] else 100.0, "false", fmt="moodle_auto_format")
    elif t == "short":
        _sub(qel, "usecase", "1" if q["case_sensitive"] else "0")
        for a in filled(q["answers"]):
            _answer(qel, 100.0, a, fmt="moodle_auto_format")
    elif t == "numerical":
        value = parse_number(q["number"])
        tol = parse_number(q["tolerance"]) if q["tolerance"].strip() else 0.0
        _answer(qel, 100.0, _num_str(value), fmt="moodle_auto_format", tolerance=_num_str(tol or 0.0))
        _sub(qel, "unitgradingtype", "0")
        _sub(qel, "unitpenalty", "0.1000000")
        _sub(qel, "showunits", "3")
        _sub(qel, "unitsleft", "0")
    elif t == "matching":
        _sub(qel, "shuffleanswers", "true")
        _combined_feedback(qel)
        for p in complete_pairs(q):
            sq = _sub(qel, "subquestion", format="html")
            _sub(sq, "text", text_to_html(p["left"]))
            a = _sub(sq, "answer")
            _sub(a, "text", p["right"].strip())
        for d in filled(q["distractors"]):
            sq = _sub(qel, "subquestion", format="html")
            _sub(sq, "text", "")
            a = _sub(sq, "answer")
            _sub(a, "text", d)
    return qel


def _num_str(value: float | None) -> str:
    if value is None:
        return "0"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


# Символы, недопустимые в XML 1.0 (бывают в тексте, скопированном из Word).
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml_safe(value: str) -> str:
    return _BAD_XML_CHARS.sub("", value)


def _cdata(value: str) -> str:
    # Как в самом Moodle (xml_escape): CDATA, «]]>» разбивается на две секции.
    return "<![CDATA[" + _xml_safe(value).replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _attrs(el: ET.Element) -> str:
    return "".join(f' {k}="{html.escape(_xml_safe(v), quote=True)}"' for k, v in el.attrib.items())


def _serialize(el: ET.Element, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    if el.tag is ET.Comment:
        text = _xml_safe(el.text or "").replace("--", "—")
        out.append(f"{pad}<!--{text}-->")
        return
    children = list(el)
    if el.tag == "text":
        # Весь пользовательский текст — в CDATA: xmlize в Moodle теряет пробелы между сущностями.
        out.append(f"{pad}<text>{_cdata(el.text) if el.text else ''}</text>")
    elif children:
        out.append(f"{pad}<{el.tag}{_attrs(el)}>")
        for child in children:
            _serialize(child, depth + 1, out)
        out.append(f"{pad}</{el.tag}>")
    elif el.text:
        out.append(f"{pad}<{el.tag}{_attrs(el)}>{html.escape(_xml_safe(el.text), quote=False)}</{el.tag}>")
    else:
        out.append(f"{pad}<{el.tag}{_attrs(el)}/>")


def build_test_xml(test: dict, get_image: ImageGetter | None = None) -> bytes:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<quiz>"]
    _serialize(ET.Comment(f" {test['discipline']} / {test['teacher']} "), 1, out)
    for i, q in enumerate(test["questions"], start=1):
        _serialize(question_element(i, q, get_image), 1, out)
    out.append("</quiz>")
    return ("\n".join(out) + "\n").encode("utf-8")


def safe_filename(name: str, fallback: str = "test") -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return (name or fallback)[:100]


def xml_filename(test: dict) -> str:
    return f"{test['id']:03d} {safe_filename(test['discipline'], 'Без названия')}.xml"


def manifest_row(test: dict, issues: list[dict]) -> dict:
    s = test["settings"]
    counts: dict[str, int] = {}
    for q in test["questions"]:
        counts[q["type"]] = counts.get(q["type"], 0) + 1
    return {
        "id": test["id"],
        "file": xml_filename(test),
        "discipline": test["discipline"],
        "teacher": test["teacher"],
        "department": test["department"],
        "status": test["status"],
        "questions_total": len(test["questions"]),
        "questions_by_type": {QUESTION_TYPES[k]: v for k, v in counts.items()},
        "time_limit_minutes": s.get("time_limit"),
        "questions_per_attempt": s.get("questions_per_attempt") or len(test["questions"]),
        "attempts": s.get("attempts"),
        "pass_percent": s.get("pass_percent"),
        "shuffle_questions": s.get("shuffle_questions"),
        "comment": s.get("comment"),
        "errors": [i["message"] for i in issues if i["level"] == "error"],
        "updated_at": test["updated_at"],
    }


def export_zip(tests: list[dict], image_getter_for: Callable[[int], ImageGetter] | None = None) -> bytes:
    """Архив: по одному XML на тест + manifest.json и tests.csv с настройками тестов.

    ``image_getter_for(test_id)`` возвращает функцию получения картинок этого теста.
    """

    buf = io.BytesIO()
    rows = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for test in tests:
            issues = validate_test(test)
            rows.append(manifest_row(test, issues))
            folder = "с ошибками/" if has_errors(issues) else ""
            getter = image_getter_for(test["id"]) if image_getter_for else None
            zf.writestr(folder + xml_filename(test), build_test_xml(test, getter))
        zf.writestr("manifest.json", json.dumps(rows, ensure_ascii=False, indent=2))
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf, delimiter=";")
        writer.writerow([
            "ID", "Файл", "Дисциплина", "Преподаватель", "Кафедра", "Статус", "Вопросов",
            "Показывать вопросов", "Время, мин", "Попыток", "Проходной %", "Ошибки", "Комментарий",
        ])
        for r in rows:
            writer.writerow([
                r["id"], r["file"], r["discipline"], r["teacher"], r["department"],
                "готов" if r["status"] == "ready" else "черновик", r["questions_total"],
                r["questions_per_attempt"], r["time_limit_minutes"] or "", r["attempts"] or "",
                r["pass_percent"] or "", " | ".join(r["errors"]), r["comment"] or "",
            ])
        # BOM, чтобы Excel правильно открыл кириллицу.
        zf.writestr("tests.csv", "﻿" + csv_buf.getvalue())
    return buf.getvalue()
