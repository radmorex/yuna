"""Оконное приложение: мост Api, очередь проверок, страница (без открытия окна)."""

import sys
from osint_kit import config
import time

import pytest

from osint_kit.app import page_html
from osint_kit.app.api import TOOLS, TOOLS_BY_ID, Api, Jobs, build_argv


@pytest.fixture
def api(osint):
    return Api(cfg=osint.cfg())


def _wait(api, jid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(j for j in api.list_jobs() if j["id"] == jid)
        if job["status"] not in ("в очереди", "выполняется"):
            return job
        time.sleep(0.1)
    raise AssertionError("проверка не завершилась")


def test_build_argv():
    assert build_argv("web_capture", " https://a.test ") == ["web", "capture", "https://a.test"]
    assert build_argv("files", "https://a.test", {"ext": "pdf  xlsx"}) == ["scrape", "files", "https://a.test", "--ext", "pdf", "xlsx"]
    assert build_argv("files", "https://a.test") == ["scrape", "files", "https://a.test", "--ext", "pdf", "docx"]
    assert build_argv("search", "Олена", {"kind": "name"}) == ["search", "Олена", "--kind", "name"]
    assert build_argv("files", "https://a.test", {"ext": "pdf"}) == ["scrape", "files", "https://a.test", "--ext", "pdf"]
    for bad in [("web_capture", "", {}), ("web_capture", "--out=/etc", {}), ("nope", "x", {}),
                ("search", "x", {"kind": "rm -rf"}), ("files", "https://a.test", {"ext": "pdf --out"})]:
        with pytest.raises(ValueError):
            build_argv(*bad)
    assert all(t["argv"][0] in ("web", "scrape", "infra", "wp", "search", "media", "username", "translate", "ioc") for t in TOOLS)


def test_cases_and_data(api, osint):
    assert api.state()["vault"] == "выключено" and api.state()["cases"] == []
    with pytest.raises(RuntimeError):
        api.create_case("   ")
    created = api.create_case("Пропал Иван 2026")
    assert created["name"] == "Пропал_Иван_2026" and created["cases"] == ["Пропал_Иван_2026"]
    with pytest.raises(RuntimeError, match="не найдено"):
        api.case("нет-такого")
    assert api.case("Пропал_Иван_2026") == {"name": "Пропал_Иван_2026", "findings": [], "evidence": [], "notes": []}


def test_preview_and_reveal_stay_inside_case(api, osint, tmp_path, monkeypatch):
    c = osint.case("d")
    png = c.save_bytes("web", "shot.png", b"\x89PNGfake")
    c.save_text("web", "page.html", "<script>alert(1)</script>")
    (tmp_path / "secret.png").write_bytes(b"secret")
    res = api.preview("d", "web/shot.png")
    assert res["ok"] and res["src"].startswith("data:image/png;base64,")
    assert api.preview("d", "web/page.html") == {"ok": False}  # HTML не отдаём в окно
    for bad in ("../../secret.png", str(tmp_path / "secret.png"), "web/none.png"):
        with pytest.raises(RuntimeError, match="не найден"):
            api.preview("d", bad)
    opened = []
    monkeypatch.setattr("osint_kit.app.api.subprocess.run", lambda argv, check=False: opened.append(argv))
    api.reveal("d", "web/shot.png")
    assert opened == [["open", "-R", str(png.resolve())]]


def test_run_job_end_to_end(api, osint):
    api.create_case("d")
    job = api.run("d", "search", "Олена Коваль", {"kind": "name"})
    assert job["status"] in ("в очереди", "выполняется")
    assert _wait(api, job["id"])["status"] == "готово"
    assert api.case("d")["findings"][0]["title"] == "Поисковые запросы (name)"


def test_job_error_is_reported(api, osint):
    # процесс проверки не под тестовым запретом сети — поэтому только локальная ошибка
    api.create_case("d")
    job = api.run("d", "video", "/нет/такого/файла.mp4")
    done = _wait(api, job["id"])
    assert done["status"] == "ошибка" and done["error"] == "Файл не найден"


def test_cancel_kills_process(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "import time; time.sleep(30)"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    api.create_case("d")
    job = api.run("d", "search", "x")
    for _ in range(50):
        if api.list_jobs()[0]["status"] == "выполняется":
            break
        time.sleep(0.05)
    assert api.cancel(job["id"]) is True
    assert _wait(api, job["id"], timeout=5)["status"] == "отменено"
    assert api.cancel(job["id"]) is False


def test_parallel_limit(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "import time; time.sleep(1)"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    api.create_case("d")
    ids = [api.run("d", "search", f"q{i}")["id"] for i in range(5)]
    time.sleep(0.4)
    running = [j for j in api.list_jobs() if j["status"] == "выполняется"]
    assert len(running) == 3  # ТЗ: не больше трёх одновременно
    for jid in ids:
        api.cancel(jid)


def test_lock_refused_while_running(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "import time; time.sleep(30)"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    api.create_case("d")
    job = api.run("d", "search", "x")
    with pytest.raises(RuntimeError, match="Идут проверки"):
        api.lock()
    api.cancel(job["id"])


def test_page_is_self_contained():
    html = page_html()
    assert "/*CSS*/" not in html and "/*JS*/" not in html
    assert "Content-Security-Policy" in html and "default-src 'none'" in html and "connect-src 'none'" in html
    assert "'unsafe-eval'" in html  # без него мост pywebview не создаётся — чёрный экран
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")  # нет внешних ресурсов
    assert "https://" not in html
    assert ".innerHTML" not in html  # данные дела выводятся только как текст


def test_cli_app_command():
    from osint_kit.cli import build_parser
    assert build_parser().parse_args(["app"]).bundle is False
    assert build_parser().parse_args(["app", "--bundle"]).bundle is True


def test_js_sees_only_methods(api):
    # pywebview отдаёт странице всё публичное; конфиг (ключи API) и очередь — только приватно
    public = {n for n in dir(api) if not n.startswith("_")}
    assert public == {"state", "unlock", "lock", "create_case", "case", "preview", "reveal", "export",
                      "run", "list_jobs", "cancel", "pick_file", "delete_case", "delete_evidence",
                      "summary", "note", "save_note", "delete_note", "cases_info", "finding_paste",
                      "thumb", "copy_text", "open_link", "route_status", "route_start", "route_check",
                      "route_set", "ai_status", "ai_ask", "plan", "run_all", "cancel_batch",
                      "assistant_plan", "assistant_run", "assistant_report", "assistant_start",
                      "settings", "set_key", "set_direct_fallback", "import_case"}


def test_dossier_notes_and_summary(api, osint):
    c = osint.case("d")
    shot = c.save_text("web", "a.txt", "a")
    c.add_finding("web", "https://a.test", "Захват страницы", {"title": "A", "empty": ""}, [shot])
    c.add_finding("username", "nick", "Аккаунты по нику @nick: 2", {"found": 2}, status="кандидаты — требуют проверки")
    text = api.summary("d")
    src, hyp = text.index("ПОЛУЧЕНО ИЗ ИСТОЧНИКОВ"), text.index("ГИПОТЕЗЫ — ТРЕБУЮТ ПРОВЕРКИ")
    assert src < text.index("Захват страницы") < hyp < text.index("Аккаунты по нику")  # гипотеза — в своём разделе
    assert "статус: кандидаты — требуют проверки" in text and "empty" not in text
    meta = api.save_note("d", "заметки", "первая версия", True)
    assert meta["name"] == "заметки.txt" and api.case("d")["notes"][0]["name"] == "заметки.txt"
    api.save_note("d", "заметки.txt", "вторая версия")
    assert api.note("d", "заметки.txt") == {"name": "заметки.txt", "text": "вторая версия"}
    assert "заметок ....... 1" in api.summary("d")
    assert api.delete_note("d", "заметки.txt") is True and api.case("d")["notes"] == []


def test_delete_from_window(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "import time; time.sleep(30)"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    c = osint.case("d")
    c.save_text("web", "a.txt", "a")
    api.create_case("other")
    job = api.run("other", "search", "x")
    with pytest.raises(RuntimeError, match="не совпадает"):
        api.delete_case("d", "D")
    assert api.delete_evidence("d", "E1")["deleted"]  # проверка идёт по другому делу — можно
    with pytest.raises(RuntimeError, match="идут проверки"):
        api.delete_case("other", "other")
    api.cancel(job["id"])
    _wait(api, job["id"], timeout=5)
    assert api.delete_case("other", "other")["cases"] == ["d"]
    with pytest.raises(RuntimeError, match="не найдено"):
        api.delete_case("other", "other")
    job = api.run("d", "search", "x")
    with pytest.raises(RuntimeError, match="идут проверки"):
        api.delete_evidence("d", "E1")
    api.cancel(job["id"])


def test_egress_shown_before_run(osint):
    from osint_kit.app.api import EGRESS, egress

    cfg = osint.cfg()
    tools = {t["id"]: t for t in Api(cfg=cfg).state()["tools"]}
    assert set(tools) == set(TOOLS_BY_ID) == set(EGRESS)  # у каждой проверки — свой список получателей
    assert tools["ioc"]["egress"] == {"local": True, "items": [], "route": cfg.route}
    assert tools["username"]["egress"]["local"] is False
    assert any("LibreTranslate" in i for i in tools["translate"]["egress"]["items"])
    # сервис с ключом показывается, только если ключ задан
    assert not any("VirusTotal" in i for i in tools["ip"]["egress"]["items"])
    osint.configure(keys={"virustotal": "k"}, libretranslate=False)
    cfg = osint.cfg()
    assert any("VirusTotal" in i for i in egress(TOOLS_BY_ID["ip"], cfg)["items"])
    assert any("Google" in i for i in egress(TOOLS_BY_ID["translate"], cfg)["items"])


def test_classify_target(tmp_path):
    from osint_kit.app.api import classify

    photo, clip, doc = tmp_path / "a.JPG", tmp_path / "b.mov", tmp_path / "c.pdf"
    for f in (photo, clip, doc):
        f.write_bytes(b"x")
    cases = {
        "https://a.test/x": "url", "http://a.test": "url", "example.com": "domain", "уф.рф": "domain",
        "93.184.216.34": "ip", "2001:db8::1": "ip", "ivan@example.org": "email",
        "+380 (50) 123-45-67": "phone", "ivan_petrov": "username", "Иван Петров": "name",
        "пропал в районе вокзала 12 октября": "text", str(photo): "image", str(clip): "video", str(doc): "file",
    }
    for value, kind in cases.items():
        assert classify(value) == kind, value


def test_plan_is_shown_and_safe(api):
    plan = api.plan("example.com")
    assert plan["kind"] == "domain"
    assert [s["tool"] for s in plan["steps"]] == ["domain", "web_capture", "wayback", "links", "headers"]
    assert plan["egress"]["items"] and not plan["egress"]["local"]
    # по людям и через сторонний перевод — только вручную
    for target in ("ivan_petrov", "Иван Петров", "ivan@example.org", "текст про событие"):
        tools = {s["tool"] for s in api.plan(target)["steps"]}
        assert not tools & {"username", "translate"}, target
    assert api.plan("ivan@example.org")["egress"]["local"] is True
    with pytest.raises(ValueError):
        api.plan("  ")


def test_run_all_batch_progress_and_cancel(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "import time; time.sleep(30)"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    api.create_case("b")
    res = api.run_all("b", "https://a.test/x")
    assert res["total"] == 4 and len({j["batch"] for j in api.list_jobs()}) == 1
    assert sum(j["status"] == "выполняется" for j in api.list_jobs()) <= 3
    assert api.cancel_batch(res["batch"]) == 4
    deadline = time.time() + 10
    while time.time() < deadline and any(j["status"] in ("в очереди", "выполняется") for j in api.list_jobs()):
        time.sleep(0.1)
    assert {j["status"] for j in api.list_jobs()} == {"отменено"}
    assert api.cancel_batch(res["batch"]) == 0


def test_find_targets_in_request():
    from osint_kit.app.api import find_targets

    text = "проанализируй сайт https://a.test/news, ещё example.com и 93.184.216.34, т.е. всё. Почта: ivan@example.org"
    assert find_targets(text) == ["https://a.test/news", "example.com", "93.184.216.34", "ivan@example.org"]
    assert find_targets("проверь a.test и https://a.test/x") == ["a.test", "https://a.test/x"]
    assert find_targets("что известно по делу? составь хронологию") == []


def test_assistant_plan_and_report(api, osint, monkeypatch):
    from osint_kit.modules import ai

    plan = api.assistant_plan("проанализируй example.com и дай отчёт")
    assert [t["target"] for t in plan["targets"]] == ["example.com"]
    assert plan["egress"]["items"]
    assert api.assistant_plan("составь хронологию")["targets"] == []
    with pytest.raises(ValueError):
        api.assistant_plan("сайты " + " ".join(f"s{i}.test" for i in range(10)))  # не массовый сбор

    api.create_case("r")
    c = osint.case("r")
    html = c.save_text("web", "page.html", "<html><head><meta name=description content='Сайт волонтёров'></head>"
                       "<body><script>x()</script><h1>Поиск пропавших</h1></body></html>", "https://a.test/", "страница")
    c.add_finding("web", "https://a.test/", "Захват страницы", {"title": "Поиск", "user_agent": "Mozilla/5.0", "files": ["web/page.html"]}, [html])
    c.add_finding("infra", "a.test", "Домен a.test", {"хостинг": "AS13335 CLOUDFLARENET", "NS": ["a", "b"],
                                                      "недоступно": ["crt.sh: 502"]})
    asked = {}
    monkeypatch.setattr(ai, "_chat", lambda cfg, system, prompt: asked.update(system=system, prompt=prompt)
                        or "Это сайт волонтёров. Его ведёт John Smith.")  # имени нет в материалах — выдумка
    res = api.assistant_report("r", "проанализируй a.test", [{"tool": "Домен", "target": "a.test", "status": "готово"},
                                                            {"tool": "Wayback", "target": "a.test", "status": "ошибка",
                                                             "error": "таймаут"}])
    # в ИИ — факты без служебных полей и текст самой страницы; ответ — коротко + факты + неудачи
    assert "AS13335" in asked["prompt"] and "Поиск пропавших" in asked["prompt"] and "Сайт волонтёров" in asked["prompt"]
    assert "Mozilla" not in asked["prompt"] and "x()" not in asked["prompt"]
    assert "crt.sh" not in asked["prompt"]  # наши сбои ИИ не видит — иначе выдумывает выводы о цели
    text = res["answer"]
    assert text.startswith("ГЛАВНОЕ:") and "a.test закрыт сетью Cloudflare" in text  # вывод программы, не ИИ
    assert "Это сайт волонтёров." in text and "John Smith" not in text  # выдуманное имя отброшено
    assert "хостинг: AS13335" in text and "заголовок: Поиск" in text and "NS: a, b" in text
    failed = text.split("НЕ УДАЛОСЬ")[1]
    assert "Wayback · a.test: таймаут" in failed and "Домен a.test — не ответили: crt.sh: 502" in failed
    assert "Домен · a.test" not in failed and "недоступно" not in text
    note = c.note_path(res["note"]).read_text(encoding="utf-8")
    assert note.startswith("ОТЧЁТ · ") and " UTC · проверок 1 из 2" in note and "Просьба: проанализируй a.test" in note

def test_assistant_start_opens_new_case(osint):
    jobs = Jobs(prefix=[sys.executable, "-c", "pass"])
    api = Api(cfg=osint.cfg(), jobs=jobs)
    first = api.assistant_start("проанализируй сайт https://example.com/news")
    assert first["case"] == "example.com" and first["total"] == 4
    assert api.assistant_start("и example.com ещё раз")["case"] == "example.com-2"  # чужое дело не трогаем
    again = api.assistant_start("теперь 93.184.216.34", "example.com")  # продолжение диалога — то же дело
    assert again["case"] == "example.com" and {j["case"] for j in api.list_jobs() if j["batch"] == again["batch"]} == {"example.com"}
    assert api.assistant_start("почта ivan@example.org")["case"] == "ivan_example.org"
    with pytest.raises(ValueError):
        api.assistant_start("составь хронологию")
    assert set(api.state()["cases"]) == {"example.com", "example.com-2", "ivan_example.org"}


def test_ai_answer_without_chinese(osint, monkeypatch):
    from osint_kit.modules import ai

    replies = iter(["IP принадлежит Datacamp. 尽管您要求用俄语回答，但上述内容。Сеть в Польше.",
                    "Сеть CDNEXT-WAR, 根据提供的信息。Провайдер Datacamp."])
    monkeypatch.setattr(ai, "ensure_server", lambda cfg: None)
    monkeypatch.setattr(ai, "status", lambda cfg: {"ready": True})

    class R:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": self.text}}

    class S:
        def post(self, *a, **k): return R(next(replies))

    monkeypatch.setattr(ai, "session", lambda *a, **k: S())
    out = ai._chat(osint.cfg(), "sys", "q")  # повтор не помог — китайские предложения вырезаны
    assert not ai._CJK.search(out) and "Провайдер Datacamp." in out


def test_settings_keys_never_leave_and_fallback_toggle(osint):
    api = Api(cfg=osint.cfg())
    st = api.settings()
    assert st["keys"]["virustotal"] is False and st["direct_fallback"] is True and st["route"] == "direct"
    api.set_key("virustotal", "  SECRET-VT-123  ")
    st = api.settings()
    assert st["keys"]["virustotal"] is True and "SECRET-VT-123" not in str(st)  # значение ключа в окно не уходит
    assert config.load().key("virustotal") == "SECRET-VT-123"
    assert (config.config_path().stat().st_mode & 0o077) == 0  # файл с ключами — только владельцу
    assert {t["id"]: t for t in api.state()["tools"]}["hash"]["available"] is True
    api.set_key("virustotal", "")
    assert api.settings()["keys"]["virustotal"] is False
    with pytest.raises(ValueError):
        api.set_key("../evil", "x")
    api.set_direct_fallback(False)
    assert config.load().direct_fallback == () and api.settings()["direct_fallback"] is False
    api.set_direct_fallback(True)
    assert config.load().direct_fallback == config.DIRECT_FALLBACK
    assert config.load().route == "direct"  # остальные настройки не тронуты


def test_keyed_and_new_tools(osint):
    tools = {t["id"]: t for t in Api(cfg=osint.cfg()).state()["tools"]}
    assert tools["download"]["egress"]["items"]
    for tid in ("hash", "urlcheck", "aicheck"):  # без ключа — недоступны и никуда не отправляют
        assert tools[tid]["available"] is False and tools[tid]["egress"]["local"], tid
    assert tools["download"]["available"] is True
    assert tools["hash"]["available"] is False and "VirusTotal" in tools["hash"]["needs_label"]
    assert build_argv("download", "https://v.test/1") == ["media", "download", "https://v.test/1"]
    assert build_argv("urlcheck", "https://a.test") == ["infra", "url", "https://a.test"]


def test_import_case_from_export(osint, monkeypatch):
    from osint_kit import export

    api = Api(cfg=osint.cfg())
    api.create_case("src")
    c = osint.case("src")
    c.add_finding("web", "a.test", "Запись", {"k": "v"}, [c.save_text("web", "a.txt", "x")])
    res = export.export(c)
    out = api.import_case(res["files"]["zip"])
    assert out["case"] == "src-2" and out["case"] in api.state()["cases"]
    assert [f["title"] for f in api.case(out["case"])["findings"]] == ["Запись"]
