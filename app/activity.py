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
        "role": session.get("role") or "гость",  # гость — запрос без входа на сайт
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


def _matching(path: Path, query: str, level: str):
    needle = query.casefold().strip()
    levels = LEVELS.get(level)
    for entry in entries_newest_first(path):
        if levels and entry["level"] not in levels:
            continue
        if needle and needle not in entry["text"].casefold():
            continue
        yield entry


def _edge(entries: list[dict], ts: str | None, already: int, at: str | None) -> tuple[str | None, int]:
    """Курсор на краю списка: время и сколько событий с этим временем уже показано."""
    if ts is None:
        return at, already
    return ts, sum(1 for e in entries if e["ts"] == ts) + (already if ts == at else 0)


def search(path: Path, *, query: str = "", level: str = "", before: str | None = None, skip: int = 0,
           limit: int = 200) -> dict:
    """Порция событий в прошлое для окна журнала, по возрастанию времени.

    Без before — самые свежие события. before/skip — курсор «что уже показано»: события
    не новее before, причём из событий ровно с временем before первые skip уже показаны.
    newest/newest_skip — курсор для автообновления (см. newer).
    """
    found: list[dict] = []
    seen_at_before = 0
    has_more = False
    for entry in _matching(path, query, level):
        if before is not None:
            if entry["ts"] > before:
                continue
            if entry["ts"] == before and seen_at_before < skip:
                seen_at_before += 1
                continue
        if len(found) >= limit:
            has_more = True
            break
        found.append(entry)
    found.reverse()
    oldest, oldest_skip = _edge(found, found[0]["ts"] if found else None, skip, before)
    result = {"entries": found, "before": oldest, "skip": oldest_skip, "has_more": has_more}
    if before is None:
        result["newest"], result["newest_skip"] = _edge(found, found[-1]["ts"] if found else None, 0, None)
    return result


def newer(path: Path, *, after: str | None, after_skip: int = 0, query: str = "", level: str = "",
          limit: int = 500) -> dict:
    """События новее уже показанных — для автообновления окна журнала.

    after/after_skip — время самого нового показанного события и сколько событий с этим
    временем уже показано. Если новых слишком много, возвращает reset: окну проще перезагрузиться.
    """
    collected: list[dict] = []
    for entry in _matching(path, query, level):
        if after is not None and entry["ts"] < after:
            break
        collected.append(entry)
        if len(collected) > limit + after_skip:
            return {"entries": [], "reset": True}
    collected.reverse()
    fresh, drop = [], after_skip
    for entry in collected:  # события ровно с временем after идут первыми, старшие из них уже показаны
        if drop and entry["ts"] == after:
            drop -= 1
            continue
        fresh.append(entry)
    newest, newest_skip = _edge(fresh, fresh[-1]["ts"] if fresh else None, after_skip, after)
    return {"entries": fresh, "newest": newest, "newest_skip": newest_skip, "reset": False}


def tail(path: Path, lines: int) -> str:
    """Последние строки журнала (текущего файла и предыдущего, если текущий короткий)."""
    files = [path.with_name(f"{path.name}.1"), path]
    chunks: list[str] = []
    for file in files:
        if file.exists():
            chunks.extend(file.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(chunks[-lines:]) + "\n"
