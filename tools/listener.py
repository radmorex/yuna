#!/usr/bin/env python3
"""Слушает звук ПК, пока в пульте включено прослушивание, и шлёт распознанный текст в ядро.

Аудио не сохраняется. Выключено — захват остановлен, модель выгружена из памяти.
Настройки: CORE_URL, WHISPER_MODEL (по умолчанию base), WHISPER_THREADS, LISTEN_CHUNK_SECS.
"""
import gc
import json
import logging
import os
import select
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CORE = os.environ.get("CORE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("CONTROL_TOKEN", "")
SYSCAP = os.environ.get("SYSCAP_BIN", os.path.join(ROOT, ".cache/bin/syscap"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
THREADS = int(os.environ.get("WHISPER_THREADS", "2"))
CHUNK_SECS = int(os.environ.get("LISTEN_CHUNK_SECS", "12"))
RATE = 16_000
SILENCE_RMS = 0.004
SLAVIC = {"ru", "be", "bg", "sr", "pl", "sk", "cs", "hr", "sl", "mk"}
log = logging.getLogger("listener")


def api(path, body=None):
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["X-Control-Token"] = TOKEN
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(CORE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def is_silent(pcm: bytes) -> bool:
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
    return samples.size == 0 or float(np.sqrt((samples**2).mean())) < SILENCE_RMS


def transcribe(model, pcm: bytes) -> str:
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
    segments, info = model.transcribe(samples, vad_filter=True, beam_size=1, condition_on_previous_text=False)
    # Звук ПК бывает на любом языке, но неуверенно определённую славянскую речь считаем украинской.
    if info.language != "uk" and info.language in SLAVIC and info.language_probability < 0.85:
        segments, _ = model.transcribe(samples, language="uk", vad_filter=True, beam_size=1, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def listening() -> bool:
    try:
        return bool(api("/api/state").get("listening"))
    except (OSError, ValueError):
        return False


def fail(message: str):
    log.error(message)
    try:
        api("/api/listen", {"enabled": False, "error": message})
    except OSError:
        pass


def session():
    """Один сеанс: от включения прослушивания до выключения."""
    if not os.access(SYSCAP, os.X_OK):
        return fail("Немає модуля захоплення звуку: запусти npm run desktop")
    from faster_whisper import WhisperModel

    log.info("Загружаю модель распознавания %s", WHISPER_MODEL)
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS,
                         download_root=os.path.join(ROOT, ".cache/whisper"))
    proc = subprocess.Popen([SYSCAP], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    need = RATE * 2 * CHUNK_SECS
    buf = bytearray()
    last_check = time.time()
    try:
        while True:
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "ignore").strip()
                return fail("Немає доступу до звуку. Дозволь «Запис екрана» в налаштуваннях macOS. " + err[:120])
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if chunk:
                    buf.extend(chunk)
            if time.time() - last_check > 2:
                last_check = time.time()
                if not listening():
                    return
            if len(buf) >= need:
                pcm, buf = bytes(buf[:need]), bytearray(buf[need:])
                if is_silent(pcm):
                    continue
                text = transcribe(model, pcm)
                if text:
                    log.info("распознано %d символов", len(text))
                    try:
                        api("/api/transcript", {"text": text})
                    except urllib.error.HTTPError as e:
                        if e.code != 409:
                            log.warning("ядро ответило %s", e.code)
    finally:
        proc.kill()
        proc.wait()
        del model
        gc.collect()
        log.info("Прослушивание остановлено, модель выгружена")


def main():
    logging.basicConfig(level=logging.INFO, format="[listener] %(message)s")
    while True:
        if listening():
            try:
                session()
            except Exception as e:  # сбой распознавания не должен ронять приложение
                fail(f"Збій прослуховування: {type(e).__name__}")
            # Python не отдаёт память системе даже после выгрузки модели, поэтому начинаем с чистого процесса.
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        time.sleep(2)


if __name__ == "__main__":
    main()
