"""Модуль media: EXIF, зворотний пошук, відео (метадані, ключові кадри), завантаження, ШІ-детекція."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ..case import Case, sha256_file, slug
from ..core import extract_iocs
from ..config import Config
from .. import net
from ..net import session, short_error

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".heic", ".bmp"}


def _is_url(s: str) -> bool:
    return s.strip().lower().startswith(("http://", "https://"))


# ---------------- EXIF ----------------
def _gps_to_deg(value, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in value)
        deg = d + m / 60 + s / 3600
        return -deg if str(ref).upper() in ("S", "W") else deg
    except Exception:  # noqa: BLE001
        return None


def _exif_pillow(path: Path) -> dict:
    from PIL import ExifTags, Image

    with Image.open(path) as img:
        raw = img.getexif()
        data = {"ImageSize": f"{img.width}x{img.height}", "Format": img.format}
        for tag, val in raw.items():
            data[ExifTags.TAGS.get(tag, str(tag))] = val
        for ifd_id in (0x8769, 0x8825):  # Exif IFD, GPS IFD
            try:
                ifd = raw.get_ifd(ifd_id)
            except Exception:  # noqa: BLE001
                continue
            names = ExifTags.GPSTAGS if ifd_id == 0x8825 else ExifTags.TAGS
            for tag, val in ifd.items():
                data[names.get(tag, str(tag))] = val
    lat = _gps_to_deg(data.get("GPSLatitude"), data.get("GPSLatitudeRef")) if "GPSLatitude" in data else None
    lon = _gps_to_deg(data.get("GPSLongitude"), data.get("GPSLongitudeRef")) if "GPSLongitude" in data else None
    clean = {}
    for k, v in data.items():
        if isinstance(v, bytes):
            v = v[:64].hex() + ("…" if len(v) > 64 else "")
        clean[str(k)] = str(v) if not isinstance(v, (int, float, str)) else v
    if lat is not None and lon is not None:
        clean["GPSPosition"] = f"{lat:.6f}, {lon:.6f}"
    return clean


def _exiftool(path: Path) -> dict:
    out = subprocess.run(["exiftool", "-j", "-G1", "-a", "-n", str(path)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.strip() or "exiftool error")
    data = json.loads(out.stdout)[0]
    lat = next((v for k, v in data.items() if k.endswith(":GPSLatitude")), None)
    lon = next((v for k, v in data.items() if k.endswith(":GPSLongitude")), None)
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        data["GPSPosition"] = f"{lat:.6f}, {lon:.6f}"
    return data


KEY_FIELDS = ("Make", "Model", "LensModel", "Software", "DateTimeOriginal", "CreateDate", "ModifyDate",
              "OffsetTimeOriginal", "GPSPosition", "GPSAltitude", "Artist", "Copyright", "ImageSize",
              "ImageWidth", "ImageHeight", "SerialNumber", "BodySerialNumber", "OwnerName", "Duration",
              "MIMEType", "Creator", "Author", "Producer", "Title")


SYSTEM_DROP = ("System:", "SourceFile")  # папка, права и даты файла на Mac аналитика — не свойства доказательства
_NAME_DATES = (
    re.compile(r"(\d{4})-(\d{2})-(\d{2})\D{1,6}?(\d{1,2})[.:](\d{2})[.:](\d{2})\s*([AP]M)?", re.I),
    re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"),
)
NO_META = "GPS и данных камеры нет — у скриншотов и фото из мессенджеров (Telegram, WhatsApp, Viber) их обычно стирают"


# родные разрешения экранов: по скриншоту — гипотеза об устройстве, где он снят
SCREENS = {
    "3024×1964": "MacBook Pro 14″", "3456×2234": "MacBook Pro 16″", "2560×1664": "MacBook Air 13″",
    "2880×1864": "MacBook Air 15″", "2560×1600": "MacBook Pro 13″ / MacBook Air 13″ (до 2022)",
    "4480×2520": "iMac 24″", "5120×2880": "iMac 27″ / Studio Display", "6016×3384": "Pro Display XDR",
    "1179×2556": "iPhone 14 Pro / 15 / 15 Pro / 16", "1290×2796": "iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus",
    "1206×2622": "iPhone 16 Pro", "1320×2868": "iPhone 16 Pro Max", "1170×2532": "iPhone 12 / 13 / 14",
    "1284×2778": "iPhone 12 Pro Max / 13 Pro Max / 14 Plus", "1080×2340": "iPhone 12 mini / 13 mini",
    "1125×2436": "iPhone X / XS / 11 Pro", "1242×2688": "iPhone XS Max / 11 Pro Max", "828×1792": "iPhone XR / 11",
    "750×1334": "iPhone 6–8 / SE", "1640×2360": "iPad Air / iPad 10", "2048×2732": "iPad Pro 12.9″",
}
_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*([AP]M)?\b", re.I)


def device_guess(size: str | None, screenshot: bool) -> str | None:
    if not screenshot or not size:
        return None
    w, _, h = size.partition("×")
    name = SCREENS.get(size) or SCREENS.get(f"{h}×{w}")  # повёрнутый экран
    return f"Возможно, {name} (родное разрешение экрана)" if name else None


def screen_clock(text: str, taken: str | None) -> str | None:
    """Время, видимое на скриншоте (строка меню, статус-бар), против даты файла — независимая сверка."""
    times = [(m.group(0).strip(), int(m.group(1)) % 12 + (12 if (m.group(3) or "").upper() == "PM" else 0)
              if m.group(3) else int(m.group(1)), int(m.group(2))) for m in _CLOCK.finditer(text or "")]
    if not times or not taken:
        return None
    hm = re.search(r"\d{4}-\d{2}-\d{2} (\d{2}):(\d{2})", taken)
    if not hm:
        return None
    want = (int(hm.group(1)), int(hm.group(2)))
    hit = next((t for t, h, m in times if (h, m) == want), None)
    return f"{hit} — совпадает с датой" if hit else f"{times[0][0]} — не совпадает с датой ({hm.group(1)}:{hm.group(2)})"


def file_date(data: dict, name: str) -> str | None:
    """Дата изменения файла на этом компьютере — в UTC (часовой пояс аналитика не сохраняется)
    и с честной подписью: это время сохранения/копирования сюда, не съёмки."""
    raw = _get(data, "FileModifyDate")
    try:
        local = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S%z")
    except (TypeError, ValueError):
        return None
    out = f"{local.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC — сохранён или скопирован сюда"
    by_name = _date_from_name(name)
    if by_name:  # имя скриншота — местное время устройства: сравниваем «по часам», без пояса
        delta = (local.replace(tzinfo=None) - datetime.strptime(by_name, "%Y-%m-%d %H:%M:%S")).total_seconds()
        if abs(delta) <= 120:
            out += f"; совпадает с именем ({delta:+.0f} с)"
    return out


def _get(data: dict, *names: str):
    for name in names:
        for k, v in data.items():
            if (k == name or k.endswith(":" + name)) and v not in (None, ""):
                return v
    return None


def _date_from_name(name: str) -> str | None:
    for rx in _NAME_DATES:
        m = rx.search(name.replace("\u202f", " "))
        if m:
            y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
            ampm = (m.group(7) or "").upper() if m.re is _NAME_DATES[0] else ""
            h = h % 12 + (12 if ampm == "PM" else 0) if ampm else h
            return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{sec:02d}"
    return None


def describe(data: dict, name: str) -> dict:
    """Понятная сводка метаданных: что это за файл, чем и когда снят, где — и чего в нём нет."""
    s = _summary(data)
    comment = str(_get(data, "UserComment") or "")
    shot = "screenshot" in comment.lower() or re.match(r"(screenshot|снимок экрана|скриншот)", name, re.I)
    camera = " ".join(str(x) for x in (_get(data, "Make"), _get(data, "Model")) if x) or None
    taken = _get(data, "DateTimeOriginal", "CreateDate", "DateCreated")
    if taken:
        taken = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", str(taken))
        offset = _get(data, "OffsetTimeOriginal")
        taken = f"{taken} {offset}" if offset else taken
    else:
        by_name = _date_from_name(name)
        taken = f"{by_name} (из имени файла, не из метаданных)" if by_name else None
    dpi = _get(data, "XResolution") if str(_get(data, "ResolutionUnit")) in ("2", "inches") else None
    if not dpi and _get(data, "PixelsPerUnitX") and str(_get(data, "PixelUnits")) in ("1", "meters"):
        dpi = round(float(_get(data, "PixelsPerUnitX")) * 0.0254)
    dpi = round(float(dpi)) if dpi else None
    out = {
        "тип": "снимок экрана" if shot else "фото с камеры" if camera else None,
        "камера": camera, "объектив": _get(data, "LensModel"),
        "программа": _get(data, "Software", "CreatorTool"), "устройство": _get(data, "HostComputer"),
        "дата": taken, "размер": "×".join(str(s["ImageSize"]).replace("x", " ").split()) if s.get("ImageSize") else None,
        "плотность": f"{dpi} dpi" + (" (экран Retina)" if shot and dpi in (144, 216) else "") if dpi else None,
        "координаты": s.get("GPSPosition"), "карта": s.get("map"), "высота": _get(data, "GPSAltitude"),
        "автор": _get(data, "Artist", "OwnerName", "Creator", "Author"), "права": _get(data, "Copyright"),
        "серийный номер": _get(data, "SerialNumber", "BodySerialNumber"),
    }
    out["устройство (гипотеза)"] = device_guess(out["размер"], bool(shot))
    if not s.get("GPSPosition"):
        out["нет данных"] = NO_META if not camera else "GPS нет — геотеги выключены или стёрты"
    return {k: v for k, v in out.items() if v not in (None, "")}


# ---------------- текст на изображении (macOS Vision, локально) ----------------
OCR_SRC = Path(__file__).with_name("ocr.swift")
OCR_MAX = 600  # символов текста в записи; полностью — в файле ocr_*.txt


def _ocr_binary() -> Path | None:
    """Собирает помощник из ocr.swift один раз (кэш по хешу исходника). Нет swiftc — распознавания нет."""
    swiftc = shutil.which("swiftc")
    if not swiftc or not OCR_SRC.exists():
        return None
    digest = hashlib.sha256(OCR_SRC.read_bytes()).hexdigest()[:12]
    out = Path.home() / "Library" / "Caches" / "osint-kit" / f"ocr-{digest}"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        subprocess.run([swiftc, "-O", "-o", str(tmp), str(OCR_SRC)], check=True, capture_output=True, timeout=300)
        tmp.replace(out)
    return out


def ocr(path: Path) -> str | None:
    """Текст на изображении. None — распознавание на этом Mac недоступно."""
    binary = _ocr_binary()
    if binary is None:
        return None
    r = subprocess.run([str(binary), str(path)], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ошибка распознавания текста")
    return r.stdout.strip()


def _text_on_image(case: Case, path: Path) -> dict:
    try:
        text = ocr(path)
    except Exception as exc:  # noqa: BLE001
        return {"распознавание текста": short_error(exc)}
    if not text:
        return {}
    out_file = case.save_text("media", f"ocr_{slug(path.name)}.txt", text, str(path), "текст на изображении (распознан локально)")
    iocs = extract_iocs(text)
    found = {k: v for k, v in (("e-mail", iocs["emails"]), ("ссылки", iocs["urls"]), ("IP", iocs["ipv4"])) if v}
    short = text if len(text) <= OCR_MAX else text[:OCR_MAX] + " …"
    return {"текст на изображении": short, **({"в тексте": found} if found else {}), "_file": out_file}


def _summary(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        short = k.split(":")[-1]
        if short in KEY_FIELDS and short not in out:
            out[short] = v
    # data["GPSPosition"] — "lat, lon"; exiftool -n дає ще Composite:GPSPosition як "lat lon"
    coords = re.findall(r"-?\d+(?:\.\d+)?", str(data.get("GPSPosition") or out.get("GPSPosition") or ""))
    if len(coords) == 2:
        out["GPSPosition"] = f"{coords[0]}, {coords[1]}"
        out["map"] = f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
    return out


def read_meta(path: Path) -> tuple[str, dict, dict]:
    """(рушій, повні метадані, ключові поля) — exiftool, якщо є, інакше Pillow."""
    use_tool = shutil.which("exiftool") is not None
    data = _exiftool(path) if use_tool else _exif_pillow(path)
    return ("exiftool" if use_tool else "pillow"), data, _summary(data)


def exif(cfg: Config, case: Case, files: list[str]) -> list[dict]:
    results = []
    for f in files:
        path = Path(f).expanduser().resolve()  # один и тот же файл — один и тот же путь (ссылки, «~», «..»)
        item: dict = {"file": str(path)}
        if not path.is_file():
            item["error"] = "файл не найден"
            results.append(item)
            continue
        item["sha256"] = sha256_file(path)
        try:
            item["engine"], data, item["summary"] = read_meta(path)
        except Exception as exc:  # noqa: BLE001
            item["error"] = short_error(exc)
            results.append(item)
            continue
        on_disk = file_date(data, path.name)
        data = {k: v for k, v in data.items() if not k.startswith(SYSTEM_DROP)}
        out = case.save_json("media", f"exif_{slug(path.name)}.json", data | {"_sha256": item["sha256"]},
                             str(path), "полные метаданные")
        item["report"] = str(out.relative_to(case.dir))
        info = describe(data, path.name)
        if on_disk:
            info["файл на этом компьютере"] = on_disk
        files = [out]
        if path.suffix.lower() in IMAGE_EXT:
            extra = _text_on_image(case, path)
            if extra.get("_file"):
                files.append(extra.pop("_file"))
            info |= extra
            if info.get("тип") == "снимок экрана":
                clock = screen_clock(extra.get("текст на изображении", ""), info.get("дата"))
                if clock:
                    info["время на снимке"] = clock
        title = f"Метаданные: {path.name}"
        prior = [x["id"] for x in case.findings() if x.get("module") == "media" and x.get("title") == title]
        if prior:
            info["повтор проверки"] = prior[-1]
        case.add_finding("media", str(path), title, info, files)
        results.append(item)
    return results


# ---------------- зворотний пошук ----------------
def reverse_links(image: str) -> dict:
    if _is_url(image):
        u = quote(image, safe="")
        return {
            "Google Lens": f"https://lens.google.com/uploadbyurl?url={u}",
            "Yandex": f"https://yandex.com/images/search?rpt=imageview&url={u}",
            "Bing": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}",
            "TinEye": f"https://tineye.com/search?url={u}",
        }
    return {
        "Google Lens": "https://lens.google.com/",
        "Yandex": "https://yandex.com/images/",
        "Bing": "https://www.bing.com/visualsearch",
        "TinEye": "https://tineye.com/",
    }


def reverse(cfg: Config, case: Case, images: list[str], open_browser: bool = False) -> list[dict]:
    results = []
    for image in images:
        local = not _is_url(image)
        if local and not Path(image).expanduser().is_file():
            results.append({"image": image, "error": "файл не найден"})
            continue
        links = reverse_links(image)
        item = {"image": image, "links": links}
        if local:
            item["note"] = "Локальный файл: перетащите его на каждую из открытых страниц поиска"
            if open_browser and shutil.which("open"):
                # показати файл у Finder, щоб його було зручно перетягнути
                subprocess.run(["open", "-R", str(Path(image).expanduser())], check=False)
        if open_browser:
            for link in links.values():
                webbrowser.open_new_tab(link)
        case.add_finding("media", image, "Обратный поиск изображения", links)
        results.append(item)
    return results


# ---------------- відео ----------------
def _need(tool: str) -> None:
    if not shutil.which(tool):
        raise RuntimeError(f"Не найден {tool}. Установите: brew install {'ffmpeg' if tool.startswith('ff') else tool}")


def video_meta(path: Path) -> dict:
    _need("ffprobe")
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300] or "ffprobe error")
    return json.loads(out.stdout)


def keyframes(path: Path, out_dir: Path, max_frames: int = 30, threshold: float = 0.3) -> list[Path]:
    """Кадри на змінах сцени; якщо їх замало — рівномірно по тривалості."""
    _need("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "scene_%03d.jpg"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                    "-vf", f"select='eq(n,0)+gt(scene,{threshold})',scale='min(1280,iw)':-2",
                    "-vsync", "vfr", "-frames:v", str(max_frames), "-q:v", "2", str(pattern)],
                   capture_output=True, timeout=900)
    frames = sorted(out_dir.glob("scene_*.jpg"))
    if len(frames) < 5:
        try:
            dur = float(video_meta(path)["format"].get("duration", 0))
        except Exception:  # noqa: BLE001
            dur = 0
        if dur > 0:
            n = min(max_frames, max(5, int(dur // 10)))
            step = dur / n
            for i in range(n):
                target = out_dir / f"time_{i:03d}.jpg"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{i * step:.2f}",
                                "-i", str(path), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "2",
                                str(target)], capture_output=True, timeout=120)
            frames = sorted(out_dir.glob("*.jpg"))
    return frames


def video(cfg: Config, case: Case, source: str, max_frames: int = 30) -> dict:
    if _is_url(source):
        dl = download(cfg, case, source)
        files = [case.dir / f for f in dl.get("files", []) if not f.endswith((".json", ".jpg", ".webp", ".png", ".vtt", ".srt"))]
        if not files:
            raise RuntimeError("Не удалось скачать видео по ссылке")
        path = files[0]
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise RuntimeError("Файл не найден")
    meta = video_meta(path)
    fmt = meta.get("format", {})
    tags = {k: v for k, v in (fmt.get("tags") or {}).items()}
    vstream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    summary = {
        "file": str(path), "sha256": sha256_file(path),
        "duration_s": round(float(fmt.get("duration", 0) or 0), 2),
        "size_mb": round(int(fmt.get("size", 0) or 0) / 1e6, 2),
        "codec": vstream.get("codec_name"), "resolution": f"{vstream.get('width')}x{vstream.get('height')}",
        "fps": vstream.get("r_frame_rate"), "creation_time": tags.get("creation_time"),
        "device": tags.get("com.apple.quicktime.model") or tags.get("model"),
        "location": tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location"),
        "encoder": tags.get("encoder"),
    }
    summary = {k: v for k, v in summary.items() if v not in (None, "", "NonexNone")}
    meta_file = case.save_json("media", f"video_{slug(path.stem)}_ffprobe.json", meta, str(path), "метаданные видео")
    files = [meta_file]
    if shutil.which("exiftool"):
        try:
            files.append(case.save_json("media", f"video_{slug(path.stem)}_exif.json", _exiftool(path), str(path), "exiftool"))
        except Exception:  # noqa: BLE001
            pass
    frame_dir = case.path("media", f"frames_{slug(path.stem)}")
    frames = keyframes(path, frame_dir, max_frames=max_frames)
    for fr in frames:
        case.register(fr, str(path), "ключевой кадр")
    summary["frames"] = len(frames)
    summary["frames_dir"] = str(frame_dir.relative_to(case.dir))
    summary["next_step"] = "Кадры можно проверить обратным поиском: osint media reverse <кадр> --open"
    case.add_finding("media", str(path), f"Видео: {path.name}", summary, files + frames[:6])
    return summary


def download(cfg: Config, case: Case, url: str, audio_only: bool = False) -> dict:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Не установлен yt-dlp (запустите install.sh ещё раз)") from exc
    out_dir = case.dir / "media" / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(out_dir.iterdir())
    opts = {
        "outtmpl": str(out_dir / "%(extractor)s_%(id)s.%(ext)s"),
        "writeinfojson": True,
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": cfg.user_agent},
        "retries": 3,
    }
    if audio_only:
        opts["format"] = "bestaudio/best"
    elif shutil.which("ffmpeg"):
        opts["format"] = "bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = "b"
    if net.routed():
        opts["proxy"] = net.proxy_url()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"yt-dlp: {short_error(exc)}") from exc
    new_files = sorted(set(out_dir.iterdir()) - before)
    for f in new_files:
        case.register(f, url, "скачано yt-dlp")
    summary = {
        "title": info.get("title"), "uploader": info.get("uploader"), "uploader_url": info.get("uploader_url"),
        "upload_date": info.get("upload_date"), "duration": info.get("duration"),
        "views": info.get("view_count"), "webpage_url": info.get("webpage_url"),
        "files": [str(f.relative_to(case.dir)) for f in new_files],
    }
    case.add_finding("media", url, f"Скачивание: {info.get('title') or url}", summary, new_files)
    return summary


def aicheck(cfg: Config, case: Case, file: str) -> dict:
    key = cfg.key("hive")
    if not key:
        raise RuntimeError("Нужен ключ Hive (keys.hive в config.toml)")
    path = Path(file).expanduser()
    if not path.is_file():
        raise RuntimeError("Файл не найден")
    s = session(cfg.user_agent, retries=1)
    with path.open("rb") as fh:
        r = s.post("https://api.thehive.ai/api/v2/task/sync", headers={"Authorization": f"Token {key}"},
                   files={"media": fh}, timeout=180)
    r.raise_for_status()
    data = r.json()
    scores = {}
    try:
        for out in data["status"][0]["response"]["output"]:
            for c in out.get("classes", []):
                if c["class"] in ("ai_generated", "not_ai_generated", "deepfake") or c["score"] > 0.5:
                    scores[c["class"]] = round(c["score"], 3)
    except (KeyError, IndexError, TypeError):
        pass
    out = case.save_json("media", f"hive_{slug(path.name)}.json", data, "thehive.ai", "ответ Hive")
    result = {"file": str(path), "scores": scores,
              "note": "Это вероятность, а не доказательство. Проверяйте другими способами."}
    case.add_finding("media", str(path), "Проверка на ИИ-генерацию (Hive)", result, [out])
    return result
