"""Прокси на выходе Chromium: адрес проверяется после соединения (DNS rebinding не проходит)."""

import socket

import pytest

from osint_kit import net


def _raw(proxy, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy.server_address[1]), timeout=5) as s:
        s.sendall(payload)
        out = b""
        while chunk := s.recv(65536):
            out += chunk
    return out


@pytest.fixture
def proxy():
    p = net.EgressProxy().start()
    yield p
    p.stop()


def test_blocks_internal_after_connect(proxy, server, monkeypatch):
    monkeypatch.setattr(net, "PEER_EXEMPT", set())  # как в работе
    server.add("/secret", "секрет")
    port = server.port
    # прокси смотрит на IP уже установленного соединения, а не на имя из запроса
    for req in (f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\n\r\n",
                f"GET http://localhost:{port}/secret HTTP/1.1\r\nHost: localhost\r\n\r\n"):
        assert _raw(proxy, req.encode()).startswith(b"HTTP/1.1 403")
    assert proxy.blocked == 2 and not server.hits("/secret") and proxy.peers == {}


def test_forwards_http_and_tunnels(proxy, server):
    server.add("/page", "привет")
    port = server.port
    out = _raw(proxy, (f"GET http://127.0.0.1:{port}/page?a=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n").encode())
    assert out.startswith(b"HTTP/1.") and "привет".encode() in out
    [hit] = server.hits("/page")
    assert hit["query"] == {"a": ["1"]}

    with socket.create_connection(("127.0.0.1", proxy.server_address[1]), timeout=5) as s:
        s.sendall(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
        assert s.recv(1024).startswith(b"HTTP/1.1 200")
        s.sendall(f"GET /page HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        body = b""
        while chunk := s.recv(65536):
            body += chunk
    assert "привет".encode() in body
    assert proxy.blocked == 0 and proxy.peers == {"127.0.0.1": "127.0.0.1"}


def test_bad_requests(proxy):
    assert _raw(proxy, b"GET /relative HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 502")
    assert _raw(proxy, b"CONNECT nohost HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 502")
    assert _raw(proxy, b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 502")  # отказ в соединении


def test_browser_egress_only_for_public_targets():
    with net.browser_egress("http://127.0.0.1:8080/") as (kwargs, proxy):
        assert kwargs == {} and proxy is None
    with net.browser_egress("http://8.8.8.8/") as (kwargs, proxy):
        assert kwargs["proxy"] == {"server": proxy.url, "bypass": "<-loopback>"}
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in kwargs["args"]


@pytest.mark.slow
def test_chromium_goes_through_proxy(server, monkeypatch):
    """Настоящий Chromium: локальный адрес через прокси не открывается, разрешённый — открывается."""
    from playwright.sync_api import Error, sync_playwright

    server.add("/page", "<title>ok</title>через прокси")
    with net.browser_egress("http://8.8.8.8/") as (kwargs, proxy), sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **kwargs)
        try:
            page = browser.new_page()
            page.goto(server.url("/page"))  # тестовый сервер в PEER_EXEMPT
            assert "через прокси" in page.content() and proxy.blocked == 0
            monkeypatch.setattr(net, "PEER_EXEMPT", set())
            server.reset()
            try:
                resp = page.goto(f"http://localhost:{server.port}/page")
                assert resp.status == 403  # ответ прокси, страница не загружена
            except Error:
                pass  # или сетевая ошибка — тоже не дошли
            assert proxy.blocked == 1 and not server.hits("/page")
        finally:
            browser.close()
