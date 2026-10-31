"""Модуль media: EXIF, зворотний пошук, відео, yt-dlp, Hive."""

import shutil
from pathlib import Path
import subprocess

import pytest

from osint_kit.case import sha256_file
from osint_kit.modules import media


@pytest.fixture
def photo(tmp_path):
    from PIL import Image

    path = tmp_path / "фото.jpg"
    img = Image.new("RGB", (64, 48), "red")
    exif = Image.Exif()
    exif[0x010F] = "TestCam"   # Make
    exif[0x0110] = "Model X"   # Model
    exif.get_ifd(0x8825).update({1: "N", 2: (50.0, 27.0, 0.0), 3: "E", 4: (30.0, 31.0, 12.0)})
    img.save(path, exif=exif)
    return path


@pytest.fixture
def clip(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("немає ffmpeg")
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=6:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _check_gps(summary):
    lat, lon = (float(x) for x in summary["GPSPosition"].split(","))
    assert lat == pytest.approx(50.45, abs=1e-4)
    assert lon == pytest.approx(30.52, abs=1e-4)
    assert summary["map"].startswith("https://www.google.com/maps?q=50.45")


@pytest.mark.skipif(not shutil.which("exiftool"), reason="немає exiftool")
def test_exif_exiftool(osint, photo):
    assert osint("media", "exif", str(photo)) == 0
    [item] = media.exif(osint.cfg(), osint.case(), [str(photo)])
    assert item["engine"] == "exiftool"
    assert item["sha256"] == sha256_file(photo)
    assert item["summary"]["Make"] == "TestCam" and item["summary"]["Model"] == "Model X"
    _check_gps(item["summary"])


def test_exif_pillow(osint, photo, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda tool: None)
    [item] = media.exif(osint.cfg(), osint.case(), [str(photo)])
    assert item["engine"] == "pillow"
    assert item["summary"]["Make"] == "TestCam"
    assert item["summary"]["ImageSize"] == "64x48"
    _check_gps(item["summary"])


def test_exif_missing_and_broken(osint, tmp_path, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda tool: None)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    missing, bad = media.exif(osint.cfg(), osint.case(), [str(tmp_path / "nope.jpg"), str(broken)])
    assert missing["error"] == "файл не найден"
    assert "error" in bad and bad["sha256"]
    assert osint.findings() == []


def test_reverse(osint, photo):
    by_url, local, missing = media.reverse(osint.cfg(), osint.case(),
                                           ["https://img.test/a b.jpg", str(photo), "/nope.png"])
    assert by_url["links"]["TinEye"] == "https://tineye.com/search?url=https%3A%2F%2Fimg.test%2Fa%20b.jpg"
    assert local["links"]["Google Lens"] == "https://lens.google.com/" and "note" in local
    assert missing["error"] == "файл не найден"
    assert len(osint.findings()) == 2


@pytest.mark.slow
def test_video_local(osint, clip):
    assert osint("media", "video", str(clip), "--frames", "8") == 0
    [finding] = osint.findings()
    data = finding["data"]
    assert data["sha256"] == sha256_file(clip)
    assert data["resolution"] == "320x240" and data["codec"] == "h264"
    assert data["duration_s"] == pytest.approx(6, abs=0.2)
    assert 1 <= data["frames"] <= 8
    frames = list((osint.case().dir / data["frames_dir"]).glob("*.jpg"))
    assert len(frames) == data["frames"]
    registered = {e["file"] for e in osint.evidence()}
    assert all(str(f.relative_to(osint.case().dir)) in registered for f in frames)


def test_video_missing(osint, capsys):
    assert osint("media", "video", "/nope.mp4") == 1
    assert "Файл не найден" in capsys.readouterr().out


@pytest.mark.slow
def test_download_and_video_by_url(osint, server, clip):
    server.add("/v/clip.mp4", clip.read_bytes(), ctype="video/mp4")
    res = media.download(osint.cfg(), osint.case(), server.url("/v/clip.mp4"))
    videos = [f for f in res["files"] if f.endswith(".mp4")]
    assert len(videos) == 1
    assert sha256_file(osint.case().dir / videos[0]) == sha256_file(clip)

    assert osint("media", "video", server.url("/v/clip.mp4"), case="by-url") == 0
    assert any(f["title"].startswith("Видео:") for f in osint.findings("by-url"))


def test_aicheck(osint, server, photo):
    osint.configure(keys={"hive": "HIVE-SECRET"})
    server.add(server.ext("api.thehive.ai", "/api/v2/task/sync"), method="POST", body={"status": [{"response": {
        "output": [{"classes": [{"class": "ai_generated", "score": 0.91234}, {"class": "not_ai_generated", "score": 0.08},
                                {"class": "midjourney", "score": 0.7}, {"class": "dalle", "score": 0.01}]}]}}]})
    assert osint("media", "aicheck", str(photo)) == 0
    [finding] = osint.findings()
    assert finding["data"]["scores"] == {"ai_generated": 0.912, "not_ai_generated": 0.08, "midjourney": 0.7}
    req = server.hits(server.ext("api.thehive.ai", "/api/v2/task/sync"))[0]
    assert req["headers"]["Authorization"] == "Token HIVE-SECRET"
    assert "HIVE-SECRET" not in osint.case_text()


@pytest.fixture
def screenshot(tmp_path, monkeypatch):
    """Скриншот macOS в домашней папке «someuser»: UserComment=Screenshot, 144 dpi, дата только в имени."""
    from PIL import Image

    home = tmp_path / "Users" / "someuser"
    (home / "Pictures").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    path = home / "Pictures" / "Screenshot 2026-10-23 at 8.47.47 PM.png"
    exif = Image.Exif()
    exif[0x8769] = {0x9286: b"ASCII\x00\x00\x00Screenshot"}
    Image.new("RGB", (60, 40), "white").save(path, exif=exif, dpi=(144, 144))
    return path


@pytest.mark.skipif(not shutil.which("exiftool"), reason="немає exiftool")
def test_exif_screenshot_is_explained_and_private(osint, screenshot):
    media.exif(osint.cfg(), osint.case(), [str(screenshot)])
    [finding] = osint.findings()
    d = finding["data"]
    assert d["тип"] == "снимок экрана"
    assert d["дата"] == "2026-10-23 20:47:47 (из имени файла, не из метаданных)"
    assert d["плотность"] == "144 dpi (экран Retina)" and d["размер"] == "60×40"
    assert "GPS" in d["нет данных"]
    # имя пользователя Mac и путь к его папкам в дело не попадают
    assert "someuser" not in osint.case_text()
    assert finding["target"] == "~/Pictures/" + screenshot.name


def test_exif_camera_photo_summary(osint, photo):
    media.exif(osint.cfg(), osint.case(), [str(photo)])
    d = osint.findings()[0]["data"]
    assert d["камера"] == "TestCam Model X" and d["карта"].startswith("https://www.google.com/maps?q=50.45")
    assert "нет данных" not in d


def test_exif_reads_text_on_image(osint, photo, monkeypatch):
    monkeypatch.setattr(media, "ocr", lambda p: "Пропал человек\nзвонить: help@volunteers.test\nhttps://t.me/poisk")
    media.exif(osint.cfg(), osint.case(), [str(photo)])
    d = osint.findings()[0]["data"]
    assert d["текст на изображении"].startswith("Пропал человек")
    assert d["в тексте"] == {"e-mail": ["help@volunteers.test"], "ссылки": ["https://t.me/poisk"]}
    assert any(e["file"].startswith("media/ocr_") for e in osint.case().evidence())


@pytest.mark.slow
@pytest.mark.live_ocr
@pytest.mark.skipif(not shutil.which("swiftc"), reason="нет swiftc (Xcode Command Line Tools)")
def test_ocr_vision_live(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(img).text((20, 40), "HELLO OSINT 2026", fill="black", font=ImageFont.load_default(size=64))
    img.save(tmp_path / "t.png")
    assert "HELLO OSINT" in media.ocr(tmp_path / "t.png").upper()


def test_screenshot_dates_device_and_screen_clock(osint, screenshot, monkeypatch):
    import os
    import time as _t

    # дата файла на диске — на 7 с позже имени; в деле — только UTC, без часового пояса аналитика
    local = _t.mktime((2026, 10, 23, 20, 47, 54, 0, 0, -1))
    os.utime(screenshot, (local, local))
    monkeypatch.setattr(media, "ocr", lambda p: "Terminal  Fri Oct 23 8:47 PM\nuser -")
    media.exif(osint.cfg(), osint.case(), [str(screenshot)])
    media.exif(osint.cfg(), osint.case(), [str(screenshot)])
    first, second = osint.findings()
    d = first["data"]
    utc = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(local))
    assert d["файл на этом компьютере"] == f"{utc} UTC — сохранён или скопирован сюда; совпадает с именем (+7 с)"
    assert d["время на снимке"] == "8:47 PM — совпадает с датой"
    assert second["data"]["повтор проверки"] == first["id"]


def test_device_guess_by_screen_resolution():
    assert media.device_guess("3024×1964", True) == "Возможно, MacBook Pro 14″ (родное разрешение экрана)"
    assert media.device_guess("1179×2556", True).startswith("Возможно, iPhone")
    assert media.device_guess("3024×1964", False) is None  # только для скриншотов
    assert media.device_guess("100×100", True) is None
