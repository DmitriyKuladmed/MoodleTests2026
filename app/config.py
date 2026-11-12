"""Настройки приложения из переменных окружения."""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Подхватывает переменные из .env (если файл есть), не перетирая уже заданные."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", ""))
    # Начальные учётные данные: записываются в базу при первом запуске,
    # дальше меняются из админки.
    editor_login: str = field(default_factory=lambda: os.getenv("EDITOR_LOGIN", "kafedra"))
    editor_password: str = field(default_factory=lambda: os.getenv("EDITOR_PASSWORD", ""))
    admin_login: str = field(default_factory=lambda: os.getenv("ADMIN_LOGIN", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))
    # За HTTPS (Caddy) cookie отдаются только по защищённому соединению.
    secure_cookies: bool = field(default_factory=lambda: _bool(os.getenv("SECURE_COOKIES"), False))
    # Скачивать базу геолокации DB-IP (страна и город по IP в журнале). На сервере включено в docker-compose.
    geoip_download: bool = field(default_factory=lambda: _bool(os.getenv("GEOIP_DOWNLOAD"), False))
    max_image_bytes: int = 5 * 1024 * 1024
    # Защита от перебора пароля.
    login_max_failures: int = 5
    login_window_seconds: int = 10 * 60
    login_block_seconds: int = 15 * 60

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tests.db"

    def resolved_secret_key(self) -> str:
        """Ключ подписи cookie: из окружения или сгенерированный и сохранённый в data/."""
        if self.secret_key:
            return self.secret_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            return key_file.read_text().strip()
        key = secrets.token_urlsafe(48)
        key_file.write_text(key)
        return key
