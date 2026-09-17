from __future__ import annotations

from datetime import date, datetime, timedelta

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QMenu, QScrollArea, QVBoxLayout, QWidget)

from ui.design import button, ElidedLabel, FocusRing, WeekBars
from ui.daily_tools import DailyTools
from ui.queue_delegate import QueueDelegate
from modules.work_insights import format_minutes, logged_minutes

from modules.ticket_utils import issue_key, project_name, status_bucket

NO_TICKET = "No ticket — general work"


class MetricCard(QFrame):
    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(1)
        caption = QLabel(label)
        caption.setObjectName("muted")
        self.value = QLabel("0")
        self.value.setObjectName("metricValue")
        layout.addWidget(caption)
        layout.addWidget(self.value)


class Dashboard(QWidget):
    ticket_requested = pyqtSignal(dict)
    ticket_preview_requested = pyqtSignal(dict)
    stop_work_requested = pyqtSignal()
    start_work_requested = pyqtSignal(str, str)
    focus_toggled = pyqtSignal(bool)
    navigate_requested = pyqtSignal(str)
    new_event_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    capture_requested = pyqtSignal(str)
    capture_toggled = pyqtSignal(str)
    seen_requested = pyqtSignal(dict, str)

    def __init__(self):
        super().__init__()
        self.issues = []
        self._favorites, self._my_day = set(), set()
        self._attention = {}
        self._loading_queue = self._loading_captures = False
        self._card_columns = 0
        self._queue_snapshot = None
        self._capture_snapshot = None
        self._insights_revision = None
        self._selected_key = ""
        self._general_project = "Company General"
        self._schedule = None
        self._loading_subject = False
        self._project_overridden = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        root = QVBoxLayout(canvas)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(13)
        scroll.setWidget(canvas)
        outer.addWidget(scroll)
        hero = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(3)
        self.subtitle = QLabel(datetime.now().strftime("%A, %B %d").upper())
        self.subtitle.setObjectName("eyebrow")
        self.greeting = QLabel("Make room for good work.")
        self.greeting.setObjectName("pageTitle")
        headings.addWidget(self.subtitle)
        headings.addWidget(self.greeting)
        self.day_summary = ElidedLabel("Everything you need for a more intentional day.")
        self.day_summary.setObjectName("muted")
        headings.addWidget(self.day_summary)
        hero.addLayout(headings, 1)
        self.connection = QLabel("Demo data")
        self.connection.setObjectName("badge")
        hero.addWidget(self.connection, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(hero)

        self.cards_layout = QGridLayout()
        self.cards_layout.setSpacing(8)
        self.cards = tuple(MetricCard(label) for label in ("Assigned to you", "In progress", "Resolved", "Due today", "Time logged today"))
        self.assigned_card, self.progress_card, self.resolved_card, self.due_card, self.logged_card = self.cards
        root.addLayout(self.cards_layout)
        self._layout_cards(5)

        body = QHBoxLayout()
        self.body = body
        body.setSpacing(13)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(11)
        right.setSpacing(11)
        self.next_actions = QListWidget()
        self.next_actions.setObjectName("quietList")
        self.next_actions.setItemDelegate(QueueDelegate(self.next_actions))
        self.next_actions.setUniformItemSizes(True)
        self.next_actions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.next_actions.setMinimumHeight(190)
        queue = self._list_panel("Your next moves", self.next_actions, "A little clarity on what comes next.")
        queue_link = button("View all tasks  →", lambda: self.navigate_requested.emit("Personal Tasks"))
        queue_link.setObjectName("textLink")
        queue.layout().itemAt(0).layout().addWidget(queue_link)
        left.addWidget(queue, 3)

        self.captures = QListWidget()
        self.captures.setObjectName("quietList")
        self.captures.setWordWrap(True)
        self.captures.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.capture_input = QLineEdit()
        self.capture_input.setObjectName("captureInput")
        self.capture_input.setPlaceholderText("Capture a thought or follow-up…")
        self.capture_add = button("+", self._submit_capture)
        self.capture_add.setToolTip("Add to your local inbox")
        self.daily_tools = DailyTools(self.captures, self.capture_input, self.capture_add)
        self.daily_tools.setMinimumHeight(180)
        left.addWidget(self.daily_tools, 2)

        focus_card = QFrame()
        focus_card.setObjectName("focusCard")
        focus_layout = QVBoxLayout(focus_card)
        focus_layout.setContentsMargins(13, 10, 13, 11)
        focus_top = QHBoxLayout()
        caption = QLabel("YOUR WORK TIMER")
        caption.setObjectName("eyebrow")
        self.focus = button("Quiet mode")
        self.focus.setCheckable(True)
        self.focus.setToolTip("Hide navigation and distractions. Escape exits.")
        focus_top.addWidget(caption)
        focus_top.addStretch()
        focus_top.addWidget(self.focus)
        focus_layout.addLayout(focus_top)
        center = QHBoxLayout()
        self.ring = FocusRing()
        center.addWidget(self.ring)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        self.focus_title = ElidedLabel("0h 00m today")
        self.focus_title.setMinimumHeight(24)
        self.focus_title.setObjectName("heroTitle")
        self.timer = ElidedLabel("Not tracking")
        self.timer.setObjectName("muted")
        self.timer.setMinimumHeight(20)
        copy.addWidget(self.focus_title)
        copy.addWidget(self.timer)
        center.addLayout(copy, 1)
        focus_layout.addLayout(center)
        subject = QHBoxLayout()
        subject.setSpacing(6)
        self.work_ticket = QComboBox()
        self.work_ticket.setToolTip("What you are working on. Leave it on general work when no ticket applies.")
        self.work_ticket.setMinimumWidth(90)
        self.work_project = QComboBox()
        self.work_project.setEditable(True)
        self.work_project.setToolTip("Which project this time belongs to. It follows the ticket, and you can override it.")
        self.work_project.setMinimumWidth(90)
        subject.addWidget(self.work_ticket, 3)
        subject.addWidget(self.work_project, 2)
        focus_layout.addLayout(subject)
        actions = QHBoxLayout()
        self.start = button("Start", self._request_start, primary=True)
        self.stop = button("Stop", self.stop_work_requested)
        self.stop.setEnabled(False)
        actions.addWidget(self.start, 1)
        actions.addWidget(self.stop, 1)
        focus_layout.addLayout(actions)
        right.addWidget(focus_card)

        self.upcoming = QListWidget()
        self.upcoming.setObjectName("quietList")
        self.upcoming.setWordWrap(True)
        self.upcoming.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.upcoming.setMinimumHeight(102)
        schedule = self._list_panel("On the horizon", self.upcoming)
        schedule.layout().addWidget(button("+  Add an event", self.new_event_requested))
        right.addWidget(schedule, 1)
        week = QFrame()
        week.setObjectName("surface")
        week_layout = QVBoxLayout(week)
        week_layout.setContentsMargins(13, 9, 13, 8)
        self.week_summary = QLabel("Your last seven days")
        self.week_summary.setObjectName("sectionTitle")
        self.week_bars = WeekBars()
        week_layout.addWidget(self.week_summary)
        week_layout.addWidget(self.week_bars)
        right.addWidget(week)
        body.addLayout(left, 3)
        body.addLayout(right, 2)
        root.addLayout(body, 1)

        self.next_actions.itemActivated.connect(self._ticket_activated)
        self.next_actions.itemClicked.connect(self._preview_ticket)
        self.next_actions.itemChanged.connect(self._queue_changed)
        self.next_actions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.next_actions.customContextMenuRequested.connect(self._queue_menu)
        self.focus.toggled.connect(self.focus_toggled)
        self.work_ticket.currentIndexChanged.connect(self._subject_changed)
        self.work_project.currentTextChanged.connect(self._project_edited)
        self.capture_input.returnPressed.connect(self._submit_capture)
        self.captures.itemChanged.connect(self._capture_changed)
        self.upcoming.itemActivated.connect(lambda _: self.navigate_requested.emit("Calendar"))

    def _request_start(self) -> None:
        self.start_work_requested.emit(self.selected_ticket_key(), self.selected_project())

    def selected_ticket_key(self) -> str:
        """The chosen ticket key, or "" for general work with no ticket."""
        return str(self.work_ticket.currentData() or "")

    def selected_project(self) -> str:
        return self.work_project.currentText().strip() or self._general_project

    def _subject_changed(self, *_args) -> None:
        """Following the ticket's project is the common case; overriding is not."""
        if self._loading_subject:
            return
        self._project_overridden = False
        self.work_project.setCurrentText(self._project_for(self.work_ticket.currentIndex()))
        self._project_overridden = False

    def _project_edited(self, *_args) -> None:
        """A project the user chose survives the next Backlog refresh."""
        if not self._loading_subject:
            self._project_overridden = True

    def _preview_ticket(self, item):
        issue = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(issue, dict):
            self.ticket_preview_requested.emit(issue)

    def set_selected_ticket(self, issue):
        self._selected_key = issue_key(issue or {})

    @staticmethod
    def _list_panel(title, widget, subtitle=""):
        panel = QFrame()
        panel.setObjectName("surface")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(13, 9, 13, 7)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        heading_row = QHBoxLayout()
        heading_row.addWidget(heading)
        heading_row.addStretch()
        layout.addLayout(heading_row)
        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("muted")
            layout.addWidget(caption)
        layout.addWidget(widget, 1)
        return panel

    def _layout_cards(self, columns: int) -> None:
        if columns == self._card_columns:
            return
        self._card_columns = columns
        for card in self.cards:
            self.cards_layout.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.cards_layout.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.body.setDirection(QHBoxLayout.Direction.TopToBottom if self.width() < 1000 else QHBoxLayout.Direction.LeftToRight)
        columns = 5 if self.width() >= 760 else 3 if self.width() >= 500 else 2
        self._layout_cards(columns)

    def set_connection(self, demo: bool, profile: dict | None = None) -> None:
        self.connection.setText("Demo data" if demo else "Backlog connected")
        if profile and profile.get("name"):
            name = str(profile["name"]).split()[0]
            self.greeting.setText(f"Good day, {name}.")

    def set_favorites(self, values: set[str] | list[str]) -> None:
        self._favorites = set(values)

    def set_my_day(self, values: set[str] | list[str]) -> None:
        self._my_day = set(values)

    def set_attention(self, values: dict[str, str]) -> None:
        self._attention = {str(key): str(value) for key, value in values.items()}

    def focus_capture(self) -> None:
        self.capture_input.setFocus()
        self.capture_input.selectAll()

    def _submit_capture(self) -> None:
        text = self.capture_input.text().strip()
        if text:
            self.capture_requested.emit(text)
            self.capture_input.clear()

    def set_captures(self, values: list[dict]) -> None:
        snapshot = repr(values)
        if snapshot == self._capture_snapshot:
            return
        self._capture_snapshot = snapshot
        self._loading_captures = True
        self.captures.clear()
        for capture in values[:50]:
            created = str(capture.get("created", ""))
            stamp = created[11:16] if len(created) >= 16 else ""
            text = f"{capture.get('text', '')}  ·  {stamp}" if stamp else str(capture.get("text", ""))
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, str(capture.get("id", "")))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if capture.get("done") else Qt.CheckState.Unchecked)
            if capture.get("done"):
                item.setForeground(Qt.GlobalColor.gray)
            self.captures.addItem(item)
        if not values:
            empty = QListWidgetItem("Nothing captured yet")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.captures.addItem(empty)
        self._loading_captures = False

    def _capture_changed(self, item: QListWidgetItem) -> None:
        capture_id = item.data(Qt.ItemDataRole.UserRole)
        if not self._loading_captures and capture_id:
            self.capture_toggled.emit(str(capture_id))

    def _ticket_activated(self, item: QListWidgetItem) -> None:
        ticket = item.data(Qt.ItemDataRole.UserRole)
        if ticket:
            self.ticket_requested.emit(ticket)

    def _queue_changed(self, item: QListWidgetItem) -> None:
        ticket = item.data(Qt.ItemDataRole.UserRole)
        if not self._loading_queue and isinstance(ticket, dict):
            mode = "back" if item.checkState() == Qt.CheckState.Checked else "unseen"
            self.seen_requested.emit(ticket, mode)

    def _queue_menu(self, point) -> None:
        item = self.next_actions.itemAt(point)
        ticket = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(ticket, dict):
            return
        state = self._attention.get(issue_key(ticket), "new")
        menu = QMenu(self)
        menu.addAction("Open ticket", lambda: self.ticket_requested.emit(ticket))
        if state in {"back", "hidden"}:
            menu.addAction("Mark unseen", lambda: self.seen_requested.emit(ticket, "unseen"))
        else:
            menu.addAction("Mark seen (move to back)", lambda: self.seen_requested.emit(ticket, "back"))
        menu.addAction("Hide this revision from queue", lambda: self.seen_requested.emit(ticket, "hidden"))
        menu.exec(self.next_actions.viewport().mapToGlobal(point))

    def update_data(self, issues: list[dict], calendar) -> None:
        self.issues = issues
        buckets = [status_bucket(ticket) for ticket in issues]
        today = date.today().isoformat()
        due = [ticket for ticket in issues if ticket.get("dueDate") == today and status_bucket(ticket) != "closed"]
        self.assigned_card.value.setText(str(len(issues)))
        self.progress_card.value.setText(str(buckets.count("progress")))
        self.resolved_card.value.setText(str(buckets.count("resolved")))
        self.due_card.value.setText(str(len(due)))
        # An active session changes every second, so it cannot take part in the
        # cache key; totals are recomputed while one is running.
        active_session = calendar.active_work()
        revision = (id(calendar.store), calendar.store.revision, date.today(), bool(active_session), id(self._schedule))
        if revision != self._insights_revision or active_session:
            days = [date.today() - timedelta(days=6-i) for i in range(7)]
            self._day_totals = logged_minutes(calendar.store.all(), days, self._schedule)
            self.week_bars.set_data(self._day_totals, [day.strftime("%a") for day in days])
            self.week_summary.setText(f"{format_minutes(sum(self._day_totals))}  ·  last 7 days")
            self._insights_revision = revision
        minutes = self._day_totals[-1]
        active_count = sum(bucket not in {"closed", "resolved"} for bucket in buckets)
        self.day_summary.setText(f"{active_count} open tasks. {len(due)} due today. One next step at a time.")
        self.logged_card.value.setText(format_minutes(minutes))
        self._refresh_subject_choices(issues, active_session)

        self._loading_queue = True
        self.next_actions.clear()
        priority_order = {"High": 0, "Normal": 1, "Low": 2}
        candidates = sorted(
            (ticket for ticket in issues if status_bucket(ticket) != "closed" and self._attention.get(issue_key(ticket), "new") != "hidden"),
            key=lambda ticket: (
                1 if self._attention.get(issue_key(ticket), "new") == "back" else 0,
                0 if issue_key(ticket) in self._my_day else 1,
                0 if issue_key(ticket) in self._favorites else 1,
                ticket.get("dueDate") or "9999-12-31",
                priority_order.get(ticket.get("priority", {}).get("name"), 9),
            ),
        )
        for ticket in candidates[:14]:
            day = "DAY · " if issue_key(ticket) in self._my_day else ""
            star = "★ " if issue_key(ticket) in self._favorites else ""
            state = self._attention.get(issue_key(ticket), "new")
            attention = {"changed": "UPDATED · ", "new": "NEW · ", "back": "SEEN · "}.get(state, "")
            assignee = (ticket.get("assignee") or {}).get("name") or "Unassigned"
            review = f"  |  Review: {assignee}" if ticket.get("_review") else ""
            item = QListWidgetItem(
                f"{attention}{day}{star}{issue_key(ticket)}  ·  {ticket.get('summary')}\n"
                f"{ticket.get('status', {}).get('name')}  |  Due {ticket.get('dueDate') or 'not set'}{review}"
            )
            item.setData(Qt.ItemDataRole.UserRole, ticket)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if state == "back" else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole + 1, {
                "day": issue_key(ticket) in self._my_day,
                "star": issue_key(ticket) in self._favorites,
                "attention": state,
            })
            item.setSizeHint(QSize(0, 56))
            self.next_actions.addItem(item)
            if issue_key(ticket) == self._selected_key:
                self.next_actions.setCurrentItem(item)
        if not candidates:
            empty = QListWidgetItem("All clear. Your next tasks will appear here.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.next_actions.addItem(empty)
        self._loading_queue = False

        self.upcoming.clear()
        events = calendar.upcoming()
        for event in events:
            self.upcoming.addItem(QListWidgetItem(f"{calendar._parse(event['start']).strftime('%a %H:%M')}  {event['title']}"))
        if not events:
            empty = QListWidgetItem("No upcoming events")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.upcoming.addItem(empty)

    def set_schedule(self, schedule, general_project: str) -> None:
        self._schedule = schedule
        self._general_project = str(general_project or "Company General")
        self._insights_revision = None

    def _refresh_subject_choices(self, issues: list[dict], active_session: dict | None) -> None:
        """Offer the open tickets, general work, and every project seen so far."""
        chosen_ticket = self.selected_ticket_key() or self._selected_key
        # A project the user typed is kept; one that merely followed an earlier
        # ticket is replaced, so a stale project can never be logged silently.
        chosen_project = self.work_project.currentText().strip() if self._project_overridden else ""
        self._loading_subject = True
        self.work_ticket.clear()
        self.work_ticket.addItem(NO_TICKET, "")
        for issue in issues:
            if status_bucket(issue) == "closed":
                continue
            key = issue_key(issue)
            self.work_ticket.addItem(f"{key}  ·  {issue.get('summary', '')}"[:80], key)
            # The project travels with the row so choosing a ticket never has to
            # look the issue up again.
            self.work_ticket.setItemData(self.work_ticket.count() - 1, project_name(issue), Qt.ItemDataRole.UserRole + 1)
        projects = sorted({project_name(issue) for issue in issues} | {self._general_project}, key=str.casefold)
        self.work_project.clear()
        self.work_project.addItems(projects)
        if active_session:
            # A running session shows exactly what it is recording.
            chosen_ticket = str(active_session.get("ticket_key") or "")
            chosen_project = str(active_session.get("project") or "")
        index = self.work_ticket.findData(chosen_ticket)
        index = index if index >= 0 else 0
        self.work_ticket.setCurrentIndex(index)
        self.work_project.setCurrentText(chosen_project or self._project_for(index))
        self._loading_subject = False

    def _project_for(self, index: int) -> str:
        return str(self.work_ticket.itemData(index, Qt.ItemDataRole.UserRole + 1) or self._general_project)

    def update_timer(self, calendar, counted_seconds: int = 0, counting: bool = True, today_minutes: int = 0) -> None:
        """Show counted time today, and what the running session is counting."""
        active = calendar.active_work()
        capacity = self._schedule.daily_minutes if self._schedule else 480
        self.ring.set_progress(today_minutes / capacity if capacity else 0)
        self.focus_title.setText(f"{format_minutes(today_minutes)} today")
        self.stop.setEnabled(bool(active))
        self.start.setEnabled(not active)
        self.work_ticket.setEnabled(not active)
        self.work_project.setEnabled(not active)
        if not active:
            self.timer.setText("Not tracking. Pick what you are working on, then Start.")
            return
        subject = str(active.get("ticket_key") or "").strip() or str(active.get("project") or "").strip() or "general work"
        state = f"{format_minutes(counted_seconds // 60)} counted" if counting else "paused · outside your hours"
        self.timer.setText(f"{subject} · {state}")
