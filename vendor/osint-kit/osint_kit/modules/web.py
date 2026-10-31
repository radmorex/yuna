"""Модуль web: доказовий захоплення сторінок і Wayback Machine."""

from __future__ import annotations

import json
from urllib.parse import quote, urljoin, urlparse

from ..case import Case, slug, utc_stamp
from ..config import Config
from ..net import block_internal_requests, browser_egress, is_internal, routed, normalize_url, session, short_error

TRACKER_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net", "googlesyndication.com",
    "facebook.net", "connect.facebook.net", "hotjar.com", "mixpanel.com", "segment.io",
    "segment.com", "amplitude.com", "clarity.ms", "scorecardresearch.com", "quantserve.com",
    "taboola.com", "outbrain.com", "criteo.com", "adnxs.com", "yandex.ru/metrika", "mc.yandex.ru",
    "tiktok.com/i18n/pixel", "analytics.tiktok.com", "bat.bing.com", "ads-twitter.com",
)


def _is_tracker(url: str) -> bool:
    low = url.lower()
    return any(t in low for t in TRACKER_HOSTS)


def capture(cfg: Config, case: Case, url: str, user_agent: str | None = None, pdf: bool = True,
            wait: float = 2.0, width: int = 1366) -> dict:
    """Повний скриншот, HTML, MHTML (одним файлом), PDF і заголовки; усе з хешами."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    url = normalize_url(url)
    ua = user_agent or cfg.user_agent
    base = utc_stamp() + "_" + slug(urlparse(url).netloc + urlparse(url).path, 50)
    result: dict = {"url": url, "user_agent": ua, "trackers_blocked": 0}
    files = []

    with sync_playwright() as p, browser_egress(url) as (egress, proxy):
        browser = p.chromium.launch(headless=True, **egress)
        try:
            context = browser.new_context(
                user_agent=ua,
                viewport={"width": width, "height": 900},
                ignore_https_errors=True,
                java_script_enabled=True,
                locale="en-US",
            )
            page = context.new_page()
            if cfg.block_trackers:
                def _route(route):
                    if _is_tracker(route.request.url):
                        result["trackers_blocked"] += 1
                        return route.abort()
                    return route.continue_()
                page.route("**/*", _route)
            internal = block_internal_requests(page, url)  # зареєстрований останнім — спрацьовує першим

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=cfg.timeout * 1000 * 3)
            except PWError as exc:
                raise RuntimeError(f"Не удалось открыть страницу: {short_error(exc)}") from exc
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWError:
                pass  # сторінки з постійними запитами ніколи не стають "idle"
            page.wait_for_timeout(int(wait * 1000))
            # прокрутка для lazy-load зображень
            try:
                page.evaluate(
                    """async () => { const step = window.innerHeight; let y = 0;
                    const max = Math.min(document.body.scrollHeight, 30000);
                    while (y < max) { window.scrollTo(0, y); y += step; await new Promise(r => setTimeout(r, 150)); }
                    window.scrollTo(0, 0); }"""
                )
                page.wait_for_timeout(500)
            except PWError:
                pass

            blocked = internal["internal_blocked"] + (proxy.blocked if proxy else 0)
            if blocked:
                result["internal_blocked"] = blocked
            result["final_url"] = page.url
            result["title"] = page.title()
            if response is not None:
                result["status"] = response.status
                result["headers"] = dict(response.headers)
                if proxy:  # браузер видит только адрес прокси — реальный IP знает прокси
                    result["server_ip"] = proxy.peers.get((urlparse(page.url).hostname or "").lower())
                elif routed():
                    result["server_ip"] = None  # соединение шло через Tor: адрес сайта видит только выход Tor
                    result["server_ip_note"] = "скрыт маршрутом Tor"
                else:
                    try:
                        result["server_ip"] = (response.server_addr() or {}).get("ipAddress")
                    except PWError:
                        pass

            png = case.path("web", f"{base}.png")
            page.screenshot(path=str(png), full_page=True)
            case.register(png, url, "полный скриншот страницы")
            files.append(png)

            html_file = case.save_text("web", f"{base}.html", page.content(), url, "HTML после рендера")
            files.append(html_file)

            try:
                cdp = context.new_cdp_session(page)
                snap = cdp.send("Page.captureSnapshot", {"format": "mhtml"})
                mhtml = case.save_text("web", f"{base}.mhtml", snap["data"], url, "архив страницы одним файлом")
                files.append(mhtml)
            except Exception as exc:  # noqa: BLE001
                result["mhtml_error"] = short_error(exc)

            if pdf:
                try:
                    pdf_path = case.path("web", f"{base}.pdf")
                    page.emulate_media(media="screen")
                    page.pdf(path=str(pdf_path), print_background=True, width=f"{width}px")
                    case.register(pdf_path, url, "PDF страницы")
                    files.append(pdf_path)
                except Exception as exc:  # noqa: BLE001
                    result["pdf_error"] = short_error(exc)

            meta = case.save_json("web", f"{base}_meta.json", result, url, "метаданные захвата")
            files.append(meta)
            context.close()
        finally:
            browser.close()

    result["files"] = [str(f.relative_to(case.dir)) for f in files]
    case.add_finding("web", url, f"Захват страницы: {result.get('title') or url}",
                     {k: v for k, v in result.items() if k != "headers"}, files)
    return result


def wayback(cfg: Config, case: Case, url: str, limit: int = 50, save: bool = False) -> dict:
    """Список знімків у Wayback Machine (CDX API) і, за бажанням, збереження поточної версії."""
    s = session(cfg.user_agent)
    target = url.strip()
    for prefix in ("https://", "http://"):
        if target.lower().startswith(prefix):
            target = target[len(prefix):]
    params = {
        "url": target,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "collapse": "digest",
        "limit": str(-abs(limit)),  # негативний ліміт = найновіші
    }
    result: dict = {"url": url, "snapshots": []}
    try:
        r = s.get("https://web.archive.org/cdx/search/cdx", params=params, timeout=cfg.timeout * 3)
        r.raise_for_status()
        rows = r.json() if r.text.strip() else []
    except Exception as exc:  # noqa: BLE001
        cdx_error = short_error(exc)
        # список снимков недоступен (часто 429) — у архива есть лёгкий запрос «ближайший снимок», он отвечает чаще
        try:
            r = s.get("https://archive.org/wayback/available", params={"url": target}, timeout=cfg.timeout * 2)
            r.raise_for_status()
            snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"Wayback CDX недоступен: {cdx_error}") from exc
        ts = str(snap.get("timestamp") or "")
        result |= {"count": None, "list_error": f"список снимков недоступен: {cdx_error}"}
        if ts:
            result["last"] = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
            result["last_url"] = snap.get("url")
        title = "Wayback Machine: " + (f"есть снимки, последний {ts[6:8]}.{ts[4:6]}.{ts[0:4]}" if ts else "снимков нет")
        out = case.save_json("web", f"wayback_{slug(target, 40)}.json", result, "archive.org", "ближайший снимок")
        case.add_finding("web", url, title, {k: v for k, v in result.items() if k != "snapshots"}, [out])
        return result

    if rows:
        header, *data = rows
        for row in data:
            item = dict(zip(header, row))
            ts = item["timestamp"]
            item["date"] = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
            item["archive_url"] = f"https://web.archive.org/web/{ts}/{item['original']}"
            result["snapshots"].append(item)
    result["snapshots"].sort(key=lambda x: x["timestamp"])
    result["count"] = len(result["snapshots"])
    if result["snapshots"]:
        # у списку лише найновіші знімки — найперший у всьому архіві беремо окремим запитом
        result["first"] = result["snapshots"][0]["date"]
        try:
            r = s.get("https://web.archive.org/cdx/search/cdx", timeout=cfg.timeout * 3,
                      params={"url": target, "output": "json", "fl": "timestamp", "limit": "1"})
            r.raise_for_status()
            ts = r.json()[1][0]
            result["first"] = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
        except Exception as exc:  # noqa: BLE001
            result["first_error"] = f"первый снимок не определён: {short_error(exc)}"
        result["last"] = result["snapshots"][-1]["date"]

    if save:
        try:
            r = s.get(f"https://web.archive.org/save/{quote(normalize_url(url), safe=':/?&=%')}",
                      timeout=120, allow_redirects=True)
            loc = r.headers.get("Content-Location") or r.url
            if r.status_code < 400 and "/web/" in loc:
                result["saved"] = loc if loc.startswith("http") else "https://web.archive.org" + loc
            else:
                result["save_error"] = f"HTTP {r.status_code} (Save Page Now часто ограничивает анонимные запросы, попробуйте позже)"
        except Exception as exc:  # noqa: BLE001
            result["save_error"] = short_error(exc)

    out = case.save_json("web", f"wayback_{slug(target, 40)}.json", result, "web.archive.org", "список снимков")
    case.add_finding("web", url, f"Wayback Machine: {result['count']} снимков", {
        k: v for k, v in result.items() if k != "snapshots"} | {"latest": result["snapshots"][-5:]}, [out])
    return result


def headers_only(cfg: Config, url: str, user_agent: str | None = None, max_redirects: int = 10) -> dict:
    """Ланцюжок редиректів і заголовки (без браузера). Редирект із публічної адреси в локальну
    мережу не виконується — у ланцюжку він позначається як заблокований."""
    link = normalize_url(url)
    allow_internal = is_internal(urlparse(link).hostname or "")  # ціль сама локальна — вибір користувача
    s = session(user_agent or cfg.user_agent, public_only=not allow_internal)
    chain = []
    for _ in range(max_redirects + 1):
        r = s.get(link, timeout=cfg.timeout, allow_redirects=False, stream=True)
        r.close()
        if not r.is_redirect:
            chain.append({"status": r.status_code, "url": r.url})
            return {"redirects": chain, "headers": dict(r.headers)}
        nxt = urljoin(link, r.headers["Location"])
        chain.append({"status": r.status_code, "url": r.url, "location": r.headers["Location"]})
        if urlparse(nxt).scheme not in ("http", "https"):
            return {"redirects": chain, "headers": dict(r.headers), "stopped": f"схема {urlparse(nxt).scheme} не поддерживается"}
        if not allow_internal and is_internal(urlparse(nxt).hostname or ""):
            chain.append({"url": nxt, "blocked": "внутренний адрес сети — не открывался"})
            return {"redirects": chain, "headers": dict(r.headers), "stopped": "редирект в локальную сеть"}
        link = nxt
    return {"redirects": chain, "headers": dict(r.headers), "stopped": "слишком много редиректов"}


def headers(cfg: Config, case: Case, url: str, user_agent: str | None = None) -> dict:
    """headers_only + запис у справу: без неї перевірка з вікна не лишала слідів."""
    res = headers_only(cfg, url, user_agent=user_agent)
    last = res["redirects"][-1]
    h = {k.lower(): v for k, v in res["headers"].items()}
    out = case.save_json("web", f"headers_{slug(url)}.json", res, url, "цепочка редиректов и заголовки")
    case.add_finding("web", url, f"Заголовки: {url}", {
        "итоговый адрес": last.get("url"), "статус": last.get("status"), "редиректов": len(res["redirects"]) - 1,
        "server": h.get("server"), "x-powered-by": h.get("x-powered-by"), "остановлено": res.get("stopped"),
    }, [out])
    return res


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
