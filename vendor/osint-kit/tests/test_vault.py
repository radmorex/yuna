"""Зашифроване сховище справ (справжній образ диска; Keychain підмінений)."""

import shutil
import sys
import types

import pytest

from osint_kit import cli, vault

MARKER = "SECRET-CASE-MARKER-7f3a"
needs_hdiutil = pytest.mark.skipif(not shutil.which("hdiutil"), reason="лише macOS")


@pytest.fixture
def fake_keychain(monkeypatch):
    store = {}
    fake = types.SimpleNamespace(get_password=lambda s, a: store.get((s, a)),
                                 set_password=lambda s, a, p: store.__setitem__((s, a), p))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return store


@pytest.fixture
def vault_paths(tmp_path):
    image, mnt = tmp_path / "cases.sparsebundle", tmp_path / "cases"
    yield image, mnt
    if vault.is_open(mnt):
        vault.lock(mnt)


def test_password_created_once(fake_keychain):
    with pytest.raises(RuntimeError, match="Keychain"):
        vault._password()
    first = vault._password(create=True)
    assert len(first) >= 40 and vault._password() == first == vault._password(create=True)


@pytest.mark.slow
@needs_hdiutil
def test_vault_roundtrip(fake_keychain, vault_paths):
    image, mnt = vault_paths
    (mnt / "old-case").mkdir(parents=True)
    (mnt / "old-case" / "note.txt").write_text(MARKER)

    state = vault.open_vault(mnt, image)
    assert state["created"] and state["moved_into_vault"] == 1
    assert vault.is_open(mnt)
    assert (mnt / "old-case" / "note.txt").read_text() == MARKER
    assert (mnt / ".metadata_never_index").exists()
    assert vault.open_vault(mnt, image) is None  # уже відкрите

    assert vault.lock(mnt) and not vault.is_open(mnt)
    assert list(mnt.iterdir()) == []  # без сховища в папці нічого немає
    for band in image.rglob("*"):
        if band.is_file():
            assert MARKER.encode() not in band.read_bytes()  # на диску лише шифротекст
    assert vault.lock(mnt) is False

    state = vault.open_vault(mnt, image)
    assert state == {"created": False, "moved_into_vault": 0, "image": str(image)}
    assert (mnt / "old-case" / "note.txt").read_text() == MARKER


@pytest.mark.slow
@needs_hdiutil
def test_vault_wrong_password(fake_keychain, vault_paths):
    image, mnt = vault_paths
    vault.open_vault(mnt, image)
    vault.lock(mnt)
    fake_keychain[(vault.KEYCHAIN_SERVICE, vault.KEYCHAIN_ACCOUNT)] = "wrong"
    with pytest.raises(RuntimeError, match="hdiutil attach"):
        vault.open_vault(mnt, image)
    assert not vault.is_open(mnt)


def test_cli_opens_vault_except_on_lock(osint, monkeypatch):
    calls = []
    monkeypatch.setattr(vault, "open_vault", lambda mnt, image: calls.append("open"))
    monkeypatch.setattr(vault, "lock", lambda mnt: calls.append("lock") or True)
    path = osint.tmp / "config.toml"
    path.write_text(path.read_text().replace("encrypt_cases = false", "encrypt_cases = true"))

    assert cli.main(["search", "x"]) == 0
    assert cli.main(["case", "lock"]) == 0
    assert calls == ["open", "lock"]


def test_cli_vault_error_is_reported(osint, monkeypatch, capsys):
    def boom(mnt, image):
        raise RuntimeError("hdiutil attach: Authentication error")

    monkeypatch.setattr(vault, "open_vault", boom)
    path = osint.tmp / "config.toml"
    path.write_text(path.read_text().replace("encrypt_cases = false", "encrypt_cases = true"))
    assert cli.main(["search", "x"]) == 1
    assert "Authentication error" in capsys.readouterr().out
