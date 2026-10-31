"""Завантаження налаштувань з config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

KEY_LABELS = {"shodan": "Shodan", "virustotal": "VirusTotal", "abuseipdb": "AbuseIPDB", "urlscan": "urlscan.io",
              "criminalip": "CriminalIP", "hive": "Hive (ИИ-генерация)", "telegram_api_id": "Telegram api_id",
              "telegram_api_hash": "Telegram api_hash"}
KEY_NAMES = (
    "shodan",
    "virustotal",
    "abuseipdb",
    "urlscan",
    "criminalip",
    "hive",
    "telegram_api_id",
    "telegram_api_hash",
)


DIRECT_FALLBACK = ("ipinfo.io", "internetdb.shodan.io")


@dataclass
class Config:
    cases_dir: Path
    user_agent: str = DEFAULT_UA
    timeout: int = 20
    block_trackers: bool = True
    encrypt_cases: bool = True
    vault_image: Path | None = None
    webcheck_url: str = ""
    libretranslate_url: str = ""
    keys: dict[str, str] = field(default_factory=dict)
    route: str = "tor"   # tor | proxy | direct — как программа выходит в интернет
    proxy: str = ""      # для route = "proxy": socks5h://адрес:порт
    # справочные сервисы, которые блокируют Tor: при 403 — один повтор напрямую (сервис видит ваш IP, цель — нет)
    direct_fallback: tuple[str, ...] = DIRECT_FALLBACK
    ai_url: str = "http://127.0.0.1:11434"  # локальная Ollama — только на этом компьютере
    ai_model: str = "qwen2.5:7b"

    def key(self, name: str) -> str:
        """Ключ з config.toml або змінної середовища OSINT_<NAME>."""
        env = os.environ.get(f"OSINT_{name.upper()}", "").strip()
        return env or str(self.keys.get(name, "")).strip()


TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def ensure_tool_path() -> None:
    """Окно из Finder получает PATH без Homebrew — exiftool, ffmpeg, tor «пропадали», и метаданные
    читались урезанно (Pillow). Дописываем папки Homebrew, если их нет."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    os.environ["PATH"] = os.pathsep.join(parts + [d for d in TOOL_DIRS if d not in parts and Path(d).is_dir()])


def config_path() -> Path:
    env = os.environ.get("OSINT_KIT_CONFIG")
    return Path(env).expanduser() if env else ROOT / "config.toml"


def load() -> Config:
    path = config_path()
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    general = data.get("general", {})
    cases = Path(general.get("cases_dir") or ROOT / "cases").expanduser()
    return Config(
        cases_dir=cases,
        user_agent=general.get("user_agent") or DEFAULT_UA,
        timeout=int(general.get("timeout", 20)),
        block_trackers=bool(general.get("block_trackers", True)),
        encrypt_cases=bool(general.get("encrypt_cases", True)),
        vault_image=Path(general["vault_image"]).expanduser() if general.get("vault_image") else None,
        webcheck_url=str(data.get("services", {}).get("webcheck_url", "")).rstrip("/"),
        libretranslate_url=str(data.get("services", {}).get("libretranslate_url", "")).rstrip("/"),
        keys={k: str(v) for k, v in data.get("keys", {}).items()},
        route=_route(os.environ.get("OSINT_ROUTE") or data.get("network", {}).get("route") or "tor"),
        proxy=str(data.get("network", {}).get("proxy", "")),
        direct_fallback=tuple(str(h).lower() for h in data.get("network", {}).get("direct_fallback", DIRECT_FALLBACK)),
        ai_url=str(data.get("ai", {}).get("url", "http://127.0.0.1:11434")).rstrip("/"),
        ai_model=str(data.get("ai", {}).get("model", "qwen2.5:7b")),
    )


ROUTES = ("tor", "proxy", "direct")


def _route(value: str) -> str:
    value = str(value).strip().lower()
    if value not in ROUTES:
        raise ValueError(f"[network] route: ожидается одно из {', '.join(ROUTES)}")
    return value


def set_value(section: str, key: str, value) -> Path:
    """Записывает одну настройку в config.toml, не трогая остальное (комментарии, другие секции).
    Файл может хранить ключи API — права только владельцу."""
    import json
    import re

    path = config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    literal = ("true" if value else "false") if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)
    line = f"{key} = {literal}"
    sec = re.search(rf"(?m)^\[{re.escape(section)}\]\s*$", text)
    if not sec:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[{section}]\n{line}\n"
    else:
        end = re.search(r"(?m)^\[", text[sec.end():])
        body_end = sec.end() + (end.start() if end else len(text) - sec.end())
        body = text[sec.end():body_end]
        rx = rf"(?m)^{re.escape(key)}\s*=.*$"
        body = re.sub(rx, lambda _m: line, body, count=1) if re.search(rx, body) else "\n" + line + body
        text = text[:sec.end()] + body + text[body_end:]
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def set_route(mode: str) -> Path:
    """Записывает [network] route в config.toml, не трогая остальное."""
    return set_value("network", "route", _route(mode))


def set_key(name: str, value: str) -> Path:
    if name not in KEY_NAMES:
        raise ValueError(f"Неизвестный ключ: {name}")
    return set_value("keys", name, str(value or "").strip())
