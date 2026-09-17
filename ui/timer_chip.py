"""The always-visible work timer in the application's top row.

Its whole job is to be impossible to forget about: a running session shows a
live counter wherever you are in the app, and a session that has stopped
counting because the working day ended says so instead of ticking silently.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from modules.work_insights import format_minutes
from ui.design import ElidedLabel
from ui.theme import active


class StatusDot(QWidget):
    """A small filled circle; it pulses only in the sense of changing colour."""

    def __init__(self):
        super().__init__()
        self.state = "idle"
        self.setFixedSize(10, 10)

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.update()

    def paintEvent(self, event) -> None:
        palette = active()
        color = {"running": palette.success, "paused": palette.warning}.get(self.state, palette.muted)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(1, 1, 8, 8))


class TimerChip(QWidget):
    """Live session state plus one button that starts or stops it."""

    toggle_requested = pyqtSignal()
    open_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("timerChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(6)
        self.dot = StatusDot()
        self.clock = QLabel("0h 00m")
        self.clock.setObjectName("timerClock")
        self.subject = ElidedLabel("Not tracking")
        self.subject.setObjectName("muted")
        self.subject.setMinimumWidth(0)
        self.subject.setMaximumWidth(150)
        self.action = QPushButton("Start")
        self.action.setProperty("secondary", True)
        self.action.setProperty("tableAction", True)
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.setFixedHeight(22)
        self.action.setFixedWidth(52)
        self.action.clicked.connect(self.toggle_requested)
        layout.addWidget(self.dot)
        layout.addWidget(self.clock)
        layout.addWidget(self.subject, 1)
        layout.addWidget(self.action)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(150)

    def sizeHint(self) -> QSize:
        return QSize(250, 28)

    def mouseReleaseEvent(self, event) -> None:
        # Clicking the chip itself opens the workspace that owns the timer.
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()

    def set_state(self, active_session: dict | None, counted_seconds: int, counting: bool, resumes: str = "") -> None:
        """Render one of three states: idle, counting, or running-but-not-counted."""
        if not active_session:
            self.dot.set_state("idle")
            self.clock.setText("—")
            self.subject.setText("Not tracking")
            self.action.setText("Start")
            self.setToolTip("No work session is running. Start one from Today.")
            return
        subject = str(active_session.get("ticket_key") or "").strip() or str(active_session.get("project") or "").strip() or "General work"
        self.dot.set_state("running" if counting else "paused")
        self.clock.setText(format_minutes(counted_seconds // 60))
        self.subject.setText(subject if counting else f"{subject} · paused")
        self.action.setText("Stop")
        detail = (
            "Counting now." if counting
            else f"Outside your work schedule, so this time is not counted.{f' Counting resumes {resumes}.' if resumes else ''}"
        )
        self.setToolTip(f"{active_session.get('title') or subject}\n{format_minutes(counted_seconds // 60)} counted · {detail}\nClick to open Today.")
