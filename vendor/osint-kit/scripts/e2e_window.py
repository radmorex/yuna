"""Сквозная проверка окна Fallen Angel: каждое действие — как пользователь, с проверкой результата.

Запуск:  [E2E_BROWSER=webkit] .venv/bin/python scripts/e2e_window.py <рабочая_папка> dark|light
(webkit — движок окна на macOS, как у pywebview; по умолчанию chromium)
Окно открывается в Chromium (Playwright) с настоящим движком (Api) и подставным мостом pywebview;
дела и config.toml — во временной папке, маршрут «напрямую», сеть не нужна. Шаг «вопрос ИИ» требует
локальной Ollama с моделью и пропускается, если её нет. Снимки упавших шагов — FAIL_*.png в рабочей папке.
"""
import json, os, shutil, sys, time, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

SP = Path(sys.argv[1]); scheme = sys.argv[2]
root = SP / "e2e" / scheme
shutil.rmtree(root, ignore_errors=True); (root / "cases").mkdir(parents=True)
cfgp = root / "config.toml"
cfgp.write_text(f'[general]\ncases_dir = "{root/"cases"}"\nencrypt_cases = false\n\n[network]\nroute = "direct"\n', encoding="utf-8")
os.environ["OSINT_KIT_CONFIG"] = str(cfgp)
from PIL import Image
photo = root / "photo.jpg"
img = Image.new("RGB", (64, 48), "red"); ex = Image.Exif(); ex[0x010F] = "TestCam"; ex[0x0110] = "X1"; img.save(photo, exif=ex)
from osint_kit.app import page_html
from osint_kit.app.api import Api
from osint_kit import config
api = Api()
picked = {"path": str(photo)}
api.pick_file = lambda: picked["path"]          # диалог выбора файла macOS в тесте не открыть
api.reveal = lambda case, rel: True
api.copy_text = staticmethod(lambda t: True)
opened = []
api.open_link = lambda url: opened.append(url) or True
errors, fails, passed = [], [], []

def call(name, args):
    try: return {"ok": getattr(api, name)(*json.loads(args))}
    except Exception as e: return {"err": str(e)}

with sync_playwright() as p:
    b = getattr(p, os.environ.get("E2E_BROWSER", "chromium")).launch()
    pg = b.new_page(viewport={"width": 1280, "height": 860}, color_scheme=scheme)
    pg.on("pageerror", lambda e: errors.append(f"JS: {e}"))
    pg.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    pg.expose_function("__call", call)
    pg.add_init_script("""window.pywebview={api:new Proxy({},{get:(_,n)=>async(...a)=>{const r=await window.__call(n,JSON.stringify(a));if('err' in r)throw new Error(r.err);return r.ok}})};""")
    (root / "p.html").write_text(page_html()); pg.goto("file://" + str(root / "p.html")); pg.wait_for_timeout(1200)
    text = lambda: pg.inner_text("body")
    def wait_text(t, ms=20000):
        pg.wait_for_function("t => document.body.innerText.includes(t)", arg=t, timeout=ms)
    def step(name, fn):
        try:
            fn(); passed.append(name)
        except Exception as e:
            fails.append(f"{name}: {e.__class__.__name__}: {str(e)[:300]}")
            pg.screenshot(path=str(root / f"FAIL_{len(fails)}.png"))
    def to_case(name="тест_дело"):
        pg.evaluate(f"openCase({json.dumps(name)})"); pg.wait_for_timeout(700)
    def wait_jobs():
        for _ in range(240):
            if not any(j["status"] in ("в очереди", "выполняется") for j in api.list_jobs()): return
            time.sleep(0.5)
        raise AssertionError("проверки не завершились")

    step("главная пустая", lambda: (wait_text("Что проверить?"), wait_text("дел пока нет")))
    def create():
        pg.fill("#new-case-name", "тест дело"); pg.click("text=Создать"); wait_text("Проверка вручную")
        assert "тест_дело" in text() or "тест дело" in text()
    step("создать дело", create)
    def manual(tool, target):
        pg.click("text=Проверка вручную"); pg.select_option("#tool", tool); pg.fill("#target", target)
        pg.click("#runform button[type=submit]"); pg.wait_for_timeout(500); wait_jobs(); pg.wait_for_timeout(1500)
    step("индикаторы из текста", lambda: (manual("ioc", "почта a@b.org сайт https://x.test 10.0.0.1"),
        pg.click(".tab >> text=Записи"), wait_text("Индикаторы из текста")))
    step("метаданные фото", lambda: (manual("exif", str(photo)), pg.click(".tab >> text=Записи"), wait_text("Метаданные: photo.jpg")))
    step("поисковые запросы", lambda: (manual("search", "Иван Петров"), pg.click(".tab >> text=Записи"), wait_text("Поисковые запросы")))
    step("все проверки готовы", lambda: [None for j in api.list_jobs() if j["status"] != "готово" and (_ for _ in ()).throw(AssertionError(f"{j['tool']}: {j['status']} {j['error']}"))])
    def open_record():
        pg.click(".rec >> text=Метаданные: photo.jpg"); wait_text("TestCam X1"); wait_text("Файлы-доказательства")
        pg.click("text=Показать как текст (паста)"); wait_text("ДЕЛО"); pg.click("text=Показать таблицей"); wait_text("камера")
    step("запись таблицей и пастой", open_record)
    def link_menu():
        pg.click("text=← "); pg.click(".rec >> text=Индикаторы из текста"); pg.click(".url >> nth=0"); wait_text("Снять безопасно")
        pg.click(".modal >> text=Открыть в браузере"); assert opened, "ссылка не открылась"
    step("меню ссылки", link_menu)
    def search_records():
        pg.click("text=← "); pg.click(".tab >> text=Записи"); pg.fill("#q", "photo"); pg.press("#q", "Enter")
        wait_text("показано 1 из 3"); pg.fill("#q", ""); pg.press("#q", "Enter"); wait_text("показано 3 из 3")
    step("поиск по записям", search_records)
    def notes():
        pg.fill("#new-note-name", "моя заметка"); pg.click(".inline-form >> text=Создать"); pg.wait_for_selector("#paste-edit")
        pg.fill("#paste-edit", "строка 1\nhttps://n.test"); pg.keyboard.press("Meta+s"); wait_text("сохранено")
        pg.click("text=удалить файл"); pg.click(".modal >> text=удалить файл"); wait_text("Записи")
        assert not any(n["name"].startswith("моя") for n in api.case("тест_дело")["notes"])
    step("заметка: создать, сохранить, удалить", notes)
    def summary():
        pg.click(".note-chip >> text=сводка.txt"); wait_text("ПОЛУЧЕНО ИЗ ИСТОЧНИКОВ"); pg.click("text=← ")
    step("сводка", summary)
    def files():
        pg.click(".tab >> text=Файлы"); wait_text("Файлы-доказательства"); pg.click(".seg >> text=список"); wait_text("Источник")
        pg.click(".seg >> text=плитка")
    step("файлы", files)
    def delete_file():
        pg.click(".seg >> text=список"); pg.click("#case-body .list tbody tr >> nth=0"); wait_text("SHA-256")
        pg.click(".ev >> text=удалить файл"); pg.click(".modal >> text=удалить файл"); wait_text("удалён")
        assert any(e.get("deleted") for e in api.case("тест_дело")["evidence"])
    step("удалить файл-доказательство", delete_file)
    step("хронология", lambda: (to_case(), pg.click(".tab >> text=Хронология"), wait_text("UTC")))
    def ai_tab():
        if not api.ai_status().get("ready"):
            return print("  (пропуск: локальная модель не запущена)")
        to_case(); pg.click(".tab >> text=Ассистент"); pg.fill("#ai-q", "какие e-mail есть в деле?"); pg.wait_for_timeout(600)
        assert "Спросить по материалам дела" in pg.inner_text("#assist-btn")
        pg.click("#assist-btn"); wait_text("Прошлые ответы", 240000)
    step("ассистент: вопрос ИИ", ai_tab)
    def export_import():
        api.export = (lambda real: (lambda case: real(case)))(api.export)  # без open -R
        import osint_kit.app.api as A; A.subprocess.run = lambda *a, **k: None
        to_case(); pg.click("text=Экспорт"); wait_text("экспорт: записей")
        z = next((root / "cases" / "тест_дело" / "export").glob("*.zip")); picked["path"] = str(z)
        pg.click(".side-brand"); pg.click("text=Импорт дела…"); wait_text("импортировано")
        assert "тест_дело-2" in api.state()["cases"]
    step("экспорт → импорт", export_import)
    def side_filter():
        pg.fill("#case-filter", "-2"); pg.wait_for_timeout(300)
        assert pg.locator(".side-case").count() == 1; pg.fill("#case-filter", "")
    step("фильтр дел", side_filter)
    def settings():
        pg.click(".side-link >> text=Настройки"); wait_text("Ключи сервисов")
        row = pg.locator(".key-row", has_text="VirusTotal"); row.locator("input").fill("vt-test-key"); row.locator("text=Сохранить").click()
        wait_text("ключ сохранён"); assert config.load().key("virustotal") == "vt-test-key"
        assert "vt-test-key" not in text()
        pg.locator(".key-row", has_text="VirusTotal").locator("text=удалить").click(); wait_text("ключ удалён")
        pg.click("text=Повторять запрос напрямую"); wait_text("сохранено"); assert config.load().direct_fallback == ()
        pg.click("text=Повторять запрос напрямую"); pg.wait_for_timeout(800); assert config.load().direct_fallback
    step("настройки: ключ и прямые повторы", settings)
    def route():
        pg.click("#route"); wait_text("Маршрут в интернет"); pg.keyboard.press("Escape")
    step("меню маршрута", route)
    def queue():
        pg.click(".side-link >> text=Очередь"); wait_text("Очередь проверок"); wait_text("готово")
    step("очередь", queue)
    def chat():
        pg.click("text=Новый поиск"); pg.fill("#chat-in", "привет"); pg.press("#chat-in", "Enter"); wait_text("Назовите, что проверить")
        pg.fill("#chat-in", "что за IP 93.184.216.34"); pg.press("#chat-in", "Enter"); wait_text("Заведу новое дело")
        pg.click(".msg >> text=Отмена"); wait_text("отменено"); pg.click("button:has-text('Новый диалог')"); wait_text("Что проверить?")
    step("чат: без цели, план, отмена", chat)
    def del_case():
        pg.click(".side-case >> text=тест_дело-2"); pg.click("text=Удалить дело"); pg.fill(".modal input", "тест_дело-2")
        pg.click(".modal button.danger"); wait_text("удалено"); assert "тест_дело-2" not in api.state()["cases"]
    step("удалить дело с вводом названия", del_case)
    def draft_isolated():
        to_case(); pg.click(".tab >> text=Ассистент"); pg.fill("#ai-q", "черновик первого дела")
        api.create_case("второе"); pg.evaluate("applyState({vault: 'выключено', cases: ['тест_дело', 'второе']})")
        to_case("второе"); pg.click(".tab >> text=Ассистент"); assert pg.input_value("#ai-q") == "", "черновик переехал в другое дело"
    step("черновик ассистента у каждого дела свой", draft_isolated)
    def drop_home():
        pg.click("text=Новый поиск")
        has = pg.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'a.png'));
          document.dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true}));
          return document.body.classList.contains('dropping'); }""")
        assert has, "нет подсказки при перетаскивании"
        pg.evaluate(f"window.fileDropped({json.dumps(str(photo))})")
        wait_text("Заведу новое дело"); wait_text("(фото)")
        assert not pg.evaluate("document.body.classList.contains('dropping')")
        pg.click(".msg >> text=Отмена"); pg.click("button:has-text('Новый диалог')"); wait_text("Что проверить?")
    step("фото: перетащить в окно на главной", drop_home)
    def attach_in_case():
        picked["path"] = str(photo); to_case("второе"); pg.click(".tab >> text=Ассистент"); pg.click(".ai button:has-text('Файл…')")
        assert pg.input_value("#ai-q") == str(photo), pg.input_value("#ai-q")
        pg.wait_for_function("() => document.querySelector('#assist-btn').innerText.includes('Проверить и составить отчёт')")
        assert "(фото)" in pg.inner_text("#assist-plan") or "фото" in pg.inner_text("#assist-plan"), pg.inner_text("#assist-plan")
    step("фото: кнопка «Файл…» в ассистенте дела", attach_in_case)
    def report_headers():
        n = pg.evaluate("""() => answerView('ГЛАВНОЕ:\\n• a\\n\\nО САЙТЕ (ИИ — гипотезы):\\nтекст\\nФАКТЫ ИЗ ПРОВЕРОК:').querySelectorAll('.h').length""")
        assert n == 3, f"заголовков {n}"
    step("отчёт: разделы заголовками", report_headers)
    b.close()

print(f"[{scheme}] пройдено {len(passed)}, ошибок {len(fails)}, ошибок JS {len(errors)}")
for f in fails: print("  FAIL", f)
for e in errors[:10]: print("  JS", e)
