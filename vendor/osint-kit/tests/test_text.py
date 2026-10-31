"""Модулі translate і search."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from osint_kit.modules import text


def test_chunks_keep_text_and_size():
    src = "\n".join(["абв " * 300] * 10 + ["x" * 10000])
    parts = text._chunks(src, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts).replace("\n", "") == src.replace("\n", "")


def _libre(req):
    q = json.loads(req["body"])
    return 200, {"Content-Type": "application/json"}, json.dumps({"translatedText": q["q"].upper()}).encode()


def test_translate_offline(osint, server, capsys):
    server.add("/libre/translate", method="POST", handler=_libre)
    assert osint("translate", "привіт світ", "--offline", "--to", "en") == 0
    assert "ПРИВІТ СВІТ" in capsys.readouterr().out
    body = json.loads(server.hits("/libre/translate")[0]["body"])
    assert body == {"q": "привіт світ", "source": "auto", "target": "en", "format": "text"}

    res = text.translate(osint.cfg(), osint.case(), "a" * 3000 + "\n" + "b" * 3000, target="en", offline=True)
    assert len(server.hits("/libre/translate")) == 3
    assert res["translation"] == "A" * 3000 + "\n" + "B" * 3000


def test_translate_file(osint, server, tmp_path, capsys):
    server.add("/libre/translate", method="POST", handler=_libre)
    f = tmp_path / "msg.txt"
    f.write_text("текст з файлу", encoding="utf-8")
    assert osint("translate", "--file", str(f), "--offline") == 0
    assert "ТЕКСТ З ФАЙЛУ" in capsys.readouterr().out


def test_translate_offline_needs_url(osint, capsys):
    osint.configure(libretranslate=False)
    assert osint("translate", "x", "--offline") == 1
    assert "libretranslate_url" in capsys.readouterr().out


def test_translate_google(osint, monkeypatch):
    import deep_translator

    class FakeTranslator:
        def __init__(self, source, target):
            self.target = target

        def translate(self, part):
            return f"[{self.target}] {part}"

    monkeypatch.setattr(deep_translator, "GoogleTranslator", FakeTranslator)
    res = text.translate(osint.cfg(), osint.case(), "  hello  ", target="uk")
    assert res == {"engine": "google", "source": "auto", "target": "uk", "translation": "[uk] hello"}
    assert osint.findings()[0]["data"]["original"] == "hello"


def test_translate_empty(osint, capsys):
    assert osint("translate", "   ") == 1
    assert "Пустой текст" in capsys.readouterr().out


@pytest.mark.parametrize("kind, value, expected", [
    ("text", "щось", ["щось"]),
    ("domain", "example.test", ["site:example.test", '"example.test" -site:example.test']),
    ("email", "ivan@mail.test", ['"ivan@mail.test"', '"ivan"']),
    ("phone", "+38 (067) 123-45-67", ['"+380671234567"']),
    ("username", "volunteer_42", ['"volunteer_42" site:github.com OR site:gitlab.com']),
    ("name", "Олена Коваль", ['"Олена Коваль" site:linkedin.com']),
])
def test_dorks(kind, value, expected):
    queries = text.dorks(kind, value)
    for q in expected:
        assert q in queries


def test_search_links(osint, monkeypatch):
    opened = []
    monkeypatch.setattr(text.webbrowser, "open_new_tab", opened.append)
    assert osint("search", "Олена Коваль", "--kind", "name", "--engine", "Google", "Bing") == 0
    [finding] = osint.findings()
    first = finding["data"][0]
    assert set(first["links"]) == {"Google", "Bing"}
    assert parse_qs(urlparse(first["links"]["Google"]).query)["q"] == ['"Олена Коваль"']
    assert opened == []  # без --open браузер не відкривається

    text.search(osint.cfg(), osint.case(), "x", kind="name", open_browser=True)
    assert len(opened) == 3 * len(text.ENGINES)  # не більше 3 запитів
