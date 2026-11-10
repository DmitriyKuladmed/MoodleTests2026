"""Пароли (scrypt), учётные записи и защита от перебора."""
import hashlib
import hmac
import secrets
import threading
import time

from .db import Database

ROLES = ("editor", "admin")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest.hex(), digest_hex)


class Accounts:
    """Две учётные записи: общая для преподавателей (editor) и админская (admin).

    Логин и хэш пароля хранятся в таблице settings. ``credential_version``
    меняется при смене пароля — все старые сессии этой роли становятся недействительными.
    """

    def __init__(self, db: Database):
        self.db = db

    def ensure(self, role: str, login: str, password: str) -> None:
        if self.db.get_setting(f"{role}_hash") is None:
            if not password:
                raise RuntimeError(
                    f"Не задан пароль для роли {role}: укажите {role.upper()}_PASSWORD в .env"
                )
            self.set(role, login, password)

    def set(self, role: str, login: str, password: str) -> None:
        self.db.set_setting(f"{role}_login", login.strip())
        self.db.set_setting(f"{role}_hash", hash_password(password))
        self.db.set_setting(f"{role}_cv", secrets.token_hex(8))

    def login_name(self, role: str) -> str:
        return self.db.get_setting(f"{role}_login") or ""

    def credential_version(self, role: str) -> str:
        return self.db.get_setting(f"{role}_cv") or ""

    def authenticate(self, login: str, password: str) -> str | None:
        """Возвращает роль или None. Проверяет все роли, чтобы время ответа не выдавало логин."""
        found = None
        for role in ROLES:
            stored_login = self.login_name(role)
            stored_hash = self.db.get_setting(f"{role}_hash") or ""
            ok_password = verify_password(password, stored_hash)
            if ok_password and hmac.compare_digest(login.strip().lower(), stored_login.lower()):
                found = found or role
        return found


class LoginThrottle:
    """Блокирует вход с адреса после нескольких неудачных попыток."""

    def __init__(self, max_failures: int, window: int, block: int):
        self.max_failures = max_failures
        self.window = window
        self.block = block
        self._failures: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked_for(self, key: str) -> int:
        with self._lock:
            until = self._blocked.get(key, 0)
            left = int(until - time.monotonic())
            if left <= 0:
                self._blocked.pop(key, None)
                return 0
            return left

    def failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                self._blocked[key] = now + self.block
                self._failures.pop(key, None)

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
