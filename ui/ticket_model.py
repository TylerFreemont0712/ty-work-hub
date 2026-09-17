"""Virtual task rows: constant widget count regardless of ticket volume."""
from __future__ import annotations
from datetime import date
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle
from modules.ticket_utils import issue_key, natural_key, project_name, status_bucket
from ui.theme import active

HEADERS = ("☆", "Ticket", "Task", "Priority", "Status", "Due date", "Project", "Assignee", "Seen", "Note")
PRIORITY_LABELS = {"High": "↑  High", "Normal": "−  Normal", "Low": "↓  Low"}


class TicketModel(QAbstractTableModel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        issue = self.rows[index.row()]
        key, column = issue_key(issue), index.column()
        if role == Qt.ItemDataRole.UserRole:
            return issue
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 8:
                return "Acknowledge this revision (does not change Backlog status)"
            if column == 9:
                return f"Open {key} in Obsidian"
            if column == 5 and key in self.owner._reminders:
                return f"Local reminder: {self.owner._reminders[key].get('at', '')}"
            return str(issue.get("summary", "")) + "\n" + str(issue.get("description", ""))[:3000]
        if role == Qt.ItemDataRole.ForegroundRole:
            palette = active()
            if column == 0 and key in self.owner._favorites:
                return QColor(palette.warning)
            if column == 5:
                due = self.owner._due_date(issue)
                if due and due < date.today() and status_bucket(issue) not in {"resolved", "closed"}:
                    return QColor(palette.danger)
            if column in {0, 1, 6, 7, 8, 9}:
                return QColor(palette.muted)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in {0, 8, 9}:
            return Qt.AlignmentFlag.AlignCenter
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        owner = issue.get("assignee") or {}
        values = (
            "★" if key in self.owner._favorites else "☆",
            key,
            str(issue.get("summary", "")),
            (issue.get("priority") or {}).get("name", "Normal"),
            (issue.get("status") or {}).get("name", "Open"),
            (issue.get("dueDate") or "—") + (" · R" if key in self.owner._reminders else ""),
            project_name(issue),
            owner.get("name") or owner.get("userId") or "Unassigned",
            "✓" if self.owner._attention.get(key) in {"back", "hidden"} else "○",
            "MD",
        )
        return str(values[column])


class TicketSortModel(QSortFilterProxyModel):
    def lessThan(self, left, right):
        a, b = left.data(), right.data()
        if left.column() == 3:
            order = {"High": 0, "Normal": 1, "Low": 2}
            return order.get(a, 3) < order.get(b, 3)
        if left.column() == 5:
            return (a if a != "—" else "9999") < (b if b != "—" else "9999")
        return natural_key(a) < natural_key(b)


class TicketDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() not in {3, 4, 8, 9}:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        text = str(index.data() or "")
        palette = active()
        color = QColor(palette.muted)
        if index.column() == 4:
            color = QColor(palette.status_colors[status_bucket(index.data(Qt.ItemDataRole.UserRole))])
        elif index.column() == 3:
            color = QColor(palette.priority_colors.get(text, palette.muted))
            text = PRIORITY_LABELS.get(text, text)
        painter.setFont(option.font)
        width = min(option.rect.width() - 12, option.fontMetrics.horizontalAdvance(text) + 16)
        pill = QRectF(option.rect.x() + 6, option.rect.center().y() - 10, width, 21)
        fill = QColor(color)
        fill.setAlpha(38 if active().dark else 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, 5, 5)
        painter.setPen(color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width - 8)))
        painter.restore()
