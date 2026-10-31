"""Быстрые функции: Rust-ядро deflix_core, если собрано (core/, maturin), иначе Python-фолбэк.

Поведение обеих реализаций одинаковое — это проверяют тесты (tests/test_core.py).
"""

from __future__ import annotations

import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    if os.environ.get("OSINT_NO_RUST"):
        raise ImportError
    import deflix_core as _rs
except ImportError:
    _rs = None

BACKEND = "rust" if _rs else "python"


def py_sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def py_sha256_files(paths: list[str]) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:  # hashlib отпускает GIL
        return dict(zip(paths, pool.map(py_sha256_file, paths)))


# Границы слов \b — ASCII (как в ядре): буквы кириллицы и диакритики не считаются частью слова.
_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
_IPV4 = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", re.ASCII)
_HEX = {n: re.compile(rf"\b[A-Fa-f0-9]{{{k}}}\b", re.ASCII) for n, k in (("md5", 32), ("sha1", 40), ("sha256", 64))}


def _standalone(text: str, start: int, end: int) -> bool:
    """IP не часть длинной цепочки через точку (номер версии 1.2.3.4.5, OID)."""
    rest = text[end:end + 2]
    return not (start > 0 and text[start - 1] == ".") and not (len(rest) == 2 and rest[0] == "." and rest[1] in "0123456789")


def py_extract_iocs(text: str) -> dict[str, list[str]]:
    urls = {t for t in (m.rstrip(".,;:!?") for m in _URL.findall(text)) if len(t) > len("http://")}
    out = {
        "urls": urls,
        "emails": {e.lower() for e in _EMAIL.findall(text)},
        "ipv4": {m[0] for m in _IPV4.finditer(text) if all(int(o) <= 255 for o in m[0].split("."))
                 and _standalone(text, m.start(), m.end())},
    }
    out |= {n: {h.lower() for h in rx.findall(text)} for n, rx in _HEX.items()}
    return {k: sorted(v) for k, v in out.items()}


def sha256_file(path: Path | str) -> str:
    return _rs.sha256_file(str(path)) if _rs else py_sha256_file(path)


def sha256_files(paths: list[Path | str]) -> dict[str, str]:
    """{str(путь): hex} — параллельно."""
    names = [str(p) for p in paths]
    return _rs.sha256_files(names) if _rs else py_sha256_files(names)


def extract_iocs(text: str) -> dict[str, list[str]]:
    return _rs.extract_iocs(text) if _rs else py_extract_iocs(text)
