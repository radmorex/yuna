"""Спільні фікстури: локальний HTTP-сервер, заборона зовнішньої мережі, тимчасовий конфіг.

Жоден тест не звертається до чужих сайтів:
- усі запити `requests` до зовнішніх хостів переписуються на локальний сервер
  (https://crt.sh/x -> http://127.0.0.1:<port>/_ext/crt.sh/x);
- будь-яке з'єднання чи DNS-запит не до localhost падає з помилкою.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests.adapters

from osint_kit import cli, config, net
from osint_kit.case import Case

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)  # обірване клієнтом з'єднання — норма


class LocalServer:
    def __init__(self):
        self.routes: dict = {}
        self.requests: list[dict] = []
        self.httpd = _QuietHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, path: str, body=b"", status: int = 200, ctype: str = "text/html; charset=utf-8",
            headers: dict | None = None, method: str = "GET", handler=None) -> None:
        """Маршрут: фіксована відповідь або handler(req) -> (status, headers, bytes)."""
        if handler is not None:
            self.routes[(method, path)] = handler
            return
        if isinstance(body, (dict, list)):
            body, ctype = json.dumps(body).encode(), "application/json"
        elif isinstance(body, str):
            body = body.encode()
        self.routes[(method, path)] = (status, {"Content-Type": ctype, **(headers or {})}, body)

    def url(self, path: str) -> str:
        return self.base + path

    @staticmethod
    def ext(host: str, path: str = "/") -> str:
        """Локальний шлях, на який переписується зовнішній https://<host><path>."""
        return f"/_ext/{host}{path}"

    def hits(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def reset(self) -> None:
        self.routes.clear()
        self.requests.clear()


def _make_handler(srv: LocalServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _serve(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            req = {"method": self.command, "path": parts.path, "query": parse_qs(parts.query),
                   "headers": dict(self.headers), "body": self.rfile.read(length) if length else b""}
            srv.requests.append(req)
            route = srv.routes.get((self.command, parts.path))
            if route is None and self.command == "HEAD":
                route = srv.routes.get(("GET", parts.path))
            if route is None:
                status, headers, payload = 404, {"Content-Type": "text/plain"}, b"not found"
            elif callable(route):
                status, headers, payload = route(req)
            else:
                status, headers, payload = route
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_GET = do_POST = do_HEAD = _serve

        def log_message(self, *args):  # не засмічувати вивід тестів
            pass

    return Handler


@pytest.fixture(scope="session")
def _httpd():
    srv = LocalServer()
    yield srv
    srv.httpd.shutdown()


@pytest.fixture(autouse=True)
def no_ocr(request, monkeypatch):
    """Розпізнавання тексту (Vision) повільне — у тестах вимкнене, крім позначених live_ocr."""
    if "live_ocr" not in request.keywords:
        from osint_kit.modules import media

        monkeypatch.setattr(media, "ocr", lambda path: None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Забороняє з'єднання й DNS-запити поза localhost."""
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOCAL_HOSTS:
            raise OSError(f"тест намагався звернутися до зовнішньої мережі: {address}")
        return real_connect(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and str(host) not in LOCAL_HOSTS:
            raise socket.gaierror(f"тест намагався зробити DNS-запит: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def server(_httpd, monkeypatch):
    """Локальний сервер; запити requests до зовнішніх хостів ідуть на нього."""
    _httpd.reset()
    real_send = requests.adapters.HTTPAdapter.send

    def send(adapter, request, **kwargs):
        parts = urlsplit(request.url)
        if parts.hostname not in LOCAL_HOSTS:
            request.url = (f"{_httpd.base}{_httpd.ext(parts.hostname, parts.path or '/')}"
                           + (f"?{parts.query}" if parts.query else ""))
        return real_send(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    # «зовнішні» запити приходять на локальний сервер — дозволяємо саме його адресу
    monkeypatch.setattr(net, "PEER_EXEMPT", {("127.0.0.1", _httpd.port)})
    return _httpd


class Runner:
    """Запуск CLI з тимчасовим конфігом і папкою справ."""

    def __init__(self, tmp_path: Path, monkeypatch, server: LocalServer):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.server = server
        self.cases = tmp_path / "cases"
        for name in list(os.environ):
            if name.startswith("OSINT_"):  # не підхопити справжні ключі користувача
                monkeypatch.delenv(name)
        self.configure()

    def configure(self, keys: dict | None = None, webcheck: bool = True, libretranslate: bool = True) -> None:
        lines = [
            "[general]", f'cases_dir = "{self.cases}"', "timeout = 5", "encrypt_cases = false", "",
            "[services]",
            f'webcheck_url = "{self.server.base + "/webcheck" if webcheck else ""}"',
            f'libretranslate_url = "{self.server.base + "/libre" if libretranslate else ""}"', "",
            "[network]", 'route = "direct"', "",  # тесты ходят только на локальный сервер
            "[keys]", *(f'{k} = "{v}"' for k, v in (keys or {}).items()),
        ]
        path = self.tmp / "config.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.monkeypatch.setenv("OSINT_KIT_CONFIG", str(path))

    def __call__(self, *argv: str, case: str = "t") -> int:
        return cli.main(["--case", case, *argv])

    def cfg(self) -> config.Config:
        return config.load()

    def case(self, name: str = "t") -> Case:
        return Case(self.cases, name)

    def findings(self, name: str = "t") -> list[dict]:
        return self.case(name).findings()

    def evidence(self, name: str = "t") -> list[dict]:
        return self.case(name).evidence()

    def case_text(self, name: str = "t") -> str:
        """Увесь текстовий вміст папки справи (для перевірки витоку секретів)."""
        out = []
        for p in sorted(self.case(name).dir.rglob("*")):
            if p.is_file() and p.suffix in (".json", ".jsonl", ".csv", ".md", ".html", ".txt"):
                out.append(p.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(out)


@pytest.fixture
def osint(tmp_path, monkeypatch, server) -> Runner:
    return Runner(tmp_path, monkeypatch, server)
