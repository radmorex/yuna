"""Командний інтерфейс osint-kit."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, config, core, export, vault
from .case import Case, delete_case, slug
from . import net
from .net import redact
from .modules import infra, media, scrape, social
from .modules import text as textmod
from .modules import web, wordpress

try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
except Exception:  # noqa: BLE001
    _console = None


def _out(obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if _console:
        from rich.syntax import Syntax
        _console.print(Syntax(text, "json", theme="ansi_dark", word_wrap=True))
    else:
        print(text)


def _say(msg: str, style: str = "") -> None:
    if _console:
        _console.print(msg, style=style)
    else:
        print(msg)


def _case(args) -> Case:
    cfg = args._cfg
    return Case(cfg.cases_dir, args.case)


def cmd_web(args):
    cfg = args._cfg
    case = _case(args)
    if args.web_cmd == "capture":
        return web.capture(cfg, case, args.url, user_agent=args.user_agent, pdf=not args.no_pdf)
    if args.web_cmd == "wayback":
        return web.wayback(cfg, case, args.url, limit=args.limit, save=args.save)
    if args.web_cmd == "headers":
        return web.headers(cfg, case, args.url, user_agent=args.user_agent)


def cmd_infra(args):
    cfg = args._cfg
    case = _case(args)
    fn = {
        "domain": lambda: infra.domain(cfg, case, args.target, subdomains=not args.no_subdomains),
        "ip": lambda: infra.ip(cfg, case, args.target),
        "hash": lambda: infra.file_hash(cfg, case, args.target),
        "url": lambda: infra.url_check(cfg, case, args.target),
        "webcheck": lambda: infra.webcheck(cfg, case, args.target),
    }[args.infra_cmd]
    return fn()


def cmd_media(args):
    cfg = args._cfg
    case = _case(args)
    if args.media_cmd == "exif":
        return media.exif(cfg, case, args.files)
    if args.media_cmd == "reverse":
        return media.reverse(cfg, case, args.images, open_browser=args.open)
    if args.media_cmd == "video":
        return media.video(cfg, case, args.source, max_frames=args.frames)
    if args.media_cmd == "download":
        return media.download(cfg, case, args.url, audio_only=args.audio)
    if args.media_cmd == "aicheck":
        return media.aicheck(cfg, case, args.file)


def cmd_scrape(args):
    cfg = args._cfg
    case = _case(args)
    if args.scrape_cmd == "links":
        return scrape.links(cfg, case, args.url, render=args.render)
    if args.scrape_cmd == "tables":
        return scrape.tables(cfg, case, args.url, render=args.render)
    if args.scrape_cmd == "files":
        return scrape.files(cfg, case, args.url, exts=args.ext, limit=args.limit,
                            max_mb=args.max_mb, render=args.render)


def cmd_translate(args):
    cfg = args._cfg
    case = _case(args)
    src = args.text
    if args.file:
        from pathlib import Path
        src = Path(args.file).expanduser().read_text(encoding="utf-8", errors="replace")
    res = textmod.translate(cfg, case, src, target=args.to, source=args.source, offline=args.offline)
    _say(res["translation"])
    return {"engine": res["engine"], "target": res["target"]}


def cmd_search(args):
    cfg = args._cfg
    case = _case(args)
    return textmod.search(cfg, case, args.value, kind=args.kind,
                          engines=args.engine, open_browser=args.open)


def cmd_ioc(args):
    from pathlib import Path
    case = _case(args)
    path = Path(args.value).expanduser()
    if args.file or path.is_file():
        if not path.is_file():
            raise FileNotFoundError("Файл не найден")
        return textmod.iocs(case, path.read_text(encoding="utf-8", errors="replace"), source=path.name)
    return textmod.iocs(case, args.value)


def cmd_username(args):
    cfg = args._cfg
    case = _case(args)
    return social.username(cfg, case, args.nick, timeout=args.timeout)


def cmd_telegram(args):
    cfg = args._cfg
    case = _case(args)
    if args.tg_cmd == "bot":
        return social.bot_info(cfg, case, args.token)
    if args.tg_cmd == "channel":
        return social.channel(cfg, case, args.target, limit=args.limit)
    if args.tg_cmd == "login":
        return social.tg_login(cfg)


def cmd_wp(args):
    cfg = args._cfg
    case = _case(args)
    return wordpress.search(cfg, case, args.query, site=args.site, limit=args.limit,
                            photos=args.photos, photos_limit=args.photos_limit)


def cmd_report(args):
    case = _case(args)
    md, html = case.build_report()
    return {"case": case.name, "folder": str(case.dir), "report_md": str(md), "report_html": str(html),
            "findings": len(case.findings()), "evidence_files": len(case.evidence())}


def cmd_export(args):
    return export.export(_case(args), fmt=args.format, out_dir=args.out)


def cmd_import(args):
    return export.import_case(_case(args), args.file)


def cmd_case(args):
    cfg = args._cfg
    if args.case_cmd == "list":
        if not cfg.cases_dir.exists():
            return {"cases": []}
        return {"cases": sorted(p.name for p in cfg.cases_dir.iterdir() if p.is_dir() and not p.name.startswith("."))}
    if args.case_cmd == "lock":
        locked = vault.lock(cfg.cases_dir)
        return {"locked": True, "note": "Хранилище закрыто" if locked else "Хранилище уже было закрыто"}
    if args.case_cmd == "unlock":
        return {"open": True, "folder": str(cfg.cases_dir)}  # відкривається в main()
    if args.case_cmd == "delete":
        if not args.yes:
            raise RuntimeError(f"Дело «{slug(args.case)}» будет удалено без возврата. Повторите с --yes")
        delete_case(cfg.cases_dir, args.case)
        return {"deleted": slug(args.case)}
    if args.case_cmd == "delete-file":
        if not (cfg.cases_dir / slug(args.case)).is_dir():
            raise RuntimeError("Дело не найдено")
        e = Case(cfg.cases_dir, args.case).delete_evidence(args.id.upper(), "удалено командой osint case delete-file")
        return {"deleted": e["id"], "file": e["file"], "file_removed": e["file_removed"]}
    if args.case_cmd == "dossier":
        if not (cfg.cases_dir / slug(args.case)).is_dir():
            raise RuntimeError("Дело не найдено")
        print(export.to_paste(export.case_data(Case(cfg.cases_dir, args.case))))
        return None
    if args.case_cmd == "path":
        return {"folder": str(Case(cfg.cases_dir, args.case).dir)}


def _needs_network(args) -> bool:
    cmd = args.command
    if cmd in ("web", "infra", "scrape", "translate", "username", "telegram", "wp"):
        return True
    if cmd == "media":
        return args.media_cmd in ("download", "aicheck") or (args.media_cmd == "video" and "://" in args.source)
    return False


def cmd_ai(args):
    from .modules import ai
    cfg = args._cfg
    if args.status or not args.question:
        return ai.status(cfg)
    res = ai.ask(cfg, _case(args), args.question)
    _say(res["answer"])
    return {"model": res["model"], "note": res["note"]}


def cmd_route(args):
    cfg = args._cfg
    if args.route_cmd == "set":
        path = config.set_route(args.mode)
        return {"route": args.mode, "config": str(path)}
    if args.route_cmd == "start":
        return net.tor_start()
    if args.route_cmd == "stop":
        return net.tor_stop()
    ep = net.proxy_endpoint(cfg)
    status = {"route": cfg.route, "proxy": f"{ep[0]}:{ep[1]}" if ep else None,
              "доступен": bool(ep and net.port_open(*ep)) if ep else None}
    if args.route_cmd == "check":
        if not ep:
            raise RuntimeError("Маршрут «напрямую» — сайты видят ваш IP. Включите: osint route set tor")
        net.apply_route(cfg)
        r = net.session(cfg.user_agent, retries=1).get("https://check.torproject.org/api/ip", timeout=cfg.timeout * 2)
        r.raise_for_status()
        data = r.json()
        status |= {"через_tor": bool(data.get("IsTor")), "видимый_ip": data.get("IP")}
    return status


def cmd_app(args):
    from .app import __main__ as window
    if args.bundle:
        from .app import bundle
        return {"app": str(bundle.build())}
    window.main()
    return None


def cmd_doctor(args):
    import shutil
    cfg = args._cfg
    checks = {"rust-ядро": "ok" if core.BACKEND == "rust" else "НЕТ — Python-фолбэк (./install.sh собирает core/)"}
    for mod in ("httpx", "requests", "bs4", "lxml", "dns", "whois", "playwright", "yt_dlp",
                "deep_translator", "telethon", "PIL", "cryptography", "rich", "webview"):
        try:
            __import__(mod)
            checks[mod] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks[mod] = f"НЕТ ({exc.__class__.__name__})"
    for tool in ("exiftool", "ffmpeg", "ffprobe", "sherlock"):
        checks[tool] = "ok" if shutil.which(tool) or (tool == "sherlock" and social._sherlock_cmd()) else "НЕТ"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        checks["chromium (playwright)"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["chromium (playwright)"] = f"НЕТ — выполните: playwright install chromium ({exc.__class__.__name__})"
    keys = {k: ("задано" if cfg.key(k) else "—") for k in config.KEY_NAMES}
    vault_state = ("выключено (encrypt_cases = false)" if not cfg.encrypt_cases
                   else "открыто" if vault.is_open(cfg.cases_dir) else "закрыто")
    ep = net.proxy_endpoint(cfg)
    route = ("напрямую — сайты видят ваш IP" if not ep
             else f"{cfg.route} {ep[0]}:{ep[1]} — " + ("доступен" if net.port_open(*ep) else "НЕ запущен (osint route start)"))
    return {"config": str(config.config_path()), "cases_dir": str(cfg.cases_dir), "encrypted_vault": vault_state, "route": route,
            "dependencies": checks, "api_keys": keys,
            "webcheck_url": cfg.webcheck_url or "—", "libretranslate_url": cfg.libretranslate_url or "—"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osint", description="osint-kit — консольный набор OSINT-инструментов.",
        epilog="Все результаты сохраняются в папку дела (--case). В конце: osint export --case <имя>.")
    p.add_argument("--version", action="version", version=f"osint-kit {__version__}")
    p.add_argument("--case", default="default", help="имя дела (папка с результатами)")
    sub = p.add_subparsers(dest="command", required=True)

    # web
    w = sub.add_parser("web", help="сайты: захват как доказательство, Wayback").add_subparsers(dest="web_cmd", required=True)
    wc = w.add_parser("capture", help="скриншот + HTML + MHTML + PDF с хешами")
    wc.add_argument("url")
    wc.add_argument("--user-agent", default=None)
    wc.add_argument("--no-pdf", action="store_true")
    ww = w.add_parser("wayback", help="снимки в веб-архиве")
    ww.add_argument("url")
    ww.add_argument("--limit", type=int, default=50)
    ww.add_argument("--save", action="store_true", help="сохранить текущую версию в архив")
    wh = w.add_parser("headers", help="редиректы и заголовки")
    wh.add_argument("url")
    wh.add_argument("--user-agent", default=None)

    # infra
    i = sub.add_parser("infra", help="домены, IP, хеши, URL").add_subparsers(dest="infra_cmd", required=True)
    for name, help_ in (("domain", "DNS/WHOIS/SSL/HTTP/поддомены"), ("ip", "данные по IP"),
                        ("hash", "проверка хеша (VirusTotal)"), ("url", "проверка URL (VT/urlscan)"),
                        ("webcheck", "локальный Web-Check")):
        sp = i.add_parser(name, help=help_)
        sp.add_argument("target")
        if name == "domain":
            sp.add_argument("--no-subdomains", action="store_true")

    # media
    m = sub.add_parser("media", help="фото, видео, обратный поиск").add_subparsers(dest="media_cmd", required=True)
    me = m.add_parser("exif", help="метаданные фото/файлов")
    me.add_argument("files", nargs="+")
    mr = m.add_parser("reverse", help="ссылки на обратный поиск изображений")
    mr.add_argument("images", nargs="+")
    mr.add_argument("--open", action="store_true", help="открыть поиск в браузере")
    mv = m.add_parser("video", help="метаданные + ключевые кадры видео")
    mv.add_argument("source", help="файл или ссылка")
    mv.add_argument("--frames", type=int, default=30)
    md = m.add_parser("download", help="скачать видео (yt-dlp)")
    md.add_argument("url")
    md.add_argument("--audio", action="store_true")
    ma = m.add_parser("aicheck", help="проверка на ИИ-генерацию (Hive)")
    ma.add_argument("file")

    # scrape
    sc = sub.add_parser("scrape", help="ссылки, таблицы, файлы").add_subparsers(dest="scrape_cmd", required=True)
    for name, help_ in (("links", "ссылки и контакты"), ("tables", "таблицы в CSV")):
        sp = sc.add_parser(name, help=help_)
        sp.add_argument("url")
        sp.add_argument("--render", action="store_true", help="через браузер (для JS-сайтов)")
    sf = sc.add_parser("files", help="массовое скачивание файлов")
    sf.add_argument("url")
    sf.add_argument("--ext", nargs="+", required=True, help="расширения, напр. pdf docx xlsx")
    sf.add_argument("--limit", type=int, default=50)
    sf.add_argument("--max-mb", type=int, default=200)
    sf.add_argument("--render", action="store_true")

    # translate
    tr = sub.add_parser("translate", help="перевод текста")
    tr.add_argument("text", nargs="?", default="")
    tr.add_argument("--file", help="перевести содержимое файла")
    tr.add_argument("--to", default="ru", help="язык перевода (ru, uk, en, ...)")
    tr.add_argument("--source", default="auto")
    tr.add_argument("--offline", action="store_true", help="через LibreTranslate (офлайн)")

    # ioc
    io_ = sub.add_parser("ioc", help="индикаторы из текста или файла: ссылки, e-mail, IP, хеши (локально)")
    io_.add_argument("value", help="текст или путь к файлу")
    io_.add_argument("--file", action="store_true", help="value — обязательно файл")

    # search
    se = sub.add_parser("search", help="поисковые запросы (дорки)")
    se.add_argument("value")
    se.add_argument("--kind", default="text",
                    choices=["text", "domain", "email", "phone", "username", "name"])
    se.add_argument("--engine", nargs="+", default=None, choices=list(textmod.ENGINES))
    se.add_argument("--open", action="store_true")

    # username
    un = sub.add_parser("username", help="поиск аккаунтов по нику (Sherlock)")
    un.add_argument("nick")
    un.add_argument("--timeout", type=int, default=15)

    # telegram
    tg = sub.add_parser("telegram", help="анализ Telegram").add_subparsers(dest="tg_cmd", required=True)
    tb = tg.add_parser("bot", help="данные бота по токену")
    tb.add_argument("token")
    tc = tg.add_parser("channel", help="публичный канал/группа")
    tc.add_argument("target")
    tc.add_argument("--limit", type=int, default=200)
    tg.add_parser("login", help="однократный вход в Telegram")

    # wordpress (медіацентр УжНУ тощо)
    wp = sub.add_parser("wp", help="упоминания в публикациях сайтов на WordPress (медиацентр УжНУ)") \
        .add_subparsers(dest="wp_cmd", required=True)
    ws = wp.add_parser("search", help="хронология упоминаний + места из тегов/текста; --photos — EXIF/GPS фото")
    ws.add_argument("query", help="имя, название, событие")
    ws.add_argument("--site", default="mediacenter",
                    help=f"готовый сайт ({', '.join(wordpress.SITES)}) или адрес сайта на WordPress")
    ws.add_argument("--limit", type=int, default=20, help="сколько публикаций (до 100)")
    ws.add_argument("--photos", action="store_true", help="скачать фото из публикаций и проверить EXIF/GPS")
    ws.add_argument("--photos-limit", type=int, default=40)

    # report / case / doctor
    sub.add_parser("report", help="собрать отчёт по делу (md + html)")
    ex = sub.add_parser("export", help="экспорт дела: TXT, JSON, CSV, ZIP с файлами и контрольными суммами")
    ex.add_argument("--format", default="all", choices=["all", "txt", "json", "csv", "zip"])
    ex.add_argument("--out", default=None, help="папка (по умолчанию — export/ внутри дела, в хранилище)")
    im = sub.add_parser("import", help="восстановить дело из экспорта JSON или ZIP (в новое дело)")
    im.add_argument("file")
    cs = sub.add_parser("case", help="управление делами").add_subparsers(dest="case_cmd", required=True)
    cs.add_parser("list", help="список дел")
    cs.add_parser("path", help="путь к папке дела")
    cd = cs.add_parser("delete", help="удалить дело целиком (без возврата)")
    cd.add_argument("--yes", action="store_true", help="подтвердить удаление")
    cf = cs.add_parser("delete-file", help="удалить файл-доказательство (запись в журнале остаётся с пометкой)")
    cf.add_argument("id", help="ID доказательства, напр. E3")
    cs.add_parser("dossier", help="сводка дела текстом (как во вкладке «досье»)")
    cs.add_parser("lock", help="закрыть зашифрованное хранилище дел")
    cs.add_parser("unlock", help="открыть зашифрованное хранилище дел")
    sub.add_parser("doctor", help="проверить зависимости и ключи")
    rt = sub.add_parser("route", help="маршрут в интернет: Tor (по умолчанию), прокси или напрямую") \
        .add_subparsers(dest="route_cmd", required=True)
    rt.add_parser("status", help="текущий маршрут")
    rt.add_parser("check", help="проверить, что запросы идут через Tor (check.torproject.org)")
    rt.add_parser("start", help="запустить свой tor (127.0.0.1:9060)")
    rt.add_parser("stop", help="остановить свой tor")
    rs = rt.add_parser("set", help="выбрать маршрут и записать в config.toml")
    rs.add_argument("mode", choices=["tor", "proxy", "direct"])
    ai_ = sub.add_parser("ai", help="вопрос локальному ИИ по материалам дела (Ollama на этом компьютере)")
    ai_.add_argument("question", nargs="?", default="")
    ai_.add_argument("--status", action="store_true", help="запущена ли Ollama и скачана ли модель")
    ap = sub.add_parser("app", help="оконное приложение Fallen Angel")
    ap.add_argument("--bundle", action="store_true", help="собрать ~/Applications/Fallen Angel.app вместо запуска окна")

    return p


HANDLERS = {
    "web": cmd_web, "infra": cmd_infra, "media": cmd_media, "scrape": cmd_scrape,
    "translate": cmd_translate, "search": cmd_search, "ioc": cmd_ioc, "username": cmd_username,
    "telegram": cmd_telegram, "wp": cmd_wp, "report": cmd_report, "export": cmd_export, "import": cmd_import, "case": cmd_case, "doctor": cmd_doctor, "app": cmd_app, "route": cmd_route, "ai": cmd_ai,
}


def main(argv=None) -> int:
    config.ensure_tool_path()
    args = build_parser().parse_args(argv)
    args._cfg = cfg = config.load()
    try:
        if cfg.encrypt_cases and args.command != "app" and not (args.command == "case" and args.case_cmd == "lock"):
            opened = vault.open_vault(cfg.cases_dir, cfg.vault_image or vault.DEFAULT_IMAGE)
            if opened and opened["created"]:
                _say(f"Создано зашифрованное хранилище дел (AES-256): {opened['image']}\n"
                     f"Пароль — в Keychain (osint-kit / cases-vault). Закрыть: osint case lock", "yellow")
            if opened and opened["moved_into_vault"]:
                _say(f"Перенесено в хранилище незашифрованных дел: {opened['moved_into_vault']}", "yellow")
        if _needs_network(args):
            net.apply_route(cfg)  # Tor/прокси для всего процесса; недоступен — исключение, ничего не уходит
        result = HANDLERS[args.command](args)
        if result is not None:
            _out(result)
        if args.command not in ("report", "case", "doctor", "translate", "export", "import", "app", "route", "ai"):
            _say(f"\n[dim]Сохранено в дело '{args.case}'. Экспорт: osint export --case {args.case}[/dim]"
                 if _console else f"\nСохранено в дело '{args.case}'. Экспорт: osint export --case {args.case}")
        return 0
    except KeyboardInterrupt:
        _say("\nПрервано.", "yellow")
        return 130
    except Exception as exc:  # noqa: BLE001
        _say(f"Ошибка: {redact(str(exc))}", "bold red")
        return 1


if __name__ == "__main__":
    sys.exit(main())
