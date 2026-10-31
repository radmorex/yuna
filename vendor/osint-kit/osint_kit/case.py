"""Папка справи: файли-докази з хешами, журнал знахідок і звіт."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import core


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_stamp() -> str:
    """Метка для имён файлов — в UTC (как время записей): местное время выдавало бы часовой пояс аналитика."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def slug(text: str, limit: int = 60) -> str:
    text = re.sub(r"^[a-z]+://", "", text.strip(), flags=re.I)
    text = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE).strip("._")
    return (text or "item")[:limit]


def private(obj: Any) -> Any:
    """Шляхи всередині домашньої папки → «~/…»: ім'я користувача Mac і його папки у справу не потрапляють."""
    home = str(Path.home())
    if isinstance(obj, str):
        return obj.replace(home, "~") if home not in ("/", "") else obj
    if isinstance(obj, list):
        return [private(x) for x in obj]
    if isinstance(obj, dict):
        return {k: private(v) for k, v in obj.items()}
    return obj


def sha256_file(path: Path) -> str:
    return core.sha256_file(path)


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Комірка не виконається як формула в Excel/LibreOffice (CSV injection з чужих сторінок)."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def csv_text(rows, header: list[str] | None = None, bom: bool = False) -> str:
    """CSV з безпечними комірками; bom=True — щоб Excel правильно відкрив кирилицю."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if header:
        w.writerow(header)
    w.writerows([csv_safe(c) for c in row] for row in rows)
    return ("\ufeff" if bom else "") + buf.getvalue()


NOTE_EXT = (".txt", ".md")
NOTE_MAX = 2_000_000  # байт на одну заметку


def delete_case(root: Path, name: str) -> None:
    """Удаляет папку дела целиком. Только существующее дело прямо внутри root, не ссылку."""
    root = Path(root).expanduser().resolve()
    folder = Path(root) / slug(name)
    if folder.is_symlink() or not folder.is_dir() or folder.resolve().parent != root:
        raise RuntimeError("Дело не найдено")
    shutil.rmtree(folder)


class Case:
    def __init__(self, root: Path, name: str):
        self.name = slug(name) or "default"
        self.dir = Path(root).expanduser() / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.evidence_log = self.dir / "evidence.jsonl"
        self.findings_log = self.dir / "findings.jsonl"

    # ---------- файли ----------
    def path(self, module: str, filename: str) -> Path:
        folder = self.dir / slug(module)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / filename
        if target.exists():
            stem, suffix = target.stem, target.suffix
            stamp = utc_stamp()
            target = folder / f"{stem}_{stamp}{suffix}"
            n = 1
            while target.exists():
                target = folder / f"{stem}_{stamp}_{n}{suffix}"
                n += 1
        return target

    def register(self, path: Path, source: str = "", note: str = "") -> dict:
        """Записує файл у журнал доказів (ID, SHA-256, розмір, час, джерело)."""
        path = Path(path)
        entry = {
            "id": self._next_id(self.evidence_log, "E"),
            "time": now_iso(),
            "file": str(path.relative_to(self.dir)) if path.is_relative_to(self.dir) else str(path),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "source": private(source),
            "note": note,
        }
        with self.evidence_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def save_bytes(self, module: str, filename: str, data: bytes, source: str = "", note: str = "") -> Path:
        target = self.path(module, filename)
        target.write_bytes(data)
        self.register(target, source, note)
        return target

    def save_text(self, module: str, filename: str, text: str, source: str = "", note: str = "") -> Path:
        return self.save_bytes(module, filename, private(text).encode("utf-8"), source, note)

    def save_json(self, module: str, filename: str, obj: Any, source: str = "", note: str = "") -> Path:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        return self.save_text(module, filename, text, source, note)

    def delete_evidence(self, eid: str, reason: str = "") -> dict:
        """Видаляє файл доказу, але не запис: у журналі лишаються ID, SHA-256, розмір і позначка
        «видалено». ID — номер рядка, тож переписується лише цей рядок, решта (і биті) — як були.
        Файл поза справою або потрібний іншому запису не чіпаємо."""
        lines = self._lines(self.evidence_log)
        rows = self._read(self.evidence_log, "E")
        at = next((n for n, line in enumerate(lines) if self._row(line, f"E{n + 1}").get("id") == eid), None)
        if at is None:
            raise RuntimeError("Доказательство не найдено")
        entry = self._row(lines[at], eid)
        if entry.get("deleted"):
            raise RuntimeError("Файл уже удалён")
        path = (self.dir / entry["file"]).resolve()
        shared = any(r.get("file") == entry["file"] and r["id"] != eid and not r.get("deleted") for r in rows)
        entry["file_removed"] = path.is_relative_to(self.dir.resolve()) and path.is_file() and not shared
        if entry["file_removed"]:
            path.unlink()
        entry["deleted"] = now_iso()
        if reason:
            entry["deleted_reason"] = reason
        lines[at] = json.dumps(entry, ensure_ascii=False)
        tmp = self.evidence_log.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        os.replace(tmp, self.evidence_log)
        return entry

    @staticmethod
    def _row(line: str, default_id: str) -> dict:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return {}
        if not isinstance(row, dict):
            return {}
        row.setdefault("id", default_id)
        return row

    # ---------- нотатки (робочі файли аналітика, не докази) ----------
    @property
    def notes_dir(self) -> Path:
        return self.dir / "notes"

    def note_path(self, name: str) -> Path:
        name = slug(name or "", 80)
        if name == "item":
            raise RuntimeError("Введите имя файла")
        if not name.lower().endswith(NOTE_EXT):
            name += ".txt"
        return self.notes_dir / name

    def notes(self) -> list[dict]:
        if not self.notes_dir.is_dir():
            return []
        out = []
        for p in sorted(self.notes_dir.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in NOTE_EXT:
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
        return out

    def read_note(self, name: str) -> str:
        path = self.note_path(name)
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Файл не найден")
        return path.read_text(encoding="utf-8", errors="replace")

    def write_note(self, name: str, text: str, new: bool = False) -> dict:
        path = self.note_path(name)
        data = (text or "").encode("utf-8")
        if len(data) > NOTE_MAX:
            raise RuntimeError(f"Файл больше {NOTE_MAX // 1_000_000} МБ")
        if new and path.exists():
            raise RuntimeError("Файл с таким именем уже есть")
        if path.is_symlink():
            raise RuntimeError("Файл не найден")
        self.notes_dir.mkdir(exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return next(n for n in self.notes() if n["name"] == path.name)

    def delete_note(self, name: str) -> None:
        path = self.note_path(name)
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Файл не найден")
        path.unlink()

    # ---------- знахідки ----------
    def add_finding(self, module: str, target: str, title: str, data: Any, files: list[Path] | None = None,
                    status: str | None = None) -> str:
        """Знахідка посилається на ID доказів (файлів), з яких отримана. status — напр. «потребує перевірки»."""
        rel = [str(Path(f).relative_to(self.dir)) for f in files or [] if Path(f).is_relative_to(self.dir)]
        by_file = {e["file"]: e["id"] for e in self.evidence()}
        entry = {
            "id": self._next_id(self.findings_log, "F"),
            "time": now_iso(),
            "module": module,
            "target": private(target),
            "title": private(title),
            "data": private(data),
            "files": rel,
            "evidence": [by_file[f] for f in rel if f in by_file],
        }
        if status:
            entry["status"] = status
        with self.findings_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry["id"]

    # ID = номер непорожнього рядка журналу (журнали лише доповнюються, тож ID стабільні;
    # старі записи без поля id отримують його так само під час читання).
    @staticmethod
    def _lines(path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _next_id(self, path: Path, prefix: str) -> str:
        # після імпорту ID можуть іти з пропусками — беремо більше з кількості рядків і найбільшого ID
        top = max((int(r["id"][len(prefix):]) for r in self._read(path, prefix)
                   if str(r["id"])[len(prefix):].isdigit()), default=0)
        return f"{prefix}{max(top, len(self._lines(path))) + 1}"

    def _read(self, path: Path, prefix: str) -> list[dict]:
        rows = []
        for n, line in enumerate(self._lines(path), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row.setdefault("id", f"{prefix}{n}")
            rows.append(row)
        return rows

    def findings(self) -> list[dict]:
        return self._read(self.findings_log, "F")

    def evidence(self) -> list[dict]:
        return self._read(self.evidence_log, "E")

    # ---------- звіт ----------
    def build_report(self) -> tuple[Path, Path]:
        findings, evidence = self.findings(), self.evidence()
        md = [f"# Отчёт по делу: {self.name}", "", f"Создано: {now_iso()}", ""]
        md.append(f"Находок: {len(findings)} · Файлов-доказательств: {len(evidence)}")
        md.append("")
        for f in findings:
            md.append(f"## [{f['module']}] {f['title']}")
            md.append(f"*Цель:* `{f['target']}` · *Время:* {f['time']}")
            md.append("")
            md.append("```json")
            md.append(json.dumps(f["data"], ensure_ascii=False, indent=2, default=str))
            md.append("```")
            for fp in f.get("files", []):
                md.append(f"- файл: `{fp}`")
            md.append("")
        md.append("## Журнал доказательств (SHA-256)")
        md.append("")
        md.append("| Время (UTC) | Файл | SHA-256 | Размер | Источник |")
        md.append("|---|---|---|---|---|")
        for e in evidence:
            md.append(f"| {e['time']} | `{e['file']}` | `{e['sha256']}` | {e['size']} | {e.get('source', '')} |")
        md_path = self.dir / "report.md"
        md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

        esc = html.escape
        parts = [
            "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
            f"<title>Отчёт: {esc(self.name)}</title>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<style>body{font:15px/1.5 -apple-system,Segoe UI,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1d1d1f}"
            "h1{margin-bottom:4px}h2{margin-top:32px;font-size:18px;border-bottom:1px solid #ddd;padding-bottom:4px}"
            ".tag{display:inline-block;background:#eef;border-radius:4px;padding:0 6px;font-size:12px;margin-right:6px}"
            "pre{background:#f6f6f8;padding:12px;border-radius:6px;overflow:auto;font-size:13px}"
            "table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}"
            "code{word-break:break-all}img{max-width:100%;border:1px solid #ddd;margin-top:8px}.meta{color:#666;font-size:13px}</style></head><body>",
            f"<h1>Отчёт по делу: {esc(self.name)}</h1>",
            f"<div class='meta'>Создано {esc(now_iso())} · находок: {len(findings)} · файлов-доказательств: {len(evidence)}</div>",
        ]
        for f in findings:
            parts.append(f"<h2><span class='tag'>{esc(f['module'])}</span>{esc(f['title'])}</h2>")
            parts.append(f"<div class='meta'>Цель: <code>{esc(str(f['target']))}</code> · {esc(f['time'])}</div>")
            parts.append("<pre>" + esc(json.dumps(f["data"], ensure_ascii=False, indent=2, default=str)) + "</pre>")
            for fp in f.get("files", []):
                link = esc(fp)
                if fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    parts.append(f"<div><a href='{link}'><img src='{link}' alt='{link}' loading='lazy'></a></div>")
                else:
                    parts.append(f"<div>файл: <a href='{link}'>{link}</a></div>")
        parts.append("<h2>Журнал доказательств (SHA-256)</h2><table><tr><th>Время (UTC)</th><th>Файл</th><th>SHA-256</th><th>Размер</th><th>Источник</th></tr>")
        for e in evidence:
            parts.append(
                f"<tr><td>{esc(e['time'])}</td><td><a href='{esc(e['file'])}'>{esc(e['file'])}</a></td>"
                f"<td><code>{esc(e['sha256'])}</code></td><td>{e['size']}</td><td>{esc(str(e.get('source', '')))}</td></tr>"
            )
        parts.append("</table></body></html>")
        html_path = self.dir / "report.html"
        html_path.write_text("\n".join(parts), encoding="utf-8")
        return md_path, html_path
