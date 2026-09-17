"""Quiet event blocks with complete time/title tooltips supplied by the calendar."""
from __future__ import annotations
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle
from ui.theme import active


class CalendarDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        values = index.data(Qt.ItemDataRole.UserRole) or []
        if not values:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        event = values[0]
        palette = active()
        color = QColor(palette.event_color(str(event.get("kind", "")), bool(event.get("readonly"))))
        fill = QColor(color)
        fill.setAlpha(48 if palette.dark else 34)
        rect = QRectF(option.rect.adjusted(4, 4, -4, -4))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(rect.x(), rect.y()+3, 3, max(1, rect.height()-6)), 1, 1)
        painter.setPen(color)
        painter.setFont(option.font)
        title = str(event.get("title", ""))
        if len(values) > 1:
            title = f"+{len(values)-1} · {title}"
        painter.drawText(rect.adjusted(8, 2, -5, -2), Qt.AlignmentFlag.AlignVCenter, option.fontMetrics.elidedText(title, Qt.TextElideMode.ElideRight, max(0, int(rect.width()-13))))
        painter.restore()
