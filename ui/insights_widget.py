"""Time insights: what was worked on, for how long, and against which project.

Presentation only. Every number comes from `modules.work_insights`, so the
schedule rules that deduct evenings, weekends, and breaks are applied in one
place and this workspace never has to recompute them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from modules.work_insights import format_minutes, summarize
from modules.work_schedule import WorkSchedule
from ui.charts import BreakdownBars, DayBars, DayTimeline
from ui.design import button

RANGES = (("This week", 7), ("Last 14 days", 14), ("Last 30 days", 30), ("Last 90 days", 90))


class MetricTile(QFrame):
    def __init__(self, caption: str, tooltip: str = ""):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(1)
        label = QLabel(caption)
        label.setObjectName("muted")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        layout.addWidget(label)
        layout.addWidget(self.value)
        if tooltip:
            self.setToolTip(tooltip)


class InsightsWidget(QWidget):
    """A read-only report over the local work sessions."""

    ticket_requested = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.schedule = WorkSchedule.from_config(config)
        self.events: list[dict] = []
        self.summary = None
        self._selected_day = date.today()
        self._days: list[date] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        root = QVBoxLayout(canvas)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)
        scroll.setWidget(canvas)
        outer.addWidget(scroll)

        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Where your hours went")
        title.setObjectName("pageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        heading.addLayout(titles, 1)
        self.range = QComboBox()
        self.range.addItems([label for label, _ in RANGES])
        self.range.setToolTip("How far back to summarise")
        refresh = button("Refresh", self.render, glyph="refresh")
        heading.addWidget(self.range, 0, Qt.AlignmentFlag.AlignTop)
        heading.addWidget(refresh, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(heading)

        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.total_tile = MetricTile("Counted in range", "Time inside your work schedule")
        self.average_tile = MetricTile("Average per tracked day")
        self.today_tile = MetricTile("Counted today")
        self.coverage_tile = MetricTile("Share of your schedule", "Counted time against the working days in this range")
        self.tiles = (self.total_tile, self.average_tile, self.today_tile, self.coverage_tile)
        for index, tile in enumerate(self.tiles):
            tiles.addWidget(tile, 0, index)
        root.addLayout(tiles)

        self.day_bars = DayBars()
        self.day_bars.day_selected.connect(self._day_clicked)
        root.addWidget(self._panel("Each day", self.day_bars, "Click a day to see how it was spent."))

        self.timeline = DayTimeline()
        self.timeline_caption = QLabel("")
        self.timeline_caption.setObjectName("muted")
        day_panel = self._panel("The selected day", self.timeline)
        day_panel.layout().addWidget(self.timeline_caption)
        root.addWidget(day_panel)

        split = QHBoxLayout()
        split.setSpacing(12)
        self.ticket_bars = BreakdownBars()
        self.project_bars = BreakdownBars()
        split.addWidget(self._panel("By ticket", self.ticket_bars), 1)
        split.addWidget(self._panel("By project", self.project_bars), 1)
        root.addLayout(split)

        self.sessions = QTableWidget(0, 5)
        self.sessions.setHorizontalHeaderLabels(["Day", "Time", "Counted", "Ticket", "Project"])
        header = self.sessions.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in {0: 106, 1: 122, 2: 84, 3: 128}.items():
            self.sessions.setColumnWidth(column, width)
        self.sessions.verticalHeader().setVisible(False)
        self.sessions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sessions.setAlternatingRowColors(True)
        self.sessions.setWordWrap(False)
        self.sessions.setMinimumHeight(180)
        self.sessions.itemDoubleClicked.connect(self._session_activated)
        root.addWidget(self._panel("Sessions", self.sessions, "Double-click a row to open its ticket."), 1)

        self.range.currentIndexChanged.connect(self.render)

    @staticmethod
    def _panel(title: str, widget: QWidget, subtitle: str = "") -> QFrame:
        panel = QFrame()
        panel.setObjectName("surface")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(13, 9, 13, 10)
        layout.setSpacing(5)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("muted")
            layout.addWidget(caption)
        layout.addWidget(widget, 1)
        return panel

    def update_config(self, config: dict) -> None:
        self.config = config
        self.schedule = WorkSchedule.from_config(config)
        self.render()

    def set_events(self, events: list[dict]) -> None:
        self.events = list(events)
        self.render()

    def _day_clicked(self, index: int) -> None:
        if 0 <= index < len(self._days):
            self._selected_day = self._days[index]
            self.render()

    def _session_activated(self, item: QTableWidgetItem) -> None:
        key = self.sessions.item(item.row(), 3)
        if key and key.text() and key.text() != "—":
            self.ticket_requested.emit(key.text())

    def render(self) -> None:
        span = dict(RANGES).get(self.range.currentText(), 7)
        today = date.today()
        if span == 7:
            first = today - timedelta(days=today.weekday())
            days = [first + timedelta(days=offset) for offset in range(7)]
        else:
            days = [today - timedelta(days=offset) for offset in range(span - 1, -1, -1)]
        self._days = days
        if self._selected_day not in days:
            self._selected_day = today if today in days else days[-1]
        general = str(self.config.get("general_project") or "Company General")
        self.summary = summarize(self.events, days, self.schedule, fallback_project=general)
        self._render_tiles(days)
        self._render_charts(days)
        self._render_timeline()
        self._render_sessions()

    def _render_tiles(self, days: list[date]) -> None:
        summary = self.summary
        today = date.today()
        tracked = summary.tracked_days
        capacity = self.schedule.daily_minutes * sum(1 for day in days if self.schedule.is_working_day(day))
        self.total_tile.value.setText(format_minutes(summary.total_minutes))
        self.average_tile.value.setText(format_minutes(summary.total_minutes // tracked) if tracked else "—")
        self.today_tile.value.setText(
            format_minutes(summary.daily_minutes[days.index(today)]) if today in days else "—"
        )
        self.coverage_tile.value.setText(f"{round(summary.total_minutes / capacity * 100)}%" if capacity else "—")
        window = f"{days[0].strftime('%b %d')} – {days[-1].strftime('%b %d')}"
        self.subtitle.setText(
            f"{window}  ·  {self.schedule.describe()}  ·  "
            f"{len(summary.sessions)} session(s) counted out of a {format_minutes(capacity)} schedule"
        )

    def _render_charts(self, days: list[date]) -> None:
        compact = len(days) > 14
        labels = [day.strftime("%d" if compact else "%a %d") for day in days]
        selected = days.index(self._selected_day) if self._selected_day in days else -1
        self.day_bars.set_data(self.summary.daily_minutes, labels, self.schedule.daily_minutes, selected)
        self.ticket_bars.set_rows(self.summary.ranked_tickets())
        self.project_bars.set_rows(self.summary.ranked_projects())

    def _render_timeline(self) -> None:
        zone = datetime.now().astimezone().tzinfo
        windows = [
            (start.hour + start.minute / 60, end.hour + end.minute / 60)
            for start, end in self.schedule.windows(self._selected_day, zone)
        ]
        names = [name for name, _ in self.summary.ranked_tickets(len(self.summary.by_ticket) or 1)]
        blocks = []
        for session in self.summary.sessions:
            if session["start"].date() != self._selected_day and session["end"].date() != self._selected_day:
                continue
            begin = session["start"] if session["start"].date() == self._selected_day else datetime.combine(self._selected_day, datetime.min.time(), zone)
            finish = session["end"] if session["end"].date() == self._selected_day else datetime.combine(self._selected_day + timedelta(days=1), datetime.min.time(), zone)
            index = names.index(session["ticket"]) if session["ticket"] in names else 0
            blocks.append((begin.hour + begin.minute / 60, finish.hour + finish.minute / 60, session["ticket"], index))
        bounds = [value for window in windows for value in window] + [value for block in blocks for value in block[:2]]
        low = min([*bounds, 9.0]) if bounds else 8.0
        high = max([*bounds, 18.0]) if bounds else 19.0
        self.timeline.set_day(windows, blocks, max(0.0, low - 1), min(24.0, high + 1))
        minutes = self.summary.daily_minutes[self._days.index(self._selected_day)] if self._selected_day in self._days else 0
        shape = "a working day" if self.schedule.is_working_day(self._selected_day) else "outside your schedule"
        self.timeline_caption.setText(
            f"{self._selected_day.strftime('%A, %B %d')}  ·  {format_minutes(minutes)} counted  ·  "
            f"{len(blocks)} session(s)  ·  {shape}"
        )

    def _render_sessions(self) -> None:
        rows = self.summary.sessions[:200]
        self.sessions.setRowCount(len(rows))
        for row, session in enumerate(rows):
            values = (
                session["start"].strftime("%a %b %d"),
                f"{session['start'].strftime('%H:%M')} – {'now' if session['active'] else session['end'].strftime('%H:%M')}",
                format_minutes(session["minutes"]),
                session["ticket"] if session["ticket"] != "No ticket" else "—",
                session["project"],
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(session["title"] or text)
                self.sessions.setItem(row, column, item)
        if not rows:
            self.sessions.setRowCount(1)
            empty = QTableWidgetItem("No sessions were counted in this range")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sessions.setItem(0, 0, empty)
            self.sessions.setSpan(0, 0, 1, 5)
        else:
            self.sessions.clearSpans()
