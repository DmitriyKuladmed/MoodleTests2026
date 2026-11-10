from app.paste_parser import parse_text


def test_numbered_with_markers():
    text = """Тест по физике

1. Чему равно g?
а) 9,8 м/с² *
б) 10 км/ч
в) 1 Н

2) Выберите векторные
величины
*а) скорость
+б) сила
в) масса
"""
    r = parse_text(text)
    q1, q2 = r["questions"]
    assert q1["type"] == "single"
    assert q1["text"] == "Чему равно g?"
    assert [o["correct"] for o in q1["options"]] == [True, False, False]
    assert q1["options"][0]["text"] == "9,8 м/с²"
    assert q2["type"] == "multiple"
    assert q2["text"] == "Выберите векторные\nвеличины"
    assert [o["correct"] for o in q2["options"]] == [True, True, False]
    assert r["warnings"] == []


def test_answer_line_letters_and_latin_lookalike():
    text = """1. Вопрос
a) один
б) два
в) три
Ответ: а, в
"""
    (q,) = parse_text(text)["questions"]
    assert [o["correct"] for o in q["options"]] == [True, False, True]
    assert q["type"] == "multiple"


def test_answer_line_compact_and_text():
    (q,) = parse_text("1. Q\nа) x\nб) y\nПравильный ответ: б")["questions"]
    assert [o["correct"] for o in q["options"]] == [False, True]
    (q,) = parse_text("1. Q\nа) Минск\nб) Брест\nОтвет: Брест")["questions"]
    assert [o["correct"] for o in q["options"]] == [False, True]


def test_no_options_gives_short_or_numerical():
    r = parse_text("1. Сколько будет 2+2?\nОтвет: 4\n2. Столица Беларуси?\nОтвет: Минск; г. Минск")
    q1, q2 = r["questions"]
    assert q1["type"] == "numerical" and q1["number"] == "4"
    assert q2["type"] == "short" and q2["answers"] == ["Минск", "г. Минск"]


def test_unnumbered_blocks():
    text = "Столица Франции?\nПариж *\nЛондон\n\nБольше нуля?\n- минус один\n+ один\n"
    q1, q2 = parse_text(text)["questions"]
    assert q1["text"] == "Столица Франции?"
    assert [(o["text"], o["correct"]) for o in q1["options"]] == [("Париж", True), ("Лондон", False)]
    assert [(o["text"], o["correct"]) for o in q2["options"]] == [("минус один", False), ("один", True)]


def test_warnings():
    r = parse_text("1. Без ответа\nа) x\nб) y\n2. Пусто")
    msgs = [(w["index"], w["message"]) for w in r["warnings"]]
    assert (0, "Не отмечен правильный ответ") in msgs
    assert (1, "Нет вариантов ответа") in msgs


def test_wrapped_option_line():
    (q,) = parse_text("1. Q\nа) длинный\nвариант *\nб) y")["questions"]
    assert q["options"][0]["text"] == "длинный вариант"
    assert q["options"][0]["correct"] is True
