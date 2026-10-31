"""Службові команди та помилки CLI."""

import os
import pytest

from osint_kit import cli


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_case_list_and_path(osint, capsys):
    assert osint("case", "list") == 0
    assert osint("search", "x", case="alpha") == 0
    assert osint("search", "y", case="beta") == 0
    capsys.readouterr()
    assert osint("case", "list") == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out
    assert osint("case", "path", case="alpha") == 0
    assert str(osint.cases / "alpha") in capsys.readouterr().out.replace("\n", "")


@pytest.mark.slow
def test_doctor(osint, capsys):
    assert osint("doctor") == 0
    out = capsys.readouterr().out
    assert "dependencies" in out and "api_keys" in out


def test_report_command(osint):
    assert osint("search", "Іван Петренко", "--kind", "name") == 0
    assert osint("report") == 0
    case = osint.case()
    assert (case.dir / "report.md").exists()
    assert "Іван Петренко" in (case.dir / "report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("argv, message", [
    (("infra", "hash", "abc"), "VirusTotal"),
    (("infra", "url", "example.test"), "VirusTotal или urlscan"),
    (("media", "aicheck", "/nonexistent.jpg"), "Hive"),
    (("telegram", "channel", "durov"), "telegram_api_id"),
    (("telegram", "login"), "telegram_api_id"),
])
def test_missing_keys_give_clear_error(osint, capsys, argv, message):
    assert osint(*argv) == 1
    assert message in capsys.readouterr().out


def test_webcheck_requires_url(osint, capsys):
    osint.configure(webcheck=False)
    assert osint("infra", "webcheck", "example.test") == 1
    assert "webcheck_url" in capsys.readouterr().out


def test_redact():
    from osint_kit.net import redact

    token = "123456789:AAH" + "x" * 32
    assert redact(f"https://api.telegram.org/bot{token}/getMe") == "https://api.telegram.org/bot<токен>/getMe"
    assert redact("https://api.test/h?ip=1&key=S3CR3T&x=1") == "https://api.test/h?ip=1&key=<скрыто>&x=1"
    assert redact("?apikey=a&api_key=b&token=c") == "?apikey=<скрыто>&api_key=<скрыто>&token=<скрыто>"
    assert redact("звичайний текст") == "звичайний текст"


def test_cli_case_delete_and_dossier(osint, capsys):
    assert osint("ioc", "a@b.test", case="d") == 0
    assert osint("case", "dossier", case="d") == 0
    out = capsys.readouterr().out
    assert "ДЕЛО .......... d" in out and "a@b.test" in out
    assert osint("case", "delete-file", "e1", case="d") == 0
    assert osint.evidence("d")[0]["deleted"]
    assert osint("case", "delete", case="d") == 1  # без --yes не удаляет
    assert "--yes" in capsys.readouterr().out and (osint.cfg().cases_dir / "d").exists()
    assert osint("case", "delete", "--yes", case="d") == 0
    assert not (osint.cfg().cases_dir / "d").exists()
    for cmd in (["case", "delete", "--yes"], ["case", "dossier"], ["case", "delete-file", "E1"]):
        assert osint(*cmd, case="d") == 1


def test_tool_path_added_for_finder_launch(monkeypatch):
    from osint_kit import config

    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # так окно получает PATH из Finder
    monkeypatch.setattr(config, "TOOL_DIRS", ("/usr/bin", "/tmp"))
    config.ensure_tool_path()
    assert os.environ["PATH"] == "/usr/bin:/bin:/tmp"
