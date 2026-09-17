"""Shared native design primitives; vector icons without external dependencies."""
from __future__ import annotations
import tempfile
from functools import lru_cache
from pathlib import Path
from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QAbstractButton, QLabel, QPushButton, QSizePolicy, QWidget
from ui.theme import active

ICONS = {
    "today": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "tasks": '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 9 1 1 2-2m2 1h3M8 15h2m3 0h3"/>',
    "detail": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8m-8 4h5"/>',
    "review": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "code": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="6" r="2"/><path d="M6 7v10m0-4h5a7 7 0 0 0 7-5"/>',
    "notes": '<path d="M4 4h6a3 3 0 0 1 3 3v14a3 3 0 0 0-3-3H4zm9 3a3 3 0 0 1 3-3h5v14h-5a3 3 0 0 0-3 3"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-14 4h2m4 0h2"/>',
    "team": '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m0-17a3 3 0 0 1 0 6m3 4a5 5 0 0 1 3 4v3"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "menu": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="m9 3-1 3-3 1 1 3-3 2 3 2-1 3 3 1 1 3h6l1-3 3-1-1-3 3-2-3-2 1-3-3-1-1-3Z"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
    "moon": '<path d="M20 15A8 8 0 0 1 9 4a8 8 0 1 0 11 11Z"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18 2 2 0 0 0 1.6-3.2 2 2 0 0 1 1.6-3.2H18a3 3 0 0 0 3-3 9 9 0 0 0-9-8.6Z"/><circle cx="7.5" cy="11.5" r="1.1"/><circle cx="10.5" cy="7.5" r="1.1"/><circle cx="15" cy="8.5" r="1.1"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "inbox": '<path d="m5 4-3 10v6h20v-6L19 4ZM2 14h6l2 3h4l2-3h6"/>',
}

ASSETS = Path(__file__).resolve().parents[1] / "assets"


@lru_cache(maxsize=128)
def _render(name: str, color: str, size: int) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """A glyph in `color`, defaulting to the active theme's secondary text colour."""
    return _render(name, color or active().muted, size)


@lru_cache(maxsize=16)
def checkmark(color: str) -> str:
    """Path to a check glyph in `color`, for the QSS `image:` of checked indicators."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="m4 8 3 3 5-6" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    try:
        folder = Path(tempfile.gettempdir()) / "ty-work-hub-icons"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"check-{color.lstrip('#')}.svg"
        target.write_text(svg, encoding="utf-8")
        return target.as_posix()
    except OSError:
        # An unwritable temp directory costs the tinted glyph, not the checkbox.
        return (ASSETS / "check.svg").as_posix()


def glyph_color(control: QAbstractButton) -> str:
    """A glyph has to read against whatever the button's own background is."""
    palette = active()
    if control.property("secondary") is False:
        return palette.primary_text
    if control.isCheckable() and control.isChecked():
        return palette.accent
    return palette.muted


def button(text: str, callback=None, *, glyph: str = "", primary: bool = False) -> QPushButton:
    control = QPushButton(text)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    control.setProperty("secondary", not primary)
    if glyph:
        # Remembered so a theme change can re-render the glyph in new colours.
        control.setProperty("glyph", glyph)
        control.setIcon(icon(glyph, glyph_color(control)))
        control.setIconSize(QSize(16, 16))
    if callback:
        control.clicked.connect(callback)
    return control


def retint_icons(root: QWidget) -> None:
    """Re-render the glyphs under `root` after the theme or a selection changed."""
    for control in root.findChildren(QAbstractButton):
        glyph = control.property("glyph")
        if glyph:
            control.setIcon(icon(str(glyph), glyph_color(control)))


class ElidedLabel(QLabel):
    """Dynamic titles never increase the containing layout's minimum width."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.rect(), self.alignment(), self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()))


class FocusRing(QWidget):
    def __init__(self):
        super().__init__()
        self.progress = 0.0
        self.setFixedSize(84, 84)

    def set_progress(self, progress: float):
        value = max(0.0, min(1.0, progress))
        if value != self.progress:
            self.progress = value
            self.update()

    def paintEvent(self, event):
        palette = active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(6, 6, 72, 72)
        painter.setPen(QPen(QColor(palette.ring_track), 4))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(palette.accent), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.progress:
            painter.drawArc(rect, 90 * 16, -int(self.progress * 360 * 16))
        painter.setPen(QPen(QColor(palette.ring_hand), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(42, 42, 42, 26)
        painter.drawLine(42, 42, 53, 48)


class WeekBars(QWidget):
    def __init__(self):
        super().__init__()
        self.values = [0] * 7
        self.labels = []
        self.setMinimumHeight(68)

    def set_data(self, values, labels):
        self.values, self.labels = values, labels
        self.setToolTip("\n".join(f"{day}: {value // 60}h {value % 60:02d}m" for day, value in zip(labels, values)))
        self.update()

    def paintEvent(self, event):
        palette = active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ceiling, slot = max(60, max(self.values)), self.width() / 7
        for i, value in enumerate(self.values):
            height = max(3, (self.height() - 28) * value / ceiling)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.chart_today if i == 6 else palette.chart))
            painter.drawRoundedRect(QRectF(i * slot + slot * .25, self.height() - 20 - height, slot * .5, height), 3, 3)
            painter.setPen(QColor(palette.chart_axis))
            if self.labels:
                painter.drawText(QRectF(i * slot, self.height() - 17, slot, 17), Qt.AlignmentFlag.AlignCenter, self.labels[i])
