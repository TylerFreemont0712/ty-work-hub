"""Painted charts for the time insights workspace.

Every colour comes from the active theme, and every widget draws itself with no
child widgets so a long range stays cheap to render.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from modules.work_insights import format_minutes
from ui.theme import active

# Enough hues to separate a normal week's tickets without becoming decorative.
SERIES_TOKENS = ("info", "success", "warning", "accent", "danger", "neutral")


def series_color(index: int) -> str:
    palette = active()
    return str(getattr(palette, SERIES_TOKENS[index % len(SERIES_TOKENS)]))


class DayBars(QWidget):
    """Counted minutes per day, with the schedule's daily capacity as a guide."""

    day_selected = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.values: list[int] = []
        self.labels: list[str] = []
        self.capacity = 0
        self.selected = -1
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, values: list[int], labels: list[str], capacity: int = 0, selected: int = -1) -> None:
        self.values, self.labels = list(values), list(labels)
        self.capacity, self.selected = max(0, int(capacity)), selected
        self.setToolTip("\n".join(f"{day}: {format_minutes(value)}" for day, value in zip(self.labels, self.values)))
        self.update()

    def _slot(self) -> float:
        return self.width() / max(1, len(self.values))

    def mousePressEvent(self, event) -> None:
        if self.values:
            self.day_selected.emit(min(len(self.values) - 1, max(0, int(event.position().x() // self._slot()))))

    def paintEvent(self, event) -> None:
        if not self.values:
            return
        palette = active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        floor = self.height() - 20
        # Leave room above the tallest bar for the capacity guide and its label.
        ceiling = max(60, max(self.values), self.capacity) * 1.12
        slot = self._slot()
        # The capacity guide turns a bar height into "how much of a day is this".
        if self.capacity:
            guide = floor - (floor - 8) * self.capacity / ceiling
            pen = QPen(QColor(palette.border), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(guide), self.width(), int(guide))
            painter.setPen(QColor(palette.muted))
            painter.drawText(QRectF(0, max(0.0, guide - 16), self.width() - 4, 15), Qt.AlignmentFlag.AlignRight, f"{format_minutes(self.capacity)} target")
        wide = slot > 34
        for index, value in enumerate(self.values):
            height = max(2.0, (floor - 8) * value / ceiling) if value else 1.0
            chosen = index == self.selected
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.chart_today if chosen else palette.chart))
            bar = QRectF(index * slot + slot * .2, floor - height, slot * .6, height)
            painter.drawRoundedRect(bar, 3, 3)
            if self.labels and wide:
                painter.setPen(QColor(palette.accent if chosen else palette.chart_axis))
                painter.drawText(QRectF(index * slot, floor + 2, slot, 17), Qt.AlignmentFlag.AlignCenter, self.labels[index])


class BreakdownBars(QWidget):
    """Horizontal ranked bars: one row per ticket or project, largest first."""

    ROW = 24

    def __init__(self, label_width: int = 130):
        super().__init__()
        self.rows: list[tuple[str, int]] = []
        self.label_width = label_width
        self.setMinimumHeight(self.ROW)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_rows(self, rows: list[tuple[str, int]]) -> None:
        self.rows = list(rows)
        self.setMinimumHeight(max(self.ROW, self.ROW * len(self.rows)))
        self.setToolTip("\n".join(f"{name}: {format_minutes(value)}" for name, value in self.rows))
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:
        palette = active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            painter.setPen(QColor(palette.muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Nothing tracked in this range")
            return
        ceiling = max(value for _, value in self.rows) or 1
        metrics = painter.fontMetrics()
        value_width = 58
        label_width = min(self.label_width, max(60, self.width() // 3))
        track = max(20.0, self.width() - label_width - value_width - 12)
        for index, (name, value) in enumerate(self.rows):
            top = index * self.ROW
            painter.setPen(QColor(palette.text))
            painter.drawText(
                QRectF(0, top, label_width, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(name, Qt.TextElideMode.ElideRight, label_width - 6),
            )
            color = QColor(series_color(index))
            rail = QColor(palette.border)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rail)
            painter.drawRoundedRect(QRectF(label_width + 6, top + 7, track, 10), 5, 5)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(label_width + 6, top + 7, max(4.0, track * value / ceiling), 10), 5, 5)
            painter.setPen(QColor(palette.muted))
            painter.drawText(
                QRectF(label_width + track + 10, top, value_width, self.ROW),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, format_minutes(value),
            )


class DayTimeline(QWidget):
    """One day drawn end to end: schedule windows, breaks, and tracked sessions."""

    def __init__(self):
        super().__init__()
        self.windows: list[tuple[float, float]] = []
        self.blocks: list[tuple[float, float, str, int]] = []
        self.start_hour, self.end_hour = 8.0, 19.0
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_day(self, windows, blocks, start_hour: float, end_hour: float) -> None:
        """`windows`/`blocks` are (start_hour, end_hour) floats in local time."""
        self.windows = list(windows)
        self.blocks = list(blocks)
        self.start_hour, self.end_hour = start_hour, max(start_hour + 1, end_hour)
        self.setToolTip("\n".join(
            f"{name}: {int(begin):02d}:{int(begin % 1 * 60):02d}–{int(finish):02d}:{int(finish % 1 * 60):02d}"
            for begin, finish, name, _ in self.blocks
        ) or "No sessions on this day")
        self.update()

    def _x(self, hour: float) -> float:
        span = self.end_hour - self.start_hour
        return (hour - self.start_hour) / span * self.width()

    def paintEvent(self, event) -> None:
        palette = active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.raised))
        painter.drawRoundedRect(QRectF(0, 12, self.width(), 22), 5, 5)
        # A translucent accent reads as "counted" against both light and dark rails.
        band = QColor(palette.accent)
        band.setAlpha(38)
        for begin, finish in self.windows:
            painter.setBrush(band)
            painter.drawRect(QRectF(self._x(begin), 12, max(1.0, self._x(finish) - self._x(begin)), 22))
        for begin, finish, _name, index in self.blocks:
            painter.setBrush(QColor(series_color(index)))
            painter.drawRoundedRect(QRectF(self._x(begin), 15, max(2.0, self._x(finish) - self._x(begin)), 16), 3, 3)
        painter.setPen(QColor(palette.chart_axis))
        step = 1 if self.end_hour - self.start_hour <= 12 else 2
        hour = int(self.start_hour)
        while hour <= self.end_hour:
            # Clamp the first and last labels so neither is clipped by the edge.
            left = min(max(0.0, self._x(hour) - 16), max(0.0, self.width() - 32))
            painter.drawText(QRectF(left, 34, 32, 16), Qt.AlignmentFlag.AlignCenter, f"{hour:02d}")
            hour += step
