"""Register system fonts for Qt's offscreen Windows renderer when needed."""
from __future__ import annotations
from pathlib import Path
import os
from PyQt6.QtGui import QFont, QFontDatabase


def configure_typography(app):
    # The offscreen QPA plugin has no font database on Windows. Native Qt does.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "YuGothM.ttc", "seguisym.ttf"):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    families = set(QFontDatabase.families())
    family = next((name for name in ("Segoe UI", "Noto Sans", "DejaVu Sans") if name in families), "sans-serif")
    app.setFont(QFont(family, 10))
