"""Журнал действий и ошибок.

Каждая строка — одно событие: время (Минск), уровень, событие и поля key=value.
В начале полей — контекст запроса: rid (код запроса, его видит пользователь в сообщении
об ошибке), sid (сеанс браузера после входа), role и ip. Пароли и тексты вопросов в журнал
не пишутся.
"""
import logging
import logging.handlers
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request

MINSK = timezone(timedelta(hours=3))  # Беларусь, без перехода на летнее время
LOG_FILE = "app.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 10

log = logging.getLogger("moodle_tests")


class _MinskFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 — имя из logging
        return datetime.fromtimestamp(record.created, MINSK).strftime("%Y-%m-%d %H:%M:%S")


def setup(log_dir: Path, to_stdout: bool = True) -> Path:
    """Пишет журнал в log_dir/app.log (с ротацией) и, по желанию, в stdout для `docker logs`."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILE
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    formatter = _MinskFormatter("%(asctime)s %(levelname)-7s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                        encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)
    if to_stdout:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        log.addHandler(stream)
    log.setLevel(logging.INFO)
    log.propagate = False
    return path


def new_id(length: int = 8) -> str:
    return secrets.token_hex(length // 2)


LONG_FIELDS = {"stack": 1500, "details": 500, "message": 500}


def _value(value, limit: int = 300) -> str:
    if value is None or value == "":
        return "-"
    text = " ".join(str(value).split())  # одна строка журнала — одно событие
    if len(text) > limit:
        text = text[:limit] + "…"
    if any(c in text for c in ' ="'):
        text = '"' + text.replace('"', "'") + '"'
    return text


def fields_text(fields: dict) -> str:
    """Поля key=value; поля со значением None пропускаются, чтобы не шуметь."""
    return " ".join(f"{key}={_value(value, LONG_FIELDS.get(key, 300))}"
                    for key, value in fields.items() if value is not None)


def context(request: Request) -> dict:
    session = request.scope.get("session") or {}
    return {  # контекст пишем всегда, пустое значение — «-»
        "rid": getattr(request.state, "rid", None) or "-",
        "sid": session.get("sid") or "-",
        "role": session.get("role") or "-",
        "ip": request.client.host if request.client else "-",
    }


def event(request: Request, name: str, level: int = logging.INFO, /, **fields) -> None:
    """Событие с контекстом запроса. Помечает запрос, чтобы не дублировать строку о нём."""
    request.state.logged = True
    log.log(level, "%-18s %s", name, fields_text({**context(request), **fields}))


_ENTRY = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\w+)\s")
LEVELS = {"WARNING": ("WARNING", "ERROR", "CRITICAL"), "ERROR": ("ERROR", "CRITICAL")}


def entries_newest_first(path: Path):
    """События журнала от новых к старым, по всем файлам ротации.

    Строки без времени в начале (трассировка ошибки) приклеиваются к своему событию.
    """
    for file in [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, BACKUPS + 1))]:
        if not file.exists():
            continue
        tail_lines: list[str] = []
        for line in reversed(file.read_text(encoding="utf-8", errors="replace").splitlines()):
            match = _ENTRY.match(line)
            if not match:
                tail_lines.append(line)
                continue
            yield {"ts": match[1], "level": match[2], "text": "\n".join([line, *reversed(tail_lines)])}
            tail_lines = []


def search(path: Path, *, query: str = "", level: str = "", before: str | None = None, skip: int = 0,
           since: str | None = None, limit: int = 200) -> dict:
    """Страница событий для просмотра в админке, по возрастанию времени.

    before/skip — курсор «что уже показано»: события не новее before, и из событий ровно
    с временем before первые skip уже показаны. since — нижняя граница времени (первая загрузка).
    """
    needle = query.casefold().strip()
    levels = LEVELS.get(level)
    found: list[dict] = []
    seen_at_before = 0
    has_more = False
    for entry in entries_newest_first(path):
        if levels and entry["level"] not in levels:
            continue
        if needle and needle not in entry["text"].casefold():
            continue
        if before is not None:
            if entry["ts"] > before:
                continue
            if entry["ts"] == before and seen_at_before < skip:
                seen_at_before += 1
                continue
        if (since is not None and entry["ts"] < since) or len(found) >= limit:
            has_more = True
            break
        found.append(entry)
    found.reverse()
    if found:
        oldest = found[0]["ts"]
        same = sum(1 for e in found if e["ts"] == oldest)
        cursor = {"before": oldest, "skip": same + (skip if oldest == before else 0)}
    else:
        cursor = {"before": since if since is not None else before, "skip": 0 if since is not None else skip}
    return {"entries": found, **cursor, "has_more": has_more}


def tail(path: Path, lines: int) -> str:
    """Последние строки журнала (текущего файла и предыдущего, если текущий короткий)."""
    files = [path.with_name(f"{path.name}.1"), path]
    chunks: list[str] = []
    for file in files:
        if file.exists():
            chunks.extend(file.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(chunks[-lines:]) + "\n"
