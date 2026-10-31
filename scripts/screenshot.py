"""Visual QA: renders public/overlay.html?demo=1&bg=1 and control.html with Playwright.

Usage:
  PLAYWRIGHT_BROWSERS_PATH=.cache/ms-playwright .venv/bin/python scripts/screenshot.py [--crops]
Screenshots go to screenshots/. --crops also saves avatar close-ups (screenshots/crops/).
"""
import functools
import http.server
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
OUT = ROOT / "screenshots"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".cache" / "ms-playwright"))

from playwright.sync_api import sync_playwright  # noqa: E402

AVATAR_CLIP = {"x": 720, "y": 360, "width": 520, "height": 680}


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(PUBLIC))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    crops = "--crops" in sys.argv
    OUT.mkdir(exist_ok=True)
    (OUT / "crops").mkdir(exist_ok=True) if crops else None
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    shots = [
        # name, emotion, mouth (None = closed)
        ("overlay_neutral", "neutral", None),
        ("overlay_smug", "smug", None),
        ("overlay_laugh", "laugh", None),
        ("overlay_surprised", "surprised", None),
        ("overlay_angry", "angry", None),
        ("overlay_speaking", "neutral", 0.8),
    ]
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/overlay.html?demo=1&bg=1")
        page.wait_for_function("window.__mira && window.MiraAvatar")
        page.evaluate("window.__mira.pauseDemo()")
        page.wait_for_timeout(600)
        for name, emo, mouth in shots:
            page.evaluate(
                """([e, m]) => { const M = window.__mira; M.setEmotion(e); M.freeze({mouth: m}); }""",
                [emo, mouth],
            )
            page.wait_for_timeout(250)
            page.screenshot(path=str(OUT / f"{name}.png"))
            if crops:
                page.screenshot(path=str(OUT / "crops" / f"{name}.png"), clip=AVATAR_CLIP)
            print("saved", name)
        # layout params
        for name, q in [("overlay_params_chat0", "chat=0"), ("overlay_params_right", "side=right&scale=0.8&x=40&y=40")]:
            pp = browser.new_page(viewport={"width": 1920, "height": 1080})
            pp.on("pageerror", lambda e, n=name: errors.append(f"{n}: {e}"))
            pp.goto(f"{base}/overlay.html?demo=1&bg=1&{q}")
            pp.wait_for_function("window.__mira")
            pp.evaluate("__mira.pauseDemo(); __mira.setEmotion('smug'); __mira.freeze()")
            pp.wait_for_timeout(300)
            pp.screenshot(path=str(OUT / f"{name}.png"))
            print("saved", name)
            pp.close()

        # VRM mode with a missing model must fall back to the SVG avatar
        vp = browser.new_page(viewport={"width": 1920, "height": 1080})
        vp.on("pageerror", lambda e: errors.append(f"vrm-fallback: {e}"))
        vp.goto(f"{base}/overlay.html?demo=1&bg=1&avatar=vrm&model=/models/__missing__.vrm")
        vp.wait_for_timeout(6000)
        kind = vp.evaluate("__mira.avatar.kind")
        stages = vp.evaluate("document.querySelectorAll('#avatar .echo-stage').length")
        print("vrm fallback:", kind, "portrait avatars:", stages)
        if kind != "svg" or stages != 1:
            errors.append(f"vrm fallback broken: kind={kind} stages={stages}")
        vp.close()

        if crops:  # contact sheet of avatar close-ups for quick review
            imgs = "".join(
                f'<figure><img src="file://{OUT / "crops" / (n + ".png")}"><figcaption>{n}</figcaption></figure>'
                for n, *_ in shots
            )
            sheet = OUT / "crops" / "sheet.html"
            sheet.write_text(
                "<style>body{margin:0;background:#111;display:flex;flex-wrap:wrap;color:#ccc;font:14px sans-serif}"
                "figure{margin:4px}img{width:420px;height:532px;object-fit:cover}</style>" + imgs
            )
            sp = browser.new_page(viewport={"width": 1720, "height": 1100})
            sp.goto(f"file://{sheet}")
            sp.wait_for_timeout(300)
            sp.screenshot(path=str(OUT / "crops" / "sheet.png"), full_page=True)

        # transparent companion window, shown on a dark backdrop
        dp = browser.new_page(viewport={"width": 380, "height": 640})
        dp.on("pageerror", lambda e: errors.append("desktop: " + str(e)))
        dp.goto(f"{base}/desktop.html")
        dp.add_style_tag(content="html,body{background:#1b1c21!important}")
        dp.evaluate("""() => { const b = document.getElementById('bubble'); b.hidden = false;
            b.textContent = 'Ну що, знову дивимось це разом? Я вже маю думку.'; }""")
        dp.wait_for_timeout(900)
        dp.screenshot(path=str(OUT / "desktop.png"))
        print("saved desktop")
        dp.close()

        tp = browser.new_page(viewport={"width": 1280, "height": 900})
        tp.on("pageerror", lambda e: errors.append("talk: " + str(e)))
        tp.goto(f"{base}/talk.html")
        tp.evaluate("document.getElementById('frame').src = 'overlay.html?demo=1'")
        tp.wait_for_timeout(1500)
        tp.screenshot(path=str(OUT / "talk.png"))
        print("saved talk")
        tp.close()

        cp = browser.new_page(viewport={"width": 1440, "height": 900})
        cp.on("pageerror", lambda e: errors.append("control: " + str(e)))
        cp.goto(f"{base}/control.html?demo=1")
        cp.wait_for_timeout(1500)
        cp.screenshot(path=str(OUT / "control.png"))
        print("saved control")
        browser.close()
    srv.shutdown()
    if errors:
        print("PAGE ERRORS:", *errors, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
