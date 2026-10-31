"""Модулі translate і search: переклад і пошукові запити (дорки)."""

from __future__ import annotations

import webbrowser
from urllib.parse import quote_plus

from ..case import Case
from ..config import Config
from ..net import session, short_error


def _chunks(text: str, size: int = 4500) -> list[str]:
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(para) > size and cur:  # спершу накопичене, інакше порядок тексту зміниться
            parts.append(cur)
            cur = ""
        while len(para) > size:
            parts.append(para[:size])
            para = para[size:]
        if cur and len(cur) + len(para) + 1 > size:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


def translate(cfg: Config, case: Case, text: str, target: str = "ru", source: str = "auto",
              offline: bool = False) -> dict:
    text = text.strip()
    if not text:
        raise RuntimeError("Пустой текст")
    out_parts = []
    if offline:
        if not cfg.libretranslate_url:
            raise RuntimeError("Для офлайн-перевода укажите services.libretranslate_url в config.toml")
        s = session(cfg.user_agent, retries=1)
        engine = "libretranslate"
        for part in _chunks(text, 4000):
            r = s.post(f"{cfg.libretranslate_url}/translate",
                       json={"q": part, "source": source, "target": target, "format": "text"}, timeout=cfg.timeout * 3)
            r.raise_for_status()
            out_parts.append(r.json()["translatedText"])
    else:
        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:
            raise RuntimeError("Не установлен deep-translator (запустите install.sh ещё раз)") from exc
        engine = "google"
        tr = GoogleTranslator(source=source, target=target)
        for part in _chunks(text):
            try:
                out_parts.append(tr.translate(part) or "")
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Ошибка перевода: {short_error(exc)}") from exc
    result = {"engine": engine, "source": source, "target": target, "translation": "\n".join(out_parts)}
    case.add_finding("translate", text[:80], f"Перевод ({source}→{target})",
                     {"original": text[:2000], "translation": result["translation"][:2000], "engine": engine})
    return result


ENGINES = {
    "Google": "https://www.google.com/search?q={q}",
    "Bing": "https://www.bing.com/search?q={q}",
    "DuckDuckGo": "https://duckduckgo.com/?q={q}",
    "Yandex": "https://yandex.com/search/?text={q}",
}


def dorks(kind: str, value: str) -> list[str]:
    v = value.strip()
    q = f'"{v}"'
    if kind == "domain":
        return [f"site:{v}", f"site:{v} filetype:pdf OR filetype:doc OR filetype:docx OR filetype:xls OR filetype:xlsx",
                f"site:{v} inurl:admin OR inurl:login OR inurl:dashboard", f'"{v}" -site:{v}',
                f"site:pastebin.com {v}", f"site:github.com {v}", f"site:*.{v} -www"]
    if kind == "email":
        user = v.split("@")[0]
        return [q, f'{q} site:pastebin.com OR site:github.com', f'"{user}"', f'{q} filetype:pdf OR filetype:xls']
    if kind == "phone":
        digits = "".join(c for c in v if c.isdigit() or c == "+")
        return [q, f'"{digits}"', f'"{digits}" site:t.me OR site:facebook.com OR site:olx.ua']
    if kind == "username":
        return [q, f'{q} site:t.me OR site:instagram.com OR site:x.com OR site:tiktok.com OR site:reddit.com',
                f'{q} site:github.com OR site:gitlab.com', f'inurl:{v}']
    if kind == "name":
        return [q, f'{q} site:linkedin.com', f'{q} site:facebook.com OR site:instagram.com',
                f'{q} filetype:pdf', f'{q} site:t.me']
    return [v]


def search(cfg: Config, case: Case, value: str, kind: str = "text", engines: list[str] | None = None,
           open_browser: bool = False) -> dict:
    engines = engines or list(ENGINES)
    queries = dorks(kind, value)
    out = []
    for q in queries:
        links = {e: ENGINES[e].format(q=quote_plus(q)) for e in engines if e in ENGINES}
        out.append({"query": q, "links": links})
    if open_browser:
        for item in out[:3]:  # не більше 3 запитів, щоб не відкривати десятки вкладок
            for link in item["links"].values():
                webbrowser.open_new_tab(link)
    case.add_finding("search", value, f"Поисковые запросы ({kind})", out)
    res = {"value": value, "kind": kind, "queries": out}
    if open_browser and cfg.route != "direct":
        res["warning"] = "Браузер открыт напрямую, не через Tor: поисковик видит ваш IP."
    return res


IOC_LABELS = {"urls": "ссылки", "emails": "e-mail", "ipv4": "IPv4", "md5": "MD5", "sha1": "SHA-1", "sha256": "SHA-256"}


def iocs(case: Case, text: str, source: str = "текст") -> dict:
    """Индикаторы (ссылки, e-mail, IP, хеши) из текста — локально, ничего не отправляется наружу."""
    from .. import core

    if not text.strip():
        raise ValueError("Пустой текст")
    found = core.extract_iocs(text)
    total = sum(len(v) for v in found.values())
    path = case.save_json("text", "iocs.json", found, source, "индикаторы из текста")
    summary = ", ".join(f"{IOC_LABELS[k]}: {len(v)}" for k, v in found.items() if v) or "ничего"
    case.add_finding("ioc", source, f"Индикаторы из текста — {summary}", found, [path])
    return {"source": source, "total": total, "engine": core.BACKEND, **found}
