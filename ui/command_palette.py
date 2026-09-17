from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QVBoxLayout)


class CommandPalette(QDialog):
    """Searchable keyboard-first command launcher."""

    def __init__(self, commands: list[dict], parent=None):
        super().__init__(parent)
        self.commands = commands
        self._search_index = [
            f"{command.get('title', '')} {command.get('subtitle', '')} {command.get('keywords', '')}".casefold()
            for command in commands
        ]
        self.matches: list[int] = []
        self.setWindowTitle("Jump to anything")
        self.setObjectName("commandPalette")
        self.setModal(True)
        self.resize(680, 510)
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        title = QLabel("Jump to anything")
        title.setObjectName("heroTitle")
        root.addWidget(title)
        root.setSpacing(5)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a ticket, open a workspace, or run a command…")
        self.search.setClearButtonEnabled(True)
        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        footer = QHBoxLayout()
        hint = QLabel("↑↓ navigate    Enter run    Esc close")
        hint.setObjectName("muted")
        self.count = QLabel()
        self.count.setObjectName("muted")
        footer.addWidget(hint)
        footer.addStretch()
        footer.addWidget(self.count)
        root.addWidget(self.search)
        root.addWidget(self.results)
        root.addLayout(footer)

        self.search.textChanged.connect(self.render)
        self.search.returnPressed.connect(self.run_current)
        self.results.itemActivated.connect(lambda _: self.run_current())
        self.search.installEventFilter(self)
        self.render()
        QTimer.singleShot(0, self.search.setFocus)

    def render(self) -> None:
        query = self.search.text().strip().casefold()
        tokens = query.split()
        self.results.clear()
        self.matches = []
        self.matches = [index for index, haystack in enumerate(self._search_index)
                        if not tokens or all(token in haystack for token in tokens)]
        if query:
            self.matches.sort(key=lambda index: (not self.commands[index].get("title", "").casefold().startswith(query), index))
        for index in self.matches[:60]:
            command = self.commands[index]
            title, subtitle = command.get("title", "Untitled command"), command.get("subtitle", "")
            item = QListWidgetItem(f"{title}\n{subtitle}" if subtitle else title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(command.get("keywords", ""))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        self.count.setText(f"{len(self.matches)} results" + (" · showing first 60" if len(self.matches) > 60 else ""))

    def run_current(self) -> None:
        item = self.results.currentItem()
        if not item:
            return
        callback = self.commands[int(item.data(Qt.ItemDataRole.UserRole))].get("callback")
        self.accept()
        if callable(callback):
            QTimer.singleShot(0, callback)

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                count = self.results.count()
                if count:
                    delta = 1 if event.key() == Qt.Key.Key_Down else -1
                    self.results.setCurrentRow((self.results.currentRow() + delta) % count)
                return True
        return super().eventFilter(watched, event)
