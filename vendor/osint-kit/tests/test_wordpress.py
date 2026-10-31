"""Модуль wp: згадки в публікаціях WordPress і фото (сайт — локальна заглушка)."""

import io
import json

import pytest

from osint_kit.modules import wordpress

POSTS = [
    {"id": 101, "date": "2025-12-08T22:20:58", "link": "https://news.test/news/a/101",
     "title": {"rendered": "Тренінг з медіаграмотності"},
     "content": {"rendered": "<p>Заняття провела Олена Коваль у Мукачеві. Потім команда поїхала до Ужгорода.</p>"
                             "<p>Учасники з Києва.</p>"},
     "tags": [1, 2], "categories": [5], "featured_media": 11},
    {"id": 102, "date": "2023-12-09T23:48:24", "link": "https://news.test/news/b/102",
     "title": {"rendered": "Екскурсія &#8211; Берегове"},
     "content": {"rendered": "<p>Серед організаторів — Олени Коваль (ФІМВ). Береговий схил тут ні до чого.</p>"},
     "tags": [2], "categories": [], "featured_media": 0},
]


def _jpeg(gps: bool) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (32, 24), "blue")
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    if gps:
        exif.get_ifd(0x8825).update({1: "N", 2: (48.0, 37.0, 12.0), 3: "E", 4: (22.0, 18.0, 0.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _media(req):
    q = req["query"]
    if q.get("parent") == ["101"]:
        rows = [{"id": 11, "source_url": "https://news.test/live/z-gps.jpg"},
                {"id": 12, "source_url": "https://news.test/live/bez-gps.jpg"},
                {"id": 13, "source_url": "https://news.test/live/plan.pdf"},
                {"id": 14, "source_url": "http://192.168.1.5/live/router.jpg"}]
    elif q.get("include") == ["11"]:
        rows = [{"id": 11, "source_url": "https://news.test/live/z-gps.jpg"}]  # дубль обкладинки
    else:
        rows = []
    return 200, {"Content-Type": "application/json"}, json.dumps(rows).encode()


@pytest.fixture
def site(server):
    api = "/wp-json/wp/v2"
    server.add(server.ext("news.test", f"{api}/posts"), POSTS)
    server.add(server.ext("news.test", f"{api}/tags"),
               [{"id": 1, "name": "Філологічний факультет"}, {"id": 2, "name": "Медіаграмотність"}])
    server.add(server.ext("news.test", f"{api}/categories"), [{"id": 5, "name": "Обмін досвідом"}])
    server.add(server.ext("news.test", f"{api}/media"), handler=_media)
    server.add(server.ext("news.test", "/live/z-gps.jpg"), _jpeg(True), ctype="image/jpeg")
    server.add(server.ext("news.test", "/live/bez-gps.jpg"), _jpeg(False), ctype="image/jpeg")
    return server


def test_search_timeline_and_places(osint, site):
    assert osint("wp", "search", "Олена Коваль", "--site", "https://news.test", "--limit", "500") == 0
    [req] = site.hits(site.ext("news.test", "/wp-json/wp/v2/posts"))
    assert req["query"]["search"] == ["Олена Коваль"] and req["query"]["per_page"] == ["100"]

    [finding] = osint.findings()
    data = finding["data"]
    assert data["status"] == "гипотезы — требуют проверки"
    assert data["found"] == 2 and data["period"] == {"from": "2023-12-09", "to": "2025-12-08"}
    assert data["places"] == {"Мукачево": 1, "Ужгород": 1, "Киев": 1, "Берегово": 1}

    first, second = data["latest"]
    assert first["title"] == "Тренінг з медіаграмотності"
    assert first["places"] == {"Мукачево": 1, "Ужгород": 1, "Киев": 1}
    assert second["title"] == "Екскурсія – Берегове"  # HTML-сутності розкодовані

    res = wordpress.search(osint.cfg(), osint.case(), "Олена Коваль", site="https://news.test")
    assert res["timeline"][0]["tags"] == ["Філологічний факультет", "Медіаграмотність"]
    assert res["timeline"][0]["categories"] == ["Обмін досвідом"]
    assert "Олена Коваль у Мукачеві" in res["timeline"][0]["snippets"][0]
    assert "Олени Коваль" in res["timeline"][1]["snippets"][0]  # інший відмінок
    assert "photos" not in res and not site.hits("/_ext/news.test/live/z-gps.jpg")


def test_search_photos_gps(osint, site):
    res = wordpress.search(osint.cfg(), osint.case(), "Олена Коваль", site="https://news.test", photos=True)
    ph = res["photos"]
    assert ph["found"] == 3 and ph["checked"] == 2 and ph["with_gps"] == 1  # pdf не фото; дубль обкладинки один раз
    [gps] = ph["gps"]
    lat, lon = (float(x) for x in gps["gps"].split(","))
    assert lat == pytest.approx(48.62, abs=1e-3) and lon == pytest.approx(22.3, abs=1e-3)
    assert gps["post"] == "https://news.test/news/a/101" and gps["map"].startswith("https://www.google.com/maps?q=")
    assert [e["error"] for e in ph["errors"]] == ["внутренний адрес сети — пропущено"]

    photo_finding = osint.findings()[-1]
    assert photo_finding["title"] == "Фото из публикаций: 2, с GPS: 1"
    assert any(f.endswith("z-gps.jpg") for f in photo_finding["files"])
    assert sum(e["file"].endswith(".jpg") for e in osint.evidence()) == 2


def test_photos_limit(osint, site):
    res = wordpress.search(osint.cfg(), osint.case(), "Коваль", site="https://news.test", photos=True, photos_limit=1)
    assert res["photos"]["checked"] == 1 and res["photos"]["skipped_by_limit"] == 2


def test_site_without_wp_api(osint, server, capsys):
    assert osint("wp", "search", "x", "--site", "https://plain.test") == 1
    assert "WordPress REST API недоступен" in capsys.readouterr().out


def test_empty_query(osint, capsys):
    assert osint("wp", "search", "  ") == 1
    assert "Пустой запрос" in capsys.readouterr().out


def test_presets_and_helpers():
    assert wordpress._base("mediacenter") == "https://mediacenter.uzhnu.edu.ua"
    assert wordpress._base("news.test/") == "https://news.test"
    assert wordpress.places_in("У Києві та Львові, потім Чопі; береговий схил") == {"Киев": 1, "Львов": 1, "Чоп": 1}
    assert wordpress.places_in("Чопенко") == {}
    assert wordpress.snippets("коротко", "") == []
