"""Модулі username і telegram (аналіз публічних профілів і каналів)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..case import Case, csv_text, slug
from ..config import Config
from .. import net
from ..net import session, short_error

NICK_RE = re.compile(r"[\w.\-]{1,64}")


def _sherlock_cmd():
    exe = Path(sys.executable).with_name("sherlock")
    if exe.exists():
        return [str(exe)]
    found = shutil.which("sherlock")
    if found:
        return [found]
    try:
        import sherlock_project  # noqa: F401
        return [sys.executable, "-m", "sherlock_project"]
    except ImportError:
        return None


def username(cfg: Config, case: Case, nick: str, timeout: int = 15) -> dict:
    """Пошук публічних акаунтів з однаковим ніком на багатьох сайтах (Sherlock)."""
    nick = nick.strip().lstrip("@")
    if not NICK_RE.fullmatch(nick):
        raise RuntimeError("Ник содержит недопустимые символы")
    cmd = _sherlock_cmd()
    if not cmd:
        raise RuntimeError("Не установлен sherlock (запустите install.sh ещё раз)")
    out_dir = case.dir / "username"
    out_dir.mkdir(parents=True, exist_ok=True)
    result_txt = out_dir / f"{slug(nick)}.txt"
    run = subprocess.run(
        cmd + [nick, "--timeout", str(timeout), "--print-found", "--no-color",
               "--folderoutput", str(out_dir)] + (["--proxy", net.proxy_url()] if net.routed() else []),
        capture_output=True, text=True, timeout=max(120, timeout * 40),
    )
    found = []
    text = run.stdout + "\n" + run.stderr
    if result_txt.exists():
        text = result_txt.read_text(encoding="utf-8", errors="replace") + "\n" + text
    for line in text.splitlines():
        m = re.search(r"https?://\S+", line)
        if m and "[+]" in line or (m and "sherlock" not in line.lower() and line.strip().startswith("http")):
            url = m.group(0).rstrip(".,)")
            site = re.sub(r"^\[\+\]\s*", "", line.split(":")[0]).strip() if "[+]" in line else url
            if url not in [f["url"] for f in found]:
                found.append({"site": site, "url": url})
    csv_path = case.save_text("username", f"{slug(nick)}_found.csv",
                              csv_text([[f["site"], f["url"]] for f in found], ["site", "url"]),
                              "sherlock", f"аккаунты для @{nick}")
    result = {"nick": nick, "found": len(found), "accounts": found}
    result["note"] = "Одинаковый ник на разных сайтах не доказывает, что это один человек."
    case.add_finding("username", nick, f"Аккаунты по нику @{nick}: {len(found)}", result, [csv_path],
                     status="кандидаты — требуют проверки")
    return result


# ---------------- Telegram ----------------
def _session_file(cfg: Config) -> str:
    """Сесія Telethon дає повний доступ до акаунта: у зашифрованому сховищі справ,
    а без нього — поза проєктом, у закритій папці."""
    if cfg.encrypt_cases:
        folder = Path(cfg.cases_dir).expanduser() / ".private"
    else:
        folder = Path.home() / "Library" / "Application Support" / "osint-kit"
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    return str(folder / "telegram")


def _tg_api(cfg: Config):
    api_id = cfg.key("telegram_api_id")
    api_hash = cfg.key("telegram_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("Нужны telegram_api_id и telegram_api_hash в config.toml (получить на my.telegram.org)")
    return int(api_id), api_hash


def bot_info(cfg: Config, case: Case, token: str) -> dict:
    """Дані про Telegram-бота за його токеном (через офіційний Bot API)."""
    token = token.strip()
    if not re.fullmatch(r"\d{6,}:[\w\-]{30,}", token):
        raise RuntimeError("Это не похоже на токен бота (формат 123456:AA...)")
    s = session(cfg.user_agent, retries=1)
    base = f"https://api.telegram.org/bot{token}"
    me = s.get(f"{base}/getMe", timeout=cfg.timeout)
    if me.status_code == 401:
        raise RuntimeError("Токен недействителен или отозван")
    me.raise_for_status()
    info = me.json().get("result", {})
    result = {"bot": info}
    try:
        upd = s.get(f"{base}/getUpdates", params={"limit": 20, "timeout": 0}, timeout=cfg.timeout)
        if upd.ok:
            chats = {}
            for u in upd.json().get("result", []):
                for key in ("message", "channel_post", "edited_message", "my_chat_member"):
                    chat = (u.get(key) or {}).get("chat")
                    if chat:
                        chats[chat["id"]] = {k: chat.get(k) for k in
                                             ("id", "title", "type", "username") if chat.get(k) is not None}
            result["recent_chats"] = list(chats.values())
            result["updates_count"] = len(upd.json().get("result", []))
    except Exception as exc:  # noqa: BLE001
        result["updates_error"] = short_error(exc)
    # коментар: токени ботів зазвичай знаходять у фішингових наборах і вредоносі,
    # що відправляє крадені дані у Telegram — getMe показує, що це за бот.
    out = case.save_json("telegram", f"bot_{info.get('username', 'unknown')}.json", result,
                         "api.telegram.org", "данные бота")
    case.add_finding("telegram", info.get("username") or token[:10], f"Telegram-бот @{info.get('username')}",
                     {"bot": info, "chats": result.get("recent_chats")}, [out])
    return result


def _no_telegram_account_over_tor() -> None:
    if net.routed():
        raise RuntimeError("Telegram через вашу учётную запись раскрывает вас по номеру телефона, Tor тут не поможет — "
                           "в режиме Tor отключено. Осознанно: osint route set direct")


def channel(cfg: Config, case: Case, target: str, limit: int = 200) -> dict:
    """Публічна інформація про канал чи групу: опис, кількість учасників, останні повідомлення."""
    _no_telegram_account_over_tor()
    try:
        from telethon.sync import TelegramClient
        from telethon.tl import functions, types
    except ImportError as exc:
        raise RuntimeError("Не установлен telethon (запустите install.sh ещё раз)") from exc

    api_id, api_hash = _tg_api(cfg)
    handle = target.strip().lstrip("@")
    handle = re.sub(r"^https?://t\.me/(s/)?", "", handle).strip("/")
    session_file = _session_file(cfg)
    result: dict = {"target": handle}

    with TelegramClient(session_file, api_id, api_hash) as client:
        if not client.is_user_authorized():
            raise RuntimeError(
                "Нужен вход в Telegram. Один раз выполните: osint telegram login — "
                "дальше сессия сохранится.")
        entity = client.get_entity(handle)
        full = client(functions.channels.GetFullChannelRequest(channel=entity)) if hasattr(entity, "broadcast") else None
        result["info"] = {
            "id": entity.id,
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
            "type": "channel" if getattr(entity, "broadcast", False) else "group",
            "verified": getattr(entity, "verified", None),
            "scam": getattr(entity, "scam", None),
            "participants": getattr(full.full_chat, "participants_count", None) if full else None,
            "about": getattr(full.full_chat, "about", None) if full else None,
            "created": str(getattr(entity, "date", "")),
        }
        messages = []
        for msg in client.iter_messages(entity, limit=limit):
            if not msg.text and not msg.media:
                continue
            messages.append({
                "id": msg.id, "date": str(msg.date), "views": msg.views,
                "forwards": msg.forwards,
                "from": str(getattr(msg, "post_author", "") or ""),
                "text": (msg.text or "")[:1000],
                "has_media": bool(msg.media),
            })
    result["messages_saved"] = len(messages)
    fields = ["id", "date", "views", "forwards", "from", "text", "has_media"]
    csv_path = case.save_text("telegram", f"channel_{slug(handle)}.csv",
                              csv_text([[m[k] for k in fields] for m in messages], fields, bom=True),
                              f"t.me/{handle}", "сообщения канала")
    json_path = case.save_json("telegram", f"channel_{slug(handle)}.json",
                               result | {"messages": messages}, f"t.me/{handle}", "данные канала")
    case.add_finding("telegram", handle, f"Telegram-канал: {result['info'].get('title') or handle}",
                     result["info"] | {"messages_saved": len(messages)}, [csv_path, json_path])
    return result


def tg_login(cfg: Config) -> dict:
    """Одноразовий вхід у Telegram для роботи модуля channel."""
    _no_telegram_account_over_tor()
    try:
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Не установлен telethon (запустите install.sh ещё раз)") from exc
    api_id, api_hash = _tg_api(cfg)
    session_file = _session_file(cfg)
    with TelegramClient(session_file, api_id, api_hash) as client:
        me = client.get_me()
        return {"ok": True, "user": {"id": me.id, "username": me.username, "phone": me.phone}}
