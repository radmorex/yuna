"""Модуль scrape: посилання, контакти, таблиці, масове завантаження."""

import csv
import hashlib

import pytest

from osint_kit.modules import scrape
from osint_kit.net import is_internal

LINKS_PAGE = """<html><head><title>Контакти</title></head><body>
<a href="/about">Про нас</a>
<a href="https://sub.127.0.0.1/x">sub</a>
<a href="https://other.test/page">Інший сайт</a>
<a href="https://t.me/somechannel">Telegram</a>
<a href="https://www.instagram.com/someone">Insta</a>
<a href="mailto:Info@Example.test?subject=hi">Пошта</a>
<a href="tel:+380441234567">Телефон</a>
<a href="javascript:void(0)">js</a><a href="#top">top</a>
<a href="/about">дубль</a>
<p>Пишіть: help@volunteers.test або дзвоніть +38 (067) 123-45-67</p>
<img src="logo@2x.png">
</body></html>"""


def test_links(osint, server):
    server.add("/contacts", LINKS_PAGE)
    assert osint("scrape", "links", server.url("/contacts")) == 0
    res = scrape.links(osint.cfg(), osint.case(), server.url("/contacts"))

    assert res["title"] == "Контакти"
    # sub.127.0.0.1 — піддомен, тому внутрішнє
    assert res["counts"] == {"internal": 2, "external": 1, "social": 2, "email": 1, "phone": 1}
    assert res["emails"] == ["help@volunteers.test", "info@example.test"]
    assert set(res["phones"]) == {"+380441234567", "+380671234567"}
    assert "https://t.me/somechannel" in res["social"]

    summary = osint.findings()[0]["data"]  # в записи — читаемая сводка, а не сырые счётчики
    assert summary["ссылок"] == {"внутренние": 2, "внешние": 1, "соцсети": 2}
    assert summary["e-mail"] == ["help@volunteers.test", "info@example.test"] and "t.me" in " ".join(summary["соцсети"])
    with (osint.case().dir / res["csv"]).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 7  # без javascript:, # і дубля


def test_links_fall_back_to_browser_when_page_has_none(osint, monkeypatch):
    calls = []

    def fake_fetch(cfg, url, render=False, user_agent=None):
        calls.append(render)
        return "https://spa.test/", LINKS_PAGE if render else "<html><title>SPA</title><div id=app></div></html>"

    monkeypatch.setattr(scrape, "fetch_html", fake_fetch)
    res = scrape.links(osint.cfg(), osint.case(), "https://spa.test/")
    assert calls == [False, True] and res["render"] == "браузер" and res["counts"]["social"] == 2


def test_links_local_file(osint, tmp_path):
    page = tmp_path / "saved page.html"
    page.write_text(LINKS_PAGE, encoding="utf-8")
    res = scrape.links(osint.cfg(), osint.case(), page.as_uri())
    assert res["title"] == "Контакти"


@pytest.mark.slow
def test_links_render_js(osint, server):
    server.add("/spa", """<html><body><div id=app></div><script>
    document.getElementById('app').innerHTML = '<a href="https://t.me/js_only">x</a>';
    </script></body></html>""")
    # без JavaScript ссылок нет — страница сама открывается в браузере
    res = scrape.links(osint.cfg(), osint.case(), server.url("/spa"))
    assert res["social"] == ["https://t.me/js_only"] and res["render"] == "браузер"
    assert scrape.links(osint.cfg(), osint.case(), server.url("/spa"), render=True)["social"] == ["https://t.me/js_only"]


def test_links_http_error(osint, capsys):
    assert osint("scrape", "links", osint.server.url("/missing")) == 1
    assert "404" in capsys.readouterr().out


def test_tables(osint, server):
    server.add("/t", """<table><tr><th>Ім'я</th><th>Місто</th></tr>
    <tr><td>Олена</td><td>Київ</td></tr><tr><td>Петро</td><td>Львів, "центр"</td></tr></table>
    <table><tr><td>одна рядок</td></tr></table>""")
    assert osint("scrape", "tables", server.url("/t")) == 0
    res = scrape.tables(osint.cfg(), osint.case(), server.url("/t"))
    [table] = res["tables"]  # таблиця з одним рядком пропускається
    assert table["rows"] == 3 and table["cols"] == 2 and table["header"] == ["Ім'я", "Місто"]
    raw = (osint.case().dir / table["csv"]).read_text(encoding="utf-8")
    assert raw.startswith("﻿")  # BOM, щоб Excel відкрив кирилицю
    assert list(csv.reader(raw.lstrip("﻿").splitlines()))[2] == ["Петро", 'Львів, "центр"']


def test_tables_fallback_to_lists(osint, server):
    server.add("/l", '<ul><li>Перший</li><li>Другий "в лапках"</li><li> </li></ul>')
    res = scrape.tables(osint.cfg(), osint.case(), server.url("/l"))
    [item] = res["tables"]
    assert item["table"] == "lists" and item["rows"] == 2
    rows = list(csv.reader((osint.case().dir / item["csv"]).read_text(encoding="utf-8").lstrip("﻿").splitlines()))
    assert rows == [["item"], ["Перший"], ['Другий "в лапках"']]


def _files_page(server):
    server.add("/docs", """<a href="/f/a.pdf">a</a><a href="/f/b.PDF">b</a><a href="/f/missing.pdf">m</a>
    <a href="/f/c.docx">c</a><a href="/f/a.pdf">дубль</a><img src="/f/pic.jpg">""")
    server.add("/f/a.pdf", b"%PDF-1 aaa", ctype="application/pdf")
    server.add("/f/b.PDF", b"%PDF-1 bbb", ctype="application/pdf")
    server.add("/f/c.docx", b"docx", ctype="application/octet-stream")


def test_files_download(osint, server):
    _files_page(server)
    assert osint("scrape", "files", server.url("/docs"), "--ext", "pdf") == 0
    [finding] = osint.findings()
    assert finding["data"]["found"] == 3 and finding["data"]["downloaded"] == 2
    assert [e["url"].rsplit("/", 1)[-1] for e in finding["data"]["errors"]] == ["missing.pdf"]

    hashes = {e["file"].rsplit("/", 1)[-1]: e["sha256"] for e in osint.evidence()}
    assert hashes["a.pdf"] == hashlib.sha256(b"%PDF-1 aaa").hexdigest()
    assert not (osint.case().dir / "scrape" / "files" / "missing.pdf").exists()


def test_files_limit_and_size(osint, server):
    _files_page(server)
    res = scrape.files(osint.cfg(), osint.case(), server.url("/docs"), exts=[".pdf", "docx"], limit=1)
    assert res["found"] == 4 and res["downloaded"] == 1 and res["skipped_by_limit"] == 3

    res = scrape.files(osint.cfg(), osint.case(), server.url("/docs"), exts=["pdf"], max_mb=0)
    assert res["downloaded"] == 0
    assert any("больше 0 МБ" in e["error"] for e in res["errors"])


def test_files_parallel_but_ordered(osint, server):
    """До трёх скачиваний одновременно; журнал доказательств — в порядке ссылок; одинаковые имена не затираются."""
    import threading
    import time

    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(req):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.2)
        with lock:
            state["now"] -= 1
        return 200, {"Content-Type": "application/pdf"}, req["path"].encode()

    links = [f"/p/{i}/doc.pdf" for i in range(7)]
    server.add("/many", "".join(f'<a href="{u}">x</a>' for u in links))
    for u in links:
        server.add(u, handler=slow)
    res = scrape.files(osint.cfg(), osint.case(), server.url("/many"), exts=["pdf"])
    assert res["downloaded"] == 7 and not res["errors"]
    assert state["peak"] == 3
    ev = osint.evidence()
    assert [e["source"] for e in ev] == [server.url(u) for u in links]
    assert [(osint.case().dir / e["file"]).read_bytes() for e in ev] == [u.encode() for u in links]


def test_files_public_page_cannot_reach_local_network(osint, server):
    """SSRF: посилання й редиректи з публічної сторінки не ведуть у локальну мережу."""
    server.add(server.ext("docs.test", "/list"), f"""
        <a href="{server.url('/f/a.pdf')}">loopback</a>
        <a href="http://192.168.0.1/router.pdf">роутер</a>
        <a href="http://[::1]:{server.port}/f/b.pdf">ipv6</a>
        <a href="https://cdn.test/ok.pdf">ok</a>
        <a href="https://cdn.test/bounce.pdf">редирект</a>""")
    server.add("/f/a.pdf", b"%PDF secret", ctype="application/pdf")
    server.add(server.ext("cdn.test", "/ok.pdf"), b"%PDF public", ctype="application/pdf")
    server.add(server.ext("cdn.test", "/bounce.pdf"), "", status=302, headers={"Location": server.url("/f/a.pdf")})

    res = scrape.files(osint.cfg(), osint.case(), "https://docs.test/list", exts=["pdf"])
    assert res["files"] == ["scrape_files/ok.pdf"]
    blocked = {e["url"].rsplit("/", 1)[-1] for e in res["errors"] if "внутренний адрес" in e["error"]}
    assert blocked == {"a.pdf", "router.pdf", "b.pdf", "bounce.pdf"}
    assert not server.hits("/f/a.pdf")  # до локального сервера запит так і не дійшов


def test_internal_detection():
    assert is_internal("127.0.0.1") and is_internal("10.0.0.5")
    assert is_internal("169.254.169.254") and is_internal("[::1]")
    assert is_internal("localhost")
    assert not is_internal("8.8.8.8")


def test_dns_rebinding_blocked_at_connect(server, monkeypatch):
    """Имя проходит проверку (DNS «публичный»), а соединение приходит на локальный адрес — рвём до запроса."""
    import requests

    from osint_kit import net

    server.add(server.ext("rebind.test", "/secret"), "router admin")
    monkeypatch.setattr(net, "PEER_EXEMPT", set())  # как в работе: исключений нет
    assert not is_internal("rebind.test")
    with pytest.raises(requests.ConnectionError, match="внутренний адрес"):
        net.session("ua", retries=0, public_only=True).get("http://rebind.test/secret", timeout=5)
    assert not server.hits(server.ext("rebind.test", "/secret"))  # запрос так и не был отправлен
    # обычная сессия (для адресов, которые указал сам пользователь) по-прежнему работает
    assert net.session("ua").get(server.url("/_ext/rebind.test/secret"), timeout=5).text == "router admin"


def test_public_page_redirect_to_local_network_blocked(osint, server, capsys):
    """Публичная страница (первый шаг проходит) редиректит на «роутер» — соединение рвётся до запроса."""
    import socket
    import threading

    router = socket.create_server(("127.0.0.1", 0))  # «устройство в локальной сети», не в исключениях
    received = []

    def accept():
        router.settimeout(5)
        try:
            conn, _ = router.accept()
            conn.settimeout(1)
            try:
                received.append(conn.recv(1024))
            except OSError:
                received.append(b"")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()
    port = router.getsockname()[1]
    server.add(server.ext("pub.test", "/go"), "", status=302, headers={"Location": f"http://127.0.0.1:{port}/admin"})
    assert osint("scrape", "links", "https://pub.test/go") == 1
    assert "внутренний адрес" in capsys.readouterr().out
    assert server.hits(server.ext("pub.test", "/go"))  # первый шаг (публичный) выполнен
    router.close()
    assert all(r == b"" for r in received)  # HTTP-запрос на «роутер» не ушёл
