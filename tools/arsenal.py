#!/usr/bin/env python3
"""Мост к поисковым инструментам osint-kit и public-tools.

Запуск: arsenal.py <free|incognito> <инструмент> <цель>. Ответ: JSON {"ok", "title", "text"}.
Только белый список инструментов и проверенные цели, без shell. Поиска людей здесь нет намеренно:
аккаунты по нику, Telegram и запросы по имени/почте/телефону не подключены (правила security-lab).
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

KIT = os.environ.get("OSINT_KIT_DIR", os.path.expanduser("~/osint-kit"))
WEB_KIT = os.environ.get("PUBLIC_TOOLS_DIR", os.path.expanduser("~/public-tools"))
MULLVAD = os.environ.get("MULLVAD_BIN", "/usr/local/bin/mullvad")
CASE = "yuna"
LIMIT = 1800
TIMEOUT = 90

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
    if tool in OSINT:
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
    elif kind == "hash" and not re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", target):
        raise Refused("Потрібен хеш MD5, SHA-1 або SHA-256")
    return target


def short(data):
    """Компактный текст результата: большие списки сокращаем, чтобы панель оставалась читаемой."""
    if isinstance(data, dict) and isinstance(data.get("snapshots"), list):
        snaps = data["snapshots"]
        data = {"знімків": len(snaps), "перший": snaps[0].get("date") if snaps else None, "останній": snaps[-1].get("date") if snaps else None}
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
    return text if len(text) <= LIMIT else text[:LIMIT] + "\n…(скорочено, повністю у справі yuna)"


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
        raise Refused("osint-kit не знайдено")
    # Весь трафик и так идёт через туннель Mullvad, собственный маршрут инструменту не нужен.
    env = {**os.environ, "OSINT_ROUTE": "direct"}
    base = [python, "-m", "osint_kit.cli"]
    argv = base + ["--case", CASE] + OSINT[tool][1] + [target]
    if tool == "dorks":
        argv += ["--kind", "text"]
    out = subprocess.run(argv, cwd=KIT, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    if out.returncode != 0:
        raise Refused("Інструмент завершився з помилкою")
    # Вокруг JSON инструмент может напечатать служебные строки.
    start = out.stdout.find("{")
    try:
        return json.JSONDecoder().raw_decode(out.stdout[start:])[0] if start >= 0 else out.stdout.strip()
    except ValueError:
        return out.stdout.strip()


def run_web(tool, target):
    sys.path.insert(0, os.path.join(WEB_KIT, "python"))
    import public_tools

    if tool == "youtube_first":
        # Для запуска нужно одно видео: компактная строка, которую ядро не должно разбирать как JSON.
        items = public_tools.run("youtube", target).get("items") or []
        if not items:
            raise Refused("Не знайшла відео за цим запитом")
        return "\t".join(str(items[0].get(k, "")).replace("\t", " ") for k in ("id", "title", "author"))
    return public_tools.run(tool, target)


def main():
    mode, tool, target = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        target = validate(tool, target)
        if mode == "incognito":
            require_vpn()
        if tool in WEB_TOOLS:
            title, data = WEB_TOOLS[tool][1], run_web(tool, target)
        else:
            title, data = OSINT[tool][2], run_osint(tool, target)
        print(json.dumps({"ok": True, "title": title, "text": short(data)}, ensure_ascii=False))
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
