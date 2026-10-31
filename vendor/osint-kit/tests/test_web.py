"""Модуль web: захоплення сторінки (локальний Chromium), Wayback, заголовки."""

import json

import pytest

from osint_kit.case import sha256_file
from osint_kit.modules import web

PAGE = """<!doctype html><html><head><title>Тестова сторінка</title>
<script src="https://www.google-analytics.com/analytics.js"></script></head>
<body><h1>MARKER-123</h1><p>Оголошення про зникнення</p></body></html>"""


@pytest.mark.slow
def test_capture(osint, server):
    server.add("/page", PAGE)
    assert osint("web", "capture", server.url("/page")) == 0

    [finding] = osint.findings()
    data = finding["data"]
    assert data["title"] == "Тестова сторінка"
    assert data["status"] == 200
    assert data["trackers_blocked"] >= 1  # google-analytics не завантажувався
    exts = sorted(f.rsplit(".", 1)[-1] for f in data["files"])
    assert exts == ["html", "json", "mhtml", "pdf", "png"]

    case = osint.case()
    html_file = next(case.dir / f for f in data["files"] if f.endswith(".html"))
    assert "MARKER-123" in html_file.read_text(encoding="utf-8")
    for entry in osint.evidence():
        assert sha256_file(case.dir / entry["file"]) == entry["sha256"]


@pytest.mark.slow
def test_capture_no_pdf(osint, server):
    server.add("/page", PAGE)
    assert osint("web", "capture", server.url("/page"), "--no-pdf") == 0
    [finding] = osint.findings()
    assert not any(f.endswith(".pdf") for f in finding["data"]["files"])


def test_capture_unreachable(osint, capsys):
    assert osint("web", "capture", "http://127.0.0.1:1/") == 1
    assert "Не удалось открыть страницу" in capsys.readouterr().out


CDX = [
    ["timestamp", "original", "statuscode", "mimetype", "digest"],
    ["20240105120000", "http://example.test/", "200", "text/html", "BBB"],
    ["20190301083000", "http://example.test/", "200", "text/html", "AAA"],
]


def _cdx(req):
    if req["query"].get("limit") == ["1"]:  # запит найпершого знімка в архіві
        rows = [["timestamp"], ["20130624074432"]]
    else:
        rows = CDX
    return 200, {"Content-Type": "application/json"}, json.dumps(rows).encode()


def test_wayback(osint, server):
    server.add(server.ext("web.archive.org", "/cdx/search/cdx"), handler=_cdx)
    assert osint("web", "wayback", "https://example.test", "--limit", "10") == 0

    req, oldest = server.hits(server.ext("web.archive.org", "/cdx/search/cdx"))
    assert req["query"]["url"] == oldest["query"]["url"] == ["example.test"]
    assert req["query"]["limit"] == ["-10"]

    res = web.wayback(osint.cfg(), osint.case(), "example.test")
    assert res["count"] == 2
    # first — найперший знімок в архіві взагалі, а не найстаріший із показаних
    assert res["first"] == "2013-06-24 07:44" and res["last"] == "2024-01-05 12:00"
    assert res["snapshots"][0]["archive_url"] == "https://web.archive.org/web/20190301083000/http://example.test/"


def test_wayback_empty(osint, server):
    server.add(server.ext("web.archive.org", "/cdx/search/cdx"), "", ctype="application/json")
    res = web.wayback(osint.cfg(), osint.case(), "nothing.test")
    assert res["count"] == 0 and "first" not in res


def test_wayback_save(osint, server):
    server.add(server.ext("web.archive.org", "/cdx/search/cdx"), CDX)
    server.add(server.ext("web.archive.org", "/save/https://example.test"), "ok",
               headers={"Content-Location": "/web/20261023000000/https://example.test"})
    res = web.wayback(osint.cfg(), osint.case(), "example.test", save=True)
    assert res["saved"] == "https://web.archive.org/web/20261023000000/https://example.test"


def test_wayback_list_limited_falls_back_to_closest(osint, server):
    """Список снимков не отдали (лимит) — берём ближайший снимок из лёгкого запроса архива, а не просто ошибку."""
    server.add(server.ext("archive.org", "/wayback/available"), {"archived_snapshots": {"closest": {
        "available": True, "timestamp": "20260301120000", "url": "http://web.archive.org/web/20260301120000/example.test"}}})
    res = web.wayback(osint.cfg(), osint.case(), "example.test")
    assert res["count"] is None and res["last"] == "2026-03-01 12:00" and "список снимков недоступен" in res["list_error"]
    finding = osint.case().findings()[-1]
    assert finding["title"] == "Wayback Machine: есть снимки, последний 01.03.2026"


def test_wayback_unavailable(osint, capsys):
    assert osint("web", "wayback", "example.test") == 1  # маршрут не задано -> 404
    assert "Wayback CDX недоступен" in capsys.readouterr().out


def test_headers_redirect_chain(osint, server):
    server.add("/old", "", status=301, headers={"Location": "/new"})
    server.add("/new", "hi", headers={"X-Test": "1"})
    res = web.headers_only(osint.cfg(), server.url("/old"))
    assert [h["status"] for h in res["redirects"]] == [301, 200]
    assert res["redirects"][0]["location"] == "/new"
    assert res["headers"]["X-Test"] == "1"
    assert osint("web", "headers", server.url("/old")) == 0
    finding = osint.case().findings()[-1]  # результат лежит в деле, а не только в выводе
    assert finding["title"].startswith("Заголовки") and finding["data"]["редиректов"] == 1


def test_headers_public_redirect_to_local_network_blocked(osint, server):
    """SSRF: публичный сайт редиректом не заводит запрос в локальную сеть; шаг виден в цепочке."""
    server.add("/secret", "секрет")
    for i, target in enumerate((server.url("/secret"), "http://169.254.169.254/latest/meta-data/")):
        server.add(server.ext("pub.test", f"/r{i}"), "", status=302, headers={"Location": target})
        res = web.headers_only(osint.cfg(), f"https://pub.test/r{i}")
        assert res["stopped"] == "редирект в локальную сеть"
        assert res["redirects"][0]["status"] == 302 and res["redirects"][-1] == {
            "url": target, "blocked": "внутренний адрес сети — не открывался"}
    assert not server.hits("/secret")


def test_headers_redirect_limits(osint, server):
    server.add(server.ext("pub.test", "/loop"), "", status=302, headers={"Location": "/loop"})
    assert web.headers_only(osint.cfg(), "https://pub.test/loop", max_redirects=3)["stopped"] == "слишком много редиректов"
    server.add(server.ext("pub.test", "/ftp"), "", status=302, headers={"Location": "ftp://pub.test/x"})
    assert web.headers_only(osint.cfg(), "https://pub.test/ftp")["stopped"] == "схема ftp не поддерживается"


def test_tracker_list():
    assert web._is_tracker("https://www.googletagmanager.com/gtm.js")
    assert not web._is_tracker("https://example.test/app.js")
    assert json.loads(web.dump({"a": "б"})) == {"a": "б"}


class _FakeRoute:
    def __init__(self, url):
        self.request = type("R", (), {"url": url})()
        self.action = None

    def abort(self):
        self.action = "abort"

    def fallback(self):
        self.action = "fallback"


class _FakePage:
    def __init__(self):
        self.handlers = []

    def route(self, pattern, handler):
        self.handlers.append(handler)


def test_browser_blocks_local_network_for_public_target():
    from osint_kit.net import block_internal_requests

    page = _FakePage()
    stats = block_internal_requests(page, "https://8.8.8.8/")
    [handler] = page.handlers
    actions = {}
    for url in ("http://192.168.1.1/admin", "http://127.0.0.1:8080/", "http://169.254.169.254/latest",
                "https://8.8.4.4/app.js", "data:image/png;base64,AAAA"):
        route = _FakeRoute(url)
        handler(route)
        actions[url] = route.action
    assert actions == {"http://192.168.1.1/admin": "abort", "http://127.0.0.1:8080/": "abort",
                       "http://169.254.169.254/latest": "abort", "https://8.8.4.4/app.js": "fallback",
                       "data:image/png;base64,AAAA": "fallback"}
    assert stats["internal_blocked"] == 3


def test_browser_local_target_is_not_filtered():
    from osint_kit.net import block_internal_requests

    page = _FakePage()
    block_internal_requests(page, "http://127.0.0.1:3000/")
    assert page.handlers == []


def test_rate_limit_error_is_readable(osint, server):
    server.add(server.ext("web.archive.org", "/cdx/search/cdx"), "", status=429)
    with pytest.raises(RuntimeError, match="429"):
        web.wayback(osint.cfg(), osint.case(), "example.test")
