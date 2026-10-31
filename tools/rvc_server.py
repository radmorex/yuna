#!/usr/bin/env python3
"""Сайдкар RVC: держит модель голоса в памяти и перекрашивает WAV.

POST /tts {"text"} → WAV (Piper + RVC), POST /convert (WAV) → WAV, GET /health. Только 127.0.0.1.
Запуск: npm run rvc. Настройки: RVC_VOICE (aria, anna), RVC_BASE (svetlana, piper), RVC_KEY, RVC_MODEL, RVC_INDEX, RVC_PORT, RVC_THREADS.

ВНИМАНИЕ: файлы .pth — это pickle. Отключение weights_only ниже нужно базовым моделям RVC,
поэтому кладите в voices/ только проверенные модели.
"""
import json
import logging
import os
import tempfile
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Без этих настроек faiss падает с segfault на macOS.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Сдвиг тона в полутонах: без него голос остаётся на высоте Piper (~185 Гц) и не похож на персонажа.
VOICE_KEYS = {"aria": 4, "anna": 7}
VOICE = os.environ.get("RVC_VOICE", "anna")
MODEL = os.environ.get("RVC_MODEL", os.path.join(ROOT, f"voices/{VOICE}/model.pth"))
INDEX = os.environ.get("RVC_INDEX", os.path.join(ROOT, f"voices/{VOICE}/model.index"))
KEY = int(os.environ.get("RVC_KEY", VOICE_KEYS.get(VOICE, 0)))
PORT = int(os.environ.get("RVC_PORT", "8812"))
THREADS = int(os.environ.get("RVC_THREADS", "3"))
MAX_BODY = 20 * 1024 * 1024
PIPER_MODEL = os.environ.get("PIPER_MODEL", os.path.join(ROOT, ".cache/piper/ru_RU-irina-medium.onnx"))
MAX_TEXT = 1000
# Базовый голос: svetlana (живая интонация, нужен интернет, текст уходит в Microsoft) или piper (локально).
BASE = os.environ.get("RVC_BASE", "svetlana")
EDGE_BIN = os.environ.get("EDGE_TTS_BIN", os.path.join(ROOT, ".venv-tts/bin/edge-tts"))
EDGE_VOICE = os.environ.get("EDGE_TTS_VOICE", "uk-UA-PolinaNeural")

log = logging.getLogger("rvc")
lock = threading.Lock()
rvc = None
piper = None


def load():
    global rvc, piper
    import torch
    from rvc_python.infer import RVCInference

    torch.set_num_threads(THREADS)
    log.info("Загружаю %s", MODEL)
    rvc = RVCInference(device="cpu:0", model_path=MODEL, index_path=INDEX, version="v2")
    rvc.set_params(f0method="rmvpe", f0up_key=KEY, index_rate=0.75, protect=0.33, rms_mix_rate=0.25)
    # Piper держим в памяти: запуск Python на каждую реплику стоил около 3 секунд.
    from piper import PiperVoice

    piper = PiperVoice.load(PIPER_MODEL)
    log.info("Готово")


def convert(wav: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="rvc-") as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.wav")
        with open(src, "wb") as f:
            f.write(wav)
        with lock:
            rvc.infer_file(src, dst)
        with open(dst, "rb") as f:
            return f.read()


def edge_base(text: str, d: str, wav_path: str) -> bool:
    """Базовая речь через edge-tts. Сервис иногда не отдаёт аудио, поэтому до трёх попыток."""
    mp3 = os.path.join(d, "base.mp3")
    for _ in range(3):
        try:
            # Форма --text=… — чтобы текст с дефиса в начале не стал флагом.
            subprocess.run([EDGE_BIN, "--voice", EDGE_VOICE, f"--text={text}", "--write-media", mp3],
                           capture_output=True, timeout=10, check=True)
            if os.path.getsize(mp3) > 0:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-threads", "1", "-i", mp3, "-ar", "44100", "-ac", "1", wav_path],
                               capture_output=True, timeout=10, check=True)
                return True
        except (subprocess.SubprocessError, OSError):
            pass
    return False


def speak(text: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="tts-") as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.wav")
        ok = BASE == "svetlana" and edge_base(text, d, src)
        if BASE == "svetlana" and not ok:
            log.warning("Svetlana недоступна, базовый голос: Piper")
        with lock:
            if not ok:
                with wave.open(src, "wb") as w:
                    piper.synthesize_wav(text, w)
            rvc.infer_file(src, dst)
        with open(dst, "rb") as f:
            return f.read()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, json.dumps({"ok": rvc is not None}).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if self.path not in ("/convert", "/tts"):
            return self._send(404, b"{}")
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BODY:
            return self._send(413, b'{"error":"body size"}')
        body = self.rfile.read(n)
        if self.path == "/tts":
            try:
                text = json.loads(body)["text"].strip()[:MAX_TEXT]
                if not text:
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                return self._send(400, b'{"error":"text"}')
            try:
                return self._send(200, speak(text), "audio/wav")
            except Exception:
                log.exception("ошибка синтеза")
                return self._send(500, b'{"error":"tts failed"}')
        if not body.startswith(b"RIFF"):
            return self._send(400, b'{"error":"not wav"}')
        try:
            self._send(200, convert(body), "audio/wav")
        except Exception:
            log.exception("ошибка конвертации")
            self._send(500, b'{"error":"convert failed"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[rvc] %(message)s")
    load()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
