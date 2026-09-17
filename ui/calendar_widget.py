"""Interactive local calendar with Backlog deadlines and a planning rail."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCalendarWidget, QComboBox, QFrame, QHeaderView,
                             QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from modules.event_store import EventStore
from modules.ticket_utils import issue_key, status_bucket
from modules.work_schedule import WorkSchedule
from services.time_tracking import TimeTracker
from ui.event_dialog import EventDialog
from ui.calendar_delegate import CalendarDelegate
from ui.theme import active

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class CalendarWidget(QWidget):
    event_changed = pyqtSignal()
    ticket_requested = pyqtSignal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.store = EventStore()
        self.tracker = TimeTracker(self.store)
        self.current_date = date.today()
        self.deadline_events: list[dict] = []
        self.issues: list[dict] = []
        self.my_day: set[str] = set()
        self.reminders: dict[str, dict] = {}
        self.selected_event: dict | None = None
        self._cell_dates: dict[tuple[int, int], date] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        title = QLabel("Make time for what matters.")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.previous = QPushButton("<")
        self.today = QPushButton("Today")
        self.next = QPushButton(">")
        for control in (self.previous, self.today, self.next):
            control.setProperty("secondary", True)
        self.period = QLabel()
        self.period.setObjectName("sectionTitle")
        self.view = QComboBox()
        self.view.addItems(["Month", "Week", "Day", "Agenda"])
        self.view.setCurrentText(config.get("calendar_default_view", "Week"))
        self.add = QPushButton("+ New event")
        self.edit = QPushButton("Edit")
        self.delete = QPushButton("Delete")
        self.edit.setProperty("secondary", True)
        self.delete.setProperty("secondary", True)
        for widget in (self.previous, self.today, self.next, self.period):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.view)
        controls.addWidget(self.add)
        controls.addWidget(self.edit)
        controls.addWidget(self.delete)
        root.addLayout(controls)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.month = self._table(6, 7)
        self.week = self._table(24, 7)
        self.day = self._table(24, 1)
        for table in (self.week, self.day):
            table.setItemDelegate(CalendarDelegate(table))
        self.agenda = QListWidget()
        self.month.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.month.setHorizontalHeaderLabels(list(WEEKDAYS))
        self.stack.addWidget(self.month)
        self.stack.addWidget(self.week)
        self.stack.addWidget(self.day)
        self.stack.addWidget(self.agenda)
        body.addWidget(self.stack)

        planning = QFrame()
        planning.setObjectName("surface")
        planning.setMinimumWidth(228)
        planning.setMaximumWidth(315)
        planning_layout = QVBoxLayout(planning)
        planning_layout.setContentsMargins(6, 5, 6, 6)
        planning_layout.setSpacing(5)
        mini_title = QHBoxLayout()
        title = QLabel("Pick a week")
        title.setObjectName("sectionTitle")
        self.week_summary = QLabel()
        self.week_summary.setObjectName("badge")
        mini_title.addWidget(title)
        mini_title.addStretch()
        mini_title.addWidget(self.week_summary)
        planning_layout.addLayout(mini_title)
        self.mini_calendar = QCalendarWidget()
        self.mini_calendar.setGridVisible(False)
        self.mini_calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.mini_calendar.setMinimumHeight(172)
        self.mini_calendar.setMaximumHeight(210)
        planning_layout.addWidget(self.mini_calendar)
        upcoming_title = QLabel("Upcoming tasks")
        upcoming_title.setObjectName("sectionTitle")
        self.upcoming_tasks = QListWidget()
        self.upcoming_tasks.setWordWrap(True)
        self.upcoming_tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        planning_layout.addWidget(upcoming_title)
        planning_layout.addWidget(self.upcoming_tasks, 1)
        upcoming_hint = QLabel("My Day, reminders, deadlines, and the next 30 days.")
        upcoming_hint.setObjectName("muted")
        upcoming_hint.setWordWrap(True)
        planning_layout.addWidget(upcoming_hint)
        body.addWidget(planning)
        body.setSizes([850, 275])
        root.addWidget(body, 1)

        self.hint = QLabel("Double-click a time slot to create an event. Backlog deadlines are red and open the related task.")
        self.hint.setObjectName("muted")
        root.addWidget(self.hint)

        self.previous.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        self.today.clicked.connect(self.go_today)
        self.view.currentTextChanged.connect(self.render)
        self.add.clicked.connect(self.new_event)
        self.edit.clicked.connect(self.edit_selected)
        self.delete.clicked.connect(self.delete_selected)
        self.month.cellClicked.connect(self.month_clicked)
        self.month.cellDoubleClicked.connect(self.month_double_clicked)
        self.week.cellClicked.connect(self.grid_clicked)
        self.week.cellDoubleClicked.connect(self.grid_double_clicked)
        self.day.cellClicked.connect(self.grid_clicked)
        self.day.cellDoubleClicked.connect(self.grid_double_clicked)
        self.agenda.itemClicked.connect(self.agenda_clicked)
        self.agenda.itemDoubleClicked.connect(lambda _: self.edit_selected())
        self.mini_calendar.clicked.connect(self.mini_date_selected)
        self.upcoming_tasks.itemActivated.connect(lambda item: self.ticket_requested.emit(str(item.data(Qt.ItemDataRole.UserRole) or "")))
        self.render()

    @staticmethod
    def _table(rows: int, columns: int) -> QTableWidget:
        table = QTableWidget(rows, columns)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        table.verticalHeader().setDefaultSectionSize(25)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone() if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)

    @staticmethod
    def _date(value) -> date | None:
        try:
            return date.fromisoformat(str(value)) if value else None
        except ValueError:
            return None

    def all_events(self) -> list[dict]:
        return [event for event in self.store.all() + self.deadline_events if self._valid_event(event)]

    def _valid_event(self, event: dict) -> bool:
        try:
            self._parse(event["start"])
            self._parse(event["end"])
            return bool(event.get("title"))
        except (KeyError, TypeError, ValueError):
            return False

    def update_issues(self, issues: list[dict]) -> None:
        self.issues = issues
        events = []
        for issue in issues:
            due = self._date(issue.get("dueDate"))
            if due:
                events.append({
                    "id": f"deadline-{issue.get('id')}",
                    "title": f"Deadline: {issue_key(issue)} - {issue.get('summary')}",
                    "kind": "deadline",
                    "start": f"{due.isoformat()}T09:00:00",
                    "end": f"{due.isoformat()}T09:30:00",
                    "ticket_key": issue_key(issue),
                    "readonly": True,
                })
        self.deadline_events = events
        self.render()

    def set_personal_planning(self, my_day: set[str] | list[str], reminders: dict[str, dict]) -> None:
        self.my_day = set(my_day)
        self.reminders = {key: dict(value) for key, value in reminders.items()}
        self.render_upcoming_tasks()

    def events_for(self, day: date) -> list[dict]:
        values = []
        for event in self.all_events():
            try:
                start = self._parse(event["start"]).date()
                end = self._parse(event["end"]).date()
                if start <= day <= end:
                    values.append(event)
            except (KeyError, ValueError):
                continue
        return sorted(values, key=lambda event: event["start"])

    def move(self, amount: int) -> None:
        mode = self.view.currentText()
        if mode == "Month":
            month = self.current_date.month + amount
            year = self.current_date.year
            if month == 0:
                month, year = 12, year - 1
            if month == 13:
                month, year = 1, year + 1
            self.current_date = date(year, month, 1)
        elif mode == "Week":
            self.current_date += timedelta(days=7 * amount)
        else:
            self.current_date += timedelta(days=amount)
        self.render()

    def go_today(self) -> None:
        self.current_date = date.today()
        self.render()

    def mini_date_selected(self, value: QDate) -> None:
        self.current_date = value.toPyDate()
        if self.view.currentText() != "Week":
            self.view.setCurrentText("Week")
        else:
            self.render()

    def render(self) -> None:
        # Validate and parse once per render, instead of once for each of 42 cells.
        self._render_events = []
        for event in self.store.all() + self.deadline_events:
            try:
                start, end = self._parse(event["start"]), self._parse(event["end"])
                if event.get("title") and end >= start:
                    self._render_events.append((event, start, end))
            except (KeyError, TypeError, ValueError):
                continue
        mode = self.view.currentText()
        self.stack.setCurrentIndex(("Month", "Week", "Day", "Agenda").index(mode))
        if mode == "Month":
            self.render_month()
        elif mode == "Week":
            self.render_week()
        elif mode == "Day":
            self.render_day()
        else:
            self.render_agenda()
        self.mini_calendar.blockSignals(True)
        self.mini_calendar.setSelectedDate(QDate(self.current_date.year, self.current_date.month, self.current_date.day))
        self.mini_calendar.setCurrentPage(self.current_date.year, self.current_date.month)
        self.mini_calendar.blockSignals(False)
        self.render_upcoming_tasks()
        self.edit.setEnabled(self.selected_event is not None)
        self.delete.setEnabled(bool(self.selected_event and not self.selected_event.get("readonly")))

    def render_month(self) -> None:
        first = self.current_date.replace(day=1)
        start = first - timedelta(days=first.weekday())
        self.period.setText(first.strftime("%B %Y"))
        self._cell_dates = {}
        for row in range(6):
            for col in range(7):
                day = start + timedelta(days=row * 7 + col)
                self._cell_dates[(row, col)] = day
                day_events = self._events_on_rendered_day(day)
                text = [str(day.day), *(event["title"][:29] for event in day_events[:3])]
                item = QTableWidgetItem("\n".join(text))
                item.setData(Qt.ItemDataRole.UserRole, day_events)
                if day.month != first.month:
                    item.setForeground(QColor(active().muted))
                self.month.setItem(row, col, item)

    def render_week(self) -> None:
        monday = self.current_date - timedelta(days=self.current_date.weekday())
        sunday = monday + timedelta(days=6)
        self.period.setText(f"{monday.strftime('%b')} {monday.day} - {sunday.strftime('%b')} {sunday.day}, {sunday.year}")
        days = [monday + timedelta(days=index) for index in range(7)]
        if not self.config.get("show_weekends", True):
            days = days[:5]
        self.week.setColumnCount(len(days))
        self.week.setHorizontalHeaderLabels([day.strftime("%a\n%b %d") for day in days])
        self._fill_time_grid(self.week, days)

    def render_day(self) -> None:
        self.period.setText(self.current_date.strftime("%A, %B %d, %Y"))
        self.day.setHorizontalHeaderLabels([self.current_date.strftime("%A")])
        self._fill_time_grid(self.day, [self.current_date])

    def _fill_time_grid(self, table: QTableWidget, days: list[date]) -> None:
        table.clearContents()
        table.setRowCount(24)
        table.setVerticalHeaderLabels([f"{hour:02d}:00" for hour in range(24)])
        schedule = WorkSchedule.from_config(self.config)
        palette = active()
        off_hours_color = palette.bg if palette.dark else palette.raised
        zone = datetime.now().astimezone().tzinfo
        for col, day in enumerate(days):
            # Hours the schedule does not count, including the break, read as
            # recessed so the working day is visible at a glance.
            counted_hours = {
                hour for window_start, window_end in schedule.windows(day, zone)
                for hour in range(window_start.hour, window_end.hour + (1 if window_end.minute else 0))
            }
            hourly: dict[int, list[dict]] = {hour: [] for hour in range(24)}
            for event, start, end in self._render_events:
                lower = datetime.combine(day, time.min).astimezone()
                upper = lower + timedelta(days=1)
                if start < upper and end > lower:
                    first_hour = max(start, lower).hour
                    last_hour = min(end - timedelta(microseconds=1), upper - timedelta(microseconds=1)).hour
                    for hour in range(first_hour, last_hour + 1):
                        hourly[hour].append(event)
            for hour, values in hourly.items():
                item = QTableWidgetItem("\n".join(event["title"] for event in values))
                item.setData(Qt.ItemDataRole.UserRole, values)
                item.setToolTip("\n".join(f"{self._parse(event['start']).strftime('%H:%M')}–{self._parse(event['end']).strftime('%H:%M')}  {event['title']}" for event in values))
                if not values and hour not in counted_hours:
                    item.setBackground(QColor(off_hours_color))
                table.setItem(hour, col, item)
        table.verticalHeader().setDefaultSectionSize(46)
        table.verticalHeader().setFixedWidth(48)
        table.verticalHeader().setStyleSheet("QHeaderView::section { padding: 2px; }")
        table.scrollTo(table.model().index(schedule.start.hour, 0), QTableWidget.ScrollHint.PositionAtTop)

    def _events_on_rendered_day(self, day):
        lower = datetime.combine(day, time.min).astimezone()
        upper = lower + timedelta(days=1)
        return sorted((event for event, start, end in self._render_events if start < upper and end > lower), key=lambda event: event["start"])

    def render_agenda(self) -> None:
        self.period.setText("Next 30 days")
        self.agenda.clear()
        palette = active()
        start, end = self.current_date, self.current_date + timedelta(days=30)
        events = [event for event in self.all_events() if start <= self._parse(event["start"]).date() <= end]
        for event in sorted(events, key=lambda value: value["start"]):
            begins = self._parse(event["start"])
            ends = self._parse(event["end"])
            item = QListWidgetItem(f"{begins.strftime('%a, %b %d  %H:%M')} - {ends.strftime('%H:%M')}\n{event['title']}")
            item.setData(Qt.ItemDataRole.UserRole, event)
            # Deadlines follow the theme; user-chosen event colours are their own data.
            item.setForeground(QColor(palette.danger if event.get("readonly") else event.get("color") or palette.info))
            self.agenda.addItem(item)

    def render_upcoming_tasks(self) -> None:
        self.upcoming_tasks.clear()
        week_start = self.current_date - timedelta(days=self.current_date.weekday())
        week_end = week_start + timedelta(days=6)
        horizon = week_start + timedelta(days=30)
        today = date.today()
        current_week = week_start <= today <= week_end
        rows = []
        due_this_week = 0
        for issue in self.issues:
            if status_bucket(issue) == "closed":
                continue
            key = issue_key(issue)
            due = self._date(issue.get("dueDate"))
            if due and week_start <= due <= week_end:
                due_this_week += 1
            reminder = self.reminders.get(key)
            reminder_at = None
            if reminder:
                try:
                    reminder_at = self._parse(reminder.get("at", ""))
                except ValueError:
                    reminder_at = None
            in_day = key in self.my_day or due == today
            due_in_range = bool(due and week_start <= due <= horizon)
            reminder_in_range = bool(reminder_at and week_start <= reminder_at.date() <= horizon)
            overdue_now = bool(current_week and due and due < today)
            if not (in_day or due_in_range or reminder_in_range or overdue_now):
                continue
            planned = reminder_at.date() if reminder_at else due or today
            rows.append((0 if in_day else 1, planned, key, issue, due, reminder_at))
        week_events = sum(len(self.events_for(week_start + timedelta(days=index))) for index in range(7))
        self.week_summary.setText(f"{due_this_week} due · {week_events} events")
        for in_day_rank, _planned, key, issue, due, reminder_at in sorted(rows, key=lambda row: (row[0], row[1], row[2]))[:18]:
            labels = []
            if in_day_rank == 0:
                labels.append("MY DAY")
            if reminder_at:
                labels.append(f"REM {reminder_at.strftime('%b %d %H:%M')}")
            if due:
                labels.append(f"DUE {due.strftime('%b %d')}")
            item = QListWidgetItem(f"{key} · {issue.get('summary', '')}\n{'  |  '.join(labels) or issue.get('status', {}).get('name', '')}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            if due and due < today and status_bucket(issue) not in {"resolved", "closed"}:
                item.setForeground(QColor(active().danger))
            elif in_day_rank == 0:
                item.setForeground(QColor(active().accent))
            self.upcoming_tasks.addItem(item)
        if not rows:
            empty = QListWidgetItem("No planned tasks in this window")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.upcoming_tasks.addItem(empty)

    def month_clicked(self, row: int, col: int) -> None:
        events = self.month.item(row, col).data(Qt.ItemDataRole.UserRole) or []
        self.current_date = self._cell_dates[(row, col)]
        self.select_event(events[0] if events else None)
        self.render_upcoming_tasks()

    def month_double_clicked(self, row: int, col: int) -> None:
        events = self.month.item(row, col).data(Qt.ItemDataRole.UserRole) or []
        if events:
            self.select_event(events[0])
            self.edit_selected()
        else:
            self.new_event(datetime.combine(self._cell_dates[(row, col)], time(hour=9)).astimezone())

    def grid_clicked(self, row: int, col: int) -> None:
        table = self.week if self.view.currentText() == "Week" else self.day
        values = table.item(row, col).data(Qt.ItemDataRole.UserRole) or []
        self.select_event(values[0] if values else None)

    def grid_double_clicked(self, row: int, col: int) -> None:
        table = self.week if self.view.currentText() == "Week" else self.day
        values = table.item(row, col).data(Qt.ItemDataRole.UserRole) or []
        if values:
            if len(values) > 1:
                menu = QMenu(self)
                for event in values:
                    menu.addAction(event["title"], lambda checked=False, item=event: self._edit_event(item))
                menu.exec(table.viewport().mapToGlobal(table.visualItemRect(table.item(row, col)).center()))
                return
            self.select_event(values[0])
            self.edit_selected()
            return
        day = self.current_date - timedelta(days=self.current_date.weekday()) + timedelta(days=col) if self.view.currentText() == "Week" else self.current_date
        self.new_event(datetime.combine(day, time(hour=row)).astimezone())

    def agenda_clicked(self, item: QListWidgetItem) -> None:
        self.select_event(item.data(Qt.ItemDataRole.UserRole))

    def _edit_event(self, event):
        self.select_event(event)
        self.edit_selected()

    def select_event(self, event: dict | None) -> None:
        self.selected_event = event
        if event:
            self.hint.setText(f"Selected: {event['title']}" + (" (Backlog deadline)" if event.get("readonly") else ""))
        else:
            self.hint.setText("Double-click a time slot to create an event. Backlog deadlines are red and open the related task.")
        self.edit.setEnabled(event is not None)
        self.delete.setEnabled(bool(event and not event.get("readonly")))

    def new_event(self, default_start: datetime | None = None) -> None:
        default_start = default_start or datetime.combine(self.current_date, time(hour=9)).astimezone()
        dialog = EventDialog(default_start=default_start, parent=self)
        if dialog.exec():
            self.store.upsert(dialog.data())
            self.selected_event = None
            self.render()
            self.event_changed.emit()

    def edit_selected(self) -> None:
        event = self.selected_event
        if not event:
            return
        if event.get("readonly"):
            self.ticket_requested.emit(event.get("ticket_key", ""))
            return
        dialog = EventDialog(event, parent=self)
        if dialog.exec():
            self.store.upsert(dialog.data())
            self.selected_event = None
            self.render()
            self.event_changed.emit()

    def delete_selected(self) -> None:
        event = self.selected_event
        if not event or event.get("readonly"):
            return
        if QMessageBox.question(self, "Delete event", f"Delete '{event['title']}'?") == QMessageBox.StandardButton.Yes:
            self.store.delete(event["id"])
            self.selected_event = None
            self.render()
            self.event_changed.emit()

    def start_work(self, ticket: dict | None = None, project: str = "") -> dict:
        event = self.tracker.start(ticket, project)
        self.render()
        self.event_changed.emit()
        return event

    def stop_work(self) -> dict | None:
        event = self.tracker.stop()
        self.render()
        self.event_changed.emit()
        return event

    def active_work(self) -> dict | None:
        return self.tracker.active()

    def elapsed_seconds(self) -> int:
        return self.tracker.elapsed_seconds()

    def counted_seconds(self, schedule) -> int:
        """Seconds of the running session that fall inside the work schedule."""
        return self.tracker.counted_seconds(schedule)

    def upcoming(self, limit: int = 8) -> list[dict]:
        now = datetime.now().astimezone()
        return sorted((event for event in self.all_events() if self._parse(event["end"]) >= now), key=lambda event: event["start"])[:limit]
