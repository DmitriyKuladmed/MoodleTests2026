"""Модель вопроса: нормализация входных данных и проверка на ошибки.

Вопрос хранится как dict. Поля всех типов хранятся всегда, чтобы при смене
типа в редакторе ничего не терялось; в Moodle выгружаются только поля,
относящиеся к текущему типу.
"""
import math
import re
import uuid

QUESTION_TYPES = {
    "single": "Выбор одного ответа",
    "multiple": "Выбор нескольких ответов",
    "truefalse": "Верно или неверно",
    "short": "Ответ словом",
    "numerical": "Ответ числом",
    "matching": "Соответствие",
}

MAX_TEXT = 20_000
MAX_ITEMS = 50
# Moodle принимает дробные оценки ответов только из фиксированного списка,
# поэтому правильных вариантов в «нескольких ответах» не больше 10 (100/n есть в списке).
MAX_CORRECT_MULTIPLE = 10


_CONTROL = re.compile(r"[\x00-\x08\x0c\x0e-\x1f]")


def _str(value, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    # \x0b — перенос строки внутри абзаца в Word (Shift+Enter).
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").replace("\t", " ")
    return _CONTROL.sub("", text)[:limit]


def _bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def _num(value, default: float) -> float:
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _opt_int(value, lo: int, hi: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, result))


def parse_number(value: str) -> float | None:
    """Число из пользовательского ввода (допускается запятая). None, если не число."""
    text = _str(value, 100).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def new_question(qtype: str = "single") -> dict:
    return normalize_question({"type": qtype})


def normalize_question(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    qtype = raw.get("type") if raw.get("type") in QUESTION_TYPES else "single"
    qid = _str(raw.get("id"), 64).strip() or uuid.uuid4().hex

    options = []
    for opt in (raw.get("options") or [])[:MAX_ITEMS]:
        if isinstance(opt, dict):
            options.append({"text": _str(opt.get("text")), "correct": _bool(opt.get("correct"))})
    if "options" not in raw:
        options = [{"text": "", "correct": False} for _ in range(4)]

    pairs = []
    for pair in (raw.get("pairs") or [])[:MAX_ITEMS]:
        if isinstance(pair, dict):
            pairs.append({"left": _str(pair.get("left")), "right": _str(pair.get("right"))})
    if "pairs" not in raw:
        pairs = [{"left": "", "right": ""} for _ in range(3)]

    answers = [_str(a, 1000) for a in (raw.get("answers") or [])[:MAX_ITEMS]]
    if "answers" not in raw:
        answers = [""]

    distractors = [_str(d) for d in (raw.get("distractors") or [])[:MAX_ITEMS]]

    image = raw.get("image")
    image = _str(image, 64).strip() if image else None

    return {
        "id": qid,
        "type": qtype,
        "text": _str(raw.get("text")),
        "image": image or None,
        "points": max(0.0, min(100.0, _num(raw.get("points"), 1.0))),
        "shuffle": _bool(raw.get("shuffle"), True),
        "options": options,
        "correct": _bool(raw.get("correct"), True),
        "answers": answers,
        "case_sensitive": _bool(raw.get("case_sensitive"), False),
        "number": _str(raw.get("number"), 100),
        "tolerance": _str(raw.get("tolerance"), 100),
        "pairs": pairs,
        "distractors": distractors,
    }


def normalize_settings(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "time_limit": _opt_int(raw.get("time_limit"), 1, 600),  # минуты, None = без ограничения
        "questions_per_attempt": _opt_int(raw.get("questions_per_attempt"), 1, 10_000),  # None = все
        "attempts": _opt_int(raw.get("attempts"), 1, 100),  # None = без ограничения
        "pass_percent": _opt_int(raw.get("pass_percent"), 1, 100),
        "shuffle_questions": _bool(raw.get("shuffle_questions"), True),
        "comment": _str(raw.get("comment"), 5000),
    }


def default_settings() -> dict:
    return normalize_settings({"attempts": 1})


# --- проверка ---------------------------------------------------------------

def _issue(level: str, message: str, qid: str | None = None) -> dict:
    return {"level": level, "message": message, "question": qid}


def filled(items: list[str]) -> list[str]:
    return [i.strip() for i in items if i.strip()]


def filled_options(question: dict) -> list[dict]:
    return [o for o in question["options"] if o["text"].strip()]


def complete_pairs(question: dict) -> list[dict]:
    return [p for p in question["pairs"] if p["left"].strip() and p["right"].strip()]


def validate_question(q: dict) -> list[dict]:
    issues: list[dict] = []
    qid = q["id"]

    def err(msg):
        issues.append(_issue("error", msg, qid))

    def warn(msg):
        issues.append(_issue("warning", msg, qid))

    # Сообщения — короткие и говорят, что сделать.
    if not q["text"].strip() and not q["image"]:
        err("Напишите текст вопроса")
    if q["points"] <= 0:
        err("Баллы должны быть больше нуля")

    t = q["type"]
    if t in ("single", "multiple"):
        opts = filled_options(q)
        correct = [o for o in opts if o["correct"]]
        if len(opts) < 2:
            err("Нужно хотя бы 2 варианта ответа")
        if len(correct) == 0:
            err("Отметьте верный ответ")
        elif t == "single" and len(correct) > 1:
            err("Отмечено несколько верных ответов")
        elif len(correct) > MAX_CORRECT_MULTIPLE:
            err(f"Не больше {MAX_CORRECT_MULTIPLE} верных ответов")
        elif t == "multiple" and len(opts) >= 2 and len(correct) == len(opts):
            warn("Все варианты отмечены верными")
        texts = [o["text"].strip().lower() for o in opts]
        if len(set(texts)) != len(texts):
            warn("Есть одинаковые варианты")
        if any(o["correct"] and not o["text"].strip() for o in q["options"]):
            warn("Верным отмечен пустой вариант")
    elif t == "short":
        if not filled(q["answers"]):
            err("Впишите правильный ответ")
    elif t == "numerical":
        if parse_number(q["number"]) is None:
            err("Ответ должен быть числом")
        if q["tolerance"].strip():
            tol = parse_number(q["tolerance"])
            if tol is None or tol < 0:
                err("Погрешность должна быть числом не меньше 0")
    elif t == "matching":
        pairs = complete_pairs(q)
        half = [p for p in q["pairs"] if bool(p["left"].strip()) != bool(p["right"].strip())]
        if half:
            err("Заполните пары с обеих сторон")
        if len(pairs) < 2:
            err("Нужно хотя бы 2 пары")
        elif len(pairs) + len(filled(q["distractors"])) < 3:
            err("Добавьте третью пару или лишний ответ")
        rights = [p["right"].strip().lower() for p in pairs] + [d.lower() for d in filled(q["distractors"])]
        if len(set(rights)) != len(rights):
            warn("Одинаковые ответы справа")
    return issues


def validate_test(test: dict) -> list[dict]:
    issues: list[dict] = []
    if not test["discipline"].strip():
        issues.append(_issue("error", "Укажите дисциплину"))
    if not test["teacher"].strip():
        issues.append(_issue("error", "Укажите ФИО преподавателя"))
    questions = test["questions"]
    if not questions:
        issues.append(_issue("error", "Добавьте вопросы"))
    per_attempt = test["settings"].get("questions_per_attempt")
    if per_attempt and questions and per_attempt > len(questions):
        issues.append(
            _issue("error", f"Студенту нужно {per_attempt} вопросов, а в тесте {len(questions)}")
        )
    seen: dict[str, str] = {}
    for q in questions:
        issues.extend(validate_question(q))
        key = q["text"].strip().lower()
        if key:
            if key in seen:
                issues.append(_issue("warning", "Такой вопрос уже есть", q["id"]))
            else:
                seen[key] = q["id"]
    return issues


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)
