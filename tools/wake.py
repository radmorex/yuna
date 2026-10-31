#!/usr/bin/env python3
"""Слово-активатор: пока включено, слушает микрофон и реагирует на фразы, начинающиеся с «Юна».

Слово ищется локально: чужие фразы не логируются и никуда не уходят. Звук команды после «Юна» разбирает `transcribe.py`
(Gemini, а в инкогнито только локально). На диске звук не остаётся.
Настройки: CORE_URL, WAKE_MODEL (по умолчанию tiny), WAKE_THREADS.
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake_logic  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CORE = os.environ.get("CORE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("CONTROL_TOKEN", "")
MODEL = os.environ.get("WAKE_MODEL", "tiny")
THREADS = int(os.environ.get("WAKE_THREADS", "2"))
FOLLOW_UP_SECS = 8
ACK_SOUND = "/System/Library/Sounds/Tink.aiff"
log = logging.getLogger("wake")


def api(path, body=None):
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["X-Control-Token"] = TOKEN
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(CORE + path, data=data, headers=headers), timeout=10) as resp:
        return json.load(resp)


def state():
    try:
        return api("/api/state")
    except (OSError, ValueError):
        return {}


def recognize(model, pcm):
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
    segments, _ = model.transcribe(samples, language="uk", vad_filter=True, beam_size=1, condition_on_previous_text=False,
                                   initial_prompt="Юна, відкрий.", without_timestamps=True)
    return " ".join(s.text.strip() for s in segments).strip()


def recognize_exactly(pcm, local):
    """Команду разбирает Gemini или точная модель. Отдельный процесс: большая модель не держится в памяти между командами."""
    with tempfile.TemporaryDirectory(prefix="yuna-wake-") as folder:
        path = os.path.join(folder, "phrase.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(wake_logic.RATE)
            w.writeframes(pcm)
        flags = ["--local"] if local else []
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools/transcribe.py"), *flags, path], capture_output=True, text=True, timeout=120)
    return out.stdout.strip() if out.returncode == 0 else ""


def ack():
    subprocess.Popen(["/usr/bin/afplay", ACK_SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def handle(model, pcm, follow_up_until, current):
    """Разбирает фразу и возвращает новый срок, до которого слушаем без слова-активатора."""
    awaiting = bool(current.get("awaitingConfirm"))
    open_ear = awaiting or time.time() < follow_up_until
    quick = recognize(model, pcm)
    rest = wake_logic.after_wake(quick)
    if rest is None and not open_ear:
        return follow_up_until
    ack()
    if rest == "":
        api("/api/wake/command", {"text": ""})
        return time.time() + FOLLOW_UP_SECS
    # Короткий ответ («так») быстрая модель слышит достаточно хорошо, а точная запускается только на команду.
    # В инкогнито звук из дома не выходит.
    exact = quick if awaiting else recognize_exactly(pcm, bool(current.get("incognito"))) or quick
    command = wake_logic.after_wake(exact)
    command = exact if command is None else command
    if command:
        api("/api/wake/command", {"text": command})
    return 0.0


def session():
    """Один сеанс: от включения до выключения. После него процесс начинается заново, чтобы вернуть память системе."""
    from faster_whisper import WhisperModel

    log.info("Завантажую модель %s", MODEL)
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS, download_root=os.path.join(ROOT, ".cache/whisper"))
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "avfoundation", "-i", ":default", "-ac", "1", "-ar", str(wake_logic.RATE), "-f", "s16le", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    segmenter = wake_logic.Segmenter()
    follow_up_until, last_check = 0.0, 0.0
    current = state()
    log.info("Слухаю слово «Юна»")
    try:
        while True:
            frame = proc.stdout.read(wake_logic.FRAME_BYTES)
            if not frame:
                api("/api/listen", {"enabled": False, "error": "Немає доступу до мікрофона: дозволь його в налаштуваннях macOS"})
                api("/api/wake", {"enabled": False})
                return
            now = time.time()
            if now - last_check > 0.5:
                last_check = now
                current = state()
                if not current.get("wake"):
                    return
            # Пока она говорит или идёт запись кнопкой, микрофон слышит не команду.
            if current.get("speaking") or current.get("wakePaused"):
                segmenter.reset()
                if current.get("awaitingConfirm"):
                    follow_up_until = now + FOLLOW_UP_SECS
                continue
            phrase = segmenter.feed(frame)
            if phrase:
                follow_up_until = handle(model, phrase, follow_up_until, current)
                # Пока шёл разбор, ffmpeg копил звук. Старое в поток не возвращаем: сегментатор начнёт с чистого листа.
                segmenter.reset()
    finally:
        proc.kill()
        proc.wait()


def main():
    logging.basicConfig(level=logging.INFO, format="[wake] %(message)s")
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    while True:
        if state().get("wake"):
            try:
                session()
            except Exception as e:  # сбой не должен ронять приложение
                log.error("Збій: %s", type(e).__name__)
                time.sleep(5)
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        time.sleep(2)


if __name__ == "__main__":
    main()
