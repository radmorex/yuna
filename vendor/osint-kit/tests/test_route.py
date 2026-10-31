"""Маршрут через Tor и узкое исключение «напрямую» для справочных сервисов, которые блокируют Tor."""

import socket
from contextlib import contextmanager

import pytest
import socks

from osint_kit import config, net


@pytest.fixture
def routed_process():
    net._install("127.0.0.1", 9)  # порт не важен: проверяем, через прокси ли сокет, а не соединение
    net.ROUTE.update(mode="tor", host="127.0.0.1", port=9)
    try:
        yield
    finally:
        net.reset_route()


def test_direct_section_skips_tor_only_inside(routed_process):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    assert s.proxy[0] == socks.SOCKS5
    with net.direct():
        d = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        assert d.proxy[0] is None
    after = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    assert after.proxy[0] == socks.SOCKS5
    for x in (s, d, after):
        x.close()


def test_fallback_direct_only_for_listed_service_after_403(osint, server, monkeypatch):
    cfg = osint.cfg()
    assert cfg.direct_fallback == ("ipinfo.io", "internetdb.shodan.io")  # по умолчанию — справочные сервисы IP
    calls = []
    server.add(server.ext("ipinfo.io", "/1.2.3.4/json"),
               handler=lambda req: (403, {}, b"blocked") if not calls.append(1) and len(calls) == 1
               else (200, {"Content-Type": "application/json"}, b'{"ok": 1}'))
    server.add(server.ext("example.test", "/"), "forbidden", status=403)
    monkeypatch.setattr(net, "routed", lambda: True)
    entered = []
    real = net.direct

    @contextmanager
    def spy():
        entered.append(1)
        with real():
            yield

    monkeypatch.setattr(net, "direct", spy)
    s = net.session(cfg.user_agent, retries=0)
    r, via = net.lookup_get(cfg, s, "https://ipinfo.io/1.2.3.4/json", timeout=5)
    assert r.status_code == 200 and via == "напрямую" and len(calls) == 2 and entered == [1]
    r, via = net.lookup_get(cfg, s, "https://example.test/", timeout=5)  # цель не из списка — без повтора
    assert r.status_code == 403 and via == "tor" and entered == [1]


def test_fallback_list_from_config(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[network]\nroute = "tor"\ndirect_fallback = []\n', encoding="utf-8")
    monkeypatch.setenv("OSINT_KIT_CONFIG", str(p))
    assert config.load().direct_fallback == ()
