"""Модуль wp: згадки в публікаціях сайтів на WordPress (офіційний REST API) і перевірка їхніх фото.

Місця беруться з тегів, рубрик і тексту статті, координати — з EXIF фото. Це зачіпки
для перевірки, а не підтверджене місцеперебування: згадка в новині ≠ присутність людини,
а місце події ≠ місце, де людина є зараз.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from ..case import Case, csv_text, now_iso, slug
from ..config import Config
from ..net import is_internal, normalize_url, session, short_error
from . import media
from .scrape import _get_external, _soup

SITES = {
    "mediacenter": "https://mediacenter.uzhnu.edu.ua",  # Медіацентр УжНУ
}

# Населені пункти й країни: основа слова з великої літери, щоб ловити відмінки (Ужгороді, Мукачева).
PLACES = {
    "Ужгород": r"Ужгород", "Мукачево": r"Мукачев", "Хуст": r"Хуст", "Берегово": r"Берегов",
    "Виноградов": r"Виноград[іо]в", "Свалява": r"Сваляв", "Рахов": r"Рах[іо]в", "Тячев": r"Тяч[іе]в",
    "Иршава": r"Іршав", "Перечин": r"Перечин", "Чоп": r"Чоп(?:і|а|у)?\b", "Великий Березний": r"Велик\w* Березн",
    "Воловец": r"Волов[еці]", "Межгорье": r"Міжгір", "Солотвино": r"Солотвин", "Ясиня": r"Ясін",
    "Невицкое": r"Невицьк", "Закарпатье": r"Закарпат",
    "Киев": r"Ки[їє]в", "Львов": r"Льв[іо]в", "Харьков": r"Харк[іо]в", "Одеса": r"Одес", "Днепр": r"Дніпр",
    "Ивано-Франковск": r"Ивано-Франковск", "Черновцы": r"Чернівц", "Тернополь": r"Тернопіл|Тернопіль",
    "Словакия": r"Словаччин", "Венгрия": r"Угорщин", "Польша": r"Польщ", "Румыния": r"Румуні",
    "Чехия": r"Чехі", "Кошице": r"Кошиц", "Братислава": r"Братислав", "Будапешт": r"Будапешт",
    "Прешов": r"Прешов", "Ньиредьхаза": r"Н[іи]їредьгаз|Ніредьгаз",
}
_PLACE_RE = {name: re.compile(r"(?<!\w)(?:" + stem + r")\w*") for name, stem in PLACES.items()}
MAX_PHOTO_MB = 25
HYPOTHESIS = "гипотезы — требуют проверки"


def _base(site: str) -> str:
    return normalize_url(SITES.get(site, site)).rstrip("/")


def _api(s, base: str, path: str, params: dict, timeout: int):
    r = s.get(f"{base}/wp-json/wp/v2/{path}", params=params, timeout=timeout)
    if r.status_code == 404:
        raise RuntimeError(f"{urlparse(base).hostname}: WordPress REST API недоступен")
    r.raise_for_status()
    return r.json()


def _names(s, base: str, kind: str, ids: set[int], timeout: int) -> dict[int, str]:
    if not ids:
        return {}
    rows = _api(s, base, kind, {"include": ",".join(map(str, sorted(ids))), "per_page": 100,
                                "_fields": "id,name"}, timeout)
    return {r["id"]: r["name"] for r in rows}


def places_in(text: str) -> dict[str, int]:
    found = {name: len(rx.findall(text)) for name, rx in _PLACE_RE.items()}
    return {k: v for k, v in found.items() if v}


def snippets(text: str, query: str, limit: int = 3, width: int = 160) -> list[str]:
    """Фрагменти навколо слів запиту; основа слова — щоб знаходити відмінки (Олена → Олени)."""
    stems = [w[:-1] if len(w) > 4 else w for w in re.findall(r"\w+", query) if len(w) > 2]
    if not stems:
        return []
    rx = re.compile("|".join(re.escape(st) for st in stems), re.I)
    out, last_end = [], -1
    for m in rx.finditer(text):
        if m.start() < last_end:
            continue
        a, b = max(0, m.start() - width), min(len(text), m.end() + width)
        out.append(("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else ""))
        last_end = b
        if len(out) >= limit:
            break
    return out


def search(cfg: Config, case: Case, query: str, site: str = "mediacenter", limit: int = 20,
           photos: bool = False, photos_limit: int = 40) -> dict:
    query = query.strip()
    if not query:
        raise RuntimeError("Пустой запрос")
    base = _base(site)
    host = urlparse(base).hostname
    s = session(cfg.user_agent, public_only=not is_internal(host or ""))
    posts = _api(s, base, "posts", {"search": query, "per_page": max(1, min(limit, 100)), "orderby": "date",
                                    "_fields": "id,date,link,title,content,tags,categories,featured_media"},
                 cfg.timeout * 2)
    tags = _names(s, base, "tags", {t for p in posts for t in p.get("tags", [])}, cfg.timeout)
    cats = _names(s, base, "categories", {c for p in posts for c in p.get("categories", [])}, cfg.timeout)

    timeline, place_posts = [], {}
    for p in posts:
        title = _soup(p["title"]["rendered"]).get_text(" ", strip=True)
        text = " ".join(_soup(p.get("content", {}).get("rendered", "")).get_text(" ", strip=True).split())
        places = places_in(title + " " + text)
        for name in places:
            place_posts[name] = place_posts.get(name, 0) + 1
        timeline.append({
            "date": p["date"][:10], "title": title, "link": p["link"], "places": places,
            "tags": [tags[t] for t in p.get("tags", []) if t in tags],
            "categories": [cats[c] for c in p.get("categories", []) if c in cats],
            "snippets": snippets(text, query), "_id": p["id"], "_featured": p.get("featured_media") or 0,
        })
    timeline.sort(key=lambda x: x["date"], reverse=True)

    result = {
        "site": host, "query": query, "found": len(timeline),
        "status": HYPOTHESIS,
        "note": "Упоминание в публикации ≠ присутствие человека; место события ≠ нынешнее местонахождение.",
        "places": dict(sorted(place_posts.items(), key=lambda kv: -kv[1])),
        "period": {"from": timeline[-1]["date"], "to": timeline[0]["date"]} if timeline else None,
        "timeline": [{k: v for k, v in t.items() if not k.startswith("_")} for t in timeline],
    }
    out_json = case.save_json("wp", f"{slug(host)}_{slug(query, 40)}.json", result, base, f"поиск «{query}»")
    out_csv = case.save_text("wp", f"{slug(host)}_{slug(query, 40)}.csv", csv_text(
        [[t["date"], t["title"], "; ".join(t["places"]), "; ".join(t["tags"] + t["categories"]), t["link"]]
         for t in result["timeline"]], ["date", "title", "places", "tags", "link"], bom=True), base, "хронология упоминаний")
    case.add_finding("wp", query, f"Упоминания на {host}: {len(timeline)}",
                     {k: v for k, v in result.items() if k != "timeline"}
                     | {"latest": [{k: t[k] for k in ("date", "title", "places", "link")} for t in result["timeline"][:10]]},
                     [out_json, out_csv], status=HYPOTHESIS)
    if photos:
        result["photos"] = _photos(cfg, case, s, base, timeline, query, photos_limit)
    return result


def _photos(cfg: Config, case: Case, s, base: str, timeline: list[dict], query: str, limit: int) -> dict:
    """Оригінали фото зі знайдених публікацій: SHA-256, EXIF, GPS (якщо сайт їх не вичистив)."""
    queue, seen = [], set()
    for t in timeline:
        try:
            items = _api(s, base, "media", {"parent": t["_id"], "per_page": 100, "_fields": "id,source_url"},
                         cfg.timeout)
            if t["_featured"]:
                items += _api(s, base, "media", {"include": t["_featured"], "_fields": "id,source_url"}, cfg.timeout)
        except Exception:  # noqa: BLE001
            continue
        for m in items:
            url = m.get("source_url") or ""
            if m["id"] not in seen and PurePosixPath(urlparse(url).path).suffix.lower() in media.IMAGE_EXT:
                seen.add(m["id"])
                queue.append((t, url))
    allow_internal = is_internal(urlparse(base).hostname or "")  # локальний сайт — свідомий вибір
    checked, with_gps, errors, files = [], [], [], []
    for t, url in queue[:limit]:
        name = slug(unquote(PurePosixPath(urlparse(url).path).name), 100)
        try:
            with _get_external(s, url, allow_internal, cfg.timeout * 3) as r:
                r.raise_for_status()
                if int(r.headers.get("content-length") or 0) > MAX_PHOTO_MB * 1e6:
                    raise RuntimeError(f"больше {MAX_PHOTO_MB} МБ")
                data = r.content
            path = case.save_bytes("wp/photos", name, data, url, f"фото из публикации {t['link']}")
            files.append(path)
            engine, _, summary = media.read_meta(path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": url, "error": short_error(exc)})
            continue
        item = {"post_date": t["date"], "post": t["link"], "photo": str(path.relative_to(case.dir)),
                "camera": " ".join(str(summary[k]) for k in ("Make", "Model") if summary.get(k)) or None,
                "taken": summary.get("DateTimeOriginal") or summary.get("CreateDate")}
        if "GPSPosition" in summary:
            item |= {"gps": summary["GPSPosition"], "map": summary["map"]}
            with_gps.append(item)
        checked.append(item)
    res = {"found": len(queue), "checked": len(checked), "with_gps": len(with_gps),
           "skipped_by_limit": max(0, len(queue) - limit), "gps": with_gps, "errors": errors,
           "note": "Сайты часто вычищают EXIF — отсутствие GPS ничего не доказывает. "
                   "Координаты фото = место съёмки события, а не место человека сейчас."}
    out = case.save_json("wp", f"photos_{slug(urlparse(base).hostname or 'site')}_{slug(query, 40)}.json",
                         res | {"photos": checked, "checked_at": now_iso()},
                         base, "фото из публикаций")
    case.add_finding("wp", query, f"Фото из публикаций: {len(checked)}, с GPS: {len(with_gps)}",
                     {k: v for k, v in res.items() if k != "errors"} | {"errors": len(errors)}, [out] + [case.dir / g["photo"] for g in with_gps], status=HYPOTHESIS)
    return res
