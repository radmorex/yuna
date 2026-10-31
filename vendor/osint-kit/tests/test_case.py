"""Папка справи, журнал доказів і звіт."""

import hashlib
import json

from osint_kit.case import Case, sha256_file, slug


def test_slug():
    assert slug("https://example.com/a b?c=1") == "example.com_a_b_c_1"
    assert slug("Справа Іванова") == "Справа_Іванова"
    assert slug("../../etc") == "etc"
    assert slug("") == "item"
    assert len(slug("x" * 200)) == 60


def test_case_name_cannot_escape_root(tmp_path):
    case = Case(tmp_path, "../../outside")
    assert case.dir.parent == tmp_path


def test_path_does_not_overwrite(tmp_path):
    case = Case(tmp_path, "c")
    first = case.save_text("web", "a.txt", "1")
    second = case.save_text("web", "a.txt", "2")
    third = case.save_text("web", "a.txt", "3")
    assert len({first, second, third}) == 3
    assert first.read_text() == "1" and second.read_text() == "2" and third.read_text() == "3"


def test_register_hashes_file(tmp_path):
    case = Case(tmp_path, "c")
    path = case.save_bytes("media", "x.bin", b"\x00\x01data", source="src", note="n")
    [entry] = case.evidence()
    assert entry["sha256"] == hashlib.sha256(b"\x00\x01data").hexdigest() == sha256_file(path)
    assert entry["size"] == 6
    assert entry["file"] == "media/x.bin"
    assert entry["source"] == "src" and entry["note"] == "n"


def test_broken_log_lines_are_skipped(tmp_path):
    case = Case(tmp_path, "c")
    case.add_finding("m", "t", "ok", {"a": 1})
    with case.findings_log.open("a") as fh:
        fh.write("{битий рядок\n\n")
    case.add_finding("m", "t", "ok2", {"a": 2})
    assert [f["title"] for f in case.findings()] == ["ok", "ok2"]


def test_report_escapes_html_and_embeds_images(tmp_path):
    case = Case(tmp_path, "c")
    png = case.save_bytes("web", "shot.png", b"\x89PNG fake")
    case.add_finding("web", "<b>target</b>", "<script>alert(1)</script>", {"x": "<img onerror=1>"}, [png])
    md, html = case.build_report()

    page = html.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img onerror" not in page
    assert "<img src='web/shot.png'" in page
    assert hashlib.sha256(b"\x89PNG fake").hexdigest() in page

    text = md.read_text(encoding="utf-8")
    assert "Находок: 1 · Файлов-доказательств: 1" in text
    assert "`web/shot.png`" in text
    assert json.dumps({"x": "<img onerror=1>"}, ensure_ascii=False, indent=2) in text


def test_csv_text_neutralizes_formulas():
    from osint_kit.case import csv_text

    out = csv_text([["=HYPERLINK(\"http://evil\")", "+380671234567", "-2+3", "@SUM(A1)", "звичайний", 5]],
                   ["a", "b", "c", "d", "e", "f"], bom=True)
    assert out.startswith("﻿a,b,c,d,e,f\n")
    assert out.splitlines()[1] == "\"'=HYPERLINK(\"\"http://evil\"\")\",'+380671234567,'-2+3,'@SUM(A1),звичайний,5"


def test_ids_link_findings_to_evidence(tmp_path):
    case = Case(tmp_path, "c")
    a = case.save_text("web", "a.html", "<p>a</p>", "https://a.test")
    b = case.save_bytes("web", "b.png", b"png")
    fid = case.add_finding("web", "a.test", "Захоплення", {"x": 1}, [a, b, tmp_path / "outside.txt"], status="перевірено")
    case.add_finding("search", "q", "Без файлів", [])
    assert fid == "F1"
    f1, f2 = case.findings()
    assert (f1["id"], f2["id"]) == ("F1", "F2")
    assert f1["evidence"] == ["E1", "E2"] and f1["status"] == "перевірено"
    assert f2["evidence"] == [] and "status" not in f2
    assert [e["id"] for e in case.evidence()] == ["E1", "E2"]


def test_legacy_entries_get_ids_by_line(tmp_path):
    case = Case(tmp_path, "c")
    case.findings_log.write_text('{"title": "старий"}\n\n{битий\n{"title": "ще старий"}\n', encoding="utf-8")
    assert [(f["id"], f["title"]) for f in case.findings()] == [("F1", "старий"), ("F3", "ще старий")]
    assert case.add_finding("m", "t", "новий", {}) == "F4"


# ---------- удаление и заметки ----------
import pytest

from osint_kit.case import delete_case


def test_delete_evidence_keeps_record_and_ids(tmp_path):
    c = Case(tmp_path, "d")
    a = c.save_text("web", "a.txt", "aaa")
    c.save_text("web", "b.txt", "bbb")
    with c.evidence_log.open("a") as fh:
        fh.write("{битая строка\n")  # E3 — повреждённая, должна остаться на месте
    c.add_finding("web", "x", "t", {}, [a])
    entry = c.delete_evidence("E1", "не нужно")
    assert entry["file_removed"] and not a.exists()
    ev = c.evidence()
    assert [e["id"] for e in ev] == ["E1", "E2"] and ev[0]["deleted"] and ev[0]["deleted_reason"] == "не нужно"
    assert ev[0]["sha256"] == hashlib.sha256(b"aaa").hexdigest()  # что было — видно и после удаления
    assert "{битая строка" in c.evidence_log.read_text()
    assert c.findings()[0]["evidence"] == ["E1"]
    assert c.register(c.save_bytes("web", "c.txt", b"c").with_name("c.txt"))["id"] == "E5"  # нумерация не сдвинулась
    with pytest.raises(RuntimeError, match="уже удалён"):
        c.delete_evidence("E1")
    with pytest.raises(RuntimeError, match="не найдено"):
        c.delete_evidence("E99")
    with pytest.raises(RuntimeError, match="не найдено"):
        c.delete_evidence("E3")  # битая строка — не доказательство


def test_delete_evidence_never_touches_outside_or_shared_files(tmp_path):
    c = Case(tmp_path / "cases", "d")
    original = tmp_path / "фото_пользователя.jpg"
    original.write_bytes(b"jpg")
    c.register(original, "локальный файл")  # media exif регистрирует оригинал вне дела
    inside = c.save_text("web", "a.txt", "a")
    c.register(inside, "повторно")
    assert c.delete_evidence("E1")["file_removed"] is False and original.exists()
    assert c.delete_evidence("E2")["file_removed"] is False and inside.exists()  # нужен E3
    assert c.delete_evidence("E3")["file_removed"] is True and not inside.exists()


def test_notes(tmp_path):
    c = Case(tmp_path, "d")
    assert c.notes() == []
    meta = c.write_note("Хронология дня", "строка 1\nстрока 2", new=True)
    assert meta["name"] == "Хронология_дня.txt" and meta["size"] == len("строка 1\nстрока 2".encode())
    assert c.read_note("Хронология_дня.txt") == "строка 1\nстрока 2"
    with pytest.raises(RuntimeError, match="уже есть"):
        c.write_note("Хронология дня", "", new=True)
    c.write_note("Хронология_дня.txt", "правка")
    assert c.read_note("Хронология_дня.txt") == "правка"
    assert c.write_note("план.md", "# план")["name"] == "план.md"
    assert [n["name"] for n in c.notes()] == ["план.md", "Хронология_дня.txt"]  # по алфавиту без регистра
    assert c.write_note("../../evil", "x")["name"] == "evil.txt"  # имя не выходит из notes/
    assert (c.notes_dir / "evil.txt").exists() and not (tmp_path / "evil.txt").exists()
    for bad in ("", "  ", "..."):
        with pytest.raises(RuntimeError, match="имя"):
            c.write_note(bad, "x")
    with pytest.raises(RuntimeError, match="больше"):
        c.write_note("big", "x" * 2_000_001)
    secret = tmp_path / "secret.txt"
    secret.write_text("секрет")
    (c.notes_dir / "link.txt").symlink_to(secret)
    assert "link.txt" not in [n["name"] for n in c.notes()]
    for fn in (lambda: c.read_note("link.txt"), lambda: c.write_note("link", "x"), lambda: c.delete_note("link.txt")):
        with pytest.raises(RuntimeError, match="не найден"):
            fn()
    assert secret.read_text() == "секрет"
    c.delete_note("план.md")
    assert "план.md" not in [n["name"] for n in c.notes()]
    with pytest.raises(RuntimeError, match="не найден"):
        c.delete_note("план.md")
    assert c.evidence() == []  # заметки — не доказательства


def test_delete_case(tmp_path):
    root = tmp_path / "cases"
    Case(root, "d").save_text("web", "a.txt", "a")
    Case(root, "keep")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    for bad in ("нет", "link", "../outside", ""):
        with pytest.raises(RuntimeError, match="не найдено"):
            delete_case(root, bad)
    assert outside.exists()
    delete_case(root, "d")
    assert sorted(p.name for p in root.iterdir()) == ["keep", "link"]
