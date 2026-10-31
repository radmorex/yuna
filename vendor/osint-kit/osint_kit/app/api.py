"""Мост между окном приложения и движком osint-kit.

Методы класса Api вызываются из JavaScript (pywebview js_api) и возвращают JSON-совместимые данные.
Каждая проверка — отдельный процесс `osint …`: его можно остановить, сбой не роняет окно.
Запросы и результаты не пишутся в журналы: очередь живёт только в памяти.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import mimetypes
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .. import config, export, net, vault
from ..case import Case, delete_case, slug
from ..net import redact

MAX_PARALLEL = 3  # ТЗ: не больше трёх внешних проверок одновременно
PREVIEW_MAX = 8_000_000
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

# Инструменты окна. {target} — основное поле; extra — дополнительные поля; flags — галочки.
TOOLS = [
    {"id": "web_capture", "group": "Сайты", "label": "Захват страницы", "argv": ["web", "capture", "{target}"],
     "placeholder": "https://…", "hint": "скриншот, HTML, MHTML, PDF + SHA-256"},
    {"id": "wayback", "group": "Сайты", "label": "История в Wayback", "argv": ["web", "wayback", "{target}"],
     "placeholder": "домен или URL", "hint": "снимки веб-архива, первый и последний"},
    {"id": "links", "group": "Сайты", "label": "Ссылки и контакты", "argv": ["scrape", "links", "{target}"],
     "placeholder": "https://…", "hint": "ссылки, e-mail, телефоны, соцсети со страницы"},
    {"id": "tables", "group": "Сайты", "label": "Таблицы в CSV", "argv": ["scrape", "tables", "{target}"],
     "placeholder": "https://…", "hint": "таблицы страницы → CSV"},
    {"id": "files", "group": "Сайты", "label": "Скачать файлы", "argv": ["scrape", "files", "{target}", "--ext", "{ext*}"],
     "placeholder": "https://…", "extra": [{"name": "ext", "label": "расширения", "default": "pdf docx"}],
     "hint": "все файлы нужных типов со страницы"},
    {"id": "headers", "group": "Сайты", "label": "Заголовки и редиректы", "argv": ["web", "headers", "{target}"],
     "placeholder": "https://…", "hint": "цепочка редиректов, заголовки сервера"},
    {"id": "domain", "group": "Инфраструктура", "label": "Домен", "argv": ["infra", "domain", "{target}"],
     "placeholder": "example.com", "hint": "DNS, WHOIS, SSL, HTTP, поддомены"},
    {"id": "ip", "group": "Инфраструктура", "label": "IP-адрес", "argv": ["infra", "ip", "{target}"],
     "placeholder": "8.8.8.8", "hint": "сеть, провайдер, примерная география сети (не человека)"},
    {"id": "search", "group": "Поиск", "label": "Поисковые запросы", "argv": ["search", "{target}", "--kind", "{kind}"],
     "placeholder": "что искать", "extra": [{"name": "kind", "label": "тип", "default": "text",
                                            "choices": ["text", "name", "username", "email", "phone", "domain"]}],
     "hint": "готовые запросы для Google/Bing/DDG/Yandex"},
    {"id": "exif", "group": "Медиа", "label": "Метаданные фото", "argv": ["media", "exif", "{target}"],
     "placeholder": "файл", "file": True, "hint": "что за файл, камера, дата, GPS и текст на картинке — всё локально"},
    {"id": "video", "group": "Медиа", "label": "Видео: кадры", "argv": ["media", "video", "{target}"],
     "placeholder": "файл или ссылка", "file": True, "hint": "метаданные и ключевые кадры"},
    {"id": "reverse", "group": "Медиа", "label": "Обратный поиск фото", "argv": ["media", "reverse", "{target}"],
     "placeholder": "файл или ссылка на фото", "file": True, "hint": "ссылки на Google Lens, Yandex, Bing, TinEye"},
    {"id": "username", "group": "Люди", "label": "Аккаунты по нику", "argv": ["username", "{target}"],
     "placeholder": "ник", "hint": "кандидаты: одинаковый ник ≠ один человек"},
    {"id": "ioc", "group": "Текст", "label": "Индикаторы из текста", "argv": ["ioc", "{target}"],
     "placeholder": "текст или файл", "file": True, "hint": "ссылки, e-mail, IP, хеши — локально, без сети"},
    {"id": "translate", "group": "Текст", "label": "Перевод на русский", "argv": ["translate", "{target}"],
     "placeholder": "текст", "hint": "через Google Translate — текст уйдёт провайдеру"},
    {"id": "download", "group": "Медиа", "label": "Скачать видео", "argv": ["media", "download", "{target}"],
     "placeholder": "ссылка на видео", "hint": "публичное видео с сайта (yt-dlp) — файл и хеш в деле"},
    {"id": "urlcheck", "group": "Сайты", "label": "Проверка ссылки (VirusTotal / urlscan)", "argv": ["infra", "url", "{target}"],
     "placeholder": "https://…", "hint": "репутация ссылки в базах угроз", "needs": ["virustotal", "urlscan"]},
    {"id": "hash", "group": "Инфраструктура", "label": "Хеш файла (VirusTotal)", "argv": ["infra", "hash", "{target}"],
     "placeholder": "MD5, SHA-1 или SHA-256", "hint": "что известно о файле по его хешу", "needs": ["virustotal"]},
    {"id": "aicheck", "group": "Медиа", "label": "Проверка на ИИ-генерацию (Hive)", "argv": ["media", "aicheck", "{target}"],
     "placeholder": "файл", "file": True, "hint": "вероятность, что фото создано ИИ", "needs": ["hive"]},
]
TOOLS_BY_ID = {t["id"]: t for t in TOOLS}

# ТЗ: перед запуском показать, что уйдёт каждому получателю. (получатели всегда, {ключ: получатель с ключом})
SITE = "сайт из запроса — адрес страницы"
EGRESS = {
    "web_capture": ([SITE + " (браузер; трекеры заблокированы)", "сторонние ресурсы страницы — картинки, шрифты, скрипты"], {}),
    "wayback": (["web.archive.org — домен или URL"], {}),
    "links": ([SITE], {}),
    "tables": ([SITE], {}),
    "files": ([SITE, "серверы файлов, на которые ведут ссылки"], {}),
    "headers": ([SITE, "сайты по цепочке редиректов"], {}),
    "domain": (["DNS — имя домена (напрямую: DNS вашей сети; через Tor: Cloudflare DoH)", "WHOIS-серверы реестра — домен",
                "сам сайт — https, robots.txt, sitemap.xml", "crt.sh — домен", "ipinfo.io — IP-адреса домена",
                "DNS Team Cymru — первый IP домена (провайдер)"],
               {"virustotal": "VirusTotal — домен"}),
    "ip": (["stat.ripe.net — IP-адрес (реестр сети, abuse-контакт)", "DNS Team Cymru — IP-адрес (провайдер)",
            "ipinfo.io — IP-адрес", "internetdb.shodan.io — IP-адрес", "DNS — обратная запись IP"],
           {"shodan": "Shodan — IP-адрес", "abuseipdb": "AbuseIPDB — IP-адрес",
            "criminalip": "CriminalIP — IP-адрес", "virustotal": "VirusTotal — IP-адрес"}),
    "search": ([], {}),  # только готовит ссылки, сам ничего не отправляет
    "exif": ([], {}),
    "video": (["для ссылки — сайт видео (yt-dlp); файл не отправляется"], {}),
    "reverse": ([], {}),  # ссылки на поисковики открываете вы сами
    "username": (["≈400 сайтов (Sherlock) — ник"], {}),
    "ioc": ([], {}),
    "translate": (["Google Translate — весь текст"], {}),
    "download": (["сайт видео — ссылка (yt-dlp, через маршрут)"], {}),
    "urlcheck": ([], {"virustotal": "VirusTotal — ссылка", "urlscan": "urlscan.io — ссылка (скан приватный)"}),
    "hash": ([], {"virustotal": "VirusTotal — хеш"}),
    "aicheck": ([], {"hive": "Hive — файл целиком"}),
}


def available(tool: dict, cfg: config.Config) -> bool:
    """Инструмент с ключом доступен, если задан хоть один из его ключей (needs)."""
    return not tool.get("needs") or any(cfg.key(k) for k in tool["needs"])


def needs_label(tool: dict) -> str:
    return "нужен ключ " + " или ".join(config.KEY_LABELS.get(k, k) for k in tool["needs"]) if tool.get("needs") else ""


# Общий поиск: тип запроса → проверки. Ник (Sherlock, ≈400 сайтов) и перевод через Google — только вручную:
# без массового сбора по человеку и без отправки текста дела стороннему сервису.
AUTO = {
    "url": [("web_capture", {}), ("wayback", {}), ("links", {}), ("headers", {})],
    "domain": [("domain", {}), ("web_capture", {}), ("wayback", {}), ("links", {}), ("headers", {})],
    "ip": [("ip", {})],
    "email": [("search", {"kind": "email"})],
    "phone": [("search", {"kind": "phone"})],
    "username": [("search", {"kind": "username"})],
    "name": [("search", {"kind": "name"})],
    "image": [("exif", {}), ("reverse", {})],
    "video": [("video", {})],
    "file": [("ioc", {})],
    "text": [("ioc", {}), ("search", {"kind": "text"})],
}
KIND_LABELS = {"url": "ссылка", "domain": "домен", "ip": "IP-адрес", "email": "e-mail", "phone": "телефон",
               "username": "ник", "name": "имя", "image": "фото", "video": "видео", "file": "файл", "text": "текст"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
FILE_EXT = {".pdf", ".txt", ".doc", ".docx", ".zip", ".json", ".csv", ".html"}
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\W\d_]{2,}")
_PHONE = re.compile(r"\+?[\d\s().-]{7,20}")
_DOMAIN = re.compile(r"(?:[\w-]+\.)+[^\W\d_]{2,}")
_USERNAME = re.compile(r"[\w.-]{2,40}")
_NAME = re.compile(r"(?:[^\W\d_][^\W\d_'’-]*(?:[-'’][^\W\d_]+)*\s+){1,3}[^\W\d_][^\W\d_'’-]*")


def classify(target: str) -> str:
    t = (target or "").strip()
    path = Path(t).expanduser()
    if t.startswith(("/", "~")) and path.is_file():
        ext = path.suffix.lower()
        return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "file"
    if t.lower().startswith(("http://", "https://")):
        return "url"
    try:
        ipaddress.ip_address(t)
        return "ip"
    except ValueError:
        pass
    if _EMAIL.fullmatch(t):
        return "email"
    if _PHONE.fullmatch(t) and sum(c.isdigit() for c in t) >= 7:
        return "phone"
    if _DOMAIN.fullmatch(t):
        return "domain"
    if _USERNAME.fullmatch(t):
        return "username"
    if _NAME.fullmatch(t) and all(w[0].isupper() for w in t.split()):
        return "name"
    return "text"


MAX_TARGETS = 5  # ассистент: не больше целей за один запрос — без массового сбора
_TOKEN = re.compile(r"https?://[^\s<>\"«»]+|\S+", re.I)


_PATH_START = re.compile(r"(?:^|(?<=[\s\"«']))(?:~|/)", re.M)


def _file_paths(text: str) -> list[tuple[int, int, str]]:
    """Пути к файлам в тексте, в том числе с пробелами («Снимок экрана … в 13.00.00.png»): от «/» или «~»
    берётся самый длинный кусок строки, который действительно оказывается файлом."""
    found = []
    for m in _PATH_START.finditer(text):
        line_end = text.find("\n", m.start())
        chunk = text[m.start(): line_end if line_end >= 0 else len(text)]
        cut = len(chunk)
        while cut > 1:
            cand = chunk[:cut].rstrip().rstrip("\"»'.,;:!?)")
            if cand and Path(cand).expanduser().is_file():
                found.append((m.start(), m.start() + len(cand), cand))
                break
            cut = chunk.rfind(" ", 0, cut)
    return found


def find_targets(text: str) -> list[str]:
    """Цели из просьбы обычным текстом: ссылки, домены, IP, e-mail и файлы на этом компьютере — по порядку."""
    text = text or ""
    paths = _file_paths(text)
    found = [(a, p) for a, _, p in paths]
    for m in _TOKEN.finditer(text):
        if any(a <= m.start() < b for a, b, _ in paths):  # кусок уже найденного пути
            continue
        t = m.group().strip("«»\"'()[]").rstrip(".,;:!?")
        if t and Path(t).suffix.lower() in IMAGE_EXT | VIDEO_EXT | FILE_EXT:  # «фото.png» — имя файла, не домен
            continue
        if t and classify(t) in ("url", "domain", "ip", "email"):
            found.append((m.start(), t))
    out: list[str] = []
    for _, t in sorted(found):
        if t not in out:
            out.append(t)
    return out


def egress(tool: dict, cfg: config.Config) -> dict:
    always, keyed = EGRESS.get(tool["id"], ([], {}))
    items = list(always)
    if tool["id"] == "translate" and cfg.libretranslate_url:
        items = [f"{urlparse(cfg.libretranslate_url).netloc or cfg.libretranslate_url} (LibreTranslate) — весь текст"]
    items += [label for key, label in keyed.items() if cfg.key(key)]
    if cfg.route != "direct":  # справочный сервис блокирует Tor — повтор напрямую: он увидит ваш IP (цель — нет)
        items = [i + " (если блокирует Tor — напрямую: сервис видит ваш IP)"
                 if i.split(" — ")[0].split()[0] in cfg.direct_fallback else i for i in items]
    return {"local": not items, "items": items, "route": cfg.route}


def build_argv(tool_id: str, target: str, options: dict | None = None) -> list[str]:
    tool = TOOLS_BY_ID.get(tool_id)
    if not tool:
        raise ValueError("Неизвестный инструмент")
    target = (target or "").strip()
    options = options or {}
    if not target:
        raise ValueError("Пустой запрос")
    if target.startswith("-"):
        raise ValueError("Запрос не может начинаться с «-»")
    argv = []
    for part in tool["argv"]:
        if part == "{target}":
            argv.append(target)
        elif part.startswith("{") and part.endswith("*}"):  # список через пробел
            name = part[1:-2]
            values = str(options.get(name) or _default(tool, name)).split()
            if not values or any(v.startswith("-") for v in values):
                raise ValueError(f"Неверное значение поля «{name}»")
            argv.extend(values)
        elif part.startswith("{"):
            name = part[1:-1]
            value = str(options.get(name) or _default(tool, name))
            field = next(f for f in tool.get("extra", []) if f["name"] == name)
            if "choices" in field and value not in field["choices"]:
                raise ValueError(f"Неверное значение поля «{name}»")
            argv.append(value)
        else:
            argv.append(part)
    for flag in tool.get("flags", []):
        if options.get(flag["name"]):
            argv.append(flag["name"])
    return argv


def _default(tool: dict, name: str) -> str:
    return next((f.get("default", "") for f in tool.get("extra", []) if f["name"] == name), "")


class Job:
    def __init__(self, jid: int, case: str, tool: dict, target: str, argv: list[str], batch: int | None = None):
        self.id, self.case, self.tool, self.target, self.argv, self.batch = jid, case, tool, target, argv, batch
        self.status = "в очереди"
        self.error = ""
        self.started = self.finished = None
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def view(self) -> dict:
        return {"id": self.id, "case": self.case, "tool": self.tool["label"], "target": self.target,
                "status": self.status, "error": self.error, "started": self.started, "finished": self.finished,
                "batch": self.batch}


class Jobs:
    """Очередь проверок: до MAX_PARALLEL процессов одновременно, отмена — завершением группы процессов."""

    def __init__(self, prefix: list[str] | None = None):
        self.prefix = prefix or [sys.executable, "-m", "osint_kit.cli"]
        self.items: list[Job] = []
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(MAX_PARALLEL)

    def start(self, case: str, tool: dict, target: str, argv: list[str], batch: int | None = None) -> Job:
        with self.lock:
            job = Job(len(self.items) + 1, case, tool, target, argv, batch)
            self.items.append(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        with self.slots:
            if job.cancelled:
                return
            with self.lock:  # статус «выполняется» — только когда процесс уже есть, иначе «стоп» нечего завершать
                job.proc = subprocess.Popen(self.prefix + ["--case", job.case, *job.argv], stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                            start_new_session=True, env=os.environ | {"NO_COLOR": "1", "COLUMNS": "200"})
                job.status, job.started = "выполняется", time.strftime("%H:%M:%S")
            if job.cancelled:
                self._kill(job)
            out, _ = job.proc.communicate()
            job.finished = time.strftime("%H:%M:%S")
            if job.cancelled:
                job.status = "отменено"
            elif job.proc.returncode == 0:
                job.status = "готово"
            else:
                job.status = "ошибка"
                lines = [ln for ln in (out or "").splitlines() if ln.strip()]
                err = next((ln for ln in reversed(lines) if ln.startswith("Ошибка:")), lines[-1] if lines else "")
                job.error = redact(err.removeprefix("Ошибка:").strip())[:400]

    def cancel(self, jid: int) -> bool:
        job = next((j for j in self.items if j.id == jid), None)
        if not job or job.status not in ("в очереди", "выполняется"):
            return False
        with self.lock:
            job.cancelled = True
            if job.status == "в очереди":
                job.status = "отменено"
            else:
                self._kill(job)
        return True

    @staticmethod
    def _kill(job: Job) -> None:
        if job.proc and job.proc.poll() is None:
            try:
                os.killpg(job.proc.pid, signal.SIGTERM)  # вместе с Chromium/ffmpeg, запущенными проверкой
            except ProcessLookupError:
                pass

    def cancel_batch(self, batch: int) -> int:
        return sum(self.cancel(j.id) for j in list(self.items) if j.batch == batch)

    def running(self, case: str | None = None) -> bool:
        return any(j.status in ("в очереди", "выполняется") and case in (None, j.case) for j in self.items)

    def view(self) -> list[dict]:
        return [j.view() for j in reversed(self.items)]


class Api:
    def __init__(self, cfg: config.Config | None = None, jobs: Jobs | None = None):
        self._cfg = cfg or config.load()
        self._jobs = jobs or Jobs()
        self._window = None  # задаётся при создании окна (диалог выбора файла)
        # поля с «_» pywebview не отдаёт в JavaScript: странице доступны только методы ниже

    # ---------- хранилище и дела ----------
    def _vault_state(self) -> str:
        if not self._cfg.encrypt_cases:
            return "выключено"
        return "открыто" if vault.is_open(self._cfg.cases_dir) else "закрыто"

    def state(self) -> dict:
        st = self._vault_state()
        cases = []
        if st != "закрыто" and self._cfg.cases_dir.exists():
            cases = sorted(p.name for p in self._cfg.cases_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        tools = [{**t, "egress": egress(t, self._cfg), "available": available(t, self._cfg), "needs_label": needs_label(t)}
                 for t in TOOLS]
        return {"vault": st, "cases": cases, "tools": tools}

    def unlock(self) -> dict:
        if self._cfg.encrypt_cases:
            vault.open_vault(self._cfg.cases_dir, self._cfg.vault_image or vault.DEFAULT_IMAGE)
        return self.state()

    def lock(self) -> dict:
        if self._jobs.running():
            raise RuntimeError("Идут проверки — остановите их перед закрытием хранилища")
        if self._cfg.encrypt_cases:
            vault.lock(self._cfg.cases_dir)
        return self.state()

    def _case(self, name: str, create: bool = False) -> Case:
        if self._vault_state() == "закрыто":
            raise RuntimeError("Хранилище закрыто")
        name = slug(name or "")
        if not create and not (self._cfg.cases_dir / name).is_dir():
            raise RuntimeError("Дело не найдено")
        return Case(self._cfg.cases_dir, name)

    def create_case(self, name: str) -> dict:
        if not (name or "").strip():
            raise RuntimeError("Введите название дела")
        return {"name": self._case(name, create=True).name, **self.state()}

    def case(self, name: str) -> dict:
        c = self._case(name)
        return {"name": c.name, "findings": c.findings(), "evidence": c.evidence(), "notes": c.notes()}

    def _idle(self, case: str) -> None:
        # проверка дописывает журнал доказательств — переписывать его в это время нельзя
        if self._jobs.running(case):
            raise RuntimeError("По этому делу идут проверки — дождитесь их или остановите")

    def delete_case(self, name: str, confirm: str) -> dict:
        c = self._case(name)
        if (confirm or "").strip() != c.name:
            raise RuntimeError("Название не совпадает — дело не удалено")
        self._idle(c.name)
        delete_case(self._cfg.cases_dir, c.name)
        return self.state()

    def delete_evidence(self, case: str, eid: str) -> dict:
        c = self._case(case)
        self._idle(c.name)
        return c.delete_evidence(str(eid), "удалено в окне Fallen Angel")

    # ---------- досье: сводка и заметки ----------
    def cases_info(self) -> list[dict]:
        """Таблица дел: сколько находок, гипотез, файлов, заметок; когда менялось."""
        if self._vault_state() == "закрыто" or not self._cfg.cases_dir.exists():
            return []
        out = []
        for name in self.state()["cases"]:
            c = Case(self._cfg.cases_dir, name)
            findings, evidence = c.findings(), c.evidence()
            stamps = [x.get("time", "") for x in findings + evidence] + [n["modified"] for n in c.notes()]
            out.append({"name": name, "findings": len(findings), "hypotheses": sum(1 for f in findings if f.get("status")),
                        "evidence": sum(1 for e in evidence if not e.get("deleted")), "notes": len(c.notes()),
                        "updated": max(stamps, default="")})
        return out

    def finding_paste(self, case: str, fid: str) -> str:
        c = self._case(case)
        f = next((x for x in c.findings() if x["id"] == fid), None)
        if f is None:
            raise RuntimeError("Находка не найдена")
        return export.finding_paste(c.name, f, c.evidence())

    def summary(self, case: str) -> str:
        return export.to_paste(export.case_data(self._case(case)))

    def note(self, case: str, name: str) -> dict:
        c = self._case(case)
        return {"name": c.note_path(name).name, "text": c.read_note(name)}

    def save_note(self, case: str, name: str, text: str, new: bool = False) -> dict:
        return self._case(case).write_note(name, text, new=bool(new))

    def delete_note(self, case: str, name: str) -> bool:
        self._case(case).delete_note(name)
        return True

    def _file(self, case: str, rel: str) -> Path:
        c = self._case(case)
        path = (c.dir / rel).resolve()
        if not path.is_relative_to(c.dir.resolve()) or not path.is_file():
            raise RuntimeError("Файл не найден в деле")
        return path

    def preview(self, case: str, rel: str) -> dict:
        """Изображение как data: URL — показывается в окне, не исполняется."""
        path = self._file(case, rel)
        mime = mimetypes.guess_type(path.name)[0] or ""
        if mime not in IMAGE_TYPES or path.stat().st_size > PREVIEW_MAX:
            return {"ok": False}
        return {"ok": True, "src": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()}

    # ---------- маршрут (Tor) и локальный ИИ ----------
    def route_status(self) -> dict:
        self._cfg = config.load()
        try:
            ep = net.proxy_endpoint(self._cfg)
        except net.RouteError as exc:
            return {"mode": self._cfg.route, "up": False, "error": str(exc)}
        return {"mode": self._cfg.route, "proxy": f"{ep[0]}:{ep[1]}" if ep else None,
                "up": bool(ep and net.port_open(*ep))}

    def route_start(self) -> dict:
        net.tor_start()
        return self.route_status()

    def route_check(self) -> dict:
        """Проверка через check.torproject.org — отдельным процессом с тем же маршрутом, что у проверок."""
        out = subprocess.run(self._jobs.prefix + ["route", "check"], capture_output=True, text=True, timeout=120,
                             env=os.environ | {"NO_COLOR": "1"})
        if out.returncode != 0:
            raise RuntimeError(redact(out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "проверка не удалась"))
        import json

        return json.loads(out.stdout[out.stdout.index("{"):out.stdout.rindex("}") + 1])

    def route_set(self, mode: str) -> dict:
        config.set_route(mode)
        return self.route_status()

    def ai_status(self) -> dict:
        from ..modules import ai

        try:
            return ai.status(self._cfg)
        except Exception as exc:  # noqa: BLE001
            return {"running": False, "ready": False, "model": self._cfg.ai_model, "error": redact(str(exc))}

    def ai_ask(self, case: str, question: str) -> dict:
        from ..modules import ai

        return ai.ask(self._cfg, self._case(case), question)

    def thumb(self, case: str, rel: str, width: int = 480) -> dict:
        """Миниатюра фото (JPEG, верх длинного скриншота) — для плитки, без передачи мегабайтов в окно."""
        path = self._file(case, rel)
        if (mimetypes.guess_type(path.name)[0] or "") not in IMAGE_TYPES:
            return {"ok": False}
        try:
            from PIL import Image

            with Image.open(path) as im:
                im = im.convert("RGB")
                w, h = im.size
                if h > w * 1.4:  # длинный скриншот страницы — показываем верх
                    im = im.crop((0, 0, w, int(w * 0.9)))
                im.thumbnail((width, width * 2))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82)
            return {"ok": True, "src": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
                    "size": [w, h]}
        except Exception:  # noqa: BLE001 — битый или чужой формат: просто без миниатюры
            return {"ok": False}

    @staticmethod
    def copy_text(text: str) -> bool:
        subprocess.run(["pbcopy"], input=str(text).encode("utf-8"), check=False)
        return True

    def open_link(self, url: str) -> bool:
        """Открыть ссылку в обычном браузере — по явному выбору (сайт увидит адрес браузера, не Tor)."""
        url = str(url).strip()
        if urlparse(url).scheme not in ("http", "https"):
            raise RuntimeError("Открываются только ссылки http(s)")
        subprocess.run(["open", url], check=False)
        return True

    def reveal(self, case: str, rel: str) -> bool:
        """Показать файл в Finder (не открывать: вложения — данные, а не программы)."""
        subprocess.run(["open", "-R", str(self._file(case, rel))], check=False)
        return True

    def export(self, case: str) -> dict:
        res = export.export(self._case(case))
        subprocess.run(["open", "-R", res["files"]["txt"]], check=False)
        return res

    # ---------- настройки: ключи в окно не отдаются — только «задан / нет» ----------
    def settings(self) -> dict:
        self._cfg = config.load()
        return {"route": self._cfg.route, "direct_fallback": bool(self._cfg.direct_fallback),
                "keys": {k: bool(self._cfg.key(k)) for k in config.KEY_NAMES}, "key_labels": config.KEY_LABELS,
                "ai_model": self._cfg.ai_model, "encrypt": self._cfg.encrypt_cases}

    def set_key(self, name: str, value: str) -> dict:
        config.set_key(name, value)
        return self.settings()

    def set_direct_fallback(self, on: bool) -> dict:
        config.set_value("network", "direct_fallback", list(config.DIRECT_FALLBACK) if on else [])
        return self.settings()

    def import_case(self, path: str) -> dict:
        """Экспорт (ZIP/JSON) → новое дело; имя — из имени файла, одноимённое не перезаписывается."""
        src = Path(path or "").expanduser()
        if not src.is_file():
            raise RuntimeError("Файл не найден")
        base = slug(re.sub(r"_\d{8}T?\d{6}.*$", "", src.stem)) or "импорт"
        name, n = base, 1
        while (self._cfg.cases_dir / name).exists():
            n += 1
            name = f"{base}-{n}"
        c = self._case(name, create=True)
        try:
            res = export.import_case(c, src)
        except Exception:
            delete_case(self._cfg.cases_dir, c.name)
            raise
        return {**res, "case": c.name, **self.state()}

    # ---------- проверки ----------
    def run(self, case: str, tool_id: str, target: str, options: dict | None = None) -> dict:
        c = self._case(case)
        argv = build_argv(tool_id, target, options)
        if not available(TOOLS_BY_ID[tool_id], self._cfg):
            raise RuntimeError(needs_label(TOOLS_BY_ID[tool_id]).capitalize() + " — добавьте его в «Настройках»")
        return self._jobs.start(c.name, TOOLS_BY_ID[tool_id], target.strip(), argv).view()

    def plan(self, target: str) -> dict:
        """Что запустит общий поиск — показывается до запуска."""
        target = (target or "").strip()
        if not target:
            raise ValueError("Пустой запрос")
        kind = classify(target)
        steps = [{"tool": tid, "label": TOOLS_BY_ID[tid]["label"], "options": opts} for tid, opts in AUTO[kind]]
        items: list[str] = []
        for st in steps:
            items += [i for i in egress(TOOLS_BY_ID[st["tool"]], self._cfg)["items"] if i not in items]
        return {"kind": kind, "kind_label": KIND_LABELS[kind], "steps": steps,
                "egress": {"local": not items, "items": items, "route": self._cfg.route}}

    def run_all(self, case: str, target: str) -> dict:
        plan = self.plan(target)
        return {**self._enqueue(case, [(target.strip(), plan)]), "kind_label": plan["kind_label"]}

    def _enqueue(self, case: str, plans: list[tuple[str, dict]]) -> dict:
        c = self._case(case)
        # все argv собираются до запуска: ошибка в одной цели не оставляет пачку наполовину запущенной
        argvs = [(target, TOOLS_BY_ID[st["tool"]], build_argv(st["tool"], target, st["options"]))
                 for target, plan in plans for st in plan["steps"]]
        batch = max((j.batch or 0 for j in self._jobs.items), default=0) + 1
        for target, tool, argv in argvs:
            self._jobs.start(c.name, tool, target, argv, batch)
        return {"batch": batch, "total": len(argvs)}

    # ---------- ассистент: просьба обычным текстом → проверки → отчёт ИИ ----------
    def assistant_plan(self, text: str) -> dict:
        targets = find_targets(text)
        if len(targets) > MAX_TARGETS:
            raise ValueError(f"Слишком много целей ({len(targets)}) — не больше {MAX_TARGETS} за один запрос")
        plans = [{"target": t, **self.plan(t)} for t in targets]
        items: list[str] = []
        for p in plans:
            items += [i for i in p["egress"]["items"] if i not in items]
        return {"targets": plans, "egress": {"local": not items, "items": items, "route": self._cfg.route}}

    def assistant_run(self, case: str, text: str) -> dict:
        plan = self.assistant_plan(text)
        if not plan["targets"]:
            return {"batch": None, "total": 0}
        return self._enqueue(case, [(p["target"], p) for p in plan["targets"]])

    def assistant_start(self, text: str, case: str | None = None) -> dict:
        """Диалог на главной: без дела — заводит новое по первой цели, иначе продолжает начатое."""
        plan = self.assistant_plan(text)
        if not plan["targets"]:
            raise ValueError("Назовите сайт, домен, IP-адрес, e-mail или перетащите фото — с этого начнётся дело")
        if not case:
            first = plan["targets"][0]["target"]
            base = (slug(urlparse(first).hostname or first) if "://" in first
                    else slug(Path(first).stem) if first.startswith(("/", "~")) else slug(first))
            case, n = base, 1
            while (self._cfg.cases_dir / case).exists():  # одноимённое дело — чужие материалы, не смешиваем
                n += 1
                case = f"{base}-{n}"
            case = self._case(case, create=True).name
        return {**self._enqueue(case, [(p["target"], p) for p in plan["targets"]]), "case": case}

    def assistant_report(self, case: str, text: str, jobs: list[dict]) -> dict:
        """Отчёт после проверок: «коротко» пишет ИИ, факты и неудачи собирает программа."""
        from ..modules import ai

        return ai.report(self._cfg, self._case(case), text, jobs)

    def cancel_batch(self, batch: int) -> int:
        return self._jobs.cancel_batch(int(batch))

    def list_jobs(self) -> list[dict]:
        return self._jobs.view()

    def cancel(self, job_id: int) -> bool:
        return self._jobs.cancel(int(job_id))

    def pick_file(self) -> str:
        if not self._window:
            return ""
        import webview

        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
        return picked[0] if picked else ""
