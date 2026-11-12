"""Откуда пришёл запрос: страна и город по IP.

База — DB-IP City Lite (бесплатная, лицензия CC BY 4.0, «IP Geolocation by DB-IP»,
https://db-ip.com). Она лежит у нас локально, так что адреса посетителей никуда не отправляются.
База обновляется раз в месяц: при запуске и раз в сутки проверяем возраст файла и, если он
старше 35 дней, скачиваем свежий в фоне. Пока базы нет, геолокация просто не пишется.
"""
import gzip
import ipaddress
import logging
import re
import shutil
import threading
import time
import urllib.request
from datetime import date
from functools import lru_cache
from pathlib import Path

import maxminddb

log = logging.getLogger("moodle_tests")
URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
MAX_AGE = 35 * 24 * 3600
CHECK_EVERY = 24 * 3600


def _months_back(today: date, count: int) -> list[str]:
    year, month = today.year, today.month
    result = []
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return result


def _name(record: dict | None) -> str | None:
    names = (record or {}).get("names") or {}
    name = names.get("ru") or names.get("en")
    if not name:
        return None
    return re.sub(r"\s*\(.*\)$", "", name)  # «Moscow (Tsentralnyy administrativnyy okrug)» → «Moscow»


class Geo:
    def __init__(self, folder: Path, download: bool = False):
        self.path = folder / "dbip-city-lite.mmdb"
        self.download = download
        self._reader = None
        self._lock = threading.Lock()
        self._open()
        if download:
            threading.Thread(target=self._keep_fresh, name="geo-update", daemon=True).start()

    # --- поиск ---------------------------------------------------------------
    def where(self, ip: str | None) -> str | None:
        """«Страна, город» или None, если базы нет или адрес неизвестен."""
        if not ip:
            return None
        return self._where(ip)

    @lru_cache(maxsize=4096)
    def _where(self, ip: str) -> str | None:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if address.is_private or address.is_loopback or address.is_link_local:
            return "локальная сеть"
        reader = self._reader
        if reader is None:
            return None
        try:
            record = reader.get(ip) or {}
        except (ValueError, maxminddb.InvalidDatabaseError):
            return None
        parts = [p for p in (_name(record.get("country")), _name(record.get("city"))) if p]
        return ", ".join(parts) or None

    # --- база ----------------------------------------------------------------
    def _open(self) -> None:
        if not self.path.exists():
            return
        try:
            reader = maxminddb.open_database(str(self.path))
        except (OSError, maxminddb.InvalidDatabaseError) as exc:
            log.warning("%-18s не удалось открыть базу: %s", "geo.error", exc)
            return
        with self._lock:
            self._reader = reader  # старую базу не закрываем: в ней может идти поиск, её соберёт GC
        self._where.cache_clear()
        built = time.strftime("%Y-%m-%d", time.gmtime(reader.metadata().build_epoch))
        log.info("%-18s база геолокации от %s", "geo.ready", built)

    def _stale(self) -> bool:
        return not self.path.exists() or time.time() - self.path.stat().st_mtime > MAX_AGE

    def _keep_fresh(self) -> None:
        while True:
            if self._stale():
                self.update()
            time.sleep(CHECK_EVERY)

    def update(self) -> bool:
        """Скачивает базу за текущий месяц (или прошлый, если текущей ещё нет)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".download")
        for month in _months_back(date.today(), 2):
            try:
                request = urllib.request.Request(URL.format(month=month), headers={"User-Agent": "moodle-tests"})
                with urllib.request.urlopen(request, timeout=120) as response, \
                        gzip.GzipFile(fileobj=response) as packed, open(tmp, "wb") as out:
                    shutil.copyfileobj(packed, out, 1024 * 1024)
                maxminddb.open_database(str(tmp)).close()  # проверяем, что файл целый
                tmp.replace(self.path)
                log.info("%-18s скачана база за %s", "geo.update", month)
                self._open()
                return True
            except Exception as exc:  # noqa: BLE001 — сеть, формат, диск: просто пробуем дальше
                log.warning("%-18s база за %s не скачалась: %s", "geo.error", month, exc)
                tmp.unlink(missing_ok=True)
        return False
