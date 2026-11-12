"""Защита от сканеров и ботов.

Сайт закрыт паролем, но его адрес публичный, и боты перебирают типовые пути
(/.env, /wp-admin, /info.php…) в поисках чужих уязвимостей. Адрес, который:
  - запросил заведомо вредоносный путь (ловушку), или
  - без входа набрал много ответов 404, или
  - без входа шлёт запросы слишком часто,
блокируется на несколько часов.

Блокировка действует только на запросы без входа. Вошедшие пользователи её не замечают,
а страница входа открыта всегда: у преподавателей бывает общий адрес (сеть университета),
и бот в той же сети не должен лишать их доступа.
"""
import re
import time
from dataclasses import dataclass

# Пути, которые на этом сайте не существуют и нужны только тем, кто ищет уязвимости.
TRAP = re.compile(
    r"""(
        \.(php\d?|phtml|asp|aspx|axd|jsp|jspx|cgi|pl|cfm|env|ini|bak|old|sql|sqlite|swp|yml|yaml|toml|config|conf|log)(/|$)
      | /\.(env|git|svn|hg|ds_store|vscode|idea|aws|ssh|docker|npmrc|htaccess|htpasswd)
      | wp-(admin|login|content|includes|json|config) | /wordpress | xmlrpc | rest_route=
      | phpmyadmin | /pma(/|$) | /cgi-bin | /actuator | /telescope | /@vite | /vendor/ | /node_modules
      | /debug/ | /server-status | /console(/|$) | /solr | /jenkins | /manager/html | /boaform
      | /owa/ | /autodiscover | /remote/login | /hnap1 | /setup\.cgi | /shell | /eval
    )""",
    re.IGNORECASE | re.VERBOSE,
)

BAN_SECONDS = 12 * 3600
STRIKE_WINDOW = 10 * 60
MAX_404 = 8  # без входа за 10 минут
RATE_WINDOW = 60
MAX_REQUESTS = 60  # без входа за минуту (статика не считается)


@dataclass
class Ban:
    until: float
    reason: str
    path: str
    since: float
    blocked: int = 0  # сколько запросов отклонено за время блокировки


class Guard:
    def __init__(self, ban_seconds: int = BAN_SECONDS, max_404: int = MAX_404, max_requests: int = MAX_REQUESTS):
        self.ban_seconds = ban_seconds
        self.max_404 = max_404
        self.max_requests = max_requests
        self.bans: dict[str, Ban] = {}
        self._404: dict[str, list[float]] = {}
        self._hits: dict[str, list[float]] = {}

    @staticmethod
    def is_trap(path_and_query: str) -> bool:
        return bool(TRAP.search(path_and_query))

    def banned(self, ip: str) -> Ban | None:
        ban = self.bans.get(ip)
        if ban and ban.until <= time.time():
            del self.bans[ip]
            return None
        return ban

    def ban(self, ip: str, reason: str, path: str) -> Ban:
        now = time.time()
        ban = Ban(until=now + self.ban_seconds, reason=reason, path=path, since=now)
        self.bans[ip] = ban
        self._404.pop(ip, None)
        self._hits.pop(ip, None)
        return ban

    def unban(self, ip: str) -> bool:
        return self.bans.pop(ip, None) is not None

    def active(self) -> list[tuple[str, Ban]]:
        now = time.time()
        for ip in [ip for ip, b in self.bans.items() if b.until <= now]:
            del self.bans[ip]
        return sorted(self.bans.items(), key=lambda item: item[1].since, reverse=True)

    def _count(self, store: dict[str, list[float]], ip: str, window: int, limit: int) -> bool:
        now = time.monotonic()
        if len(store) > 10_000:  # не даём словарю разрастись от тысяч адресов
            store.clear()
        hits = [t for t in store.get(ip, []) if now - t < window]
        hits.append(now)
        store[ip] = hits
        return len(hits) > limit

    def too_fast(self, ip: str) -> bool:
        return self._count(self._hits, ip, RATE_WINDOW, self.max_requests)

    def not_found(self, ip: str) -> bool:
        return self._count(self._404, ip, STRIKE_WINDOW, self.max_404)
