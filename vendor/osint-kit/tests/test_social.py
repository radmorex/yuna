"""Модулі username (Sherlock — підмінений скриптом) і Telegram Bot API."""

import sys
import textwrap

from osint_kit.modules import social

TOKEN = "123456789:AAH" + "x" * 32
FAKE_SHERLOCK = textwrap.dedent("""
    import sys
    from pathlib import Path
    nick = sys.argv[1]
    out = Path(sys.argv[sys.argv.index("--folderoutput") + 1])
    print(f"[*] Checking username {nick} on:")
    print(f"[+] GitHub: https://github.com/{nick}")
    print(f"[+] Reddit: https://www.reddit.com/user/{nick}")
    print("[*] Search completed with 2 results")
    (out / f"{nick}.txt").write_text(
        f"https://github.com/{nick}\\nhttps://www.reddit.com/user/{nick}\\nhttps://t.me/{nick}\\nTotal Websites Username Detected On : 3\\n")
""")


def test_username(osint, tmp_path, monkeypatch):
    script = tmp_path / "fake_sherlock.py"
    script.write_text(FAKE_SHERLOCK)
    monkeypatch.setattr(social, "_sherlock_cmd", lambda: [sys.executable, str(script)])

    assert osint("username", "@volunteer_42") == 0
    [finding] = osint.findings()
    accounts = finding["data"]["accounts"]
    assert finding["data"]["nick"] == "volunteer_42"
    assert [a["url"] for a in accounts] == ["https://github.com/volunteer_42", "https://www.reddit.com/user/volunteer_42",
                                            "https://t.me/volunteer_42"]
    csv_text = (osint.case().dir / finding["files"][0]).read_text(encoding="utf-8")
    assert csv_text.startswith("site,url\n") and "t.me/volunteer_42" in csv_text


def test_username_rejects_bad_nick(osint, capsys):
    assert osint("username", "a b;rm -rf") == 1
    assert "недопустимые символы" in capsys.readouterr().out


def _bot_routes(server):
    server.add(server.ext("api.telegram.org", f"/bot{TOKEN}/getMe"),
               {"ok": True, "result": {"id": 123456789, "is_bot": True, "username": "stealer_bot"}})
    server.add(server.ext("api.telegram.org", f"/bot{TOKEN}/getUpdates"), {"ok": True, "result": [
        {"message": {"chat": {"id": -100, "title": "Logs", "type": "supergroup"}}},
        {"channel_post": {"chat": {"id": -100, "title": "Logs", "type": "supergroup"}}},
        {"my_chat_member": {"chat": {"id": 7, "type": "private", "username": "op"}}},
    ]})


def test_bot_info(osint, server):
    _bot_routes(server)
    assert osint("telegram", "bot", TOKEN) == 0
    [finding] = osint.findings()
    assert finding["title"] == "Telegram-бот @stealer_bot"
    assert finding["data"]["chats"] == [{"id": -100, "title": "Logs", "type": "supergroup"},
                                        {"id": 7, "type": "private", "username": "op"}]
    assert TOKEN not in osint.case_text()


def test_bot_invalid_format(osint, server, capsys):
    assert osint("telegram", "bot", "not-a-token") == 1
    assert "токен" in capsys.readouterr().out
    assert not server.requests


def test_bot_revoked(osint, server, capsys):
    server.add(server.ext("api.telegram.org", f"/bot{TOKEN}/getMe"), {"ok": False}, status=401)
    assert osint("telegram", "bot", TOKEN) == 1
    assert "недействителен" in capsys.readouterr().out


def test_bot_error_does_not_print_token(osint, server, capsys):
    server.add(server.ext("api.telegram.org", f"/bot{TOKEN}/getMe"), "forbidden", status=403)
    assert osint("telegram", "bot", TOKEN) == 1
    out = capsys.readouterr().out
    assert "403" in out
    assert TOKEN.split(":")[1] not in out.replace("\n", "")
