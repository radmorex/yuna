"""Экспорт и импорт дела.

TXT — читается без программы; JSON — для повторного импорта (ID сохраняются); CSV — таблицы
находок и доказательств; ZIP — всё вместе плюс файлы-доказательства и контрольные суммы.
Экспортированные файлы — открытые (незашифрованные), если лежат вне хранилища дел.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from . import core
from .case import Case, csv_text, now_iso

FORMAT = "osint-kit-case"
VERSION = 1
FINDING_FIELDS = ["id", "time", "module", "target", "title", "status", "evidence"]
EVIDENCE_FIELDS = ["id", "time", "file", "sha256", "size", "source", "note"]


def case_data(case: Case) -> dict:
    notes = [n | {"text": case.read_note(n["name"])} for n in case.notes()]
    return {"format": FORMAT, "version": VERSION, "case": case.name, "exported_at": now_iso(),
            "findings": case.findings(), "evidence": case.evidence(), "notes": notes}


# ---------------- сводка-паста (вкладка «досье») ----------------
MODULE_TITLES = {
    "web": "САЙТЫ", "scrape": "СТРАНИЦЫ", "infra": "ИНФРАСТРУКТУРА", "media": "ФОТО И ВИДЕО",
    "username": "НИКИ", "telegram": "TELEGRAM", "wp": "ПУБЛИКАЦИИ", "search": "ПОИСКОВЫЕ ЗАПРОСЫ",
    "ioc": "ИНДИКАТОРЫ", "translate": "ПЕРЕВОДЫ", "text": "ТЕКСТ",
}
RULE = "═" * 66
DATA_LINES = 24  # дальше — «ещё N строк», полностью — в карточке находки


def _dots(label: str, value) -> str:
    return f"  {label} {'.' * max(2, 14 - len(label))} {value}"


def _prune(value):
    """Без пустых полей: в пасте важно то, что найдено."""
    if isinstance(value, dict):
        return {k: v for k, v in ((k, _prune(v)) for k, v in value.items()) if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [v for v in (_prune(x) for x in value) if v not in (None, "", [], {})]
    return value


def _finding_block(f: dict, ev: dict, limit: int | None) -> list[str]:
    out = [f"  ┌─ {f['id']} · {str(f.get('time', ''))[:16].replace('T', ' ')} UTC",
           f"  │  {f.get('title', '')}", f"  │  цель: {f.get('target', '—')}"]
    if f.get("status"):
        out.append(f"  │  статус: {f['status']}")
    data = _prune(f.get("data"))
    body = _lines(data, "  │    ") if data not in (None, "", [], {}) else []
    out += body[:limit] if limit else body
    if limit and len(body) > limit:
        out.append(f"  │    … ещё {len(body) - limit} строк — полностью в пасте {f['id']}")
    if f.get("evidence"):
        refs = [i + ("(удалён)" if ev.get(i, {}).get("deleted") else "") for i in f["evidence"]]
        out.append("  │  доказательства: " + " ".join(refs))
    return out + ["  └─"]


def finding_paste(case_name: str, f: dict, evidence: list[dict]) -> str:
    """Одна находка целиком — как отдельная паста."""
    ev = {e["id"]: e for e in evidence}
    head = [_dots("ДЕЛО", case_name), _dots("находка", f["id"]),
            _dots("модуль", MODULE_TITLES.get(f.get("module", ""), f.get("module", "?"))),
            _dots("статус", f.get("status") or "получено из источника"), ""]
    if f.get("status"):
        head += ["  ! Это гипотеза, а не установленный факт. Проверьте по первоисточнику.", ""]
    files = [""] + [f"  {i:<5} {ev[i].get('file')}" + ("  [удалён]" if ev[i].get("deleted") else f"\n        sha256 {ev[i].get('sha256')}")
                    for i in f.get("evidence", []) if i in ev]
    return "\n".join(head + _finding_block(f, ev, None) + (files if len(files) > 1 else []) + [""])


def to_paste(data: dict) -> str:
    """Сводка дела одним текстом: данные дела, находки по разделам (источник / гипотезы), файлы."""
    findings, evidence = data["findings"], data["evidence"]
    hyp = [f for f in findings if f.get("status")]
    deleted = [e for e in evidence if e.get("deleted")]
    out = [_dots("ДЕЛО", data["case"]), _dots("сводка", data["exported_at"].replace("T", " ")[:19] + " UTC"),
           _dots("находок", f"{len(findings)}   (гипотез: {len(hyp)})"),
           _dots("файлов", f"{len(evidence) - len(deleted)}   (удалено: {len(deleted)})"),
           _dots("заметок", len(data.get("notes", []))), ""]
    sections = [("ПОЛУЧЕНО ИЗ ИСТОЧНИКОВ", [f for f in findings if not f.get("status")]),
                ("ГИПОТЕЗЫ — ТРЕБУЮТ ПРОВЕРКИ", hyp)]
    ev = {e["id"]: e for e in evidence}
    for n, (title, items) in enumerate(sections, 1):
        out += [RULE, f" [{n}] {title}", RULE]
        if not items:
            out += ["", "  —", ""]
        groups: dict[str, list] = {}
        for f in items:
            groups.setdefault(f.get("module", "?"), []).append(f)
        for module, group in groups.items():
            out += ([] if out[-1] == "" else [""]) + [f"  ── {MODULE_TITLES.get(module, module.upper())} ({len(group)})", ""]
            for f in group:
                out += _finding_block(f, ev, DATA_LINES) + [""]
    out += [RULE, f" [{len(sections) + 1}] ФАЙЛЫ-ДОКАЗАТЕЛЬСТВА", RULE, ""]
    for e in evidence:
        if e.get("deleted"):
            out.append(f"  {e['id']:<5} [удалён {e['deleted'][:10]}]  {e.get('file')}  sha256 {e.get('sha256')}")
        else:
            out.append(f"  {e['id']:<5} {e.get('file')}")
            out.append(f"        sha256 {e.get('sha256')} · {e.get('size')} Б · {e.get('source') or '—'}")
    if not evidence:
        out.append("  —")
    out += ["", RULE, "  SHA-256 подтверждает неизменность файла, а не правдивость содержимого.",
            "  «Гипотеза» — не установленный факт: проверьте по первоисточнику.", ""]
    return "\n".join(out)


# ---------------- TXT ----------------
def _lines(value, indent: str = "  ") -> list[str]:
    """Данные находки в читаемом виде: ключ: значение, вложенное — с отступом."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
                    out.append(f"{indent}{k}: {', '.join(map(str, v))}")
                else:
                    out.append(f"{indent}{k}:")
                    out.extend(_lines(v, indent + "  "))
            elif isinstance(v, str) and "\n" in v:  # многострочный текст (напр. с картинки) — блоком под ключом
                out.append(f"{indent}{k}:")
                out.extend(f"{indent}  {line}" for line in v.splitlines())
            else:
                out.append(f"{indent}{k}: {'—' if v in (None, '', [], {}) else v}")
        return out
    if isinstance(value, list):
        out = []
        for i, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                out.append(f"{indent}{i}.")
                out.extend(_lines(item, indent + "   "))
            else:
                out.append(f"{indent}- {item}")
        return out
    return [f"{indent}{value}"]


def _errors(value, path: str = "") -> list[str]:
    """Ошибки источников внутри данных находки (ключи error / *_error)."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else str(k)
            if (k == "error" or str(k).endswith("_error")) and isinstance(v, str):
                found.append(f"{path or k}: {v}")
            else:
                found.extend(_errors(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(_errors(v, f"{path}[{i}]"))
    return found


def to_txt(data: dict) -> str:
    findings, evidence = data["findings"], data["evidence"]
    by_module = Counter(f.get("module", "?") for f in findings)
    unverified = [f for f in findings if f.get("status")]
    out = [
        f"ДЕЛО: {data['case']}",
        f"Экспорт: {data['exported_at']} · находок: {len(findings)} · файлов-доказательств: {len(evidence)}",
        "Внимание: это открытый (незашифрованный) файл. Храните его в защищённом месте.",
        "",
        "== Обзор ==",
        "По модулям: " + (", ".join(f"{m}: {n}" for m, n in by_module.most_common()) or "—"),
        f"С пометкой о проверке: {len(unverified)}",
        "",
        "== Находки ==",
    ]
    for f in findings:
        out.append("")
        out.append(f"[{f['id']}] {f.get('title', '')}")
        meta = f"  Модуль: {f.get('module')} · Цель: {f.get('target')} · Получено: {f.get('time')}"
        if f.get("status"):
            meta += f" · Статус: {f['status']}"
        out.append(meta)
        if f.get("evidence"):
            out.append("  Доказательства: " + " ".join(f"[{e}]" for e in f["evidence"]))
        out.extend(_lines(f.get("data"), "    "))
    out += ["", "== Доказательства (SHA-256) =="]
    for e in evidence:
        out += ["", f"[{e['id']}] {e.get('file')}", f"  SHA-256: {e.get('sha256')}",
                f"  Размер: {e.get('size')} байт · Получено: {e.get('time')} · Источник: {e.get('source') or '—'}"]
        if e.get("note"):
            out.append(f"  Примечание: {e['note']}")
        if e.get("deleted"):
            out.append(f"  Файл удалён: {e['deleted']}" + (f" · {e['deleted_reason']}" if e.get("deleted_reason") else ""))
    for n in data.get("notes", []):
        out += ["", f"== Заметка: {n['name']} (изменена {n.get('modified', '—')}) ==", n.get("text", "")]
    errors = [f"[{f['id']}] {err}" for f in findings for err in _errors(f.get("data"))]
    out += ["", "== Покрытие и ошибки =="]
    out += [f"- {e}" for e in errors] or ["Ошибок источников не зафиксировано."]
    out += ["", "SHA-256 подтверждает неизменность файла, а не правдивость его содержимого.",
            "Пометка «требует проверки» означает гипотезу, а не установленный факт.", ""]
    return "\n".join(out)


def to_csv(data: dict) -> tuple[str, str]:
    findings = csv_text([[f.get(k) if k != "evidence" else " ".join(f.get("evidence", [])) for k in FINDING_FIELDS]
                         for f in data["findings"]], FINDING_FIELDS, bom=True)
    evidence = csv_text([[e.get(k) for k in EVIDENCE_FIELDS] for e in data["evidence"]], EVIDENCE_FIELDS, bom=True)
    return findings, evidence


# ---------------- запись ----------------
def export(case: Case, fmt: str = "all", out_dir: Path | None = None) -> dict:
    data = case_data(case)
    out_dir = Path(out_dir).expanduser() if out_dir else case.dir / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = data["exported_at"][:19].replace(":", "").replace("-", "")
    base = out_dir / f"{case.name}_{stamp}"
    written = {}
    if fmt in ("txt", "all"):
        written["txt"] = base.with_suffix(".txt")
        written["txt"].write_text(to_txt(data), encoding="utf-8")
    if fmt in ("json", "all"):
        written["json"] = base.with_suffix(".json")
        written["json"].write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if fmt in ("csv", "all"):
        f_csv, e_csv = to_csv(data)
        written["csv_findings"] = Path(f"{base}_findings.csv")
        written["csv_evidence"] = Path(f"{base}_evidence.csv")
        written["csv_findings"].write_text(f_csv, encoding="utf-8")
        written["csv_evidence"].write_text(e_csv, encoding="utf-8")
    result: dict = {"case": case.name, "findings": len(data["findings"]), "evidence": len(data["evidence"])}
    if fmt in ("zip", "all"):
        written["zip"] = base.with_suffix(".zip")
        result |= _zip(case, data, written["zip"])
    result["files"] = {k: str(v) for k, v in written.items()}
    inside = out_dir.resolve().is_relative_to(case.dir.resolve())
    result["note"] = ("Экспорт лежит внутри зашифрованного хранилища дел." if inside else
                      "Экспорт вне хранилища — это открытые файлы, храните их в защищённом месте.")
    return result


def _zip(case: Case, data: dict, target: Path) -> dict:
    root = PurePosixPath(case.name)
    entries: dict[str, bytes] = {
        "case.json": json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(),
        "report.txt": to_txt(data).encode(),
    }
    f_csv, e_csv = to_csv(data)
    entries["findings.csv"], entries["evidence.csv"] = f_csv.encode(), e_csv.encode()
    checksums = {name: hashlib.sha256(body).hexdigest() for name, body in entries.items()}
    missing = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(str(root / name), body)
        root_dir = case.dir.resolve()
        files = []
        for e in data["evidence"]:
            # путь из журнала мог прийти из импортированного файла — не выходим за пределы дела
            src = (case.dir / e["file"]).resolve()
            if not src.is_relative_to(root_dir) or not src.is_file():
                missing.append({"id": e["id"], "file": e["file"],
                                "reason": "удалён пользователем" if e.get("deleted") else "файл вне дела или удалён"})
                continue
            files.append((f"files/{e['file']}", src))
        digests = core.sha256_files([src for _, src in files])  # параллельно (Rust-ядро или потоки)
        for name, src in files:
            zf.write(src, str(root / name))
            checksums[name] = digests[str(src)]
        manifest = {"format": FORMAT, "version": VERSION, "case": case.name, "exported_at": data["exported_at"],
                    "checksums_sha256": checksums, "missing_files": missing}
        zf.writestr(str(root / "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"zip_files": len(checksums), "missing_files": missing}


# ---------------- импорт ----------------
def import_case(case: Case, src: Path) -> dict:
    """Восстанавливает дело из JSON или ZIP с теми же ID. Только в пустое дело."""
    src = Path(src).expanduser()
    if case.findings() or case.evidence():
        raise RuntimeError(f"Дело «{case.name}» не пустое — импортируйте в новое: --case <новое_имя>")
    restored, bad = 0, []
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as zf:
            names = zf.namelist()
            manifest_name = next((n for n in names if PurePosixPath(n).name == "manifest.json"
                                  and len(PurePosixPath(n).parts) == 2), None)
            if not manifest_name:
                raise RuntimeError("В архиве нет manifest.json — это не экспорт osint-kit")
            prefix = str(PurePosixPath(manifest_name).parent) + "/"
            manifest = json.loads(zf.read(manifest_name))
            data = json.loads(zf.read(prefix + "case.json"))
            root = case.dir.resolve()
            for name in names:
                if not name.startswith(prefix + "files/") or name.endswith("/"):
                    continue
                rel = name[len(prefix):]
                target = (case.dir / rel[len("files/"):]).resolve()
                if not target.is_relative_to(root):  # путь вида ../../ — не распаковываем
                    bad.append({"file": rel, "reason": "путь вне дела"})
                    continue
                body = zf.read(name)
                if hashlib.sha256(body).hexdigest() != manifest.get("checksums_sha256", {}).get(rel):
                    bad.append({"file": rel, "reason": "контрольная сумма не совпадает"})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                restored += 1
    else:
        data = json.loads(src.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise RuntimeError("Неизвестный формат файла — ожидается экспорт osint-kit (JSON или ZIP)")
    origin = f"импорт: {src.name}"
    with case.evidence_log.open("w", encoding="utf-8") as fh:
        for e in data["evidence"]:
            fh.write(json.dumps(e | {"imported_from": origin}, ensure_ascii=False) + "\n")
    with case.findings_log.open("w", encoding="utf-8") as fh:
        for f in data["findings"]:
            fh.write(json.dumps(f | {"imported_from": origin}, ensure_ascii=False, default=str) + "\n")
    notes = 0
    for n in data.get("notes", []):
        try:
            case.write_note(n["name"], n.get("text", ""), new=True)
            notes += 1
        except (RuntimeError, KeyError, TypeError) as exc:
            bad.append({"file": f"notes/{n.get('name') if isinstance(n, dict) else '?'}", "reason": str(exc)})
    return {"case": case.name, "findings": len(data["findings"]), "evidence": len(data["evidence"]),
            "files_restored": restored, "notes_restored": notes, "rejected": bad,
            "note": "Импортированные записи помечены imported_from и не считаются новой проверкой источников."}
