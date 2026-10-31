#!/usr/bin/env python3
"""Silero TTS сайдкар для «Юны».

    python tools/silero_server.py            # реальный Silero (нужны torch + numpy)
    python tools/silero_server.py --mock     # без torch: синус-бипы (для тестов)

API:
    GET  /health -> {"ok": true, "mock": bool, "modelLoaded": bool}
    POST /tts {"text": "...", "speaker": "xenia", "sample_rate": 48000} -> audio/wav (PCM int16 mono)

Env: SILERO_HOST (127.0.0.1), SILERO_PORT (8811), SILERO_SPEAKER (xenia), SILERO_MODEL (v4_ru).
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import re
import socketserver
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("silero")

ALLOWED_SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")
ALLOWED_RATES = (8000, 24000, 48000)
MAX_TEXT = 1000
MAX_BODY = 16 * 1024

_UNITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
          "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
         "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
             "восемьсот", "девятьсот"]


def _num_words(n: int) -> str:
    """Число 0..999999 словами (мужской род, без склонений — для TTS достаточно)."""
    if n < 20:
        return _UNITS[n]
    if n < 100:
        t, u = divmod(n, 10)
        return _TENS[t] + ("" if u == 0 else " " + _UNITS[u])
    if n < 1000:
        h, r = divmod(n, 100)
        return _HUNDREDS[h] + ("" if r == 0 else " " + _num_words(r))
    th, r = divmod(n, 1000)
    last2, last = th % 100, th % 10
    if 11 <= last2 <= 19:
        w = "тысяч"
    elif last == 1:
        w = "тысяча"
    elif 2 <= last <= 4:
        w = "тысячи"
    else:
        w = "тысяч"
    head = _num_words(th)
    head = re.sub(r"(^|\s)один$", r"\1одна", head)
    head = re.sub(r"(^|\s)два$", r"\1две", head)
    return f"{head} {w}" + ("" if r == 0 else " " + _num_words(r))


def digits_to_words(text: str) -> str:
    def rep(m):
        s = m.group(0)
        if len(s) > 6:
            return " ".join(_UNITS[int(c)] for c in s)
        return _num_words(int(s))
    return re.sub(r"\d+", rep, text)


_ALLOWED_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё\s.,!?:;\-—–()«»\"'…+%]")


def sanitize_text(text: str) -> str:
    """Оставляет кириллицу/латиницу/цифры/пунктуацию, цифры → слова, схлопывает пробелы."""
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = _ALLOWED_RE.sub(" ", text)
    text = digits_to_words(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class ValidationError(ValueError):
    pass


def validate_request(data, default_speaker: str):
    if not isinstance(data, dict):
        raise ValidationError("тело должно быть JSON-объектом")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("text обязателен")
    if len(text) > MAX_TEXT:
        raise ValidationError(f"text длиннее {MAX_TEXT} символов")
    speaker = data.get("speaker") or default_speaker
    if speaker not in ALLOWED_SPEAKERS:
        raise ValidationError(f"speaker должен быть одним из {', '.join(ALLOWED_SPEAKERS)}")
    rate = data.get("sample_rate", 48000)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate not in ALLOWED_RATES:
        raise ValidationError(f"sample_rate должен быть одним из {ALLOWED_RATES}")
    clean = sanitize_text(text)
    if not re.search(r"[A-Za-zА-Яа-яЁё]", clean):
        raise ValidationError("после очистки в тексте не осталось букв")
    return clean, speaker, rate


def pcm16_to_wav(samples_int16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples_int16)
    return buf.getvalue()


# ---------------- Движки ----------------

class MockEngine:
    name = "mock"
    loaded = True

    def synthesize(self, text: str, speaker: str, sample_rate: int) -> bytes:
        # по «бипу» на слово, длина ~ по числу букв; без numpy
        words = text.split()[:60] or ["x"]
        freq = 300.0 + ALLOWED_SPEAKERS.index(speaker) * 40
        out = bytearray()
        for wd in words:
            dur = min(0.08 + 0.03 * len(wd), 0.5)
            n = int(sample_rate * dur)
            for i in range(n):
                env = math.sin(math.pi * i / n)
                v = int(0.3 * 32767 * env * math.sin(2 * math.pi * freq * i / sample_rate))
                out += struct.pack("<h", v)
            out += b"\x00\x00" * int(sample_rate * 0.04)
        return pcm16_to_wav(bytes(out), sample_rate)


class SileroEngine:
    name = "silero"

    def __init__(self, model_id: str = "v4_ru"):
        self.model_id = model_id
        self.model = None
        self._lock = threading.Lock()  # загрузка и инференс — последовательно

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def _model_path(self) -> str:
        default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache", "silero", f"{self.model_id}.pt")
        return os.path.abspath(os.environ.get("SILERO_MODEL_PATH") or default)

    def _download(self, path: str):
        import urllib.request
        url = f"https://models.silero.ai/models/tts/ru/{self.model_id}.pt"
        log.info("Скачиваю модель %s → %s (~40 МБ, один раз)...", url, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except OSError as e:
            raise RuntimeError(
                f"не удалось скачать {url}: {e}. Скачайте файл вручную (браузер/VPN) и положите в {path} "
                "или укажите путь в SILERO_MODEL_PATH"
            ) from e
        os.replace(tmp, path)

    def _load(self):
        import torch  # noqa: WPS433 — ленивый импорт
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        path = self._model_path()
        if not os.path.isfile(path):
            self._download(path)
        # Модель — torch.package-архив с официального сайта; torch.hub не нужен (не исполняем код с GitHub).
        log.info("Загружаю Silero из %s", path)
        model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        model.to(torch.device("cpu"))
        self.model = model
        log.info("Модель загружена")

    def synthesize(self, text: str, speaker: str, sample_rate: int) -> bytes:
        import numpy as np
        with self._lock:
            if self.model is None:
                self._load()
            audio = self.model.apply_tts(text=text, speaker=speaker, sample_rate=sample_rate,
                                         put_accent=True, put_yo=True)
        arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        arr = np.clip(arr, -1.0, 1.0)
        pcm = (arr * 32767.0).astype("<i2").tobytes()
        return pcm16_to_wav(pcm, sample_rate)


# ---------------- HTTP ----------------

def make_handler(engine, default_speaker: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MiraSilero/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def _json(self, code: int, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/health":
                self._json(200, {"ok": True, "engine": engine.name, "mock": engine.name == "mock",
                                 "modelLoaded": engine.loaded})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/tts":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                self._json(400 if length <= 0 else 413, {"error": "некорректный размер тела"})
                return
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                text, speaker, rate = validate_request(data, default_speaker)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "невалидный JSON"})
                return
            except ValidationError as e:
                self._json(400, {"error": str(e)})
                return
            try:
                wav_bytes = engine.synthesize(text, speaker, rate)
            except ImportError as e:
                log.error("Нет зависимостей: %s", e)
                self._json(503, {"error": "torch/numpy не установлены — см. tools/SILERO.md"})
                return
            except Exception as e:  # noqa: BLE001
                log.exception("Ошибка синтеза")
                self._json(500, {"error": f"ошибка синтеза: {type(e).__name__}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.end_headers()
            self.wfile.write(wav_bytes)

        do_PUT = do_DELETE = lambda self: self._json(405, {"error": "method not allowed"})  # noqa: E731

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind зовёт socket.getfqdn() — на некоторых машинах это секунды DNS.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


def make_server(host: str, port: int, mock: bool, speaker: str = "xenia", model_id: str = "v4_ru"):
    if speaker not in ALLOWED_SPEAKERS:
        raise SystemExit(f"SILERO_SPEAKER={speaker!r} не из списка {ALLOWED_SPEAKERS}")
    engine = MockEngine() if mock else SileroEngine(model_id)
    srv = _Server((host, port), make_handler(engine, speaker))
    srv.engine = engine
    return srv


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Silero TTS sidecar для Юны")
    p.add_argument("--mock", action="store_true", help="без torch: синтетические бипы")
    p.add_argument("--host", default=os.environ.get("SILERO_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("SILERO_PORT", "8811")))
    p.add_argument("--speaker", default=os.environ.get("SILERO_SPEAKER", "xenia"))
    p.add_argument("--model", default=os.environ.get("SILERO_MODEL", "v4_ru"))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("Сайдкар слушает %s — он без авторизации, лучше 127.0.0.1", a.host)
    srv = make_server(a.host, a.port, a.mock, a.speaker, a.model)
    log.info("Silero sidecar (%s) на http://%s:%d", srv.engine.name, a.host, a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
