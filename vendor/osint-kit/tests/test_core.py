"""Rust-ядро и Python-фолбэк дают одинаковый результат; без ядра всё работает."""

import hashlib
import json
import os
import random
import subprocess
import sys

import pytest

from osint_kit import core

rs = pytest.importorskip("deflix_core", reason="Rust-ядро не собрано (./install.sh)") if not os.environ.get("OSINT_NO_RUST") else None

SAMPLE = """Пишіть: Ivan.Petrenko@Mail.TEST, a@b.co; сайт https://uzhnu.edu.ua/uk/news/1?x=2. і (http://a.test/b),
HTTP://UPPER.TEST/x! 999.1.1.1 10.0.0.255 1.2.3.4.5 256.0.0.1 192.168.1.1/24
md5 D41D8CD98F00B204E9800998ECF8427E sha1 da39a3ee5e6b4b0d3255bfef95601890afd80709
sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 не хеш: abcd1234 http:// https://x"""


def test_extract_iocs_sample():
    got = core.py_extract_iocs(SAMPLE)
    assert got["emails"] == ["a@b.co", "ivan.petrenko@mail.test"]
    assert got["urls"] == ["HTTP://UPPER.TEST/x", "http://a.test/b", "https://uzhnu.edu.ua/uk/news/1?x=2", "https://x"]
    assert got["ipv4"] == ["10.0.0.255", "192.168.1.1"]  # не 999.…, не 256.…, не кусок версии 1.2.3.4.5
    assert got["md5"] == ["d41d8cd98f00b204e9800998ecf8427e"]
    assert got["sha1"] == ["da39a3ee5e6b4b0d3255bfef95601890afd80709"]
    assert got["sha256"] == ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]


@pytest.mark.skipif(rs is None, reason="без Rust-ядра")
def test_rust_matches_python_on_sample():
    assert rs.extract_iocs(SAMPLE) == core.py_extract_iocs(SAMPLE)


@pytest.mark.skipif(rs is None, reason="без Rust-ядра")
def test_rust_matches_python_on_random_text():
    rnd = random.Random(7)
    alphabet = list("abcdefABCDEF0123456789.:/@-_%+ \n,;!?()<>\"'[]{}") + ["http://", "https://", "HTTP://", "@", "ї", "é", "٣", "\u0306", "\u00a0",
                                                                    "\u2028", "1.2.3.4", "_"]
    for _ in range(400):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 300)))
        assert rs.extract_iocs(text) == core.py_extract_iocs(text), text


def test_ascii_word_boundaries():
    # кириллица вплотную к индикатору не мешает его найти (диакритика NFD — тоже)
    got = core.extract_iocs("адреса8.8.8.8, хеш" + "a" * 32 + "и\u0306")
    assert got["ipv4"] == ["8.8.8.8"] and got["md5"] == ["a" * 32]
    assert core.extract_iocs("x8.8.8.8 _" + "a" * 32)["ipv4"] == [] == core.extract_iocs("_" + "a" * 32)["md5"]


def test_sha256_file_and_files(tmp_path):
    paths = []
    for i, body in enumerate([b"", b"abc", os.urandom(3_000_000)]):
        p = tmp_path / f"f{i}"
        p.write_bytes(body)
        paths.append(p)
    expected = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert core.sha256_files(paths) == expected == core.py_sha256_files([str(p) for p in paths])
    assert core.sha256_file(paths[1]) == expected[str(paths[1])] == core.py_sha256_file(paths[1])
    with pytest.raises(OSError):
        core.sha256_files([*paths, tmp_path / "нет"])
    with pytest.raises(OSError):
        core.sha256_file(tmp_path / "нет")


def test_fallback_without_rust():
    code = "from osint_kit import core; print(core.BACKEND, core.extract_iocs('x 8.8.8.8')['ipv4'])"
    out = subprocess.run([sys.executable, "-c", code], env=os.environ | {"OSINT_NO_RUST": "1"},
                         capture_output=True, text=True, check=True).stdout
    assert out.split() == ["python", "['8.8.8.8']"]


def test_cli_ioc_text_and_file(osint, tmp_path, capsys):
    assert osint("ioc", "пишіть a@b.test або 8.8.8.8", case="i") == 0
    out = capsys.readouterr().out
    assert "a@b.test" in out and "8.8.8.8" in out
    f = tmp_path / "лист.txt"
    f.write_text("див. https://a.test/p", encoding="utf-8")
    assert osint("ioc", str(f), case="i") == 0
    findings = osint.findings("i")
    assert [x["module"] for x in findings] == ["ioc", "ioc"]
    assert findings[1]["target"] == "лист.txt" and findings[1]["data"]["urls"] == ["https://a.test/p"]
    saved = json.loads((osint.case("i").dir / osint.evidence("i")[1]["file"]).read_text())
    assert saved["urls"] == ["https://a.test/p"]
    assert osint("ioc", "   ", case="i") == 1
    assert osint("ioc", str(tmp_path / "нет.txt"), "--file", case="i") == 1
