from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QTimer, Qt, pyqtSignal
from ui.design import button
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHeaderView,
                             QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QPushButton, QTableView,
                             QVBoxLayout, QWidget)

from ui.ticket_model import TicketModel, TicketSortModel, TicketDelegate

from modules.ticket_utils import issue_key, project_name, status_bucket

ROW_HEIGHT = 38
VIEWS = (
    ("All assigned", "all"),
    ("Needs attention", "attention"),
    ("Review tickets", "review"),
    ("Seen / deferred", "seen"),
    ("My Day", "my_day"),
    ("In progress", "progress"),
    ("Due today", "today"),
    ("Overdue", "overdue"),
    ("High priority", "high"),
    ("Starred", "starred"),
    ("No due date", "none"),
    ("Resolved", "resolved"),
)


class TicketList(QWidget):
    selected = pyqtSignal(dict)
    action_requested = pyqtSignal(str, dict)
    bulk_action_requested = pyqtSignal(str, list)
    favorite_requested = pyqtSignal(dict)
    my_day_requested = pyqtSignal(dict)
    seen_requested = pyqtSignal(dict, str)
    filter_panel_toggled = pyqtSignal(bool)
    view_changed = pyqtSignal(str)
    columns_changed = pyqtSignal(list)
    refresh_requested = pyqtSignal()
    save_view_requested = pyqtSignal(dict)

    def __init__(self, config: dict | None = None):
        super().__init__()
        self.config = config or {}
        self.issues: list[dict] = []
        self._favorites, self._my_day = set(), set()
        self._reminders, self._attention, self._search_index = {}, {}, {}
        self._filter_requested = bool(self.config.get("task_filter_panel", False))
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(self.render)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel("Your tasks")
        title.setObjectName("pageTitle")
        self.count = QLabel("0 tasks")
        self.count.setObjectName("badge")
        heading.addWidget(title)
        heading.addWidget(self.count)
        heading.addStretch()
        self.refresh_button = button("Sync tasks", self.refresh_requested, glyph="refresh")
        heading.addWidget(self.refresh_button)
        root.addLayout(heading)
        subtitle = QLabel("A clear view of what needs you. Plan, focus, and move it forward.")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)
        chips = QHBoxLayout()
        self.quick_views = {}
        for label, value in (("All tasks", "all"), ("My day", "my_day"), ("In progress", "progress"), ("Needs attention", "attention"), ("Starred", "starred")):
            chip = button(label)
            chip.setProperty("chip", True)
            chip.setCheckable(True)
            chip.clicked.connect(lambda _, v=value: self.activate_view(v))
            self.quick_views[value] = chip
            chips.addWidget(chip)
        chips.addStretch()
        self.attention_summary = QLabel()
        self.today_summary = QLabel()
        self.progress_summary = QLabel()
        for summary in (self.attention_summary, self.today_summary, self.progress_summary):
            summary.setObjectName("muted")
            summary.hide()
        root.addLayout(chips)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tickets, people, or projects…    Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(120)
        self.view = QComboBox()
        self.view.setToolTip("All task views, including your saved filters")
        for label, value in VIEWS:
            self.view.addItem(label, value)
        for saved in self.config.get("saved_task_views", []):
            self.view.addItem(f"★ {saved['name']}", {"saved": dict(saved)})
        self.view.setCurrentIndex(max(0, self.view.findData(self.config.get("task_view", "all"))))
        self.save_view_button = button("Save view", self._save_view)
        self.filters_button = button("Filters")
        self.filters_button.setCheckable(True)
        self.filters_button.setChecked(self._filter_requested)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.view)
        controls.addWidget(self.save_view_button)
        controls.addWidget(self.filters_button)
        root.addLayout(controls)

        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("toolbarSurface")
        row = QHBoxLayout(self.selection_bar)
        row.setContentsMargins(10, 6, 10, 6)
        self.selection_count = QLabel("0 selected")
        row.addWidget(self.selection_count)
        row.addStretch()
        row.addWidget(button("Start work", lambda: self._bulk_action("start_first"), primary=True))
        row.addWidget(button("My day", lambda: self._bulk_action("add_my_day")))
        more = button("Actions")
        menu = QMenu(more)
        menu.addAction("Mark in progress", lambda: self._bulk_action("mark_progress"))
        menu.addAction("Sync notes", lambda: self._bulk_action("sync_notes"))
        menu.addAction("Open full ticket", lambda: self._action("send_detail"))
        more.setMenu(menu)
        row.addWidget(more)
        row.addWidget(button("Clear", lambda: self.table.clearSelection()))
        root.addWidget(self.selection_bar)
        self.selection_bar.hide()

        body = QHBoxLayout()
        self.table = QTableView()
        self.model = TicketModel(self)
        self.sort_model = TicketSortModel(self)
        self.sort_model.setSourceModel(self.model)
        self.table.setModel(self.sort_model)
        self.table.setItemDelegate(TicketDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT if self.config.get("density") == "comfortable" else ROW_HEIGHT - 4)
        self.table.installEventFilter(self)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(28)
        for column in range(10):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col, width in {0: 30, 1: 125, 3: 92, 4: 108, 5: 105, 6: 98, 7: 105, 8: 42, 9: 46}.items():
            self.table.setColumnWidth(col, width)
        body.addWidget(self.table, 1)
        self.filter_panel = self._build_filter_panel()
        body.addWidget(self.filter_panel)
        root.addLayout(body, 1)
        self.empty_state = QLabel("No tasks match this view. Try another view or clear your filters.")
        self.empty_state.setObjectName("muted")
        self.empty_state.setWordWrap(True)
        self.empty_state.hide()
        root.addWidget(self.empty_state)
        hint = QLabel("J / K navigate   ·   Enter open   ·   Space star   ·   Right-click for all actions")
        hint.setObjectName("muted")
        root.addWidget(hint)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.view.currentIndexChanged.connect(self._view_changed)
        for combo in (self.status, self.project, self.priority, self.due):
            combo.currentIndexChanged.connect(self.render)
        self.filters_button.toggled.connect(self.set_filter_panel_visible)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _: self._action("send_detail"))
        self.table.clicked.connect(lambda index: self._cell_clicked(index.row(), index.column()))
        self.table.customContextMenuRequested.connect(self.menu)
        self._apply_filter_panel_visibility()
        self._apply_column_visibility()
        self._sync_chips()

    def _sync_chips(self):
        for value, chip in self.quick_views.items():
            chip.setChecked(self.view.currentData() == value)

    def set_workspace_state(self, favorites, my_day, reminders, attention):
        """Apply one workspace snapshot and render once."""
        self._favorites, self._my_day = set(favorites), set(my_day)
        self._reminders, self._attention = reminders, attention
        self._update_summary()
        self.render()

    def _build_filter_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("filterPanel")
        panel.setMinimumWidth(175)
        panel.setMaximumWidth(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(6)
        title = QLabel("Filter & display")
        title.setObjectName("sectionTitle")
        reset = QPushButton("Reset all")
        reset.setProperty("secondary", True)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(reset)
        layout.addLayout(top)

        self.status = QComboBox()
        self.status.addItem("All statuses", "")
        self.project = QComboBox()
        self.project.addItem("All projects", "")
        self.priority = QComboBox()
        self.priority.addItem("All priorities", "")
        for value in ("High", "Normal", "Low"):
            self.priority.addItem(value, value)
        self.due = QComboBox()
        for label, value in (("Any due date", "all"), ("Next 4 days", "next4"), ("Due today", "today"), ("Overdue", "overdue"), ("No due date", "none")):
            self.due.addItem(label, value)
        for label, widget in (("Status", self.status), ("Project", self.project), ("Priority", self.priority), ("Due", self.due)):
            caption = QLabel(label)
            caption.setObjectName("muted")
            layout.addWidget(caption)
            layout.addWidget(widget)

        columns = QLabel("Visible columns")
        columns.setObjectName("muted")
        layout.addSpacing(4)
        layout.addWidget(columns)
        self.column_checks: dict[int, QCheckBox] = {}
        visible_columns = set(self.config.get("task_columns", ["priority", "status", "due", "project"]))
        for column, key, label in ((3, "priority", "Priority"), (4, "status", "Status"), (5, "due", "Due date"), (6, "project", "Project"), (7, "assignee", "Assignee")):
            checkbox = QCheckBox(label)
            checkbox.setProperty("columnKey", key)
            checkbox.setChecked(key in visible_columns)
            checkbox.toggled.connect(self._column_changed)
            self.column_checks[column] = checkbox
            layout.addWidget(checkbox)
        layout.addStretch()
        help_text = QLabel("J / K moves rows\nSpace toggles star\nSeen defers the current revision\nMD opens the note\nDouble-click opens ticket studio")
        help_text.setObjectName("muted")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        reset.clicked.connect(self.clear_filters)
        return panel

    def set_issues(self, issues: list[dict], render: bool = True) -> None:
        current_status = self.status.currentData()
        current_project = self.project.currentData()
        self.issues = issues
        self._search_index = {
            issue_key(issue): " ".join((
                issue_key(issue), str(issue.get("summary", "")), str(issue.get("description", "")),
                project_name(issue), (issue.get("status") or {}).get("name", ""),
                (issue.get("assignee") or {}).get("name", ""), (issue.get("assignee") or {}).get("userId", ""),
            )).casefold() for issue in issues
        }
        self._update_summary()
        statuses = sorted({str(issue.get("status", {}).get("name", "")) for issue in issues if issue.get("status")})
        projects = sorted({project_name(issue) for issue in issues})
        self._replace_combo(self.status, "All statuses", statuses, current_status)
        self._replace_combo(self.project, "All projects", projects, current_project)
        if render:
            self.render()

    @staticmethod
    def _replace_combo(combo: QComboBox, first: str, values: list[str], selected) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(first, "")
        for value in values:
            combo.addItem(value, value)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def set_favorites(self, values: set[str] | list[str]) -> None:
        self._favorites = set(values)
        self.render()

    def set_my_day(self, values: set[str] | list[str]) -> None:
        self._my_day = set(values)
        self.render()

    def set_reminders(self, values: dict[str, dict]) -> None:
        self._reminders = {key: dict(value) for key, value in values.items()}
        self.render()

    def set_attention(self, values: dict[str, str]) -> None:
        self._attention = {str(key): str(value) for key, value in values.items()}
        self._update_summary()
        self.render()

    def _update_summary(self) -> None:
        today = date.today()
        attention = sum(1 for issue in self.issues if self._attention.get(issue_key(issue), "new") in {"new", "changed"})
        due_today = sum(1 for issue in self.issues if self._due_date(issue) == today)
        progress = sum(1 for issue in self.issues if status_bucket(issue) == "progress")
        self.attention_summary.setText(f"Attention {attention}")
        self.today_summary.setText(f"Today {due_today}")
        self.progress_summary.setText(f"In progress {progress}")

    def set_due_filter(self, value: str) -> None:
        index = self.due.findData(value)
        self.due.setCurrentIndex(max(0, index))

    def activate_view(self, value: str) -> None:
        index = self.view.findData(value)
        if index >= 0:
            self.view.setCurrentIndex(index)
        self.focus_search(select_all=False)

    def clear_filters(self) -> None:
        widgets = (self.search, self.view, self.status, self.project, self.priority, self.due)
        for widget in widgets:
            widget.blockSignals(True)
        self.search.clear(); self.view.setCurrentIndex(0); self.status.setCurrentIndex(0)
        self.project.setCurrentIndex(0); self.priority.setCurrentIndex(0); self.due.setCurrentIndex(0)
        for widget in widgets:
            widget.blockSignals(False)
        for checkbox in self.column_checks.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
        self.view_changed.emit("all")
        self.columns_changed.emit(self.visible_column_keys())
        self._apply_column_visibility()
        self.render()

    def _view_changed(self, *_args) -> None:
        self._sync_chips()
        selected = self.view.currentData()
        if isinstance(selected, dict) and selected.get("saved"):
            self._apply_saved_view(selected["saved"])
            return
        self.view_changed.emit(str(selected or "all"))
        self.render()

    def _save_view(self) -> None:
        self.save_view_requested.emit({
            "status": self.status.currentData() or "",
            "project": self.project.currentData() or "",
            "priority": self.priority.currentData() or "",
            "due": self.due.currentData() or "all",
            "view": self.view.currentData() if isinstance(self.view.currentData(), str) else "all",
        })

    def add_saved_view(self, name: str, definition: dict) -> None:
        self.view.addItem(f"★ {name}", {"saved": {"name": name, **definition}})

    def _apply_saved_view(self, saved: dict) -> None:
        for widget, value in ((self.status, saved.get("status", "")), (self.project, saved.get("project", "")), (self.priority, saved.get("priority", "")), (self.due, saved.get("due", "all"))):
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
        base_view = saved.get("view", "all")
        index = self.view.findData(base_view)
        if index >= 0:
            self.view.blockSignals(True)
            self.view.setCurrentIndex(index)
            self.view.blockSignals(False)
        self.render()

    def _column_changed(self, *_args) -> None:
        self._apply_column_visibility()
        self.columns_changed.emit(self.visible_column_keys())

    def visible_column_keys(self) -> list[str]:
        return [str(checkbox.property("columnKey")) for checkbox in self.column_checks.values() if checkbox.isChecked()]

    @staticmethod
    def _due_date(issue: dict) -> date | None:
        try:
            return date.fromisoformat(issue.get("dueDate")) if issue.get("dueDate") else None
        except (TypeError, ValueError):
            return None

    def filtered(self) -> list[dict]:
        query = self.search.text().casefold().strip()
        selected_view = self.view.currentData() or "all"
        selected_status = self.status.currentData()
        selected_project = self.project.currentData()
        selected_priority = self.priority.currentData()
        selected_due = self.due.currentData() or "all"
        today = date.today()
        rows = []
        for issue in self.issues:
            key = issue_key(issue)
            priority = issue.get("priority", {}).get("name", "")
            bucket = status_bucket(issue)
            due = self._due_date(issue)
            blob = self._search_index.get(key, "")
            if query and not all(token in blob for token in query.split()):
                continue
            if selected_status and issue.get("status", {}).get("name") != selected_status:
                continue
            if selected_project and project_name(issue) != selected_project:
                continue
            if selected_priority and priority != selected_priority:
                continue
            if not self._matches_due(selected_due, due, today):
                continue
            if selected_view == "progress" and bucket != "progress":
                continue
            if selected_view == "attention" and self._attention.get(key, "new") not in {"new", "changed"}:
                continue
            if selected_view == "review" and not issue.get("_review"):
                continue
            if selected_view == "seen" and self._attention.get(key) not in {"back", "hidden"}:
                continue
            if selected_view == "my_day" and key not in self._my_day and due != today:
                continue
            if selected_view == "today" and due != today:
                continue
            if selected_view == "overdue" and (not due or due >= today or bucket in {"resolved", "closed"}):
                continue
            if selected_view == "high" and priority != "High":
                continue
            if selected_view == "starred" and key not in self._favorites:
                continue
            if selected_view == "none" and due is not None:
                continue
            if selected_view == "resolved" and bucket not in {"resolved", "closed"}:
                continue
            rows.append(issue)
        return rows

    @staticmethod
    def _matches_due(value: str, due: date | None, today: date) -> bool:
        if value == "next4":
            return bool(due and today <= due <= today + timedelta(days=4))
        if value == "today":
            return due == today
        if value == "overdue":
            return bool(due and due < today)
        if value == "none":
            return due is None
        return True

    def render(self) -> None:
        self.search_timer.stop()
        rows = self.filtered()
        selected_keys = {issue_key(issue) for issue in self.selected_issues()}
        current = self.current_issue()
        current_key = issue_key(current) if current else ""
        scroll = self.table.verticalScrollBar().value()
        selection = self.table.selectionModel()
        selection.blockSignals(True)
        self.model.replace(rows)
        restored = QItemSelection()
        for row in range(self.sort_model.rowCount() if selected_keys or current_key else 0):
            index = self.sort_model.index(row, 1)
            key = issue_key(index.data(Qt.ItemDataRole.UserRole))
            if key in selected_keys:
                restored.select(self.sort_model.index(row, 0), self.sort_model.index(row, 9))
            if key == current_key:
                selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        selection.select(restored, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        selection.blockSignals(False)
        self.table.verticalScrollBar().setValue(scroll)
        self.count.setText(f"{len(rows)} / {len(self.issues)} tasks")
        self.empty_state.setVisible(not rows)
        self._update_selection_bar()
        self._sync_chips()

    def selected_issues(self) -> list[dict]:
        return [index.data(Qt.ItemDataRole.UserRole) for index in self.table.selectionModel().selectedRows(1)]

    def current_issue(self) -> dict | None:
        index = self.table.currentIndex()
        return index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None

    def _selection_changed(self, *_args) -> None:
        self._update_selection_bar()
        issue = self.current_issue()
        if issue:
            self.selected.emit(issue)

    def _update_selection_bar(self) -> None:
        count = len(self.selected_issues())
        self.selection_count.setText(f"{count} selected")
        self.selection_bar.setVisible(count > 0)

    def _cell_clicked(self, row: int, column: int) -> None:
        issue = self.sort_model.index(row, 1).data(Qt.ItemDataRole.UserRole)
        if not issue:
            return
        if column == 0:
            self.favorite_requested.emit(issue)
        elif column == 8:
            state = self._attention.get(issue_key(issue))
            self.seen_requested.emit(issue, "unseen" if state in {"back", "hidden"} else "back")
        elif column == 9:
            self.action_requested.emit("open_note", issue)

    def _action(self, action: str) -> None:
        issue = self.current_issue()
        if issue:
            self.action_requested.emit(action, issue)

    def _bulk_action(self, action: str) -> None:
        issues = self.selected_issues()
        if issues:
            self.bulk_action_requested.emit(action, issues)

    def menu(self, point) -> None:
        item = self.table.indexAt(point)
        if not item.isValid():
            return
        if item.row() not in {index.row() for index in self.table.selectionModel().selectedRows()}:
            self.table.selectRow(item.row())
        issue = self.current_issue()
        if not issue:
            return
        key = issue_key(issue)
        menu = QMenu(self)
        menu.addAction("Start work", lambda: self._action("start_work"))
        menu.addAction("Show inspector", lambda: self._action("show_detail"))
        menu.addAction("Send to Detail", lambda: self._action("send_detail"))
        menu.addAction("Unstar" if key in self._favorites else "Star", lambda: self.favorite_requested.emit(issue))
        menu.addAction("Remove from My Day" if key in self._my_day else "Add to My Day", lambda: self.my_day_requested.emit(issue))
        state = self._attention.get(key, "new")
        if state in {"back", "hidden"}:
            menu.addAction("Mark unseen", lambda: self.seen_requested.emit(issue, "unseen"))
        else:
            menu.addAction("Mark seen (move to back)", lambda: self.seen_requested.emit(issue, "back"))
            menu.addAction("Mark seen (hide from queue)", lambda: self.seen_requested.emit(issue, "hidden"))
        menu.addSeparator()
        menu.addAction("Open Obsidian note", lambda: self.action_requested.emit("open_note", issue))
        menu.addAction("Open project environment", lambda: self.action_requested.emit("environment", issue))
        source_menu = menu.addMenu("Source control / SVN")
        source_menu.addAction("Open Source Control", lambda: self.action_requested.emit("svn_open", issue))
        source_menu.addAction("Show working-copy status", lambda: self.action_requested.emit("svn_status", issue))
        source_menu.addAction("Show diff", lambda: self.action_requested.emit("svn_diff", issue))
        source_menu.addAction("Compare with base", lambda: self.action_requested.emit("svn_compare", issue))
        source_menu.addSeparator()
        source_menu.addAction("Create ticket patch", lambda: self.action_requested.emit("svn_create_patch", issue))
        source_menu.addAction("Shelve changes…", lambda: self.action_requested.emit("svn_shelve", issue))
        source_menu.addAction("Open ticket patches", lambda: self.action_requested.emit("svn_patches", issue))
        source_menu.addAction("Apply / unshelve patch…", lambda: self.action_requested.emit("svn_apply_patch", issue))
        menu.addAction("Open in Backlog", lambda: self._action("open_browser"))
        reminder_menu = menu.addMenu("Reminder / snooze")
        reminder_menu.addAction("In 1 hour", lambda: self.action_requested.emit("remind_hour", issue))
        reminder_menu.addAction("Tomorrow at 09:00", lambda: self.action_requested.emit("remind_tomorrow", issue))
        reminder_menu.addAction("Next Monday at 09:00", lambda: self.action_requested.emit("remind_next_week", issue))
        reminder_menu.addAction("Custom...", lambda: self.action_requested.emit("remind_custom", issue))
        if key in self._reminders:
            reminder_menu.addSeparator()
            reminder_menu.addAction("Clear reminder", lambda: self.action_requested.emit("remind_clear", issue))
        if len(self.selected_issues()) > 1:
            menu.addSeparator()
            menu.addAction("Mark selected in progress", lambda: self._bulk_action("mark_progress"))
            menu.addAction("Sync selected notes", lambda: self._bulk_action("sync_notes"))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def set_filter_panel_visible(self, visible: bool) -> None:
        self._filter_requested = visible
        self._apply_filter_panel_visibility()
        self.filter_panel_toggled.emit(visible)

    def toggle_filter_panel(self) -> None:
        self.filters_button.setChecked(not self._filter_requested)

    def refresh_theme(self) -> None:
        """Rows are painted from the active theme, so a repaint is the whole update."""
        self.table.viewport().update()

    def _apply_filter_panel_visibility(self) -> None:
        enough_space = self.width() >= 880
        visible = self._filter_requested and enough_space
        self.filter_panel.setVisible(visible)
        self.filters_button.blockSignals(True)
        self.filters_button.setChecked(self._filter_requested)
        self.filters_button.blockSignals(False)
        if self._filter_requested and not enough_space:
            self.filters_button.setToolTip("The filter rail is hidden while the task page is narrow")
        else:
            self.filters_button.setToolTip("Show or hide filters and display options")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_filter_panel_visibility()
        self._apply_column_visibility()

    def _apply_column_visibility(self, *_args) -> None:
        auto_hidden = {3, 6, 7} if self.width() < 950 else {7} if self.width() < 1250 else set()
        if self.width() < 660:
            auto_hidden.add(5)
        for column, checkbox in self.column_checks.items():
            self.table.setColumnHidden(column, not checkbox.isChecked() or column in auto_hidden)
        for column in (8, 9):
            self.table.setColumnHidden(column, self.width() < 660)

    def focus_search(self, select_all: bool = True) -> None:
        self.search.setFocus()
        if select_all:
            self.search.selectAll()

    def _move_current(self, delta: int) -> None:
        count = self.sort_model.rowCount()
        if not count:
            return
        row = self.table.currentIndex().row()
        row = 0 if row < 0 else max(0, min(count - 1, row + delta))
        index = self.sort_model.index(row, 1)
        self.table.setCurrentIndex(index)
        self.table.selectRow(row)
        self.table.scrollTo(index)

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() == QEvent.Type.KeyPress and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() == Qt.Key.Key_J:
                self._move_current(1)
                return True
            if event.key() == Qt.Key.Key_K:
                self._move_current(-1)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._action("send_detail")
                return True
            if event.key() == Qt.Key.Key_Space:
                issue = self.current_issue()
                if issue:
                    self.favorite_requested.emit(issue)
                return True
        return super().eventFilter(watched, event)
