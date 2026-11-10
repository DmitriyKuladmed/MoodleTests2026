"""Хранение данных в SQLite: тесты (вопросы — JSON внутри теста), картинки, настройки."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    discipline  TEXT NOT NULL DEFAULT '',
    teacher     TEXT NOT NULL DEFAULT '',
    department  TEXT NOT NULL DEFAULT '',
    settings    TEXT NOT NULL DEFAULT '{}',
    questions   TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'draft',
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE TABLE IF NOT EXISTS images (
    id          TEXT PRIMARY KEY,
    test_id     INTEGER NOT NULL,
    filename    TEXT NOT NULL,
    mime        TEXT NOT NULL,
    data        BLOB NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS images_test ON images(test_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- настройки -------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --- тесты -----------------------------------------------------------
    @staticmethod
    def _row_to_test(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "discipline": row["discipline"],
            "teacher": row["teacher"],
            "department": row["department"],
            "settings": json.loads(row["settings"]),
            "questions": json.loads(row["questions"]),
            "status": row["status"],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
        }

    def create_test(self, discipline: str, teacher: str, department: str, settings: dict) -> dict:
        ts = now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tests(discipline, teacher, department, settings, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (discipline, teacher, department, json.dumps(settings, ensure_ascii=False), ts, ts),
            )
            test_id = cur.lastrowid
        return self.get_test(test_id)

    def get_test(self, test_id: int, include_deleted: bool = False) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if row is None:
            return None
        test = self._row_to_test(row)
        if test["deleted_at"] and not include_deleted:
            return None
        return test

    def list_tests(self, deleted: bool = False) -> list[dict]:
        cond = "deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tests WHERE {cond} ORDER BY discipline COLLATE NOCASE, id"
            ).fetchall()
        return [self._row_to_test(r) for r in rows]

    def update_test(self, test_id: int, expected_version: int, fields: dict) -> dict | None:
        """Сохраняет тест, если версия совпала. Возвращает новый тест или None при конфликте."""
        ts = now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE tests SET discipline=?, teacher=?, department=?, settings=?, questions=?, "
                "status=?, version=version+1, updated_at=? "
                "WHERE id=? AND version=? AND deleted_at IS NULL",
                (
                    fields["discipline"],
                    fields["teacher"],
                    fields["department"],
                    json.dumps(fields["settings"], ensure_ascii=False),
                    json.dumps(fields["questions"], ensure_ascii=False),
                    fields["status"],
                    ts,
                    test_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                return None
        return self.get_test(test_id)

    def set_status(self, test_id: int, status: str) -> dict | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tests SET status=?, version=version+1, updated_at=? WHERE id=? AND deleted_at IS NULL",
                (status, now_iso(), test_id),
            )
        return self.get_test(test_id)

    def soft_delete(self, test_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE tests SET deleted_at=?, version=version+1 WHERE id=? AND deleted_at IS NULL",
                (now_iso(), test_id),
            )
            return cur.rowcount > 0

    def restore(self, test_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE tests SET deleted_at=NULL, version=version+1 WHERE id=? AND deleted_at IS NOT NULL",
                (test_id,),
            )
            return cur.rowcount > 0

    # --- картинки --------------------------------------------------------
    def add_image(self, test_id: int, filename: str, mime: str, data: bytes) -> str:
        image_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO images(id, test_id, filename, mime, data, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (image_id, test_id, filename, mime, data, now_iso()),
            )
        return image_id

    def get_image(self, image_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "test_id": row["test_id"],
            "filename": row["filename"],
            "mime": row["mime"],
            "data": bytes(row["data"]),
        }

    def backup_to(self, target: Path) -> None:
        """Целостная копия базы (через backup API SQLite)."""
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
