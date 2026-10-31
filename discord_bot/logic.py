"""Чистая логика Discord-адаптера. НЕ импортирует discord — тестируется stdlib unittest."""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

DISCORD_MAX_LEN = 2000
MAX_BRIDGE_TEXT = 4000

DEFAULT_BOT_NAMES = ("юна", "yuna")


def bot_names_from_env(env) -> tuple:
    """Имена из BOT_NAME и BOT_ALIASES (те же переменные, что читает ядро)."""
    raw = [env.get("BOT_NAME", "")] + env.get("BOT_ALIASES", "").split(",")
    names = tuple(n.strip().lower() for n in raw if n.strip())
    return names or DEFAULT_BOT_NAMES

# Имя бота в начале сообщения, затем пунктуация/пробел+конец либо пунктуация.
# «Юна, привет», «юна! как дела», «Yuna: hi», «Юна» (одно слово).
_PUNCT = r"[,，.!?:;—–\-…]"


def _trigger_re(names: Iterable[str]) -> re.Pattern:
    alt = "|".join(re.escape(n) for n in names if n)
    return re.compile(
        rf"^\s*(?:@)?(?:{alt})(?:\s*{_PUNCT}+|\s*$)\s*",
        re.IGNORECASE | re.UNICODE,
    )


def should_respond(text: str, mentioned: bool, bot_names: Iterable[str] = DEFAULT_BOT_NAMES) -> bool:
    """True, если сообщение адресовано Юне: упоминание бота или «Юна,»/«Yuna!» в начале."""
    if mentioned:
        return True
    if not text:
        return False
    names = tuple(bot_names) or DEFAULT_BOT_NAMES
    return bool(_trigger_re(names).match(text))


_MENTION_RE = re.compile(r"<@!?\d+>")


def strip_trigger(text: str, bot_names: Iterable[str] = DEFAULT_BOT_NAMES) -> str:
    """Убирает обращение («Юна,») и упоминания бота <@id> из начала, возвращает суть."""
    if not text:
        return ""
    names = tuple(bot_names) or DEFAULT_BOT_NAMES
    s = text.strip()
    # Ведущие упоминания
    while True:
        m = _MENTION_RE.match(s)
        if not m:
            break
        s = s[m.end():].lstrip(" ,:;!.").strip()
    m = _trigger_re(names).match(s)
    if m:
        s = s[m.end():]
    return s.strip()


def is_admin(user_id, role_ids: Iterable, admin_user_ids: Iterable, admin_role_ids: Iterable) -> bool:
    """Админ = id пользователя в списке ИЛИ одна из его ролей в списке админ-ролей."""
    uid = str(user_id).strip() if user_id is not None else ""
    admins = {str(x).strip() for x in admin_user_ids if str(x).strip()}
    if uid and uid in admins:
        return True
    aroles = {str(x).strip() for x in admin_role_ids if str(x).strip()}
    if not aroles:
        return False
    return any(str(r).strip() in aroles for r in (role_ids or ()))


class RateLimiter:
    """Скользящее окно на пользователя: не более `max_events` за `window` секунд."""

    def __init__(self, max_events: int = 3, window: float = 30.0, clock: Callable[[], float] = time.monotonic):
        if max_events < 1 or window <= 0:
            raise ValueError("max_events >= 1 и window > 0")
        self.max_events = max_events
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque] = {}

    def allow(self, key) -> bool:
        now = self._clock()
        k = str(key)
        q = self._hits.setdefault(k, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        return True

    def reset(self, key=None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(str(key), None)


def mask_token(s: Optional[str]) -> str:
    """Маскирует секрет для логов: оставляет максимум 4 последних символа."""
    if not s:
        return "<пусто>"
    s = str(s)
    if len(s) <= 8:
        return "****"
    return "****" + s[-4:]


def mask_url_token(url: str) -> str:
    """Заменяет значение token=... в URL на маску."""
    return re.sub(r"(token=)[^&\s]+", lambda m: m.group(1) + "****", url)


def build_bridge_event(*, user: str, text: str, is_admin: bool, channel_id, message_id, mentioned: bool,
                       user_id=None) -> dict:
    """Событие бот → core. userId — неизменяемый ID для лимитов в core."""
    ev = {
        "type": "chat",
        "platform": "discord",
        "user": str(user)[:64],
        "text": str(text)[:MAX_BRIDGE_TEXT],
        "isAdmin": bool(is_admin),
        "channelId": str(channel_id),
        "messageId": str(message_id),
        "mentioned": bool(mentioned),
    }
    if user_id is not None:
        ev["userId"] = str(user_id)
    return ev


@dataclass
class BridgeReply:
    channel_id: str
    reply_to_message_id: Optional[str]
    text: str
    audio_url: Optional[str] = None


class BridgeMessageError(ValueError):
    pass


_SNOWFLAKE = re.compile(r"^\d{1,25}$")


def parse_bridge_message(raw) -> BridgeReply:
    """Валидирует сообщение core → бот. Принимает str/bytes/dict. Бросает BridgeMessageError."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="strict")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BridgeMessageError(f"невалидный JSON: {e}") from e
    else:
        data = raw
    if not isinstance(data, dict):
        raise BridgeMessageError("ожидался JSON-объект")
    if data.get("type") != "reply":
        raise BridgeMessageError(f"неизвестный type: {data.get('type')!r}")
    ch = data.get("channelId")
    if not isinstance(ch, (str, int)) or not _SNOWFLAKE.match(str(ch)):
        raise BridgeMessageError("channelId должен быть Discord snowflake")
    rid = data.get("replyToMessageId")
    if rid is not None:
        if not isinstance(rid, (str, int)) or not _SNOWFLAKE.match(str(rid)):
            raise BridgeMessageError("replyToMessageId должен быть snowflake или null")
        rid = str(rid)
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise BridgeMessageError("text обязателен и не пуст")
    audio = data.get("audioUrl")
    if audio is not None:
        if not isinstance(audio, str):
            raise BridgeMessageError("audioUrl должен быть строкой или null")
        if audio == "":
            audio = None
        elif not (audio.startswith("/audio/") or audio.startswith("http://") or audio.startswith("https://")):
            raise BridgeMessageError("audioUrl должен быть /audio/... или http(s) URL")
    return BridgeReply(channel_id=str(ch), reply_to_message_id=rid, text=text, audio_url=audio)


def resolve_audio_url(core_url: str, audio_url: str) -> Optional[str]:
    """Разрешает audioUrl относительно CORE_URL. Абсолютные URL принимаются только на хост ядра
    (защита от SSRF: бот не ходит на произвольные адреса по команде из bridge)."""
    if not audio_url:
        return None
    base = urlsplit(core_url)
    if audio_url.startswith("/"):
        if not re.match(r"^/audio/[A-Za-z0-9_\-]+\.wav$", audio_url):
            return None
        return urlunsplit((base.scheme, base.netloc, audio_url, "", ""))
    u = urlsplit(audio_url)
    if u.scheme in ("http", "https") and u.netloc == base.netloc and u.path.startswith("/audio/"):
        return audio_url
    return None


def chunk_text(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """Режет текст на куски ≤ limit, стараясь по переводу строки / пробелу."""
    if limit < 1:
        raise ValueError("limit >= 1")
    text = text or ""
    out: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        piece = text[:cut].rstrip()
        if piece:
            out.append(piece)
        text = text[cut:].lstrip()
    if text.strip():
        out.append(text)
    return out


def bridge_ws_url(core_url: str, token: str) -> str:
    """http://host:port → ws://host:port/bridge?token=..."""
    u = urlsplit(core_url)
    scheme = "wss" if u.scheme == "https" else "ws"
    return urlunsplit((scheme, u.netloc, "/bridge", "token=" + quote(token or "", safe=""), ""))


def backoff_delays(base: float = 1.0, cap: float = 30.0):
    """Генератор задержек экспоненциального backoff: 1, 2, 4, ... cap."""
    d = base
    while True:
        yield d
        d = min(cap, d * 2)


# ---------- .env ----------

def parse_env(text: str) -> dict:
    """Минимальный парсер .env: KEY=VALUE, комментарии #, кавычки '…'/"…", префикс export."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        else:
            # инлайн-комментарий только после пробела
            v = re.split(r"\s+#", v, maxsplit=1)[0].rstrip()
        out[k] = v
    return out


def load_env_file(path: str, environ=None) -> dict:
    """Читает .env и дополняет environ (существующие переменные окружения важнее)."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8") as f:
            vals = parse_env(f.read())
    except FileNotFoundError:
        vals = {}
    for k, v in vals.items():
        environ.setdefault(k, v)
    return vals


def parse_id_list(s: Optional[str]) -> set:
    """«123, 456 789» → {"123","456","789"} (только цифры)."""
    if not s:
        return set()
    return {p for p in re.split(r"[\s,;]+", s.strip()) if p.isdigit()}


def env_flag(s: Optional[str]) -> bool:
    return str(s or "").strip().lower() in ("1", "true", "yes", "on", "да")


@dataclass
class BotConfig:
    token: str = ""
    admin_user_ids: set = field(default_factory=set)
    admin_role_ids: set = field(default_factory=set)
    channel_ids: set = field(default_factory=set)
    voice_enabled: bool = False
    voice_channel_id: Optional[str] = None
    core_url: str = "http://127.0.0.1:8787"
    bridge_token: str = ""

    @classmethod
    def from_env(cls, env) -> "BotConfig":
        vc = (env.get("DISCORD_VOICE_CHANNEL_ID") or "").strip()
        return cls(
            token=(env.get("DISCORD_TOKEN") or "").strip(),
            admin_user_ids=parse_id_list(env.get("DISCORD_ADMIN_USER_IDS")),
            admin_role_ids=parse_id_list(env.get("DISCORD_ADMIN_ROLE_IDS")),
            channel_ids=parse_id_list(env.get("DISCORD_CHANNEL_IDS")),
            voice_enabled=env_flag(env.get("DISCORD_VOICE_ENABLED")),
            voice_channel_id=vc if vc.isdigit() else None,
            core_url=(env.get("CORE_URL") or "http://127.0.0.1:8787").strip().rstrip("/"),
            bridge_token=(env.get("BRIDGE_TOKEN") or "").strip(),
        )

    def channel_allowed(self, channel_id) -> bool:
        return not self.channel_ids or str(channel_id) in self.channel_ids

    def __repr__(self) -> str:  # никогда не печатаем секреты
        return (f"BotConfig(token={mask_token(self.token)}, channels={sorted(self.channel_ids)}, "
                f"voice={self.voice_enabled}, voice_channel={self.voice_channel_id}, core_url={self.core_url}, "
                f"bridge_token={mask_token(self.bridge_token)})")
