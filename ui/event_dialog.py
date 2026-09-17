from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
                             QFormLayout, QLineEdit, QMessageBox, QTextEdit)

EVENT_TYPES = {
    "Event": "#8b5cf6",
    "Focus time": "#64748b",
    "Meeting": "#a855f7",
    "Work block": "#22c55e",
}

class EventDialog(QDialog):
    def __init__(self, event: dict | None = None, default_start: datetime | None = None, parent=None):
        super().__init__(parent)
        self.event = event or {}
        self.setWindowTitle("Edit event" if event else "Create event")
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.title = QLineEdit(self.event.get("title", ""))
        self.kind = QComboBox(); self.kind.addItems(EVENT_TYPES)
        kind_name = self.event.get("kind", "event").replace("_", " ").title()
        self.kind.setCurrentText(kind_name if kind_name in EVENT_TYPES else "Event")
        start = self._parse(self.event.get("start")) or default_start or datetime.now().astimezone().replace(second=0, microsecond=0)
        end = self._parse(self.event.get("end")) or start + timedelta(minutes=30)
        self.start = QDateTimeEdit(QDateTime(start)); self.end = QDateTimeEdit(QDateTime(end))
        for widget in (self.start, self.end):
            widget.setCalendarPopup(True); widget.setDisplayFormat("ddd, MMM d yyyy h:mm AP")
        self.notes = QTextEdit(self.event.get("notes", "")); self.notes.setFixedHeight(90)
        form.addRow("Title", self.title); form.addRow("Type", self.kind); form.addRow("Starts", self.start); form.addRow("Ends", self.end); form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate); buttons.rejected.connect(self.reject); form.addRow(buttons)

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        try: return datetime.fromisoformat(value) if value else None
        except ValueError: return None

    def validate(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Event title needed", "Give this event a clear title.")
            return
        if self.end.dateTime() <= self.start.dateTime():
            QMessageBox.warning(self, "Invalid duration", "The event must end after it starts.")
            return
        self.accept()

    def data(self) -> dict:
        label = self.kind.currentText()
        return self.event | {
            "title": self.title.text().strip(),
            "kind": label.lower().replace(" ", "_"),
            "color": EVENT_TYPES[label],
            "start": self.start.dateTime().toPyDateTime().astimezone().isoformat(),
            "end": self.end.dateTime().toPyDateTime().astimezone().isoformat(),
            "notes": self.notes.toPlainText().strip(),
            "active": False,
        }
