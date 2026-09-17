"""A compact, two-line ticket preview painted without child widgets."""
from __future__ import annotations
from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate
from modules.ticket_utils import issue_key, status_bucket
from ui.theme import active


class QueueDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        issue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(issue, dict):
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = active()
        rect = option.rect.adjusted(0, 0, -1, 0)
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.fillRect(rect, option.palette.highlight())
        color = QColor(palette.status_colors[status_bucket(issue)])
        painter.setPen(QPen(color, 1.6))
        painter.drawEllipse(QRectF(rect.x()+5, rect.y()+15, 11, 11))
        painter.setFont(option.font)
        painter.setPen(option.palette.text().color())
        title_rect = QRect(rect.x()+28, rect.y()+5, max(0, rect.width()-36), 24)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, option.fontMetrics.elidedText(str(issue.get("summary", "")), Qt.TextElideMode.ElideRight, title_rect.width()))
        metadata = index.data(Qt.ItemDataRole.UserRole+1) or {}
        parts = [issue_key(issue), (issue.get("status") or {}).get("name", "Open")]
        if metadata.get("day"):
            parts.append("MY DAY")
        if issue.get("dueDate"):
            parts.append(str(issue["dueDate"]))
        if metadata.get("attention") == "back":
            parts.append("Seen")
        font = QFont(option.font)
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(palette.muted))
        painter.drawText(QRect(rect.x()+28, rect.y()+30, max(0, rect.width()-36), 20), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText("  ·  ".join(parts), Qt.TextElideMode.ElideRight, max(0, rect.width()-36)))
        line_color = QColor(palette.border)
        line_color.setAlpha(140)
        painter.setPen(line_color)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()
