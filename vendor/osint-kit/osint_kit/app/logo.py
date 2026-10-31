"""Логотип Fallen Angel: глаз-сфера, два кольца с глазами, четыре крыла (векторный, без внешних файлов).

    python -m osint_kit.app.logo          # → ui/logo.svg и ui/fallen-angel.png (иконка 1024×1024)
"""

from __future__ import annotations

import math
from pathlib import Path

UI = Path(__file__).resolve().parent / "ui"
CX, CY = 300, 196

def feather(bx, by, ang, length, width, grad, curl=0.0):
    a = math.radians(ang)
    dx, dy = math.cos(a), math.sin(a)
    px, py = -dy, dx
    bend = curl / max(length, 1)
    tx, ty = bx + dx * length + px * curl, by + dy * length + py * curl
    c1 = (bx + dx * length * .30 + px * width, by + dy * length * .30 + py * width)
    c2 = (tx - dx * length * .22 + px * width * 1.05, ty - dy * length * .22 + py * width * 1.05)
    c3 = (tx - dx * length * .18 - px * width * .55, ty - dy * length * .18 - py * width * .55)
    c4 = (bx + dx * length * .30 - px * width * .45, by + dy * length * .30 - py * width * .45)
    f = lambda q: f"{q[0]:.1f},{q[1]:.1f}"
    d = f"M{f((bx, by))} C{f(c1)} {f(c2)} {f((tx, ty))} C{f(c3)} {f(c4)} {f((bx, by))}Z"
    mid = (bx + dx * length * .5 + px * curl * .4, by + dy * length * .5 + py * curl * .4)
    rach = f"M{f((bx, by))} Q{f(mid)} {f((tx, ty))}"
    return (f'<path d="{d}" fill="url(#{grad})" stroke="#0a0a0a" stroke-width="1.3"/>'
            f'<path d="{rach}" stroke="#ffffff" stroke-opacity=".35" stroke-width=".9" fill="none"/>')


def wing(bx, by, a0, a1, side, upper):
    out = []
    s = 1 if side == "r" else -1
    k = 1.0 if upper else 0.72
    rows = [(9, 120, 205, 17), (8, 85, 135, 19), (6, 50, 75, 21)]  # маховые, второстепенные, кроющие
    for n, l0, l1, w in rows:
        for i in range(n):
            t = i / (n - 1)
            ang = a0 + (a1 - a0) * t
            prof = math.sin(math.pi * (0.15 + 0.7 * t)) if upper else math.sin(math.pi * (0.85 - 0.7 * t))
            length = (l0 + (l1 - l0) * prof) * k
            out.append(feather(bx, by, ang, length, w * (0.9 if upper else 0.8), "fea", -10 * s if upper else 8 * s))
    return "".join(out)


def ring_points(rx, ry, rot, t0, t1, n=90):
    r = math.radians(rot)
    pts = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((CX + x * math.cos(r) - y * math.sin(r), CY + x * math.sin(r) + y * math.cos(r)))
    return pts

def poly(pts):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)

def ring(rx, ry, rot, front):
    t0, t1 = (0, math.pi) if front else (math.pi, 2 * math.pi)
    d = poly(ring_points(rx, ry, rot, t0, t1))
    out = [f'<path d="{d}" stroke="#050505" stroke-width="15" fill="none" stroke-linecap="round"/>',
           f'<path d="{d}" stroke="url(#ring)" stroke-width="11" fill="none" stroke-linecap="round"/>',
           f'<path d="{d}" stroke="#ffffff" stroke-opacity=".35" stroke-width="2" fill="none"/>']
    # маленькие глаза вдоль кольца
    k = 7
    for i in range(k):
        t = t0 + (t1 - t0) * (i + 0.5) / k
        x, y = ring_points(rx, ry, rot, t, t, 1)[0]
        tang = math.degrees(math.atan2(ry * math.cos(t), -rx * math.sin(t))) + rot
        out.append(f'<g transform="translate({x:.1f},{y:.1f}) rotate({tang:.1f})">'
                   f'<path d="M-7,0 Q0,-6 7,0 Q0,6 -7,0Z" fill="#e9e9e9" stroke="#111" stroke-width="1.2"/>'
                   f'<circle r="2.6" fill="#111"/><circle cx="-.8" cy="-.9" r=".8" fill="#fff"/></g>')
    return "".join(out)

def svg(size_w: int = 600, size_h: int = 390, bg: str | None = None) -> str:
    wings = [wing(CX - 46, CY - 12, -172, -112, "l", True), wing(CX + 46, CY - 12, -8, -68, "r", True),
             wing(CX - 46, CY + 18, 170, 128, "l", False), wing(CX + 46, CY + 18, 10, 52, "r", False)]
    back = ring(118, 30, -28, False) + ring(118, 30, 28, False)
    front = ring(118, 30, -28, True) + ring(118, 30, 28, True)
    eye = (f'<circle cx="{CX}" cy="{CY}" r="72" fill="url(#ball)" stroke="#000" stroke-width="3"/>'
           f'<circle cx="{CX}" cy="{CY}" r="44" fill="url(#iris)" stroke="#0a0a0a" stroke-width="2"/>'
           + "".join(f'<line x1="{CX + 24*math.cos(a):.1f}" y1="{CY + 24*math.sin(a):.1f}" x2="{CX + 38*math.cos(a):.1f}" y2="{CY + 38*math.sin(a):.1f}" stroke="#bdbdbd" stroke-opacity=".5" stroke-width="1.2"/>'
                     for a in [i * math.pi / 14 for i in range(28)])
           + f'<circle cx="{CX}" cy="{CY}" r="21" fill="#020202"/>'
           f'<ellipse cx="{CX - 14}" cy="{CY - 16}" rx="9" ry="6" fill="#fff" fill-opacity=".85" transform="rotate(-30 {CX - 14} {CY - 16})"/>'
           f'<path d="M{CX - 50},{CY - 30} A72 72 0 0 1 {CX + 30},{CY - 56}" stroke="#fff" stroke-opacity=".25" stroke-width="4" fill="none"/>')
    defs = ('<defs>'
            '<linearGradient id="fea" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset=".55" stop-color="#bfbfbf"/><stop offset="1" stop-color="#4a4a4a"/></linearGradient>'
            '<linearGradient id="ring" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f5f5f5"/><stop offset=".5" stop-color="#8a8a8a"/><stop offset="1" stop-color="#2b2b2b"/></linearGradient>'
            '<radialGradient id="ball" cx=".38" cy=".32" r=".8"><stop offset="0" stop-color="#9a9a9a"/><stop offset=".45" stop-color="#3a3a3a"/><stop offset="1" stop-color="#050505"/></radialGradient>'
            '<radialGradient id="iris" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#1a1a1a"/><stop offset=".6" stop-color="#6e6e6e"/><stop offset="1" stop-color="#d0d0d0"/></radialGradient>'
            '</defs>')
    bgr = f'<rect width="100%" height="100%" fill="{bg}"/>' if bg else ""
    return (f'<svg class="logo-art" viewBox="40 8 520 335" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            + defs + bgr + "".join(wings) + back + eye + front + "</svg>")

def icon_html(size: int = 1024) -> str:
    """Иконка приложения: логотип на чёрном скруглённом квадрате (форма как у иконок macOS)."""
    art = svg().replace("<svg ", f'<svg width="{int(size * .9)}" height="{int(size * .9 * 335 / 520)}" ', 1)
    pad = int(size * .1)
    return (f"<html><body style='margin:0;background:transparent'>"
            f"<div style='position:absolute;left:{pad}px;top:{pad}px;width:{size - 2 * pad}px;height:{size - 2 * pad}px;"
            f"border-radius:{int(size * .18)}px;background:radial-gradient(circle at 50% 42%,#1c1c1c,#000 70%);"
            f"box-shadow:inset 0 0 0 {max(2, size // 200)}px #2e2e2e;overflow:hidden;display:flex;align-items:center;justify-content:center'>"
            f"<div style='margin-top:-2%'>{art}</div></div></body></html>")


def build(size: int = 1024) -> tuple[Path, Path]:
    from playwright.sync_api import sync_playwright

    UI.mkdir(exist_ok=True)
    (UI / "logo.svg").write_text(svg(), encoding="utf-8")
    png = UI / "fallen-angel.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": size, "height": size})
        page.set_content(icon_html(size))
        page.screenshot(path=str(png), omit_background=True)
        browser.close()
    return UI / "logo.svg", png


if __name__ == "__main__":
    print(*build())
