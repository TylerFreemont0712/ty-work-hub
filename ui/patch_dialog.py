"""Name a patch before it is written to the shelf.

A long ticket accumulates many small patches. Without a per-patch name they all
inherit the ticket title and become indistinguishable a week later, so this
dialog collects the three things that make one patch findable: what it is
(label), which part of the work it belongs to (group), and anything worth
remembering about it (note).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout,
)

# Offered on every ticket so the first patch of a series still has a vocabulary.
SUGGESTED_GROUPS = ("Backend", "Frontend", "Database", "Config", "Review fixes", "WIP")


class PatchDetailsDialog(QDialog):
    """Collect the label, group, and note for one new patch."""

    def __init__(
        self,
        parent=None,
        *,
        action: str = "Create patch",
        ticket_key: str = "",
        sequence: int = 1,
        file_count: int = 0,
        working_copy: str = "",
        suggested_label: str = "",
        known_groups: tuple[str, ...] | list[str] = (),
        last_group: str = "",
        note: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(action)
        self.setMinimumWidth(430)
        root = QVBoxLayout(self)

        headline = QLabel(f"{ticket_key or 'MANUAL'}  ·  patch #{sequence}")
        headline.setObjectName("sectionTitle")
        summary = QLabel(f"{file_count} file(s) selected  ·  {working_copy or 'working copy'}")
        summary.setObjectName("muted")
        summary.setWordWrap(True)
        root.addWidget(headline)
        root.addWidget(summary)

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)
        self.label = QLineEdit(suggested_label)
        self.label.setMaxLength(120)
        self.label.setPlaceholderText(f"Part {sequence}")
        self.label.setToolTip("Short name for this patch. It becomes the file name and the shelf label.")
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.setToolTip("Optional. Groups related patches of the same ticket, such as Backend or Review fixes.")
        self.group.addItem("")
        seen = {""}
        for name in (*known_groups, *SUGGESTED_GROUPS):
            value = str(name).strip()
            if value and value.casefold() not in {item.casefold() for item in seen}:
                seen.add(value)
                self.group.addItem(value)
        self.group.setCurrentText(str(last_group or ""))
        self.note = QPlainTextEdit(str(note or ""))
        self.note.setPlaceholderText("Optional. What is in this patch, or what still has to happen to it.")
        self.note.setFixedHeight(64)
        form.addRow("Label", self.label)
        form.addRow("Group", self.group)
        form.addRow("Note", self.note)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(action)
        # Only the confirming button carries the filled treatment.
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("secondary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sequence = max(1, int(sequence))
        self.label.setFocus()
        self.label.selectAll()

    def values(self) -> dict:
        """Trimmed input. An empty label falls back to the patch's position."""
        return {
            "label": self.label.text().strip()[:120] or f"Part {self._sequence}",
            "group": self.group.currentText().strip()[:60],
            "note": self.note.toPlainText().strip()[:2000],
        }

    def keyPressEvent(self, event):
        # The note box owns Enter; everywhere else Enter accepts the dialog.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.note.hasFocus():
            QPlainTextEdit.keyPressEvent(self.note, event)
            return
        super().keyPressEvent(event)
