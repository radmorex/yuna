"""Отчёт ассистента: выводы программы, проверка ответа ИИ на выдумки, фото из диалога."""

import sys
from pathlib import Path

import pytest

from osint_kit.app.api import Api, Jobs, find_targets
from osint_kit.modules import ai


def test_grounded_drops_invented_names_and_mixed_script():
    source = "OSINT framework. Follow me on Twitter: @jnordine. Watch the project on Github."
    text, dropped = ai.grounded("Это каталог OSINT. Сайт ведёт Джон Нордinem. Автор — John Nordine. Автор — @jnordine.", source)
    assert text == "Это каталог OSINT. Автор — @jnordine." and dropped == 2


def test_facts_readable_and_without_repeats(osint):
    c = osint.case()
    c.add_finding("web", "https://a.test", "Захват страницы: Привет", {"title": "Привет", "status": 200, "server_ip": "1.2.3.4"})
    c.add_finding("web", "https://a.test", "Заголовки: https://a.test", {"итоговый адрес": "https://a.test", "статус": 200,
                                                                         "редиректов": 0, "x-powered-by": None})
    c.add_finding("infra", "a.test", "Домен a.test", {"IP-адреса": ["1.2.3.4"], "создан": "2016-02-18 21:07:42+00:00",
                                                      "NS": ["ns1.a.test."], "недоступно": ["crt.sh: 502"]})
    text = ai.facts(c)
    assert text.index("Домен a.test") < text.index("Захват страницы")  # домен — первым
    assert "создан: 18.02.2016" in text and "NS: ns1.a.test" in text and "ns1.a.test." not in text
    assert "IP сервера" not in text and text.count("HTTP-статус: 200") == 1  # одно и то же — один раз
    assert "Заголовки" not in text and "редиректов" not in text and "недоступно" not in text


def test_highlights_cdn_age_and_contacts(osint):
    c = osint.case()
    c.add_finding("infra", "new.test", "Домен new.test", {"IP-адреса": ["104.16.1.1"], "хостинг": "AS13335 CLOUDFLARENET",
                                                          "создан": "2026-09-01", "регистратор": "R"})
    c.add_finding("scrape", "https://new.test", "Ссылки", {"соцсети": ["https://t.me/x"], "e-mail": ["a@new.test"]})
    lines = ai.highlights(c)
    assert lines[0].startswith("new.test закрыт сетью Cloudflare")
    assert "Домен свежий" in lines[1] and "(гипотеза)" in lines[1]
    assert "На сайте указаны аккаунты: t.me/x." in lines and "Контакты на сайте: a@new.test." in lines


def test_photo_report_needs_no_model_and_hides_home(osint, monkeypatch):
    c = osint.case()
    photo = str(Path.home() / "Desktop" / "Снимок экрана.png")
    c.add_finding("media", photo, "Метаданные: Снимок экрана.png", {"тип": "снимок экрана", "дата": "2026-10-24 10:00 UTC"})
    monkeypatch.setattr(ai, "_chat", lambda *a: pytest.fail("без текста страницы ИИ не нужен"))
    res = ai.report(osint.cfg(), c, f"проверь {photo}", [{"tool": "Метаданные", "target": photo, "status": "готово"}])
    assert "Снимок экрана.png: снимок экрана, сделан 2026-10-24 10:00 UTC." in res["answer"]
    assert "Координат (GPS) в файле нет." in res["answer"] and "О САЙТЕ" not in res["answer"]
    note = c.note_path(res["note"]).read_text(encoding="utf-8")
    assert str(Path.home()) not in note and "~/Desktop/Снимок экрана.png" in note


def test_report_survives_model_failure(osint, monkeypatch):
    c = osint.case()
    page = c.save_text("web", "p.html", "<html><body>Текст</body></html>", "https://a.test/", "страница")
    c.add_finding("web", "https://a.test", "Захват страницы", {"title": "T", "status": 200}, [page])

    def down(*a):
        raise RuntimeError("Ollama не установлена")
    monkeypatch.setattr(ai, "_chat", down)
    res = ai.report(osint.cfg(), c, "a.test", [])
    assert "ИИ недоступен: Ollama не установлена" in res["answer"] and "Страница a.test открывается" in res["answer"]


def test_dialog_takes_dropped_photo_with_spaces(osint, tmp_path):
    photo = tmp_path / "Снимок экрана 2026-10-24 в 13.00.00.png"
    photo.write_bytes(b"\x89PNG")
    assert find_targets(f"проверь {photo} и example.com") == [str(photo), "example.com"]
    assert find_targets(f"«{photo}»") == [str(photo)]
    assert find_targets("путь /нет/такого файла.png") == []
    api = Api(cfg=osint.cfg(), jobs=Jobs(prefix=[sys.executable, "-c", "pass"]))
    plan = api.assistant_plan(str(photo))
    assert [(t["target"], t["kind"]) for t in plan["targets"]] == [(str(photo), "image")]
    assert api.assistant_start(str(photo))["case"] == "Снимок_экрана_2026-10-24_в_13.00.00"
