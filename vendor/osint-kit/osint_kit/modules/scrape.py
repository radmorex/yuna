"""Модуль scrape: посилання, контакти, таблиці, масове завантаження файлів."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

from ..case import Case, csv_text, slug
from ..config import Config
from ..net import block_internal_requests, browser_egress, is_internal, normalize_url, session, short_error

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().\-]{8,17}\d(?![\w/])")
SOCIAL = ("facebook.com", "instagram.com", "x.com", "twitter.com", "t.me", "telegram.me", "linkedin.com",
          "youtube.com", "tiktok.com", "vk.com", "github.com", "reddit.com", "threads.net", "snapchat.com")
DOWNLOAD_WORKERS = 3  # ТЗ: не больше трёх одновременных внешних запросов
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".woff", ".woff2")


def fetch_html(cfg: Config, url: str, render: bool = False, user_agent: str | None = None) -> tuple[str, str]:
    """Повертає (фінальний URL, HTML). render=True — через браузер, для сайтів на JavaScript."""
    url = normalize_url(url)
    ua = user_agent or cfg.user_agent
    if url.startswith("file://"):  # локально збережена сторінка
        from urllib.request import url2pathname

        path = url2pathname(urlparse(url).path)
        return url, Path(path).read_text(encoding="utf-8", errors="replace")
    if render:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p, browser_egress(url) as (egress, _):
            browser = p.chromium.launch(headless=True, **egress)
            try:
                page = browser.new_context(user_agent=ua, ignore_https_errors=True).new_page()
                block_internal_requests(page, url)
                page.goto(url, wait_until="domcontentloaded", timeout=cfg.timeout * 3000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:  # noqa: BLE001
                    pass
                return page.url, page.content()
            finally:
                browser.close()
    # редирект з публічної сторінки не повинен привести в локальну мережу (адреса перевіряється при з'єднанні)
    r = session(ua, public_only=not is_internal(urlparse(url).hostname or "")).get(url, timeout=cfg.timeout)
    r.raise_for_status()
    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
    return r.url, r.text


def _soup(html: str):
    from bs4 import BeautifulSoup

    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return BeautifulSoup(html, "html.parser")


def links(cfg: Config, case: Case, url: str, render: bool = False) -> dict:
    final, html = fetch_html(cfg, url, render)
    soup = _soup(html)
    local = url.startswith("file://") or Path(url).exists()
    if not render and not local and not soup.find("a", href=True):  # ссылки строит JavaScript — открыть в браузере
        render = True
        final, html = fetch_html(cfg, url, True)
        soup = _soup(html)
    base_host = urlparse(final).hostname or ""
    rows, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "#")):
            continue
        full = href if href.startswith(("mailto:", "tel:")) else urljoin(final, href)
        if full in seen:
            continue
        seen.add(full)
        host = urlparse(full).hostname or ""
        kind = ("email" if full.startswith("mailto:") else "phone" if full.startswith("tel:")
                else "social" if any(host == s or host.endswith("." + s) for s in SOCIAL)
                else "internal" if host == base_host or host.endswith("." + base_host) else "external")
        rows.append({"type": kind, "url": full, "text": " ".join(a.get_text(" ", strip=True).split())[:200]})
    text = soup.get_text(" ", strip=True)
    emails = sorted({e.lower() for e in EMAIL_RE.findall(text + " " + html)
                     if not e.lower().endswith(IMG_EXT)} | {r["url"][7:].split("?")[0].lower() for r in rows if r["type"] == "email"})
    phones = sorted({re.sub(r"[^\d+]", "", p) for p in PHONE_RE.findall(text) if 9 <= len(re.sub(r"\D", "", p)) <= 15}
                    | {unquote(r["url"][4:]) for r in rows if r["type"] == "phone"})
    fields = ["type", "url", "text"]
    out = case.save_text("scrape", f"links_{slug(final, 40)}.csv", csv_text([[r[k] for k in fields] for r in rows], fields),
                         final, "все ссылки страницы")
    counts = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    result = {"url": final, "title": soup.title.get_text(strip=True) if soup.title else None, "counts": counts,
              "emails": emails, "phones": phones, "social": [r["url"] for r in rows if r["type"] == "social"],
              "csv": str(out.relative_to(case.dir))}
    if render:
        result["render"] = "браузер"
    ext_hosts: dict[str, int] = {}
    for r in rows:
        if r["type"] == "external":
            h = (urlparse(r["url"]).hostname or "").removeprefix("www.")
            ext_hosts[h] = ext_hosts.get(h, 0) + 1
    names = {"internal": "внутренние", "external": "внешние", "social": "соцсети"}
    case.add_finding("scrape", final, f"Ссылки и контакты: {result['title'] or final}"[:120], {
        k: v for k, v in {
            "ссылок": {names[k]: counts[k] for k in names if counts.get(k)},
            "e-mail": emails, "телефоны": phones, "соцсети": result["social"][:20],
            "ведёт на сайты": [h for h, _ in sorted(ext_hosts.items(), key=lambda x: -x[1])[:15]],
            "открыто": "в браузере (ссылки строит JavaScript)" if render else None,
        }.items() if v}, [out])
    return result


def tables(cfg: Config, case: Case, url: str, render: bool = False) -> dict:
    final, html = fetch_html(cfg, url, render)
    soup = _soup(html)
    saved = []
    for i, table in enumerate(soup.find_all("table"), 1):
        rows = []
        for tr in table.find_all("tr"):
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if len(rows) < 2:
            continue
        p = case.save_text("scrape", f"table_{slug(final, 30)}_{i}.csv", csv_text(rows, bom=True), final,
                           f"таблица {i}")
        saved.append({"table": i, "rows": len(rows), "cols": max(len(r) for r in rows),
                      "csv": str(p.relative_to(case.dir)), "header": rows[0][:8]})
    # списки як запасний варіант, якщо таблиць немає
    if not saved:
        items = [" ".join(li.get_text(" ", strip=True).split()) for li in soup.find_all("li")]
        items = [x for x in items if x]
        if items:
            p = case.save_text("scrape", f"list_{slug(final, 30)}.csv", csv_text([[x] for x in items], ["item"], bom=True),
                               final, "элементы списков")
            saved.append({"table": "lists", "rows": len(items), "csv": str(p.relative_to(case.dir))})
    result = {"url": final, "tables": saved}
    case.add_finding("scrape", final, f"Таблицы: {len(saved)}", result, [case.dir / s["csv"] for s in saved])
    return result


def _get_external(s, link: str, allow_internal: bool, timeout: int, max_redirects: int = 5):
    """GET лише на http(s); посилання з публічної сторінки не можуть вести в локальну мережу,
    у тому числі через редирект."""
    for _ in range(max_redirects + 1):
        parts = urlparse(link)
        if parts.scheme not in ("http", "https"):
            raise RuntimeError(f"схема {parts.scheme or '?'} не поддерживается")
        if not allow_internal and is_internal(parts.hostname or ""):
            raise RuntimeError("внутренний адрес сети — пропущено")
        r = s.get(link, timeout=timeout, stream=True, allow_redirects=False)
        if not r.is_redirect:
            return r
        r.close()
        link = urljoin(link, r.headers["Location"])
    raise RuntimeError("слишком много редиректов")


def _capture(fn, *args) -> Exception | None:
    try:
        fn(*args)
        return None
    except Exception as exc:  # noqa: BLE001
        return exc


def files(cfg: Config, case: Case, url: str, exts: list[str], limit: int = 50, max_mb: int = 200,
          render: bool = False) -> dict:
    final, html = fetch_html(cfg, url, render)
    soup = _soup(html)
    exts = [e.lower().lstrip(".") for e in exts]
    candidates = []
    for tag, attr in (("a", "href"), ("img", "src"), ("source", "src"), ("video", "src"), ("audio", "src")):
        for el in soup.find_all(tag):
            v = el.get(attr)
            if not v:
                continue
            full = urljoin(final, v.strip())
            ext = PurePosixPath(urlparse(full).path).suffix.lower().lstrip(".")
            if ext in exts and full not in candidates:
                candidates.append(full)
    # локальна сторінка (свій сервер, file://) може посилатися на локальні файли; публічна — ні
    source_host = urlparse(normalize_url(url)).hostname  # те, що вказав користувач
    allow_internal = source_host is None or is_internal(source_host)
    local = threading.local()  # requests.Session не рассчитана на общий доступ из потоков

    def fetch(link: str, target: Path) -> None:
        if not hasattr(local, "s"):
            local.s = session(cfg.user_agent, public_only=not allow_internal)
        with _get_external(local.s, link, allow_internal, cfg.timeout * 3) as r:
            r.raise_for_status()
            size = int(r.headers.get("content-length") or 0)
            if size and size > max_mb * 1e6:
                raise RuntimeError(f"больше {max_mb} МБ")
            total = 0
            with target.open("wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    total += len(chunk)
                    if total > max_mb * 1e6:
                        raise RuntimeError(f"больше {max_mb} МБ")
                    fh.write(chunk)

    # имена резервируются заранее (пустой файл), чтобы потоки не выбрали одно и то же
    jobs = []
    for link in candidates[:limit]:
        target = case.path("scrape/files", slug(unquote(PurePosixPath(urlparse(link).path).name) or "file", 100))
        target.touch()
        jobs.append((link, target))
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        outcomes = list(pool.map(lambda job: _capture(fetch, *job), jobs))
    downloaded, errors = [], []
    for (link, target), exc in zip(jobs, outcomes):  # журнал доказательств — по порядку ссылок, в одном потоке
        if exc is None:
            case.register(target, link, "массовое скачивание")
            downloaded.append(str(target.relative_to(case.dir)))
        else:
            errors.append({"url": link, "error": short_error(exc)})
            target.unlink(missing_ok=True)  # не оставлять пустой или оборванный файл
    result = {"url": final, "found": len(candidates), "downloaded": len(downloaded), "files": downloaded,
              "errors": errors, "skipped_by_limit": max(0, len(candidates) - limit)}
    case.add_finding("scrape", final, f"Скачано файлов: {len(downloaded)} из {len(candidates)}",
                     {k: v for k, v in result.items() if k != "files"})
    return result
