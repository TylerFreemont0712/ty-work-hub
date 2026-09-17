from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (QDateTimeEdit, QDialog, QDialogButtonBox,
                             QFormLayout, QLabel)


class ReminderDialog(QDialog):
    def __init__(self, initial: datetime | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set task reminder")
        form = QFormLayout(self)
        self.when = QDateTimeEdit()
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self.when.setMinimumDateTime(QDateTime.currentDateTime())
        value = initial or datetime.now().astimezone()
        self.when.setDateTime(QDateTime.fromSecsSinceEpoch(int(value.timestamp())).addSecs(3600 if initial is None else 0))
        hint = QLabel("The reminder stays local to Ty Work Hub and can be snoozed from the task menu.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow("Remind me", self.when)
        form.addRow("", hint)
        form.addRow(buttons)

    def value(self) -> datetime:
        return datetime.fromtimestamp(self.when.dateTime().toSecsSinceEpoch()).astimezone()
