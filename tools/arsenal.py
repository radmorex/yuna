#!/usr/bin/env python3
"""Мост к поисковым инструментам osint-kit и public-tools.

Запуск: arsenal.py <free|incognito> <инструмент> <цель>. Ответ: JSON {"ok", "title", "summary", "text"}.
summary: одна строка для голоса, text: подробности для панели.
Только белый список инструментов и проверенные цели, без shell. Поиска людей здесь нет намеренно:
аккаунты по нику, Telegram и запросы по имени/почте/телефону не подключены (правила security-lab).
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osint_report  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.environ.get("OSINT_KIT_DIR", os.path.join(HERE, "..", "vendor", "osint-kit"))
WEB_KIT = os.environ.get("PUBLIC_TOOLS_DIR", HERE)
MULLVAD = os.environ.get("MULLVAD_BIN", "/usr/local/bin/mullvad")
DATA = os.path.join(HERE, "..", "data")
CASE = "yuna"
LIMIT = 6000
TIMEOUT = 90
# Список целей: не больше MAX_BATCH и с паузой, чтобы не нагружать чужие сервисы. Ядро ждёт 150 с.
MAX_BATCH = 10
BATCH_PAUSE = 1
BATCH_DEADLINE = 130
# Ключи сервисов из .env, остальное из него инструменту не передаём.
KEY_NAMES = ("OSINT_VIRUSTOTAL", "OSINT_URLSCAN", "OSINT_SHODAN", "OSINT_ABUSEIPDB", "OSINT_CRIMINALIP")

# инструмент: (тип цели, аргументы CLI, подпись)
OSINT = {
    "domain": ("domain", ["infra", "domain"], "Домен"),
    "ip": ("ip", ["infra", "ip"], "IP-адреса"),
    "headers": ("url", ["web", "headers"], "Заголовки й редиректи"),
    "wayback": ("site", ["web", "wayback"], "Вебархів"),
    "links": ("url", ["scrape", "links"], "Посилання зі сторінки"),
    "tables": ("url", ["scrape", "tables"], "Таблиці зі сторінки"),
    "urlcheck": ("url", ["infra", "url"], "Репутація посилання"),
    "hash": ("hash", ["infra", "hash"], "Хеш файлу"),
    "dorks": ("text", ["search"], "Пошукові запити"),
}
WEB_TOOLS = {"page": ("url", "Сторінка"), "web": ("text", "Пошук"), "youtube": ("text", "YouTube"), "maps": ("text", "Карта"),
                "youtube_first": ("text", "Перше відео")}
REPORT_TOOLS = {"batch": "Перевірка списку", "save_report": "Збереження звіту"}
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")


class Refused(Exception):
    pass


def check_domain(value):
    if not DOMAIN_RE.match(value.lower()):
        raise Refused("Некоректний домен")


def check_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise Refused("Некоректна IP-адреса")
    if not ip.is_global:
        raise Refused("Це локальна або внутрішня адреса, назовні не запитую")


def check_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise Refused("Потрібне звичайне посилання http або https")
    host = parts.hostname.lower()
    try:
        check_ip(host)
    except Refused as e:
        if "локальна" in str(e):
            raise
        check_domain(host)


def validate(tool, target):
    """Проверяет цель до любого запроса. Цель никогда не начинается с дефиса: это был бы флаг CLI."""
    target = (target or "").strip()
    if not target or len(target) > 500 or target.startswith("-") or any(ord(c) < 32 for c in target):
        raise Refused("Некоректна ціль")
    if tool in REPORT_TOOLS:
        kind = tool
    elif tool in OSINT:
        kind = OSINT[tool][0]
    elif tool in WEB_TOOLS:
        kind = WEB_TOOLS[tool][0]
    else:
        raise Refused("Такого інструмента немає")
    if kind == "domain":
        check_domain(target)
    elif kind == "ip":
        check_ip(target)
    elif kind == "url":
        check_url(target)
    elif kind == "site":
        check_url(target) if "://" in target else check_domain(target)
    elif kind == "batch":
        batch_targets(target)
    elif kind == "hash" and not re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", target):
        raise Refused("Потрібен хеш MD5, SHA-1 або SHA-256")
    return target


def batch_targets(value):
    """Цели списка без повторов. Домен или IP, иначе отказ: так проверяется весь список сразу."""
    targets = list(dict.fromkeys(value.lower().split()))
    if len(targets) < 2 or len(targets) > MAX_BATCH:
        raise Refused(f"У списку має бути від 2 до {MAX_BATCH} цілей")
    for target in targets:
        kind_of(target)
    return targets


def kind_of(target):
    """Определяет инструмент цели. Локальный адрес здесь же получает отказ."""
    try:
        ipaddress.ip_address(target)
    except ValueError:
        check_domain(target)
        return "domain"
    check_ip(target)
    return "ip"


def short(data):
    """Подробности для панели: длинный текст сокращаем, полный лежит в сохраняемом отчёте."""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
    return text if len(text) <= LIMIT else text[:LIMIT] + "\n…(скорочено, повний текст: збережи звіт)"


def env_keys():
    """Ключи сервисов из .env. Значения никуда не печатаем."""
    keys = {}
    try:
        with open(os.path.join(HERE, "..", ".env"), encoding="utf-8") as fh:
            for line in fh:
                name, sep, value = line.strip().partition("=")
                if sep and name in KEY_NAMES and value.strip():
                    keys[name] = value.strip().strip("\"'")
    except OSError:
        pass
    return keys


def explain_failure(stderr, stdout):
    """Настоящая причина отказа инструмента, понятными словами."""
    message = " ".join(((stderr or "") + " " + (stdout or "")).split())
    if "Ошибка:" in message:
        message = message.split("Ошибка:", 1)[1].strip()
    low = message.lower()
    if "ключ" in low and "urlscan" in low:
        return "Потрібен ключ сервісу: задай OSINT_VIRUSTOTAL або OSINT_URLSCAN у .env"
    if "ключ" in low and "virustotal" in low:
        return "Потрібен ключ сервісу: задай OSINT_VIRUSTOTAL у .env"
    if "hdiutil" in low:
        return "Не відкрилось сховище справ osint-kit: перевір vault_image у vendor/osint-kit/config.toml"
    return "Інструмент не зміг: " + (message[:160] or "без пояснення")


def vpn_confirmed(returncode, stdout):
    """Истина только при явном подтверждении Mullvad: любой непонятный ответ считаем отказом."""
    if returncode != 0:
        return False
    try:
        status = json.loads(stdout)
        return status["state"] == "connected" and status["details"]["location"]["mullvad_exit_ip"] is True
    except (ValueError, KeyError, TypeError):
        return False


def require_vpn():
    """Инкогнито не должно молча стать прямым подключением."""
    try:
        out = subprocess.run([MULLVAD, "status", "-j"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("Mullvad VPN не відповідає, запит не надіслано")
    if not vpn_confirmed(out.returncode, out.stdout):
        raise Refused("Mullvad VPN не підключений, запит не надіслано")


def run_osint(tool, target):
    python = os.path.join(KIT, ".venv/bin/python")
    if not os.access(python, os.X_OK):
        raise Refused("osint-kit не встановлено: bash scripts/setup.sh tools")
    # Весь трафик и так идёт через туннель Mullvad, собственный маршрут инструменту не нужен.
    # Широкий COLUMNS: иначе консоль переносит длинные строки и ломает JSON.
    env = {**os.environ, **env_keys(), "OSINT_ROUTE": "direct", "COLUMNS": "100000", "NO_COLOR": "1"}
    base = [python, "-m", "osint_kit.cli"]
    argv = base + ["--case", CASE] + OSINT[tool][1] + [target]
    if tool == "dorks":
        argv += ["--kind", "domain" if DOMAIN_RE.match(target.lower()) else "text"]
    out = subprocess.run(argv, cwd=KIT, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    if out.returncode != 0:
        raise Refused(explain_failure(out.stderr, out.stdout))
    # Вокруг JSON инструмент может напечатать служебные строки.
    start = out.stdout.find("{")
    try:
        return json.JSONDecoder().raw_decode(out.stdout[start:])[0] if start >= 0 else out.stdout.strip()
    except ValueError:
        return out.stdout.strip()


def run_web(tool, target):
    sys.path.insert(0, WEB_KIT)
    import public_tools

    if tool == "youtube_first":
        # Для запуска нужно одно видео: компактная строка, которую ядро не должно разбирать как JSON.
        items = public_tools.run("youtube", target).get("items") or []
        if not items:
            raise Refused("Не знайшла відео за цим запитом")
        return "\t".join(str(items[0].get(k, "")).replace("\t", " ") for k in ("id", "title", "author"))
    return public_tools.run(tool, target)


def run_batch(targets):
    """Проверяет цели по очереди. Сбой одной цели не останавливает остальные."""
    deadline = time.monotonic() + BATCH_DEADLINE
    lines, details, failed = [], [], 0
    for number, target in enumerate(targets):
        if time.monotonic() > deadline:
            lines.append(f"Не встигла перевірити: {', '.join(targets[number:])}")
            failed += len(targets) - number
            break
        if number:
            time.sleep(BATCH_PAUSE)
        try:
            summary, text = osint_report.render(kind_of(target), run_osint(kind_of(target), target))
        except (Refused, subprocess.TimeoutExpired, OSError) as e:
            summary, text = f"{target}: не вийшло. {e}".strip(), ""
            failed += 1
        lines.append(summary)
        details.append(f"## {target}\n{summary}\n\n{text}".strip())
    done = len(targets) - failed
    summary = f"Перевірила {done} із {len(targets)}" + (f", не вийшло {failed}." if failed else ".") + " Подробиці в панелі."
    return summary, "\n".join(lines), "\n\n".join(details)


def report_dir(name):
    path = os.path.join(DATA, name)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def remember(title, target, summary, text, mode):
    """Последний отчёт для команды «збережи звіт». В инкогнито следов на диске не оставляем."""
    if mode == "incognito":
        return
    body = f"# {title}: {target}\n\n{datetime.now():%Y-%m-%d %H:%M}\n\n{summary}\n\n{text}\n"
    path = os.path.join(report_dir("osint"), "last.md")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)


def save_report(mode):
    if mode == "incognito":
        raise Refused("В інкогніто звіти не зберігаю")
    try:
        with open(os.path.join(DATA, "osint", "last.md"), encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        raise Refused("Поки немає що зберігати: спершу щось перевір")
    first = body.splitlines()[0] if body else ""
    name = re.sub(r"[^a-z0-9.-]+", "-", first.split(":", 1)[-1].strip().lower()).strip("-")[:50] or "report"
    path = os.path.join(report_dir("reports"), f"{datetime.now():%Y%m%d-%H%M%S}-{name}.md")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    shown = os.path.relpath(path, os.path.join(HERE, ".."))
    return f"Звіт збережено: {shown}", shown


def main():
    mode, tool, target = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        target = validate(tool, target)
        if tool == "save_report":
            summary, text = save_report(mode)
            print(json.dumps({"ok": True, "title": REPORT_TOOLS[tool], "summary": summary, "text": text}, ensure_ascii=False))
            return
        if mode == "incognito":
            require_vpn()
        if tool == "batch":
            title = REPORT_TOOLS[tool]
            summary, text, full = run_batch(batch_targets(target))
            remember(title, target, summary, full, mode)
            print(json.dumps({"ok": True, "title": title, "summary": summary, "text": short(text)}, ensure_ascii=False))
        elif tool in WEB_TOOLS:
            title, data = WEB_TOOLS[tool][1], run_web(tool, target)
            print(json.dumps({"ok": True, "title": title, "text": short(osint_report.web_text(tool, data))}, ensure_ascii=False))
        else:
            title = OSINT[tool][2]
            summary, text = osint_report.render(tool, run_osint(tool, target))
            remember(title, target, summary, text, mode)
            print(json.dumps({"ok": True, "title": title, "summary": summary, "text": short(text)}, ensure_ascii=False))
    except Refused as e:
        print(json.dumps({"ok": False, "title": "Відмова", "text": str(e)}, ensure_ascii=False))
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "title": "Таймаут", "text": "Інструмент не відповів вчасно"}, ensure_ascii=False))
    except OSError:
        print(json.dumps({"ok": False, "title": "Помилка", "text": "Немає зв'язку або сайт не відповів"}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "title": "Помилка", "text": type(e).__name__}, ensure_ascii=False))


if __name__ == "__main__":
    main()
