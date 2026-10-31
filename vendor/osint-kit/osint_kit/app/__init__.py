"""Оконное приложение Fallen Angel поверх движка osint-kit."""

from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "ui"


def page_html() -> str:
    """index.html со встроенными CSS и JS: окно не загружает ничего извне."""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "app.css").read_text(encoding="utf-8")
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    logo = (UI_DIR / "logo.svg").read_text(encoding="utf-8")
    return html.replace("<!--LOGO-->", logo, 1).replace("/*CSS*/", css, 1).replace("/*JS*/", js, 1)
