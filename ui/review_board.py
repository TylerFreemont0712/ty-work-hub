"""Review queue with virtual rows and selection-scoped actions."""
from __future__ import annotations
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableView, QVBoxLayout, QWidget
from modules.ticket_utils import issue_key, natural_key
from ui.design import button
from ui.theme import active


class ReviewModel(QAbstractTableModel):
    HEADERS = ("Attention", "Ticket", "Task", "Assignee", "Status", "Updated")

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key, issue, state = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return issue
        if role == Qt.ItemDataRole.UserRole + 1:
            return key
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            palette = active()
            return QColor(palette.warning if state in {"new", "changed"} else palette.muted)
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        if issue:
            owner = issue.get("assignee") or {}
            values = [
                {"new": "New", "changed": "Updated", "back": "Seen", "hidden": "Hidden"}.get(state, state),
                key, issue.get("summary", ""), owner.get("name") or owner.get("userId") or "Unassigned",
                (issue.get("status") or {}).get("name", ""), str(issue.get("updated") or "")[:16].replace("T", " "),
            ]
        else:
            values = ["Unavailable", key, "Not returned by Backlog. Check the key or your access.", "—", "—", "—"]
        return str(values[index.column()])


class ReviewBoard(QWidget):
    ticket_requested = pyqtSignal(dict)
    add_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    seen_requested = pyqtSignal(dict, str)
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.issues, self.attention = [], {}
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel("Keep work moving.")
        title.setObjectName("pageTitle")
        self.count = QLabel("0 watched")
        self.count.setObjectName("badge")
        heading.addWidget(title)
        heading.addWidget(self.count)
        heading.addStretch()
        heading.addWidget(button("Sync", self.refresh_requested, glyph="refresh"))
        root.addLayout(heading)
        subtitle = QLabel("A shared-work watchlist. Updated tickets return to your attention automatically.")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        panel = QFrame()
        panel.setObjectName("surface")
        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 8, 10, 8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find a ticket by key or title…")
        self.add = button("Find & track", self._submit, glyph="plus", primary=True)
        row.addWidget(self.query, 1)
        row.addWidget(self.add)
        root.addWidget(panel)
        actions = QHBoxLayout()
        self.open_button = button("Open ticket", lambda: self._action("open"))
        self.seen_button = button("Mark seen", lambda: self._action("seen"))
        self.hide_button = button("Hide revision", lambda: self._action("hidden"))
        self.remove_button = button("Stop tracking", lambda: self._action("remove"))
        for control in (self.open_button, self.seen_button, self.hide_button, self.remove_button):
            actions.addWidget(control)
        actions.addStretch()
        root.addLayout(actions)
        self.table = QTableView()
        self.model = ReviewModel(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 95), (1, 135), (3, 115), (4, 100), (5, 135)):
            self.table.setColumnWidth(column, width)
        root.addWidget(self.table, 1)
        self.empty = QLabel("Your review board is clear. Track a ticket to stay in the loop.")
        self.empty.setObjectName("muted")
        root.addWidget(self.empty)
        self.query.returnPressed.connect(self._submit)
        self.table.doubleClicked.connect(lambda _: self._action("open"))
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self._update_actions()

    def _submit(self):
        query = self.query.text().strip()
        if query:
            self.add_requested.emit(query)
            self.query.clear()

    def set_data(self, issues, watched_keys, attention):
        watched = {str(key).upper() for key in watched_keys}
        self.issues = [issue for issue in issues if issue_key(issue).upper() in watched]
        self.attention = attention
        current_key = self.table.currentIndex().data(Qt.ItemDataRole.UserRole + 1)
        by_key = {issue_key(issue).upper(): issue for issue in self.issues}
        rows = [(key, by_key.get(key), attention.get(key, "new")) for key in watched]
        rows.sort(key=lambda row: (row[2] not in {"new", "changed"}, natural_key(row[0])))
        self.model.beginResetModel()
        self.model.rows = rows
        self.model.endResetModel()
        for row, (key, _, _) in enumerate(rows):
            if key == current_key:
                self.table.selectRow(row)
                break
        self.count.setText(f"{len(rows)} watched")
        self.empty.setVisible(not rows)
        self._update_actions()

    def _update_actions(self, *_):
        index = self.table.currentIndex()
        issue = index.data(Qt.ItemDataRole.UserRole)
        key = index.data(Qt.ItemDataRole.UserRole + 1)
        for control in (self.open_button, self.seen_button, self.hide_button):
            control.setEnabled(bool(issue))
        self.remove_button.setEnabled(bool(key))
        self.seen_button.setText("Mark unseen" if self.attention.get(key) in {"back", "hidden"} else "Mark seen")

    def _action(self, action):
        index = self.table.currentIndex()
        key = index.data(Qt.ItemDataRole.UserRole + 1)
        issue = index.data(Qt.ItemDataRole.UserRole)
        if action == "remove" and key:
            self.remove_requested.emit(key)
        elif issue:
            if action == "open":
                self.ticket_requested.emit(issue)
            else:
                mode = "unseen" if action == "seen" and self.attention.get(key) in {"back", "hidden"} else "back" if action == "seen" else "hidden"
                self.seen_requested.emit(issue, mode)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.table.setColumnHidden(5, self.width() < 900)
