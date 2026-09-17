"""Review a generated ticket note before it replaces the one in the vault.

The Obsidian workflow never writes generated text without an explicit apply, so
the note-only action shows exactly what will be written, and says where the
current note will be backed up, before anything touches the vault.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)


class NotePreviewDialog(QDialog):
    """Read-only preview of the rendered note with an apply/cancel decision."""

    def __init__(self, parent=None, *, ticket_key: str = "", note_path: str = "", content: str = "", exists: bool = False):
        super().__init__(parent)
        self.setWindowTitle(f"Update note · {ticket_key}" if ticket_key else "Update note")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        heading = QLabel(f"{ticket_key or 'Ticket'} · Obsidian note")
        heading.setObjectName("sectionTitle")
        detail = QLabel(
            (f"{note_path}\n\nThe current note is backed up under the .history folder before it is replaced. "
             if exists else f"{note_path}\n\nThis note does not exist yet and will be created. ")
            + "Only this note is written: no study material, no Backlog comment."
        )
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        self.preview = QPlainTextEdit(content)
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Update note")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("secondary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addWidget(self.preview, 1)
        layout.addWidget(buttons)
