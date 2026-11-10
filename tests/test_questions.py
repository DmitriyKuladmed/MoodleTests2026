from app.questions import normalize_question, normalize_settings, validate_question, validate_test


def msgs(q):
    return [i["message"] for i in validate_question(normalize_question(q)) if i["level"] == "error"]


def test_defaults_and_unknown_type():
    q = normalize_question({"type": "nonsense"})
    assert q["type"] == "single"
    assert len(q["options"]) == 4
    assert q["points"] == 1.0
    assert q["id"]


def test_single_requires_one_correct():
    assert "Отметьте верный ответ" in msgs({"type": "single", "text": "x", "options": [
        {"text": "a"}, {"text": "b"}]})
    assert any("несколько верных" in m for m in msgs({"type": "single", "text": "x", "options": [
        {"text": "a", "correct": True}, {"text": "b", "correct": True}]}))
    assert msgs({"type": "single", "text": "x", "options": [{"text": "a", "correct": True}, {"text": "b"}]}) == []


def test_needs_two_options_and_text():
    errors = msgs({"type": "single", "text": " ", "options": [{"text": "a", "correct": True}, {"text": ""}]})
    assert "Напишите текст вопроса" in errors
    assert "Нужно хотя бы 2 варианта ответа" in errors


def test_image_only_question_is_ok():
    assert msgs({"type": "truefalse", "text": "", "image": "abc"}) == []


def test_multiple_limits():
    opts = [{"text": str(i), "correct": True} for i in range(11)] + [{"text": "x"}]
    assert any("Не больше 10" in m for m in msgs({"type": "multiple", "text": "q", "options": opts}))


def test_numerical_and_short():
    assert msgs({"type": "numerical", "text": "q", "number": "abc"})
    assert msgs({"type": "numerical", "text": "q", "number": "1,5", "tolerance": "-1"})
    assert msgs({"type": "numerical", "text": "q", "number": "1,5", "tolerance": "0,1"}) == []
    assert msgs({"type": "short", "text": "q", "answers": [" "]})


def test_matching_rules():
    assert msgs({"type": "matching", "text": "q", "pairs": [{"left": "a", "right": "1"}, {"left": "b", "right": "2"}]})
    assert msgs({"type": "matching", "text": "q", "pairs": [
        {"left": "a", "right": "1"}, {"left": "b", "right": "2"}], "distractors": ["3"]}) == []
    assert any("Заполните пары" in m for m in msgs({"type": "matching", "text": "q", "pairs": [
        {"left": "a", "right": "1"}, {"left": "b", "right": "2"}, {"left": "c", "right": ""}]}))


def test_validate_test_level():
    test = {"discipline": "", "teacher": "", "settings": normalize_settings({"questions_per_attempt": 5}),
            "questions": [normalize_question({"type": "truefalse", "text": "x"})]}
    errors = [i["message"] for i in validate_test(test) if i["level"] == "error"]
    assert "Укажите дисциплину" in errors
    assert any("ФИО преподавателя" in e for e in errors)
    assert any("в тесте 1" in e for e in errors)


def test_settings_clamped():
    s = normalize_settings({"time_limit": "-5", "attempts": "abc", "questions_per_attempt": "20"})
    assert s["time_limit"] == 1
    assert s["attempts"] is None
    assert s["questions_per_attempt"] == 20
