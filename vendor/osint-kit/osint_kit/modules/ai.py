"""Локальный ИИ (Ollama на этом компьютере): вопросы по материалам дела.

Материалы дела не уходят в интернет: разрешён только сервер на 127.0.0.1. Ответ сохраняется заметкой
с пометкой «гипотезы ИИ» — это не доказательство и не новая проверка источников.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .. import export
from ..case import Case, private, utc_stamp
from ..config import Config
from ..net import port_open, session

CONTEXT_MAX = 24_000  # символов материалов дела в запросе (модель 7B держит ~16k токенов)
SYSTEM = (
    "Ты помощник аналитика в законном поиске пропавших людей. Отвечай по-русски и только по материалам дела, "
    "которые даны ниже. Разделяй: что прямо есть в материалах (ссылайся на ID записей F… и файлов E…) и что "
    "является предположением — его помечай словом «гипотеза». Не выдумывай факты, адреса, связи и людей. "
    "Совпадение ника или фото не доказывает, что это один человек. Если в материалах ответа нет — так и скажи "
    "и предложи, какую проверку сделать следующей. Предлагай только открытые источники и проверки этой программы: "
    "захват и архив страниц, ссылки и файлы сайта, домен, IP-сеть, метаданные фото, обратный поиск фото, "
    "аккаунты по нику, поисковые запросы. Никогда не предлагай доступ к чужим аккаунтам, почте, истории входов, "
    "паролям, утечкам баз или закрытым данным и обход ограничений — передать такое можно только полиции. "
    "Пиши коротко: сначала прямой ответ на вопрос, без пересказа всех материалов, без технических полей "
    "(user-agent, имена файлов, хеши) и без общих советов."
)

BRIEF = (
    "Ты помощник аналитика. Ниже текст страницы сайта и факты проверок. Напиши 2–4 предложения по-русски: что это "
    "за ресурс и для чего он — по тому, что он пишет о себе; кто за ним стоит — только если имя или ник прямо есть "
    "в тексте. Имена, ники, названия и адреса пиши точно как в источнике, латиницей, не переводи и не склоняй их. "
    "OSINT — это «разведка по открытым источникам». Не повторяй IP, хостинг, даты, регистратора и SSL — программа "
    "покажет их сама. Предположения начинай со слова «Возможно». Без списков, заголовков, ID записей и советов."
)
SKIP_FIELDS = {"user_agent", "files", "csv", "latest", "trackers_blocked", "server_ip_note", "counts", "emails",
               "phones", "social", "url", "final_url", "ссылок", "x-powered-by", "остановлено", "недоступно",
               "повтор проверки", "sha256", "engine", "report", "frames_dir", "next_step"}
LABELS = {"title": "заголовок", "status": "HTTP-статус", "статус": "HTTP-статус", "server_ip": "IP сервера",
          "count": "снимков", "first": "первый снимок", "last": "последний снимок", "server": "сервер",
          "save_error": "сохранение", "saved": "сохранено в архив", "list_error": "список снимков",
          "last_url": "последний снимок, ссылка"}
SAME = {"IP сервера": "IP-адреса"}  # одно и то же из разных проверок — в фактах один раз
PAGE_MAX = 3_000  # символов текста страницы на одну цель


def _endpoint(cfg: Config) -> tuple[str, int]:
    parts = urlsplit(cfg.ai_url)
    if parts.hostname not in ("127.0.0.1", "localhost", "::1") or not parts.port:
        raise RuntimeError("ИИ разрешён только на этом компьютере (127.0.0.1) — материалы дела не уходят наружу")
    return parts.hostname, parts.port


def ensure_server(cfg: Config, wait: float = 20) -> None:
    host, port = _endpoint(cfg)
    if port_open(host, port):
        return
    binary = shutil.which("ollama") or ("/opt/homebrew/bin/ollama" if shutil.which("/opt/homebrew/bin/ollama") else None)
    if not binary:
        raise RuntimeError("Ollama не установлена: brew install ollama")
    subprocess.Popen([binary, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if port_open(host, port):
            return
    raise RuntimeError("Ollama не запустилась")


def status(cfg: Config) -> dict:
    host, port = _endpoint(cfg)
    if not port_open(host, port):
        return {"running": False, "model": cfg.ai_model, "ready": False, "models": []}
    r = session(cfg.user_agent, retries=0).get(f"{cfg.ai_url}/api/tags", timeout=5)
    r.raise_for_status()
    models = [m.get("name", "") for m in r.json().get("models", [])]
    return {"running": True, "model": cfg.ai_model, "ready": cfg.ai_model in models, "models": models}


def _context(case: Case) -> str:
    data = export.case_data(case)
    text = export.to_paste(data)
    for n in data.get("notes", []):
        if not n["name"].startswith("ИИ_"):  # прошлые ответы ИИ не подаём как материалы дела
            text += f"\n\n== Заметка аналитика: {n['name']} ==\n{n.get('text', '')}"
    return text if len(text) <= CONTEXT_MAX else text[:CONTEXT_MAX] + "\n… (материалы обрезаны по длине)"


_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]")


def _drop_cjk(text: str) -> str:
    """Qwen иногда соскальзывает на китайский посреди ответа — такие предложения выбрасываются."""
    parts = re.split(r"(?<=[.!?。！？])\s*", text)
    return " ".join(p for p in parts if p and not _CJK.search(p)).strip()


def _chat(cfg: Config, system: str, prompt: str) -> str:
    ensure_server(cfg)
    st = status(cfg)
    if not st["ready"]:
        raise RuntimeError(f"Модель {cfg.ai_model} не скачана: ollama pull {cfg.ai_model}")
    answer = ""
    for attempt in range(2):
        sys_text = system if not attempt else system + " Отвечай строго на русском языке, ни одного слова на китайском."
        body = {"model": cfg.ai_model, "stream": False, "options": {"num_ctx": 16384, "temperature": 0.2 if not attempt else 0.1},
                "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": prompt}]}
        r = session(cfg.user_agent, retries=0).post(f"{cfg.ai_url}/api/chat", json=body, timeout=600)
        r.raise_for_status()
        answer = r.json().get("message", {}).get("content", "").strip()
        if not _CJK.search(answer):
            return answer
    return _drop_cjk(answer)


_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]00:?00)?$")


def _value(v) -> str:
    """Значение для чтения: даты — «18.02.2016», ссылки — без «https://», списки — через запятую."""
    if isinstance(v, dict):
        return ", ".join(f"{a}: {_value(b)}" for a, b in v.items())
    if isinstance(v, list):
        return ", ".join(_value(x) for x in v[:15]) + (f" … (ещё {len(v) - 15})" if len(v) > 15 else "")
    v = " · ".join(x.strip() for x in str(v).splitlines() if x.strip())  # многострочное (текст с картинки) — в строку
    m = _ISO.match(v)
    if m:
        return f"{m[3]}.{m[2]}.{m[1]}"
    if re.fullmatch(r"[\w-]+(?:\.[\w-]+)+\.", v):  # имя из DNS с точкой на конце
        return v[:-1]
    return re.sub(r"^https?://", "", v).rstrip("/") if re.match(r"https?://\S+$", v) else v


KIND_ORDER = ("domain", "ip", "page", "headers", "links", "wayback", "photo", "other")


def _kind(d: dict) -> str:
    if "IP-адреса" in d or "регистратор" in d or "хостинг" in d:
        return "domain"
    if "провайдер" in d or "сеть" in d:
        return "ip"
    if d.get("title") and d.get("status"):
        return "page"
    if "итоговый адрес" in d:
        return "headers"
    if any(k in d for k in ("соцсети", "e-mail", "телефоны", "ведёт на сайты")):
        return "links"
    if "count" in d and ("first" in d or "list_error" in d or d.get("count") == 0):
        return "wayback"
    if "тип" in d or "камера" in d or "координаты" in d:
        return "photo"
    return "other"


def _ordered(case: Case) -> list[dict]:
    """Записи в порядке чтения отчёта: домен и IP первыми, затем страница, ссылки, архив, фото."""
    def data(f):
        return f.get("data") if isinstance(f.get("data"), dict) else {}
    return sorted(case.findings(), key=lambda f: KIND_ORDER.index(_kind(data(f))))


def _reverse_only(data: dict) -> bool:
    """Обратный поиск по файлу — лишь адреса сервисов для ручной загрузки, в отчёте это одна строка."""
    return bool(data) and all(isinstance(v, str) and v.startswith("https://") for v in data.values()) and "Google Lens" in data


def facts(case: Case, for_ai: bool = False) -> str:
    """Факты проверок из записей дела — коротко, без служебных полей и без повторов. Собирает программа, не ИИ.
    Сбои наших запросов («недоступно») сюда не входят: это не свойство цели (часто — блокировка Tor)."""
    lines, seen = [], set()
    for f in _ordered(case):
        title = f["title"]
        data = f.get("data") if isinstance(f.get("data"), dict) else {}
        if _reverse_only(data):
            lines.append(f"• {title}: вручную — " + ", ".join(data))
            continue
        head, rows = f"• {title}" + ("  [гипотеза]" if f.get("status") else ""), []
        for k, v in data.items():
            if k in SKIP_FIELDS or v in (None, "", [], {}) or (k == "редиректов" and not v):
                continue
            label = LABELS.get(k, k)
            text = _value(v)
            if (label == "заголовок" and text in title) or (k == "итоговый адрес" and text == _value(f.get("target", ""))):
                continue
            key = (SAME.get(label, label), text)
            if key in seen or (label in SAME and any(text in t for l, t in seen if l == SAME[label])):
                continue
            seen.add(key)
            rows.append(f"    {label}: {text[:300]}")
        if rows or not data:  # запись, все поля которой уже показаны выше, не повторяем
            lines += [head, *rows]
    return "\n".join(lines)


def _when(text: str) -> datetime | None:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})$", text or "") or None
    if m:
        return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=timezone.utc)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc) if m else None


def _age(days: int) -> str:
    years, months = days // 365, days % 365 // 30
    if years:
        return f"{years} {_plural(years, 'год', 'года', 'лет')}" + (f" {months} мес." if months and years < 3 else "")
    return f"{months} мес." if months else f"{days} {_plural(days, 'день', 'дня', 'дней')}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    return many if 11 <= n <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def highlights(case: Case) -> list[str]:
    """Главное — выводы программы по правилам из фактов (без ИИ): за CDN ли сайт, возраст домена, контакты,
    координаты в фото. Каждое утверждение прямо следует из записи; «гипотеза» помечена словом."""
    from .infra import cdn_name

    now = datetime.now(timezone.utc)
    out: list[str] = []
    for f in _ordered(case):
        d = f.get("data") if isinstance(f.get("data"), dict) else {}
        target = f.get("target") or ""
        kind = _kind(d)
        if kind == "domain":
            cdn = (d.get("CDN") or "").split(" — ")[0] or cdn_name(d.get("хостинг")) or cdn_name(d.get("сервер"))
            if cdn:
                out.append(f"{target} закрыт сетью {cdn}: адреса {_value(d.get('IP-адреса', []))} принадлежат ей, "
                           "настоящий сервер сайта и его хостинг по ним не определить.")
            elif d.get("хостинг"):
                out.append(f"{target} размещён в сети {d['хостинг']}" + (f" ({_value(d['IP-адреса'])})." if d.get("IP-адреса") else "."))
            born = _when(str(d.get("создан") or ""))
            if born:
                days = (now - born).days
                reg = f", регистратор {d['регистратор']}" if d.get("регистратор") else ""
                out.append(f"Домену {_age(days)} (создан {_value(d['создан'])}{reg})."
                           + (" Домен свежий — так часто бывает у одноразовых сайтов (гипотеза)." if days < 180 else ""))
            ends = _when(str(d.get("истекает") or ""))
            if ends and (ends - now).days < 60:
                out.append(f"Регистрация домена истекает {_value(d['истекает'])} — через {(ends - now).days} дн.")
            m = re.search(r"осталось (-?\d+) дн", str(d.get("SSL") or ""))
            if m and int(m[1]) < 15:
                out.append(f"SSL-сертификат почти истёк: осталось {m[1]} дн.")
        elif kind == "ip":
            where = ", ".join(x for x in (d.get("страна"), d.get("город")) if x)
            out.append(f"{target} — {d.get('провайдер') or d.get('сеть')}" + (f", {where}" if where else "") + ".")
            cdn = (d.get("CDN") or "").split(" — ")[0] or cdn_name(d.get("провайдер"))
            if cdn:
                out.append(f"Это адрес сети {cdn}: за ним может стоять любой сайт её клиентов.")
            if d.get("владелец") and d.get("владелец") not in str(d.get("провайдер")):
                out.append(f"Владелец сети по реестру: {d['владелец']}" + (f", жалобы: {d['abuse']}." if d.get("abuse") else "."))
            if d.get("уязвимости"):
                out.append(f"Shodan знает уязвимости на этом адресе: {_value(d['уязвимости'])}.")
        elif kind == "page":
            code = int(d["status"]) if str(d["status"]).isdigit() else 0
            out.append(f"Страница {_value(d.get('final_url') or target)} " + (
                f"открывается, заголовок «{d['title']}»." if code < 400 else f"отвечает ошибкой {code}."))
        elif kind == "headers" and _value(d["итоговый адрес"]).split("/")[0] != _value(target).split("/")[0]:
            out.append(f"{target} перенаправляет на {_value(d['итоговый адрес'])}.")
        elif kind == "links":
            if d.get("соцсети"):
                out.append(f"На сайте указаны аккаунты: {_value(d['соцсети'])}.")
            if d.get("e-mail") or d.get("телефоны"):
                out.append("Контакты на сайте: " + "; ".join(_value(d[k]) for k in ("e-mail", "телефоны") if d.get(k)) + ".")
            if d.get("ведёт на сайты"):
                out.append(f"Ссылается на внешние сайты: {_value(d['ведёт на сайты'])}.")
        elif kind == "wayback":
            if d.get("count") is None:
                out.append(f"В Wayback Machine есть снимки, последний — {_value(d['last'])} (полный список архив не отдал)."
                           if d.get("last") else "Снимков в Wayback Machine не найдено (полный список архив не отдал).")
            else:
                out.append(f"В Wayback Machine снимков: {d['count']}" + (
                    f", первый — {_value(d['first'])}, последний — {_value(d.get('last'))}." if d.get("first") else "."))
        elif kind == "photo":
            what = d.get("тип") or "файл"
            when = (f", {'сделан' if what == 'снимок экрана' else 'снят'} {d['дата']}") if d.get("дата") else ""
            dev = d.get("камера") or d.get("устройство")
            out.append(f"{target.split('/')[-1]}: {what}{when}" + (f", устройство: {dev}" if dev else "") + ".")
            out.append(f"В файле есть координаты: {d['координаты']} — {d.get('карта', '')}".rstrip(" —") + "."
                       if d.get("координаты") else "Координат (GPS) в файле нет.")
            if d.get("автор"):
                out.append(f"Автор в метаданных: {d['автор']}.")
            if d.get("время на снимке"):
                out.append(f"Время на снимке: {d['время на снимке']}.")
            if d.get("текст на изображении"):
                out.append("На изображении распознан текст — он в фактах ниже.")
    return list(dict.fromkeys(out))


def page_text(case: Case) -> str:
    """Видимый текст снятых страниц: ИИ должен знать, что сайт пишет о себе, а не только DNS."""
    from bs4 import BeautifulSoup

    out = []
    for e in case.evidence():
        rel = str(e.get("file") or "")
        if e.get("deleted") or not rel.startswith("web/") or not rel.endswith(".html"):
            continue
        try:
            soup = BeautifulSoup((case.dir / rel).read_text(encoding="utf-8", errors="replace"), "html.parser")
        except OSError:
            continue
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        desc = soup.find("meta", attrs={"name": "description"})
        text = " ".join(soup.get_text(" ", strip=True).split())
        out.append(f"[{e.get('source') or rel}] " + (f"описание: {desc.get('content', '')}. " if desc else "") + text[:PAGE_MAX])
    return "\n\n".join(out[-3:])


_MIXED = re.compile(r"\w*(?:[a-zA-Z][а-яА-ЯёЁ]|[а-яА-ЯёЁ][a-zA-Z])\w*")
_LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.@-]{3,}")


def grounded(answer: str, source: str) -> tuple[str, int]:
    """Предложения ИИ, которые держатся на данных: без слов из смеси алфавитов («Нордinem») и без латинских имён и
    названий, которых нет в материалах (выдуманный автор). Возвращает (текст, сколько выброшено)."""
    low = source.lower()
    keep, dropped = [], 0
    for sent in re.split(r"(?<=[.!?])\s+", answer.strip()):
        bad = _MIXED.search(sent) or any(w.lower().strip(".@-") not in low for w in _LATIN.findall(sent))
        if sent and (bad or _CJK.search(sent)):
            dropped += 1
        elif sent:
            keep.append(sent)
    return " ".join(keep), dropped


def _brief(cfg: Config, request: str, case: Case, page: str) -> str:
    prompt = (f"Просьба аналитика: «{request}»\n\nТЕКСТ СТРАНИЦЫ:\n{page}\n\n"
              f"ФАКТЫ ПРОВЕРОК:\n{facts(case, True)[:CONTEXT_MAX]}")
    best, best_drop = "", 99
    for attempt in range(2):
        system = BRIEF if not attempt else BRIEF + " Не называй никого и ничего, чего нет в тексте страницы."
        text, dropped = grounded(_chat(cfg, system, prompt), prompt)
        if text and dropped < best_drop:
            best, best_drop = text, dropped
        if not dropped:
            break
    return best


def report(cfg: Config, case: Case, request: str, jobs: list[dict]) -> dict:
    """Отчёт ассистента: главное (выводы программы по фактам) + о сайте (ИИ, только если есть текст страницы)
    + факты + что не удалось. Без модели отчёт тоже собирается."""
    request = private((request or "").strip())
    done = [j for j in jobs or [] if j.get("status") == "готово"]
    failed = [f"{j.get('tool')} · {j.get('target')}: {j.get('error') or j.get('status')}"
              for j in jobs or [] if j.get("status") != "готово"]
    failed += [f"{f['title']} — не ответили: {_value(f['data']['недоступно'])}" for f in case.findings()
               if isinstance(f.get("data"), dict) and f["data"].get("недоступно")]
    page = page_text(case)
    sections = ["ГЛАВНОЕ:", *([f"• {x}" for x in highlights(case)] or ["• выводов нет — записей пока нет"])]
    if page:
        try:
            brief = _brief(cfg, request, case, page)
        except Exception as exc:  # noqa: BLE001 — без ИИ отчёт всё равно нужен
            brief = f"ИИ недоступен: {exc}"
        sections += ["", "О САЙТЕ (ИИ — гипотезы):", brief or "ИИ не дал описания, которое подтверждается текстом страницы."]
    sections += ["", "ФАКТЫ ИЗ ПРОВЕРОК:", facts(case) or "    — нет записей"]
    if failed:
        sections += ["", "НЕ УДАЛОСЬ:", *[f"• {x}" for x in failed]]
    text = "\n".join(sections)
    now = datetime.now(timezone.utc)
    head = (f"ОТЧЁТ · {now:%d.%m.%Y %H:%M} UTC · проверок {len(done)} из {len(jobs or [])}"
            + (f" · раздел «О сайте» — модель {cfg.ai_model}, гипотезы" if page else ""))
    note = case.write_note(f"ИИ_{utc_stamp()}", "\n".join([head, "", f"Просьба: {request}", "", text, ""]), new=True)
    return {"question": request, "answer": text, "model": cfg.ai_model, "note": note["name"]}


def ask(cfg: Config, case: Case, question: str, shown: str | None = None) -> dict:
    """shown — что записать в заметку как вопрос (служебное задание отчёта аналитику не нужно)."""
    question = (question or "").strip()
    shown = private(shown or question)
    if not question:
        raise RuntimeError("Пустой вопрос")
    answer = _chat(cfg, SYSTEM, f"МАТЕРИАЛЫ ДЕЛА «{case.name}»:\n{_context(case)}\n\nВОПРОС: {question}")
    stamp = utc_stamp()
    note = case.write_note(f"ИИ_{stamp}", "\n".join([
        f"ГИПОТЕЗЫ ИИ — модель {cfg.ai_model}, {stamp} UTC. Не доказательство: проверяйте по записям и первоисточникам.",
        "", f"Вопрос: {shown}", "", answer, ""]), new=True)
    return {"question": question, "answer": answer, "model": cfg.ai_model, "note": note["name"]}
