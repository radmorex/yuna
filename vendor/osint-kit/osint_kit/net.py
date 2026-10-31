"""HTTP-сесія з таймаутами, повторами та потрібним User-Agent."""

from __future__ import annotations

import ipaddress
import re
import socket
import socketserver
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util.retry import Retry

# (ip, port), до яких дозволено підключатися попри перевірку (лише для тестового сервера)
PEER_EXEMPT: set[tuple[str, int]] = set()


class InternalAddressError(requests.ConnectionError):
    pass


class _PeerCheck:
    """Перевірка адреси вже після TCP-з'єднання, до надсилання запиту: DNS rebinding
    (публічна адреса під час перевірки, локальна під час запиту) не проходить."""

    def _new_conn(self):
        sock = super()._new_conn()
        if routed():  # через Tor/прокси пир — это сам прокси; локальная сеть через выход Tor недоступна
            return sock
        ip, port = sock.getpeername()[:2]
        if (ip, port) not in PEER_EXEMPT and is_internal(ip):
            sock.close()
            raise InternalAddressError("внутренний адрес сети — пропущено")
        return sock


class _CheckedHTTP(_PeerCheck, HTTPConnection):
    pass


class _CheckedHTTPS(_PeerCheck, HTTPSConnection):
    pass


class _CheckedPool(HTTPConnectionPool):
    ConnectionCls = _CheckedHTTP


class _CheckedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _CheckedHTTPS


class PublicOnlyAdapter(HTTPAdapter):
    """Адаптер, що не з'єднується з loopback/приватними/link-local адресами."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _CheckedPool, "https": _CheckedHTTPSPool}


def session(user_agent: str, retries: int = 2, public_only: bool = False) -> requests.Session:
    """public_only=True — для посилань із чужих сторінок: з'єднання лише з публічними адресами."""
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"})
    retry = Retry(
        total=retries,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = (PublicOnlyAdapter if public_only else HTTPAdapter)(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def normalize_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:  # домен без схеми -> додаємо https; file://, http:// тощо лишаємо
        url = "https://" + url
    return url


_SECRETS = (
    (re.compile(r"bot\d{6,}:[\w\-]{30,}"), "bot<токен>"),  # токен Telegram-бота в URL
    (re.compile(r"(?i)([?&](?:key|api_?key|token|access_token)=)[^&\s#\"']+"), r"\1<скрыто>"),
)


def redact(text: str) -> str:
    """Прибирає ключі й токени з тексту (помилки requests містять повний URL)."""
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


_STATUS_ERR = re.compile(r"too many (\d{3}) error responses")


def short_error(exc: Exception) -> str:
    text = redact(str(exc) or exc.__class__.__name__)
    m = _STATUS_ERR.search(text)  # повторы urllib3 исчерпаны — сказать по-человечески, что ответил сервис
    if m and m.group(1) == "429":
        return "сервис ограничил частоту запросов (429) — повторите позже"
    if m:
        return f"сервис временно недоступен (код {m.group(1)}) — повторите позже"
    return text if len(text) < 300 else text[:300] + "…"


@lru_cache(maxsize=512)
def is_internal(host: str) -> bool:
    """Loopback, приватна чи link-local адреса (локальна мережа аналітика)."""
    if not host:
        return False
    try:
        addrs = [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        if routed():  # в режиме Tor имена локально не разрешаем — только явные локальные
            h = host.lower().rstrip(".")
            return h == "localhost" or h.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa"))
        try:
            addrs = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(host, None)]
        except (socket.gaierror, UnicodeError):
            return False  # не розв'язується — запит просто впаде з помилкою
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved for a in addrs)


def block_internal_requests(page, target_url: str) -> dict:
    """Для публічної цілі браузер не ходить у локальну мережу аналітика (JS сторінки теж).
    Повертає лічильник заблокованих запитів."""
    stats = {"internal_blocked": 0}
    if is_internal(urlparse(target_url).hostname or ""):
        return stats  # ціль сама локальна — свідомий вибір користувача

    def _route(route):
        if is_internal(urlparse(route.request.url).hostname or ""):
            stats["internal_blocked"] += 1
            return route.abort()
        return route.fallback()  # далі — інші обробники (блок трекерів)

    page.route("**/*", _route)
    return stats


# ---------------- выход браузера ----------------
class EgressProxy(socketserver.ThreadingTCPServer):
    """Локальный HTTP-прокси для Chromium: соединяется с адресатом сам и проверяет IP уже после
    соединения (как _PeerCheck). Имя, которое при проверке было публичным, а при запросе стало
    локальным (DNS rebinding), не проходит. Ничего не пишет в журналы."""

    daemon_threads = True
    allow_reuse_address = True
    IDLE = 60  # с без данных — туннель закрывается

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.blocked = 0
        self.peers: dict[str, str] = {}  # хост → IP, с которым реально соединились (для доказательств)
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "EgressProxy":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    def connect(self, host: str, port: int) -> socket.socket:
        sock = socket.create_connection((host, port), timeout=self.IDLE)
        ip, peer_port = sock.getpeername()[:2]
        if (ip, peer_port) not in PEER_EXEMPT and is_internal(ip):
            sock.close()
            with self._lock:
                self.blocked += 1
            raise InternalAddressError("внутренний адрес сети — пропущено")
        with self._lock:
            self.peers[host.lower()] = ip
        return sock


_HOP_HEADERS = (b"proxy-connection:", b"proxy-authorization:", b"connection:", b"keep-alive:")


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(EgressProxy.IDLE)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(65536)
            if not chunk or len(head) > 65536:
                return
            head += chunk
        head, rest = head.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("latin-1").split(" ", 2)
            if method == "CONNECT":
                host, port = target.rsplit(":", 1)
                upstream = self.server.connect(host.strip("[]"), int(port))
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    raise ValueError("ожидался абсолютный http-URL")
                upstream = self.server.connect(parts.hostname, parts.port or 80)
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                # Connection: close — иначе Chromium пошлёт по тому же соединению запрос к другому хосту
                headers = [ln for ln in lines[1:] if not ln.lower().startswith(_HOP_HEADERS)]
                upstream.sendall(b"\r\n".join([f"{method} {path} {version}".encode("latin-1"), *headers,
                                                b"Connection: close"]) + b"\r\n\r\n")
        except InternalAddressError:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        except (OSError, ValueError):
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        with upstream:
            if rest:
                upstream.sendall(rest)
            threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()
            _pipe(client, upstream)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


@contextmanager
def browser_egress(target_url: str):
    """Аргументы chromium.launch() для публичной цели: весь трафик браузера — через EgressProxy,
    WebRTC — только через прокси. Локальная цель (выбор пользователя) — без ограничений.
    Отдаёт (kwargs, proxy | None)."""
    if routed():
        # Chromium сам разрешает имена через SOCKS5; правило резолвера запрещает любые локальные DNS-запросы
        yield {"proxy": {"server": f"socks5://{ROUTE['host']}:{ROUTE['port']}"},
               "args": ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {ROUTE['host']}"]}, None
        return
    if is_internal(urlparse(target_url).hostname or ""):
        yield {}, None
        return
    proxy = EgressProxy().start()
    try:
        # «<-loopback>» — Chromium иначе ходит на localhost мимо прокси
        yield {"proxy": {"server": proxy.url, "bypass": "<-loopback>"},
               "args": ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]}, proxy
    finally:
        proxy.stop()


# ---------------- маршрут: Tor / прокси / напрямую ----------------
# Режим Tor (по умолчанию) защищает самого искателя: сайты и сервисы видят выход Tor, а не его IP,
# имена разрешает Tor (провайдер не видит DNS-запросов), прямые UDP-запросы запрещены.
# Если Tor недоступен — проверка не выполняется (ничего не уходит напрямую).
TOR_PORT = 9060  # свой порт, чтобы не мешать Tor Browser (9150) и системному tor (9050)
ROUTE: dict = {"mode": "direct", "host": None, "port": None}
_DIRECT = threading.local()  # «напрямую» лишь внутри net.direct() и лишь в этом потоке
_ORIG: dict = {}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class RouteError(RuntimeError):
    pass


def proxy_endpoint(cfg) -> tuple[str, int] | None:
    if cfg.route == "direct":
        return None
    if cfg.route == "tor":
        return "127.0.0.1", TOR_PORT
    parts = urlsplit(cfg.proxy or "")
    if parts.scheme not in ("socks5", "socks5h") or not parts.hostname or not parts.port:
        raise RouteError("Для route = \"proxy\" укажите proxy = \"socks5h://адрес:порт\"")
    return parts.hostname, parts.port


def proxy_url() -> str | None:
    """socks5h://… для внешних программ (Chromium, Sherlock, yt-dlp); None — напрямую."""
    return f"socks5h://{ROUTE['host']}:{ROUTE['port']}" if ROUTE["mode"] != "direct" else None


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    connect = _ORIG.get("connect", socket.socket.connect)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        connect(s, (host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def apply_route(cfg, start_tor: bool = True) -> dict:
    """Включает маршрут для всего процесса. Вызывается при запуске команды, до любого запроса."""
    ep = proxy_endpoint(cfg)
    if ep is None:
        return {"mode": "direct"}
    host, port = ep
    if not port_open(host, port):
        if cfg.route == "tor" and start_tor:
            tor_start()
        if not port_open(host, port):
            raise RouteError("Tor не запущен — проверка не выполнена, чтобы не раскрыть ваш IP. "
                             "Запустите: osint route start (нужен tor: brew install tor)"
                             if cfg.route == "tor" else f"Прокси {host}:{port} недоступен — проверка не выполнена")
    _install(host, port)
    ROUTE.update(mode=cfg.route, host=host, port=port)
    return {"mode": cfg.route, "proxy": proxy_url()}


def _install(host: str, port: int) -> None:
    import socks

    if _ORIG:
        return
    _ORIG.update(socket=socket.socket, getaddrinfo=socket.getaddrinfo, gethostbyname=socket.gethostbyname,
                 gethostbyname_ex=socket.gethostbyname_ex, gethostbyaddr=socket.gethostbyaddr,
                 connect=socket.socket.connect, create_connection=socket.create_connection)
    real_getaddrinfo = socket.getaddrinfo

    class RoutedSocket(socks.socksocket):
        """TCP — через прокси с разрешением имён на его стороне; UDP наружу — запрещён;
        локальные сервисы на этом же компьютере (127.0.0.1) — напрямую, это не раскрывает IP."""

        def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, *args, **kwargs):
            super().__init__(family, type, proto, *args, **kwargs)
            if getattr(_DIRECT, "on", False):
                return
            if self.type == socket.SOCK_STREAM and self.family in (socket.AF_INET, socket.AF_INET6):
                self.set_proxy(socks.SOCKS5, host, port, rdns=True)

        def connect(self, address):
            if self.family not in (socket.AF_INET, socket.AF_INET6):  # AF_UNIX и т. п. — локальные
                self.set_proxy(None)
                return super().connect(address)
            if getattr(_DIRECT, "on", False):
                return super().connect(address)
            if self.type != socket.SOCK_STREAM and str(address[0]) not in _LOOPBACK:
                raise RouteError("UDP наружу в режиме Tor запрещён")
            if str(address[0]) in _LOOPBACK:
                self.set_proxy(None)
            return super().connect(address)

        def sendto(self, *args):
            address = args[-1]
            if self.family in (socket.AF_INET, socket.AF_INET6) and str(address[0]) not in _LOOPBACK:
                raise RouteError("UDP наружу в режиме Tor запрещён")
            return super().sendto(*args)

    def getaddrinfo(host_, port_, family=0, type=0, proto=0, flags=0):
        # имя не разрешаем локально: отдаём его как есть — прокси разрешит его сам (без утечки DNS)
        if host_ is None or str(host_) in _LOOPBACK or _literal(str(host_)) or getattr(_DIRECT, "on", False):
            return real_getaddrinfo(host_, port_, family, type, proto, flags)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (str(host_), port_ or 0))]

    def no_dns(*_args, **_kwargs):
        raise socket.gaierror("DNS-запрос мимо Tor запрещён")

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_kw):
        sock = RoutedSocket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        try:
            sock.connect((address[0], address[1]))
        except BaseException:
            sock.close()
            raise
        return sock

    socket.socket = RoutedSocket
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = socket.gethostbyname_ex = socket.gethostbyaddr = no_dns
    socket.create_connection = create_connection
    is_internal.cache_clear()


@contextmanager
def direct():
    """Участок «напрямую» при маршруте Tor: только для справочных сервисов из [network] direct_fallback."""
    prev = getattr(_DIRECT, "on", False)
    _DIRECT.on = True
    try:
        yield
    finally:
        _DIRECT.on = prev


def lookup_get(cfg, s: requests.Session, url: str, **kw) -> tuple[requests.Response, str]:
    """GET к справочному сервису. Ответ 403 через Tor (сервис блокирует выходы Tor) — один повтор напрямую,
    если сервис в cfg.direct_fallback. Сервис тогда видит IP искателя; цель проверки — нет."""
    r = s.get(url, **kw)
    host = (urlparse(url).hostname or "").lower()
    if r.status_code != 403 or not routed() or host not in cfg.direct_fallback:
        return r, "tor" if routed() else "напрямую"
    with direct():
        fresh = session(s.headers.get("User-Agent", ""), retries=0)  # новое соединение: старое шло через Tor
        return fresh.get(url, **kw), "напрямую"


def reset_route() -> None:
    """Вернуть прямое соединение (для тестов)."""
    if _ORIG:
        socket.socket = _ORIG["socket"]
        socket.getaddrinfo = _ORIG["getaddrinfo"]
        socket.gethostbyname = _ORIG["gethostbyname"]
        socket.gethostbyname_ex = _ORIG["gethostbyname_ex"]
        socket.gethostbyaddr = _ORIG["gethostbyaddr"]
        socket.create_connection = _ORIG["create_connection"]
        _ORIG.clear()
    ROUTE.update(mode="direct", host=None, port=None)
    is_internal.cache_clear()


def _literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def routed() -> bool:
    return ROUTE["mode"] != "direct"


# ---------------- свой процесс tor ----------------
def tor_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "osint-kit" / "tor"


def tor_start(wait: float = 90) -> dict:
    """Запускает tor в фоне на 127.0.0.1:TOR_PORT и ждёт подключения к сети. Журнал — только служебный
    (tor не пишет, какие сайты открывались)."""
    import shutil
    import subprocess
    import time

    if port_open("127.0.0.1", TOR_PORT):
        return {"tor": "уже запущен", "port": TOR_PORT}
    binary = shutil.which("tor") or next((p for p in ("/opt/homebrew/bin/tor", "/usr/local/bin/tor") if Path(p).exists()), None)
    if not binary:
        raise RouteError("tor не установлен: brew install tor")
    d = tor_dir()
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    log = d / "notice.log"
    start = log.stat().st_size if log.exists() else 0
    subprocess.Popen([binary, "--SocksPort", f"127.0.0.1:{TOR_PORT}", "--DataDirectory", str(d),
                      "--PidFile", str(d / "tor.pid"), "--Log", f"notice file {log}", "--ClientOnly", "1",
                      "--AvoidDiskWrites", "1", "--SafeLogging", "1"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if log.exists():
            with log.open("rb") as fh:
                fh.seek(start)
                tail = fh.read().decode("utf-8", "replace")
            if "Bootstrapped 100%" in tail:
                return {"tor": "запущен", "port": TOR_PORT}
            if "[err]" in tail:
                raise RouteError("tor не запустился: " + tail.split("[err]")[-1].strip()[:200])
    raise RouteError("tor не подключился к сети за отведённое время — проверьте интернет")


def tor_stop() -> dict:
    import os
    import signal

    pid_file = tor_dir() / "tor.pid"
    if not pid_file.exists():
        return {"tor": "не запущен"}
    try:
        os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass
    pid_file.unlink(missing_ok=True)
    return {"tor": "остановлен"}
