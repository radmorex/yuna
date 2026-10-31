#!/usr/bin/env python3
"""Окно компаньона: прозрачное, поверх остальных. После перетаскивания прилипает к ближайшему углу."""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desktop_layout as layout  # noqa: E402
import mic_silence  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CORE = os.environ.get("CORE_URL", "http://127.0.0.1:8787").rstrip("/")
STATE = os.path.join(ROOT, "data", "desktop.json")
SILENCE_DB = os.environ.get("MIC_SILENCE_DB", "-35dB")
SILENCE_SECS = os.environ.get("MIC_SILENCE_SECS", "1.5")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("corner") in layout.CORNERS:
            return {"corner": s["corner"], "compact": bool(s.get("compact"))}
    except (OSError, ValueError):
        pass
    return {"corner": "br", "compact": False}


def save_state():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)


state = load_state()
window = None
expected = (0, 0)
lock = threading.Lock()


def screen():
    s = webview.screens[0]
    return s.width, s.height, getattr(s, "x", 0) or 0, getattr(s, "y", 0) or 0


def apply_layout():
    """Ставит окно в сохранённый угол и сообщает странице, с какой стороны персонаж."""
    global expected
    sw, sh, sx, sy = screen()
    w, h = layout.size(state["compact"])
    x, y = layout.position(state["corner"], state["compact"], sw, sh, sx, sy)
    with lock:
        expected = (x, y)
        window.resize(w, h)
        window.move(x, y)
    save_state()
    window.evaluate_js(f"window.__dock && window.__dock({json.dumps(state)})")


mic = {"proc": None, "dir": None, "stt": None}


def incognito():
    """При сбое считаем, что инкогнито включено: лучше распознать хуже, чем отправить звук."""
    try:
        with urllib.request.urlopen(CORE + "/api/state", timeout=3) as resp:
            return bool(json.load(resp).get("incognito"))
    except (OSError, ValueError):
        return True


def wake_pause(paused):
    """Пока идёт запись кнопкой, слово-активатор молчит: иначе одна фраза уйдёт дважды."""
    try:
        urllib.request.urlopen(urllib.request.Request(CORE + "/api/wake", json.dumps({"paused": paused}).encode(),
                                                      {"Content-Type": "application/json"}), timeout=3).read()
    except OSError:
        pass


def watch_silence(proc):
    """Фраза закончилась: страница нажимает «Стоп» сама. Кнопка продолжает работать как раньше."""
    for line in proc.stderr:
        if mic_silence.phrase_ended(line) and mic["proc"] is proc:
            window.evaluate_js("window.__micAuto && window.__micAuto()")
            break
    # Дочитываем, чтобы ffmpeg не встал на заполненном канале.
    for _ in proc.stderr:
        pass


def drop_recording():
    """Окно закрыли посреди записи: запись обещана как несохраняемая, поэтому убираем и её."""
    for key in ("proc", "stt"):
        if mic[key]:
            mic[key].kill()
    if mic["dir"]:
        shutil.rmtree(mic["dir"], ignore_errors=True)


atexit.register(drop_recording)


def pc_control():
    """Разрешён ли доступ к ПК. Решает ядро, а без ответа считаем, что нет."""
    try:
        with urllib.request.urlopen(CORE + "/api/state", timeout=3) as r:
            return json.load(r).get("pcControl") is True
    except (OSError, ValueError):
        return False


class Api:
    def get_layout(self):
        return state

    def mic_start(self):
        """Запись с микрофона по умолчанию во временный файл. Не дольше минуты."""
        if mic["proc"]:
            return {"ok": True}
        if not pc_control():
            return {"ok": False, "error": "увімкни PC_CONTROL=true у .env"}
        wake_pause(True)
        mic["dir"] = tempfile.mkdtemp(prefix="yuna-mic-")
        wav = os.path.join(mic["dir"], "mic.wav")
        try:
            mic["proc"] = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-nostats", "-y", "-f", "avfoundation", "-i", ":default", "-t", "60", "-ac", "1", "-ar", "16000",
                 "-af", f"silencedetect=noise={SILENCE_DB}:d={SILENCE_SECS}", wav],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        except OSError as e:
            shutil.rmtree(mic["dir"], ignore_errors=True)
            mic["dir"] = None
            wake_pause(False)
            return {"ok": False, "error": str(e)}
        threading.Thread(target=watch_silence, args=(mic["proc"],), daemon=True).start()
        # Модель грузим уже сейчас: живёт только до конца распознавания и память потом освобождает.
        flags = ["--local"] if incognito() else []
        mic["stt"] = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools/transcribe.py"), *flags, "--serve"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        return {"ok": True}

    def mic_stop(self):
        """Останавливает запись и возвращает распознанный текст. Запись сразу удаляется."""
        proc, folder, stt = mic["proc"], mic["dir"], mic["stt"]
        mic["proc"], mic["dir"], mic["stt"] = None, None, None
        wake_pause(False)
        if not proc:
            return {"text": ""}
        try:
            # stderr читает watch_silence, поэтому communicate здесь нельзя.
            try:
                proc.stdin.write("q")
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            wav = os.path.join(folder, "mic.wav")
            if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
                return {"text": "", "error": "Немає звуку. Дозвольте доступ до мікрофона в налаштуваннях macOS."}
            text, _ = stt.communicate(wav + "\n", timeout=180)
            if stt.returncode != 0:
                return {"text": "", "error": "Не вдалося розпізнати."}
            return {"text": text.strip()}
        except subprocess.TimeoutExpired:
            return {"text": "", "error": "Розпізнавання надто довге."}
        finally:
            if stt.poll() is None:
                stt.kill()
            shutil.rmtree(folder, ignore_errors=True)

    def toggle_compact(self):
        state["compact"] = not state["compact"]
        apply_layout()
        return state


def watch_drag():
    """Окно перетащили и отпустили: прилипаем к ближайшему углу."""
    last, still = None, 0
    while True:
        time.sleep(0.2)
        try:
            pos = (window.x, window.y)
        except Exception:
            return
        with lock:
            exp = expected
        if not layout.moved(pos[0], pos[1], exp[0], exp[1]):
            last, still = None, 0
            continue
        still = still + 1 if pos == last else 0
        last = pos
        if still >= 2:
            sw, sh, sx, sy = screen()
            state["corner"] = layout.nearest_corner(pos[0], pos[1], state["compact"], sw, sh, sx, sy)
            apply_layout()
            last, still = None, 0


def on_start():
    apply_layout()
    threading.Thread(target=watch_drag, daemon=True).start()


for _ in range(60):
    try:
        urllib.request.urlopen(CORE + "/health", timeout=1)
        break
    except OSError:
        time.sleep(0.5)

w0, h0 = layout.size(state["compact"])
window = webview.create_window(
    "Юна",
    CORE + "/desktop.html",
    js_api=Api(),
    width=w0,
    height=h0,
    frameless=True,
    easy_drag=False,
    on_top=True,
    transparent=True,
    background_color="#000000",
)
webview.start(on_start)
