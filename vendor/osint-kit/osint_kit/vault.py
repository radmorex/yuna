"""Зашифроване сховище справ: образ диска macOS (AES-256), пароль — у Keychain.

Образ підключається на місце папки справ (cases_dir), тож усі модулі й зовнішні
програми (Chromium, ffmpeg, yt-dlp) пишуть у нього як у звичайну папку.
Після `osint case lock` на диску лишається лише шифротекст.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

KEYCHAIN_SERVICE = "osint-kit"
KEYCHAIN_ACCOUNT = "cases-vault"
DEFAULT_IMAGE = Path.home() / "Library" / "Application Support" / "osint-kit" / "cases.sparsebundle"
MAX_SIZE = "200g"  # sparsebundle росте за потребою, це лише стеля
IGNORED = {".DS_Store"}


def _password(create: bool = False) -> str:
    import keyring

    pw = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    if pw is None and create:
        pw = secrets.token_urlsafe(32)
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, pw)
    if pw is None:
        raise RuntimeError(f"Пароль хранилища дел не найден в Keychain ({KEYCHAIN_SERVICE} / {KEYCHAIN_ACCOUNT}). "
                           "Без него дела не открыть.")
    return pw


def _hdiutil(args: list[str], password: str | None = None) -> str:
    if not shutil.which("hdiutil"):
        raise RuntimeError("Шифрованное хранилище работает только на macOS (hdiutil). "
                           "Чтобы выключить: encrypt_cases = false в config.toml")
    run = subprocess.run(["hdiutil", *args], input=None if password is None else password + "\0",
                         capture_output=True, text=True, timeout=300)
    if run.returncode != 0:
        raise RuntimeError(f"hdiutil {args[0]}: {(run.stderr or run.stdout).strip()}")
    return run.stdout


def is_open(mountpoint: Path) -> bool:
    return os.path.ismount(mountpoint)


def _attach(image: Path, mountpoint: Path, password: str) -> None:
    mountpoint.mkdir(parents=True, exist_ok=True)
    _hdiutil(["attach", str(image), "-stdinpass", "-mountpoint", str(mountpoint), "-nobrowse", "-owners", "on"],
             password)
    (mountpoint / ".metadata_never_index").touch(exist_ok=True)  # Spotlight не індексує вміст справ


def _plain_files(mountpoint: Path) -> list[Path]:
    if not mountpoint.is_dir() or is_open(mountpoint):
        return []
    return [p for p in mountpoint.iterdir() if p.name not in IGNORED]


def _move_into_vault(image: Path, mountpoint: Path, password: str) -> int:
    """Переносить незашифровані справи з папки всередину сховища."""
    items = _plain_files(mountpoint)
    if not items:
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="osint-vault-"))
    try:
        _attach(image, tmp, password)
        try:
            for item in items:
                if (tmp / item.name).exists():
                    raise RuntimeError(f"В хранилище уже есть '{item.name}' — перенесите незашифрованную копию вручную")
                shutil.move(str(item), str(tmp / item.name))
        finally:
            _hdiutil(["detach", str(tmp)])
    finally:
        tmp.rmdir()
    return len(items)


def open_vault(mountpoint: Path, image: Path = DEFAULT_IMAGE) -> dict | None:
    """Відкриває сховище на місці папки справ; за першого запуску створює його.
    Повертає None, якщо сховище вже відкрите."""
    mountpoint = Path(mountpoint).expanduser()
    if is_open(mountpoint):
        return None
    created = not image.exists()
    password = _password(create=created)
    if created:
        image.parent.mkdir(parents=True, exist_ok=True)
        _hdiutil(["create", "-size", MAX_SIZE, "-type", "SPARSEBUNDLE", "-fs", "APFS", "-encryption", "AES-256",
                  "-stdinpass", "-volname", "osint-cases", str(image)], password)
    moved = _move_into_vault(image, mountpoint, password)
    _attach(image, mountpoint, password)
    return {"created": created, "moved_into_vault": moved, "image": str(image)}


def lock(mountpoint: Path) -> bool:
    mountpoint = Path(mountpoint).expanduser()
    if not is_open(mountpoint):
        return False
    _hdiutil(["detach", str(mountpoint)])
    return True
