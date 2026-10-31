"""Сборка Fallen Angel.app: иконка (ui/fallen-angel.png, рисует logo.py) и пакет приложения macOS,
который запускает окно из venv проекта.

    python -m osint_kit.app.bundle            # → ~/Applications/Fallen Angel.app
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import UI_DIR

ROOT = Path(__file__).resolve().parents[2]
ICON_PNG = UI_DIR / "fallen-angel.png"
APP_NAME = "Fallen Angel"


def make_icns(png: Path, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "FallenAngel.iconset"
        iconset.mkdir()
        for px in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{px}x{px}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px * scale), str(px * scale), str(png), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    return out


def build(dest: Path = Path.home() / "Applications") -> Path:
    app = dest / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    if not ICON_PNG.exists():
        from .logo import build as draw_logo
        draw_logo()
    make_icns(ICON_PNG, app / "Contents" / "Resources" / "FallenAngel.icns")
    python = ROOT / ".venv" / "bin" / "python"
    launcher = app / "Contents" / "MacOS" / "FallenAngel"
    launcher.write_text("#!/bin/bash\n"
                        "# Окно Fallen Angel из venv проекта osint-kit\n"
                        f'cd "{ROOT}" || exit 1\n'
                        f'exec "{python}" -m osint_kit.app "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({
            "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": "local.osintkit.fallenangel",
            "CFBundleExecutable": "FallenAngel", "CFBundleIconFile": "FallenAngel", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.4.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True, "LSApplicationCategoryType": "public.app-category.utilities",
        }, fh)
    subprocess.run(["touch", str(app)], check=False)  # Finder перечитает иконку
    return app


if __name__ == "__main__":
    print(build(Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Applications"))
