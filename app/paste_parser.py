"""Разбор теста, вставленного текстом (например, скопированного из Word).

Поддерживаемый формат (как обычно оформляют тесты):

    1. Текст вопроса
    а) неверный вариант
    *б) правильный вариант          ← правильный помечается * или + в начале
    в) ещё вариант *                ← …или * в конце строки
    Ответ: б                        ← …или строкой «Ответ:» (можно несколько: «а, в»)

    2. Вопрос без вариантов
    Ответ: 42                       ← без вариантов → короткий/числовой ответ

Если вопросы не пронумерованы, они разделяются пустой строкой: первая строка
блока — вопрос, остальные — варианты.
"""
import re

from .questions import normalize_question, parse_number

Q_HEADER = re.compile(r"^\s*(?:вопрос\s*№?\s*)?(\d{1,4})\s*[.)]\s*(.*)$", re.IGNORECASE)
OPT_LETTER = re.compile(r"^\s*([*+]?)\s*([a-zа-яё])\s*(?:\)\s*|\.\s+)(.+)$", re.IGNORECASE)
OPT_BULLET_OK = re.compile(r"^\s*[+*]\s+(.+)$")
OPT_BULLET = re.compile(r"^\s*[-–—•·]\s*(.+)$")
ANSWER_LINE = re.compile(
    r"^\s*(?:правильн(?:ый|ые)\s+ответ(?:ы)?|ответ(?:ы)?|верн(?:ый|ые)\s+ответ(?:ы)?)\s*[:\-–—]\s*(.+)$",
    re.IGNORECASE,
)
TRAILING_MARK = re.compile(r"\s*(?:\*|\(\s*[+*]\s*\)|\(\s*(?:верно|правильно|правильный)\s*\))\s*$", re.IGNORECASE)

# Латиница, похожая на кириллицу, в метках вариантов: «a)» и «а)» — одно и то же.
LOOKALIKE = str.maketrans({"a": "а", "b": "б", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х"})


def _label_key(label: str) -> str:
    return label.lower().translate(LOOKALIKE)


def _strip_mark(text: str) -> tuple[str, bool]:
    m = TRAILING_MARK.search(text)
    if m and m.start() > 0:
        return text[: m.start()].rstrip(), True
    return text, False


class _Draft:
    def __init__(self, text: str = "", plain: bool = False):
        # Строки текста хранятся как есть (с возможной «*»): если окажется, что это
        # варианты без букв, отметка верного ответа понадобится.
        self.text_lines = [text] if text else []
        self.options: list[dict] = []  # {"label", "text", "correct"}
        self.answer: str | None = None
        self.plain = plain  # вопрос без номера: строки после текста — варианты

    def add_option(self, text: str, correct: bool, label: str | None = None):
        text, marked = _strip_mark(text.strip())
        self.options.append({"label": label, "text": text, "correct": correct or marked})

    @property
    def empty(self) -> bool:
        return not self.text_lines and not self.options and self.answer is None


def _is_answer_part(line: str) -> bool:
    return bool(ANSWER_LINE.match(line) or OPT_LETTER.match(line) or OPT_BULLET_OK.match(line)
                or OPT_BULLET.match(line))


def _parse_line_into(draft: _Draft, line: str, plain_options: bool) -> None:
    m = ANSWER_LINE.match(line)
    if m:
        draft.answer = m.group(1).strip()
        return
    m = OPT_LETTER.match(line)
    if m and (draft.text_lines or draft.options):
        draft.add_option(m.group(3), bool(m.group(1)), _label_key(m.group(2)))
        return
    m = OPT_BULLET_OK.match(line)
    if m and draft.text_lines:
        draft.add_option(m.group(1), True)
        return
    m = OPT_BULLET.match(line)
    if m and draft.text_lines:
        draft.add_option(m.group(1), False)
        return
    if draft.options:
        if plain_options:
            draft.add_option(line, False)
        else:
            # Перенос строки внутри варианта.
            last = draft.options[-1]
            text, marked = _strip_mark(last["text"] + " " + line.strip())
            last["text"] = text
            last["correct"] = last["correct"] or marked
        return
    if plain_options and draft.text_lines:
        draft.add_option(line, False)
        return
    draft.text_lines.append(line.strip())


def _apply_answer(draft: _Draft) -> str | None:
    """Отмечает правильные варианты по строке «Ответ:». Возвращает предупреждение или None."""
    if draft.answer is None or not draft.options:
        return None
    answer = draft.answer.strip().rstrip(".")
    tokens = [t for t in re.split(r"[\s,;]+", answer) if t]
    labels = {o["label"] for o in draft.options if o["label"]}
    keys = [_label_key(t.rstrip(").")) for t in tokens]
    if labels and keys and all(k in labels for k in keys):
        for o in draft.options:
            o["correct"] = o["label"] in keys
        return None
    # Слитно: «Ответ: ав».
    compact = _label_key(answer.replace(" ", "").replace(",", ""))
    if labels and compact and all(ch in labels for ch in compact):
        for o in draft.options:
            o["correct"] = o["label"] in compact
        return None
    # Ответ текстом, совпадающим с вариантом.
    for o in draft.options:
        if o["text"].strip().lower() == answer.lower():
            for other in draft.options:
                other["correct"] = other is o
            return None
    return f"Не понял строку «Ответ: {draft.answer}»"


def _to_question(draft: _Draft) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    lines = draft.text_lines
    # «1. Вопрос» и дальше варианты без букв (каждый с новой строки): первая строка — вопрос.
    if not draft.options and len(lines) >= 3:
        for raw in lines[1:]:
            draft.add_option(raw, False)
        lines = lines[:1]
    # Если в Word вопрос был выделен жирным, при вставке он тоже получил «*» — для вопроса это ничего не значит.
    text = "\n".join(_strip_mark(line)[0] for line in lines).strip()
    if draft.options:
        problem = _apply_answer(draft)
        if problem:
            warnings.append(problem)
        n_correct = sum(1 for o in draft.options if o["correct"])
        qtype = "multiple" if n_correct > 1 else "single"
        if n_correct == 0:
            warnings.append("Нет верного ответа")
        q = normalize_question({
            "type": qtype,
            "text": text,
            "options": [{"text": o["text"], "correct": o["correct"]} for o in draft.options],
        })
    elif draft.answer is not None:
        number = parse_number(draft.answer)
        if number is not None:
            q = normalize_question({"type": "numerical", "text": text, "number": draft.answer.strip()})
        else:
            variants = [a.strip() for a in re.split(r"\s*(?:;|\||\s/\s)\s*", draft.answer) if a.strip()]
            q = normalize_question({"type": "short", "text": text, "answers": variants})
    else:
        warnings.append("Нет вариантов ответа")
        q = normalize_question({"type": "single", "text": text})
    if not text:
        warnings.append("Нет текста вопроса")
    return q, warnings


def parse_text(raw: str) -> dict:
    """Возвращает {"questions": [...], "warnings": [{"index": n, "message": ...}]}."""
    lines = [ln.rstrip() for ln in (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    numbered = any(Q_HEADER.match(ln) for ln in lines)
    drafts: list[_Draft] = []

    if numbered:
        current: _Draft | None = None
        after_blank = False
        for line in lines:
            if not line.strip():
                after_blank = True
                continue
            m = Q_HEADER.match(line)
            if m:
                current = _Draft(m.group(2).strip())
                drafts.append(current)
                after_blank = False
                continue
            if current is None:
                continue  # текст до первого вопроса (заголовок документа и т. п.)
            # После пустой строки за вариантами — новый вопрос, у которого просто нет номера.
            if after_blank and current.options and not _is_answer_part(line):
                current = _Draft(line.strip(), plain=True)
                drafts.append(current)
                after_blank = False
                continue
            after_blank = False
            _parse_line_into(current, line, plain_options=current.plain)
    else:
        current = _Draft()
        for line in lines + [""]:
            if not line.strip():
                if not current.empty:
                    drafts.append(current)
                current = _Draft()
                continue
            _parse_line_into(current, line, plain_options=True)

    questions, warnings = [], []
    for index, draft in enumerate(d for d in drafts if not d.empty):
        q, problems = _to_question(draft)
        questions.append(q)
        warnings.extend({"index": index, "message": p} for p in problems)
    return {"questions": questions, "warnings": warnings}
