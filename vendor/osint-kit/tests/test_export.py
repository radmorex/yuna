"""Экспорт TXT/JSON/CSV/ZIP и импорт с сохранением ID."""

import csv
import json
import zipfile

import pytest

from osint_kit import export
from osint_kit.case import Case


@pytest.fixture
def case(tmp_path):
    c = Case(tmp_path / "cases", "дело-1")
    shot = c.save_bytes("web", "shot.png", b"\x89PNG data", "https://a.test", "скриншот")
    page = c.save_text("web", "page.html", "<p>Привіт</p>", "https://a.test")
    c.add_finding("web", "https://a.test", "Захват страницы", {"title": "Привет", "status": 200}, [shot, page])
    c.add_finding("infra", "a.test", "Домен a.test", {"dns": {"A": ["1.2.3.4"]}, "whois": {"error": "timeout"}})
    c.add_finding("wp", "Олена", "Упоминания: 1", {"places": {"Ужгород": 1}, "latest": [{"date": "2025-01-01"}]},
                  status="требует проверки")
    c.add_finding("scrape", "x", "=формула", {"note": "=HYPERLINK(\"http://evil\")"})
    return c


def test_txt(case):
    text = export.to_txt(export.case_data(case))
    assert text.startswith("ДЕЛО: дело-1\n")
    assert "находок: 4 · файлов-доказательств: 2" in text
    assert "[F1] Захват страницы" in text and "Доказательства: [E1] [E2]" in text
    assert "    title: Привет" in text and "      A: 1.2.3.4" in text
    assert "Статус: требует проверки" in text
    assert "- [F2] whois: timeout" in text  # ошибка источника попала в «Покрытие»
    assert "[E1] web/shot.png" in text and case.evidence()[0]["sha256"] in text


def test_export_all_inside_case(case):
    res = export.export(case)
    assert set(res["files"]) == {"txt", "json", "csv_findings", "csv_evidence", "zip"}
    assert "хранилища" in res["note"] and res["missing_files"] == []
    data = json.loads(open(res["files"]["json"], encoding="utf-8").read())
    assert data["format"] == export.FORMAT and [f["id"] for f in data["findings"]] == ["F1", "F2", "F3", "F4"]

    rows = list(csv.reader(open(res["files"]["csv_findings"], encoding="utf-8-sig")))
    assert rows[0] == export.FINDING_FIELDS
    assert rows[1][6] == "E1 E2" and rows[4][4] == "'=формула"  # формула нейтрализована


def test_zip_contents_and_checksums(case, tmp_path):
    res = export.export(case, fmt="zip", out_dir=tmp_path / "out")
    assert "открытые файлы" in res["note"]
    with zipfile.ZipFile(res["files"]["zip"]) as zf:
        names = set(zf.namelist())
        assert {"дело-1/manifest.json", "дело-1/case.json", "дело-1/report.txt", "дело-1/findings.csv",
                "дело-1/evidence.csv", "дело-1/files/web/shot.png", "дело-1/files/web/page.html"} <= names
        manifest = json.loads(zf.read("дело-1/manifest.json"))
        assert manifest["checksums_sha256"]["files/web/shot.png"] == case.evidence()[0]["sha256"]


def test_roundtrip_zip_keeps_ids_and_files(case, tmp_path):
    res = export.export(case, fmt="zip", out_dir=tmp_path / "out")
    new = Case(tmp_path / "cases", "копия")
    info = export.import_case(new, res["files"]["zip"])
    assert info["files_restored"] == 2 and info["rejected"] == []
    assert [f["id"] for f in new.findings()] == ["F1", "F2", "F3", "F4"]
    assert new.findings()[0]["evidence"] == ["E1", "E2"]
    assert (new.dir / "web" / "shot.png").read_bytes() == b"\x89PNG data"
    assert all(f["imported_from"].startswith("импорт:") for f in new.findings())
    assert new.add_finding("m", "t", "после импорта", {}) == "F5"

    with pytest.raises(RuntimeError, match="не пустое"):
        export.import_case(new, res["files"]["zip"])


def test_import_json_without_files(case, tmp_path):
    res = export.export(case, fmt="json", out_dir=tmp_path / "out")
    new = Case(tmp_path / "cases", "из-json")
    assert export.import_case(new, res["files"]["json"])["files_restored"] == 0
    assert len(new.evidence()) == 2


def test_import_rejects_tampered_and_traversal(case, tmp_path):
    src = export.export(case, fmt="zip", out_dir=tmp_path / "out")["files"]["zip"]
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(evil, "w") as zout:
        for item in zin.infolist():
            body = zin.read(item)
            if item.filename.endswith("shot.png"):
                body = b"tampered"
            zout.writestr(item, body)
        zout.writestr("дело-1/files/../../../escape.txt", b"x")
    new = Case(tmp_path / "cases", "проверка")
    info = export.import_case(new, evil)
    assert {r["reason"] for r in info["rejected"]} == {"контрольная сумма не совпадает", "путь вне дела"}
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "cases" / "escape.txt").exists()
    assert not (new.dir / "web" / "shot.png").exists()


def test_import_unknown_format(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"findings": [], "evidence": []}')
    with pytest.raises(RuntimeError, match="Неизвестный формат"):
        export.import_case(Case(tmp_path, "c"), bad)


def test_cli_export_import(osint):
    assert osint("search", "Олена Коваль", "--kind", "name") == 0
    assert osint("export", "--format", "zip") == 0
    [zip_path] = (osint.case().dir / "export").glob("*.zip")
    assert osint("import", str(zip_path), case="restored") == 0
    assert osint.findings("restored")[0]["title"] == osint.findings()[0]["title"]


def test_export_never_reads_outside_case(tmp_path):
    """Подложенный журнал с ../ не должен затянуть в архив чужой файл."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": export.FORMAT, "version": export.VERSION, "case": "x", "findings": [],
                               "evidence": [{"id": "E1", "file": "../../secret.txt", "sha256": "0", "size": 1},
                                            {"id": "E2", "file": str(secret), "sha256": "0", "size": 1}]}))
    case = Case(tmp_path / "cases", "жертва")
    export.import_case(case, bad)
    res = export.export(case, fmt="zip", out_dir=tmp_path / "out")
    assert [m["id"] for m in res["missing_files"]] == ["E1", "E2"]
    with zipfile.ZipFile(res["files"]["zip"]) as zf:
        assert not any(n.endswith("secret.txt") for n in zf.namelist())
        assert all(b"TOP-SECRET" not in zf.read(n) for n in zf.namelist())


def test_notes_and_deleted_files_survive_export(osint, tmp_path):
    from osint_kit import export as ex
    from pathlib import Path

    from osint_kit.case import Case

    c = osint.case("src")
    c.save_text("web", "a.txt", "a")
    c.save_text("web", "b.txt", "b")
    c.delete_evidence("E1", "лишнее")
    c.write_note("версия", "моя хронология\nстрока 2")
    res = ex.export(c, out_dir=tmp_path)
    txt = Path(res["files"]["txt"]).read_text(encoding="utf-8")
    assert "== Заметка: версия.txt" in txt and "моя хронология" in txt and "Файл удалён:" in txt
    assert res["missing_files"] == [{"id": "E1", "file": "web/a.txt", "reason": "удалён пользователем"}]
    new = Case(osint.cfg().cases_dir, "dst")
    out = ex.import_case(new, Path(res["files"]["zip"]))
    assert out["notes_restored"] == 1 and new.read_note("версия.txt") == "моя хронология\nстрока 2"
    assert new.evidence()[0]["deleted"] and (new.dir / "web" / "b.txt").read_text() == "b"


def test_paste_multiline_value_stays_in_box():
    from osint_kit.export import _lines

    assert _lines({"текст": "строка 1\nстрока 2", "a": 1}, "  │    ") == [
        "  │    текст:", "  │      строка 1", "  │      строка 2", "  │    a: 1"]


def test_times_are_utc_and_labelled(osint):
    import re

    c = osint.case()
    p = c.save_text("web", "x.txt", "x")
    c.add_finding("web", "t", "T", {"a": 1}, [p])
    from osint_kit import export

    text = export.to_paste(export.case_data(c))
    assert re.search(r"F1 · \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", text)
    # имена файлов — тоже UTC, с пометкой Z
    assert re.search(r"\d{8}-\d{6}Z", c.path("web", "x.txt").name)
