"""Скриншоты оверлея, подключённого к живому ядру (npm run demo).

Запуск: .venv/bin/python scripts/screenshot_live.py [port] [секунд]
"""
import os
import sys
import time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(os.path.dirname(__file__), "..", ".cache", "ms-playwright"))
from playwright.sync_api import sync_playwright  # noqa: E402

port = sys.argv[1] if len(sys.argv) > 1 else "8787"
total = int(sys.argv[2]) if len(sys.argv) > 2 else 60
out = os.path.join(os.path.dirname(__file__), "..", "screenshots")
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1920, "height": 1080})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(f"http://127.0.0.1:{port}/overlay.html?bg=1")
    start = time.time()
    n = 0
    while time.time() - start < total:
        time.sleep(total / 6)
        n += 1
        page.screenshot(path=os.path.join(out, f"live_{n:02d}.png"))
    b.close()
print("JS errors:", errors or "нет")
sys.exit(1 if errors else 0)
