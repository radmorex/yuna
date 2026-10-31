"""Запуск окна: python -m osint_kit.app (или osint app, или Fallen Angel.app)."""

from __future__ import annotations

import sys

from . import UI_DIR, page_html
from .api import Api

ICON = UI_DIR / "fallen-angel.png"


def _macos_identity() -> None:
    """Имя и иконка в Dock и меню вместо «python»."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Fallen Angel"
        if ICON.exists():
            NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except Exception:  # noqa: BLE001 — косметика, окно работает и без неё
        pass


def _accept_drops(window) -> None:
    """Файл, перетащенный в окно, — в поле ввода. Полный путь знает только pywebview (Python-обработчик drop),
    в самой странице у файла есть лишь имя."""
    import json

    from webview.dom import DOMEventHandler

    def on_drop(event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"window.fileDropped && window.fileDropped({json.dumps(paths[0])})")

    doc = window.dom.document
    doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
    doc.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)


def main() -> int:
    from .. import config

    config.ensure_tool_path()  # из Finder PATH без Homebrew
    _macos_identity()  # до импорта pywebview: он создаёт NSApplication, и имя берётся в этот момент
    import webview

    api = Api()
    if api._cfg.encrypt_cases:
        try:
            api.unlock()  # как CLI: хранилище открывается при запуске; ошибка покажется на экране блокировки
        except Exception:  # noqa: BLE001
            pass
    window = webview.create_window("Fallen Angel", html=page_html(), js_api=api, width=1380, height=860,
                                   min_size=(900, 560), background_color="#000000", text_select=True)
    api._window = window
    window.events.loaded += lambda: _accept_drops(window)
    webview.start(private_mode=True, debug="--debug" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
