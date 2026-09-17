from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (QApplication, QFrame,
                             QHBoxLayout, QMainWindow, QMenu,
                             QInputDialog, QMessageBox, QSplitter,
                             QStackedWidget, QStyle, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from config import backlog_key, save_config
from modules.obsidian_sync import ObsidianSync
from modules.patch_store import PatchStore
from modules.svn_service import SvnError
from modules.ticket_utils import issue_key, issue_url, project_name, status_bucket
from modules.work_insights import format_minutes, logged_minutes
from modules.work_schedule import WorkSchedule
from modules.workspace_store import WorkspaceStore
from services.background import running_tasks
from services.credentials import save_backlog_key
from services.local_llm import TicketAnalysisThread
from services.sync_service import BacklogActionThread, BacklogCommentsThread, BacklogLookupThread, SyncThread
from ui.calendar_widget import CalendarWidget
from ui.command_palette import CommandPalette
from ui.dashboard import Dashboard
from ui.environment_dialog import EnvironmentDialog
from ui.insights_widget import InsightsWidget
from ui.note_preview_dialog import NotePreviewDialog
from ui.obsidian_widget import ObsidianWidget
from ui.reminder_dialog import ReminderDialog
from ui.review_board import ReviewBoard
from ui.settings_dialog import SettingsDialog
from ui.source_control_widget import SourceControlWidget
from ui.git_widget import GitWidget
from ui.standup_widget import StandupWidget
from ui.design import retint_icons
from ui.inspector import build_inspector
from ui.theme import choices as theme_choices, resolve as resolve_theme
from ui.shell import ShellMixin, PAGES, NAV_SECTIONS as NAV_SECTIONS, PAGE_INFO
from ui.ticket_list import TicketList
from ui.ticket_detail import TicketDetailWidget

APP_ICON = Path(__file__).resolve().parents[1] / "assets" / "ty-work-hub.ico"


class MainWindow(ShellMixin, QMainWindow):
    def __init__(self, config: dict, workspace_store: WorkspaceStore | None = None, persist_preferences: bool = True):
        super().__init__()
        self.config = config
        self.schedule = WorkSchedule.from_config(config)
        self.workspace = workspace_store or WorkspaceStore()
        self._persist_preferences = persist_preferences
        self.issues: list[dict] = []
        self.current: dict | None = None
        self.sync_thread: SyncThread | None = None
        self._refresh_pending = False
        self._closing = False
        self.action_workers: list[BacklogActionThread] = []
        self.lookup_workers: list[BacklogLookupThread] = []
        self.analysis_workers: list[TicketAnalysisThread] = []
        self.comment_workers: list[BacklogCommentsThread] = []
        self._backlog_action_context: dict[BacklogActionThread, dict] = {}
        self._retrying_pending_action = False
        self._inspector_ai_running = False
        self._current_patch_count = 0
        self._comment_drafts: dict[str, str] = {}
        self._comment_editor_ticket = ""
        self._focus_mode = False
        self._inspector_requested = False
        self._nav_user_collapsed = bool(config.get("navigation_collapsed", False))
        self._nav_force_expanded = False
        self._nav_actual_collapsed = False
        self.nav_badges: dict[str, int] = {}
        self._last_reminder_check = ""
        self._notification_issue = ""
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle("Ty Work Hub")
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1480, 940)
        self.setMinimumSize(900, 600)
        self.toolbar = QFrame()
        self.toolbar.setObjectName("topbar")

        self.dashboard = Dashboard()
        self.dashboard.daily_tools.bind(self.workspace)
        self.tickets = TicketList(config)
        self.review_board = ReviewBoard()
        self.calendar = CalendarWidget(config)
        self.obsidian = ObsidianWidget(config.get("obsidian_vault_path", ""))
        self.standup = StandupWidget()
        self.source_control = SourceControlWidget(config)
        self.git = GitWidget(config)
        self.source_workspaces = QTabWidget()
        self.source_workspaces.addTab(self.git, "Git")
        self.source_workspaces.addTab(self.source_control, "SVN")
        self.source_workspaces.setCurrentIndex(0 if config.get("source_control_provider", "git") == "git" else 1)
        self.git.preference_changed.connect(self.persist_preference)
        self.git.settings_requested.connect(self.settings)
        self.ticket_detail = TicketDetailWidget(config)
        self.insights = InsightsWidget(config)
        self.page_widgets = {
            "Command Center": self.dashboard,
            "Personal Tasks": self.tickets,
            "Review Board": self.review_board,
            "Calendar": self.calendar,
            "Obsidian": self.obsidian,
            "Morning Standup": self.standup,
            "Source Control": self.source_workspaces,
            "Ticket Detail": self.ticket_detail,
            "Time Insights": self.insights,
        }

        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.navigation = self.build_navigation()
        shell_layout.addWidget(self.navigation)
        self.stack = QStackedWidget()
        for name in PAGES:
            self.stack.addWidget(self.page_widgets[name])
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.addWidget(self.stack)
        self.inspector = self.detail_panel()
        self.content_splitter.addWidget(self.inspector)
        self.content_splitter.setSizes([980, 350])
        self.inspector.hide()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.toolbar)
        content_layout.addWidget(self.content_splitter, 1)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)

        self.build_toolbar()
        self.apply_schedule()
        self.connect_modules()
        self.install_shortcuts()
        self._refresh_workspace_views()
        self._apply_responsive_layout()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.refresh)
        self.reset_poll_timer()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.tick)
        self.clock_timer.start(1000)
        self.setup_notifications()
        self.navigate(self.config.get("default_page", "Command Center"))
        self.refresh()



    def set_nav_badge(self, page: str, count: int) -> None:
        """Show how much work a destination is holding without opening it."""
        total = max(0, int(count))
        if self.nav_badges.get(page, 0) == total:
            return
        self.nav_badges[page] = total
        button = self.nav_buttons.get(page)
        if button:
            button.setText(self._nav_text(page, self._nav_actual_collapsed))

    def connect_modules(self) -> None:
        self.tickets.selected.connect(self.show_ticket)
        self.tickets.action_requested.connect(self.ticket_action)
        self.tickets.bulk_action_requested.connect(self.bulk_ticket_action)
        self.tickets.favorite_requested.connect(self.toggle_favorite)
        self.tickets.my_day_requested.connect(self.toggle_my_day)
        self.tickets.seen_requested.connect(self.set_issue_seen)
        self.tickets.filter_panel_toggled.connect(self.persist_filter_panel)
        self.tickets.view_changed.connect(self.persist_task_view)
        self.tickets.columns_changed.connect(self.persist_task_columns)
        self.tickets.refresh_requested.connect(self.refresh)
        self.tickets.save_view_requested.connect(self.save_task_view)
        self.dashboard.ticket_requested.connect(self.select_ticket)
        self.dashboard.ticket_preview_requested.connect(lambda issue: self.show_ticket(issue, navigate=False))
        self.dashboard.stop_work_requested.connect(self.stop_work)
        self.dashboard.start_work_requested.connect(self.start_tracking)
        self.insights.ticket_requested.connect(self.select_ticket_by_key)
        self.dashboard.focus_toggled.connect(self.set_focus_mode)
        self.dashboard.navigate_requested.connect(self.navigate)
        self.dashboard.new_event_requested.connect(self.calendar_new_event)
        self.dashboard.refresh_requested.connect(self.refresh)
        self.dashboard.capture_requested.connect(self.add_capture)
        self.dashboard.capture_toggled.connect(self.toggle_capture)
        self.dashboard.seen_requested.connect(self.set_issue_seen)
        self.review_board.ticket_requested.connect(self.select_ticket)
        self.review_board.add_requested.connect(self.add_watched_ticket)
        self.review_board.remove_requested.connect(self.remove_watched_ticket)
        self.review_board.seen_requested.connect(self.set_issue_seen)
        self.review_board.refresh_requested.connect(self.refresh)
        self.ticket_detail.analyze_requested.connect(self.generate_ticket_analysis)
        self.ticket_detail.cancel_requested.connect(self.cancel_ticket_analysis)
        self.ticket_detail.draft_comment_requested.connect(lambda issue: self.generate_ticket_analysis(issue, "draft_comment"))
        self.ticket_detail.apply_note_requested.connect(self.apply_ticket_analysis)
        self.ticket_detail.apply_lessons_requested.connect(self.apply_lessons)
        self.ticket_detail.open_note_requested.connect(self.open_detail_note)
        self.ticket_detail.open_backlog_requested.connect(self.open_detail_backlog)
        self.ticket_detail.environment_requested.connect(self.open_ticket_environment)
        self.obsidian.ai_requested.connect(self.obsidian_ai)
        self.obsidian.settings_requested.connect(self.settings)
        self.obsidian.cancel_requested.connect(self.cancel_ticket_analysis)
        self.obsidian.open_ticket_note_requested.connect(self.create_note)
        self.obsidian.note_ticket_detected.connect(self.bind_note_ticket)
        self.calendar.event_changed.connect(self.refresh_dashboard)
        self.calendar.ticket_requested.connect(self.select_ticket_by_key)
        self.standup.export_requested.connect(self.export_standup)
        self.standup.preparation_requested.connect(self.prepare_standup)
        self.source_control.settings_requested.connect(self.settings)
        self.source_control.status_message.connect(lambda message, timeout: self.statusBar().showMessage(message, timeout))
        self.source_control.patch_count_changed.connect(self.set_patch_badges)
        self.source_control.preference_changed.connect(self.persist_preference)
        self.ticket_detail.source_control_requested.connect(self.source_control_action)


    def install_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+R", self.refresh),
            ("Ctrl+N", self.calendar_new_event),
            ("Ctrl+Shift+N", self.create_note),
            ("Ctrl+S", self.start_work),
            ("Ctrl+Shift+R", self.open_ticket_detail),
            ("Ctrl+Shift+C", self.post_comment),
            ("Ctrl+Shift+M", self.toggle_current_my_day),
            ("Ctrl+Shift+A", lambda: self.generate_ticket_analysis(self.current, "brief") if self.current else None),
            ("Ctrl+Shift+L", lambda: self.generate_ticket_analysis(self.current, "lessons") if self.current else None),
            ("Ctrl+Alt+S", self.open_source_control),
            ("Ctrl+Alt+D", lambda: self.open_source_control("diff")),
            ("Ctrl+K", self.command_menu),
            ("Ctrl+F", self.focus_task_search),
            ("Ctrl+B", self.toggle_navigation),
            ("Ctrl+Shift+T", self.cycle_theme),
            ("Ctrl+Shift+S", self.toggle_tracking),
            ("Ctrl+Shift+B", self.toggle_inspector),
            ("Ctrl+,", self.settings),
            ("Escape", lambda: self.set_focus_mode(False)),
        ]
        shortcuts.extend((f"Ctrl+{index}", lambda _=False, page=page: self.navigate(page)) for index, page in enumerate(PAGES, 1))
        self.shortcut_actions = []
        for sequence, callback in shortcuts:
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            self.addAction(action)
            self.shortcut_actions.append(action)

    def detail_panel(self) -> QWidget:
        return build_inspector(self)

    def confirm_action(self, action: str, target: str = "this ticket") -> bool:
        answer = QMessageBox.question(
            self,
            "Please confirm",
            f"Would you like to {action} {target}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def navigate(self, name: str) -> None:
        if name not in self.page_widgets:
            name = "Command Center"
        self.stack.setCurrentWidget(self.page_widgets[name])
        self.nav_buttons[name].setChecked(True)
        # The selected rail entry carries the accent colour in its glyph too.
        retint_icons(self.navigation)
        self.breadcrumb.setText(f"Workspace  /  {PAGE_INFO[name][0]}")
        show_inspector = name == "Personal Tasks" and self.current is not None and self._inspector_requested and not self._focus_mode
        self.inspector.setVisible(show_inspector)
        self._apply_responsive_layout()

    def reset_poll_timer(self) -> None:
        self.poll_timer.start(int(self.config.get("polling_interval_minutes", 10)) * 60 * 1000)

    def refresh(self) -> None:
        if self._closing:
            return
        if self.sync_thread is not None:
            try:
                if self.sync_thread.isRunning():
                    self._refresh_pending = True
                    return
            except RuntimeError:
                # Qt may already have destroyed an older worker while Python still
                # holds its wrapper. Clear it before constructing the next worker.
                self.sync_thread = None
        self._refresh_pending = False
        self.last_synced.setText("Syncing Backlog...")
        self.nav_connection.setText("Backlog: syncing")
        worker = SyncThread(backlog_key(), self.config, self.workspace.watched_keys(), list(self.workspace.seen_records()))
        self.sync_thread = worker
        worker.synced.connect(self.synced)
        worker.failed.connect(self.sync_failed)
        worker.finished.connect(lambda current=worker: self._sync_finished(current))
        worker.start()

    def _sync_finished(self, worker: SyncThread) -> None:
        if self.sync_thread is worker:
            self.sync_thread = None
        worker.deleteLater()
        if self._refresh_pending:
            self._refresh_pending = False
            QTimer.singleShot(0, self.refresh)

    def synced(self, issues: list[dict], demo: bool) -> None:
        watched = {key.upper() for key in self.workspace.watched_keys()}
        for issue in issues:
            if issue_key(issue).upper() in watched:
                issue["_watched"] = True
                viewer_id = (issue.get("_viewer") or {}).get("id")
                issue["_review"] = bool((issue.get("assignee") or {}).get("id") != viewer_id)
        self.issues = issues
        self.tickets.set_issues(issues, render=False)
        self.calendar.update_issues(issues)
        profile = issues[0].get("_viewer") if issues else None
        self.dashboard.set_connection(demo, profile)
        self._refresh_workspace_views()
        suffix = " · Demo" if demo else ""
        self.last_synced.setText(f"Synced {datetime.now().strftime('%H:%M')}{suffix}")
        self.nav_connection.setText("Backlog: demo data" if demo else f"Backlog: connected\n{len(issues)} personal tasks")
        queued = len(self.workspace.pending_actions())
        if queued:
            self.nav_connection.setText(self.nav_connection.text() + f"\nQueued actions: {queued}")
            if not demo:
                QTimer.singleShot(250, self.retry_pending_actions)
        if self.current:
            fresh = next((issue for issue in issues if issue_key(issue) == issue_key(self.current)), None)
            if fresh:
                self.show_ticket(fresh, navigate=False)

    def sync_failed(self, message: str) -> None:
        self.last_synced.setText("Backlog offline")
        self.nav_connection.setText("Backlog: connection error")
        QMessageBox.warning(self, "Backlog sync failed", f"Could not load Personal Tasks. Existing data is still available.\n\n{message}")

    def apply_schedule(self) -> None:
        """Re-read the work schedule everywhere it decides what counts."""
        self.schedule = WorkSchedule.from_config(self.config)
        self.dashboard.set_schedule(self.schedule, self.config.get("general_project", "Company General"))

    def refresh_dashboard(self) -> None:
        self.dashboard.update_data(self.issues, self.calendar)
        self.insights.set_events(self.calendar.store.all())

    def tick(self) -> None:
        """One second of clock work: counted time, the chip, and the inspector."""
        active = self.calendar.active_work()
        counting = self.schedule.counts_now()
        counted = self.calendar.counted_seconds(self.schedule) if active else 0
        today_minutes = logged_minutes(self.calendar.store.all(), [date.today()], self.schedule)[0]
        resumes = ""
        if active and not counting:
            upcoming = self.schedule.next_window_start()
            resumes = upcoming.strftime("%a %H:%M") if upcoming else ""
        self.dashboard.update_timer(self.calendar, counted, counting, today_minutes)
        self.timer_chip.set_state(active, counted, counting, resumes)
        if active:
            subject = str(active.get("ticket_key") or "").strip() or str(active.get("project") or "").strip() or "general work"
            suffix = format_minutes(counted // 60) if counting else "paused · outside hours"
            self.work_state.setText(f"● {subject} · {suffix}")
        else:
            self.work_state.setText("No active timer")
        current_key = issue_key(self.current) if self.current else ""
        active_key = str(active.get("ticket_key") or "") if active else ""
        same_ticket = bool(active_key and active_key == current_key)
        self.start.setEnabled(bool(current_key) and not active)
        self.start.setText("Tracking work" if same_ticket else "Start work")
        self.stop.setEnabled(bool(active))
        self.stop.setText("Stop work" if same_ticket or not active_key else f"Stop {active_key}")
        if active and not same_ticket:
            self.work_help.setText(f"{active_key or 'General work'} is currently being tracked. Stop it before starting this ticket.")
        else:
            self.work_help.setText("Local work timer. Starting can also move the ticket to the project’s in-progress status.")
        minute = datetime.now().strftime("%Y-%m-%dT%H:%M")
        if minute != self._last_reminder_check:
            self._last_reminder_check = minute
            self.check_reminders()

    def show_ticket(self, issue: dict, navigate: bool = True) -> None:
        previous_key = self._comment_editor_ticket
        if previous_key and hasattr(self, "comment"):
            previous_text = self.comment.toPlainText()
            if previous_text.strip():
                self._comment_drafts[previous_key] = previous_text
            else:
                self._comment_drafts.pop(previous_key, None)
        self.current = issue
        self._inspector_requested = True
        self.dashboard.set_selected_ticket(issue)
        self.ticket_key_label.setText(issue_key(issue))
        self.title.setText(issue.get("summary", "") or "Untitled ticket")
        assignee = issue.get("assignee") or {}
        owner = assignee.get("name") or assignee.get("userId") or "Unassigned"
        review = " · REVIEW" if issue.get("_review") else ""
        self.meta.setText(
            f"{project_name(issue)} · {issue.get('priority', {}).get('name', 'No priority')} · "
            f"Due {issue.get('dueDate') or 'not set'} · Assigned to {owner}{review}"
        )
        extras = []
        for field in issue.get("customFields", []):
            value = field.get("value")
            if value not in (None, "", []):
                extras.append(f"{field.get('name', 'Custom field')}: {value}")
        description = issue.get("description") or "No description provided."
        self.description.setPlainText(description + ("\n\n" + "\n".join(extras) if extras else ""))
        key = issue_key(issue)
        self.comment.blockSignals(True)
        self.comment.setPlainText(self._comment_drafts.get(key, ""))
        self.comment.blockSignals(False)
        self._comment_editor_ticket = key
        self.local_workflow.blockSignals(True)
        self.local_workflow.setCurrentText(self.workspace.workflow_state(key))
        self.local_workflow.blockSignals(False)
        self.related_input.setText(", ".join(self.workspace.related_tickets(key)))
        statuses = issue.get("_availableStatuses") or [issue.get("status", {})]
        self.status.blockSignals(True)
        self.status.clear()
        for value in statuses:
            self.status.addItem(value.get("name", "Unknown"), value)
        current_id = issue.get("status", {}).get("id")
        index = next((i for i, value in enumerate(statuses) if value.get("id") == current_id), 0)
        self.status.setCurrentIndex(index)
        self.status.blockSignals(False)
        key = issue_key(issue)
        self.favorite.setText("★ Starred" if self.workspace.is_favorite(key) else "☆ Star")
        attention = self.workspace.issue_attention(issue)
        self.seen.setText("Mark unseen" if attention in {"back", "hidden"} else "Mark seen")
        self.my_day.setText("Remove Day" if key in self.workspace.my_day_keys() else "My Day")
        reminder = self.workspace.reminder_for(key)
        self.reminder_state.setText(f"Reminder · {self._format_reminder(reminder['at'])}" if reminder else "No local reminder")
        self._update_backlog_panel_state()
        if self.stack.currentWidget() is self.ticket_detail:
            self._load_ticket_detail(issue)
        self.source_control.set_ticket(issue)
        self.obsidian.set_ticket(issue)
        self.refresh_patch_badges(issue)
        self.tick()
        if navigate:
            self.navigate("Personal Tasks")
        else:
            self.inspector.setVisible(self.stack.currentWidget() is self.tickets and not self._focus_mode)
            self._apply_responsive_layout()

    def _remember_comment_draft(self) -> None:
        key = self._comment_editor_ticket
        if not key:
            return
        text = self.comment.toPlainText()
        if text.strip():
            self._comment_drafts[key] = text
        else:
            self._comment_drafts.pop(key, None)

    def _update_backlog_panel_state(self, preserve_message: bool = False) -> None:
        """Keep Backlog controls honest about local, queued, and remote state."""
        key = issue_key(self.current) if self.current else ""
        active = [
            context for context in self._backlog_action_context.values()
            if str(context.get("issue_id") or "").strip().upper() == key.strip().upper()
        ]
        busy_kinds = {str(context.get("action") or "") for context in active}
        pending = self.workspace.pending_actions_for(key) if key else []
        has_key = bool(backlog_key())

        if not preserve_message and not self._inspector_ai_running:
            if not has_key:
                self.backlog_sync_state.setText("Demo mode · status changes are local only and comments cannot be posted")
            elif "status" in busy_kinds:
                self.backlog_sync_state.setText("Updating the ticket status in Backlog…")
            elif "comment" in busy_kinds:
                self.backlog_sync_state.setText("Posting the reviewed comment to Backlog…")
            elif pending:
                kinds = sorted({str(item.get("action") or "action") for item in pending})
                self.backlog_sync_state.setText(f"Queued for retry · {', '.join(kinds)}")
            else:
                current_status = (self.current.get("status") or {}).get("name", "Unknown") if self.current else "Unknown"
                self.backlog_sync_state.setText(f"Connected · current status: {current_status}")

        self.apply_status.setEnabled(bool(key) and "status" not in busy_kinds)
        self.apply_status.setText("Updating…" if "status" in busy_kinds else "Update")
        self.add_comment.setEnabled(bool(key) and has_key and "comment" not in busy_kinds)
        self.add_comment.setText("Posting…" if "comment" in busy_kinds else "Post comment")
        if self._inspector_ai_running:
            return
        self.ai_comment.setText("AI Generate Ticket Comment")
        self.ai_comment.setEnabled(bool(key) and self._current_patch_count > 0)
        self.ai_comment.setToolTip(
            "Draft the fixed Japanese comment template from this ticket's verified SVN patch and llama.cpp"
            if self._current_patch_count > 0
            else "Create a saved SVN patch for this ticket before generating its implementation comment"
        )

    def hide_inspector(self) -> None:
        self._inspector_requested = False
        self.inspector.hide()
        self._apply_responsive_layout()

    def toggle_inspector(self) -> None:
        if self.inspector.isVisible():
            self.hide_inspector()
        elif self.current:
            self._inspector_requested = True
            self.navigate("Personal Tasks")
            self.inspector.show()
            self._apply_responsive_layout()
        else:
            self.statusBar().showMessage("Select a task before opening the inspector.", 3500)

    def select_ticket(self, ticket: dict) -> None:
        self.show_ticket(ticket)

    def select_ticket_by_key(self, key: str) -> None:
        issue = next((ticket for ticket in self.issues if issue_key(ticket) == key), None)
        if issue:
            self.show_ticket(issue)

    def require_ticket(self) -> bool:
        if not self.current:
            QMessageBox.information(self, "No ticket selected", "Select a Personal Task before using this action.")
            return False
        return True

    def ticket_action(self, action: str, issue: dict) -> None:
        self.current = issue
        if action == "start_work":
            self.start_work()
        elif action in {"create_note", "open_note"}:
            self.create_note()
        elif action == "open_browser":
            self.open_current_ticket()
        elif action == "show_detail":
            self.open_ticket_detail()
        elif action == "send_detail":
            self.open_ticket_detail()
        elif action == "environment":
            self.open_ticket_environment(issue)
        elif action.startswith("svn_"):
            self.source_control_action(action.removeprefix("svn_"), issue)
        elif action.startswith("remind_"):
            self.set_task_reminder(action.removeprefix("remind_"), issue)
        else:
            self.show_ticket(issue)

    def bulk_ticket_action(self, action: str, issues: list[dict]) -> None:
        if not issues:
            return
        if action == "start_first":
            self.current = issues[0]
            self.start_work()
            return
        if action == "mark_progress":
            if not self.confirm_action("mark the selected tickets in progress", f"({len(issues)} ticket(s))"):
                return
            updated = 0
            for issue in issues:
                progress = next((value for value in issue.get("_availableStatuses", []) if status_bucket({"status": value}) == "progress"), None)
                if not progress:
                    continue
                if backlog_key():
                    if self.run_action("status", issue_key(issue), progress.get("id")):
                        updated += 1
                else:
                    issue["status"] = dict(progress)
                    updated += 1
            self.tickets.set_issues(self.issues)
            self.refresh_dashboard()
            if self.current:
                self.show_ticket(self.current, navigate=False)
            message = f"Requested status updates for {updated} tasks. Waiting for Backlog confirmation." if backlog_key() else f"Marked {updated} demo tasks in progress."
            self.statusBar().showMessage(message, 5000)
            return
        if action == "add_my_day":
            added = self.workspace.add_to_my_day({issue_key(issue) for issue in issues})
            self._refresh_workspace_views()
            self.statusBar().showMessage(f"Added {added} new task{'s' if added != 1 else ''} to My Day.", 4500)
            return
        if action == "sync_notes":
            try:
                sync = self.obsidian_sync()
                paths = [sync.create_note(issue) for issue in issues]
                self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
                if paths:
                    self.obsidian.load_path(paths[-1])
                self.statusBar().showMessage(f"Synced {len(paths)} Obsidian notes.", 6000)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "Could not sync Obsidian notes", str(exc))

    def start_work(self) -> None:
        if not self.require_ticket():
            return
        active = self.calendar.active_work()
        current_key = issue_key(self.current)
        if active and active.get("ticket_key") != current_key:
            QMessageBox.information(self, "Work session already active", f"Stop {active.get('ticket_key')} before starting another ticket.")
            return
        if active:
            self.statusBar().showMessage(f"Already tracking {current_key}.", 3500)
            self.tick()
            return
        if not self._prepare_ticket_for_work(self.current):
            return
        event = self.calendar.start_work(self.current, project_name(self.current))
        self.statusBar().showMessage(f"Started: {event['title']}", 5000)
        self.refresh_dashboard()
        self.tick()

    def start_tracking(self, ticket_key: str = "", project: str = "") -> None:
        """Start the timer for a ticket, or for a project with no ticket at all.

        This is the plain clock: it never changes a Backlog status and never
        creates a note, because the Today card is about time, not about the
        ticket's workflow. `start_work` on the ticket inspector still does both.
        """
        active = self.calendar.active_work()
        if active:
            self.statusBar().showMessage("A work session is already running. Stop it first.", 3500)
            self.tick()
            return
        key = str(ticket_key or "").strip()
        ticket = next((issue for issue in self.issues if issue_key(issue) == key), None) if key else None
        if key and ticket is None and self.current and issue_key(self.current) == key:
            ticket = self.current
        event = self.calendar.start_work(ticket, project or self.config.get("general_project", "Company General"))
        self.statusBar().showMessage(f"Started: {event['title']}", 5000)
        self.refresh_dashboard()
        self.tick()

    def toggle_tracking(self) -> None:
        """One shortcut for the whole timer, matching the top-bar chip.

        Uses the plain clock rather than `start_work`, so a shortcut pressed in
        passing can never trigger a Backlog status confirmation.
        """
        if self.calendar.active_work():
            self.stop_work()
            return
        key = issue_key(self.current) if self.current else ""
        self.start_tracking(key, project_name(self.current) if self.current else self.config.get("general_project", "Company General"))

    def _prepare_ticket_for_work(self, ticket: dict) -> bool:
        """Move the ticket to in-progress and make sure its note exists."""
        available = ticket.get("_availableStatuses", [])
        progress = next((value for value in available if status_bucket({"status": value}) == "progress"), None)
        if progress and self.current is ticket and not self._apply_status(progress, create_note=False):
            return False
        try:
            self._create_note_for(ticket)
        except (ValueError, OSError):
            pass
        return True

    def stop_work(self) -> None:
        event = self.calendar.stop_work()
        if event:
            counted = logged_minutes([event], [date.today()], self.schedule)[0]
            self.statusBar().showMessage(f"Stopped: {event['title']} · {format_minutes(counted)} counted today", 5000)
        else:
            self.statusBar().showMessage("No active work session.", 4500)
        self.refresh_dashboard()
        self.tick()

    def _note_label(self, path: Path) -> str:
        """Vault-relative note path; the absolute one is noise in a metadata line."""
        vault = str(self.config.get("obsidian_vault_path", "")).strip()
        try:
            return path.relative_to(Path(vault).expanduser()).as_posix() if vault else str(path)
        except ValueError:
            return str(path)

    def _load_ticket_detail(self, issue: dict) -> None:
        note_text = ""
        note_path = ""
        try:
            path, note_text = self.obsidian_sync().read_note(issue)
            note_path = self._note_label(path) if path.exists() else "Not created"
        except (ValueError, OSError):
            note_path = "Vault not configured"
        self.ticket_detail.set_ticket(issue, note_text, note_path, issue.get("_comments", []))
        self.ticket_detail.comments_loading()
        api_key = backlog_key()
        if not api_key:
            return
        worker = BacklogCommentsThread(api_key, issue_key(issue), self.config)
        self.comment_workers.append(worker)
        worker.completed.connect(self._comments_loaded)
        worker.failed.connect(lambda message, key=issue_key(issue): self._comments_failed(key, message))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.comment_workers.remove(worker) if worker in self.comment_workers else None)
        worker.start()

    def _comments_loaded(self, key: str, comments: list[dict]) -> None:
        issue = next((item for item in self.issues if issue_key(item) == key), None)
        if issue is not None:
            issue["_comments"] = comments
        if self.current and issue_key(self.current) == key:
            self.current["_comments"] = comments
            self.ticket_detail.set_comments(comments)
            self.statusBar().showMessage(f"Loaded {len(comments)} Backlog comment(s) for {key}.", 4000)

    def _comments_failed(self, key: str, message: str) -> None:
        if self.current and issue_key(self.current) == key:
            self.ticket_detail.set_comments([])
            self.statusBar().showMessage(f"Could not load comments for {key}: {message}", 5000)

    def open_ticket_detail(self) -> None:
        if not self.require_ticket():
            return
        self._load_ticket_detail(self.current)
        self.navigate("Ticket Detail")

    def open_detail_note(self, issue: dict) -> None:
        self.current = issue
        self.create_note()

    def open_detail_backlog(self, issue: dict) -> None:
        self.current = issue
        self.open_current_ticket()

    def open_ticket_environment(self, issue: dict) -> None:
        self.current = issue
        project = project_name(issue)
        configured = self.config.get("environment_profiles", {})
        profiles = configured.get(project, {}) if isinstance(configured, dict) else {}
        dialog = EnvironmentDialog(project, profiles, self)
        if not dialog.exec():
            return
        updated = dialog.profiles()
        environment_profiles = dict(configured) if isinstance(configured, dict) else {}
        if updated:
            environment_profiles[project] = updated
        else:
            environment_profiles.pop(project, None)
        self.config["environment_profiles"] = environment_profiles
        self._save_preferences()
        self.statusBar().showMessage(f"Saved environment profiles for {project}.", 4500)

    def generate_inspector_comment(self) -> None:
        if self._inspector_ai_running:
            self.cancel_ticket_analysis()
            return
        if not self.require_ticket():
            return
        try:
            patch_count = PatchStore(self.config.get("svn_patch_root", "")).count_for_ticket(issue_key(self.current))
        except (SvnError, OSError):
            patch_count = 0
        if not patch_count:
            QMessageBox.information(
                self,
                "Create a ticket patch first",
                "AI ticket comments are grounded in a verified SVN patch. Create a patch for this ticket, then generate the comment again.",
            )
            return
        self.generate_ticket_analysis(self.current, "draft_comment", target="inspector")

    def generate_ticket_analysis(self, issue: dict, mode: str = "brief", target: str = "detail") -> None:
        self.current = issue
        if mode == "draft_comment":
            try:
                patch_count = PatchStore(self.config.get("svn_patch_root", "")).count_for_ticket(issue_key(issue))
            except (SvnError, OSError):
                patch_count = 0
            if not patch_count:
                QMessageBox.information(
                    self,
                    "Create a ticket patch first",
                    "AI ticket comments are grounded in a verified SVN patch. Create a patch for this ticket, then generate the comment again.",
                )
                return
        note_text = ""
        try:
            _, note_text = self.obsidian_sync().read_note(issue)
        except (ValueError, OSError):
            pass
        if self.analysis_workers:
            self.statusBar().showMessage("A local-AI generation is already running.", 4000)
            return
        task = {
            "note": "Preparing the full note template",
            "lessons": "Writing study material",
            "draft_comment": "Drafting a Backlog comment",
        }.get(mode, "Generating the English brief")
        vault_target = target in {"obsidian", "note_only"}
        if target == "inspector":
            self._inspector_ai_running = True
            self.ai_comment.setText("Stop generation")
            self.ai_comment.setEnabled(True)
            self.backlog_sync_state.setText("Collecting the verified SVN patch and ticket context…")
            self.inspector_tabs.setCurrentIndex(1)
        else:
            self.ticket_detail.set_busy(True, f"{task}…")
            self.obsidian.set_ai_busy(vault_target, f"{task}…" if vault_target else "Ready")
        worker = TicketAnalysisThread(self.config, issue, note_text, backlog_key(), mode, issue.get("_comments"))
        self.analysis_workers.append(worker)
        worker.completed.connect(lambda analysis, ticket=issue, current=note_text, destination=target: self._ticket_analysis_ready(ticket, current, analysis, destination))
        worker.failed.connect(lambda message, destination=target: self._ticket_analysis_failed(message, destination))
        worker.progress.connect(lambda message, destination=target: self._ticket_analysis_progress(message, destination))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.analysis_workers.remove(worker) if worker in self.analysis_workers else None)
        worker.start()

    def cancel_ticket_analysis(self) -> None:
        """Ask the running generation to stop at its next streamed chunk."""
        if not self.analysis_workers:
            return
        for worker in list(self.analysis_workers):
            worker.cancel()
        if self._inspector_ai_running:
            self.ai_comment.setText("Stopping…")
            self.ai_comment.setEnabled(False)
            self.backlog_sync_state.setText("Stopping the local-AI generation…")
        else:
            self.ticket_detail.set_progress("Stopping…")
            self.obsidian.set_ai_status("Stopping…")
        self.statusBar().showMessage("Stopping the local-AI generation…", 4000)

    def _ticket_analysis_progress(self, message: str, target: str) -> None:
        if target == "inspector":
            self.backlog_sync_state.setText(message)
            return
        self.ticket_detail.set_progress(message)
        if target in {"obsidian", "note_only"}:
            self.obsidian.set_ai_status(message)

    def _ticket_analysis_ready(self, issue: dict, current_note: str, analysis: dict, target: str = "detail") -> None:
        self.ticket_detail.set_source_control_context(int(analysis.get("_patch_count") or 0))
        if target == "note_only":
            self.ticket_detail.set_busy(False)
            self._apply_note_only(issue, analysis)
            return
        if target == "obsidian":
            self.obsidian.set_ai_busy(False, "Draft ready · review it in Ticket Detail before saving")
        if analysis.get("_mode") == "lessons":
            self.ticket_detail.set_lessons(analysis)
            if target == "obsidian":
                self.navigate("Ticket Detail")
            count = len(analysis.get("learning_references", []))
            self.statusBar().showMessage(
                f"{count} lesson{'s' if count != 1 else ''} ready for {issue_key(issue)}. Review before saving to the vault.", 7000,
            )
            return
        if analysis.get("_mode") == "brief":
            self.ticket_detail.set_brief(analysis)
            self.statusBar().showMessage(f"Local-AI brief ready for {issue_key(issue)}.", 7000)
            return
        if analysis.get("_mode") == "draft_comment":
            self.ticket_detail.set_draft_comment(analysis)
            key = issue_key(issue)
            draft = str(analysis.get("comment") or "")
            if draft:
                self._comment_drafts[key] = draft
            if self.current and issue_key(self.current) == key and self._comment_editor_ticket == key:
                self.comment.setPlainText(draft)
            self._inspector_ai_running = False
            self.ai_comment.setText("AI Generate Ticket Comment")
            self.ai_comment.setEnabled(True)
            self.backlog_sync_state.setText("AI draft ready · review the Japanese comment before posting")
            if target == "inspector":
                self.inspector_tabs.setCurrentIndex(1)
            self.statusBar().showMessage(f"Draft comment ready for {issue_key(issue)}. Review it before posting.", 7000)
            return
        try:
            preview = self.obsidian_sync().render_ai_note(issue, analysis, current_note)
        except (ValueError, OSError) as exc:
            self.ticket_detail.analysis_failed(str(exc))
            return
        self.ticket_detail.set_analysis(analysis, preview)
        if target == "obsidian":
            self.navigate("Ticket Detail")
        self.statusBar().showMessage(f"Local-AI brief ready for {issue_key(issue)}. Review it before applying.", 7000)

    def _ticket_analysis_failed(self, message: str, target: str = "detail") -> None:
        if target == "inspector":
            self._inspector_ai_running = False
            self.ai_comment.setText("AI Generate Ticket Comment")
            self.ai_comment.setEnabled(True)
            self.backlog_sync_state.setText(f"AI generation failed · {message}")
        else:
            self.ticket_detail.analysis_failed(message)
            self.obsidian.set_ai_busy(False, f"Generation failed · {message}"[:160])
        self.statusBar().showMessage("Local-AI ticket analysis failed.", 6000)

    def apply_ticket_analysis(self, issue: dict, analysis: dict) -> None:
        reply = QMessageBox.question(
            self,
            "Apply AI ticket template",
            "Update the ticket note with the reviewed template and create its learning-reference notes?\n\n"
            "If the ticket note already exists, its current contents will be backed up under Work/Tickets/.history first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            sync = self.obsidian_sync()
            path, references, backup = sync.apply_ai_update(issue, analysis)
            self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
            self.obsidian.load_path(path)
            content = path.read_text(encoding="utf-8", errors="replace")
            self.ticket_detail.set_ticket(issue, content, self._note_label(path), issue.get("_comments", []))
            self.ticket_detail.set_analysis(analysis, content)
            self.ticket_detail.note_applied(str(path), len(references), str(backup or ""))
            self.statusBar().showMessage(f"Updated {path.name} and {len(references)} learning reference{'s' if len(references) != 1 else ''}.", 8000)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not update Obsidian notes", str(exc))

    def apply_lessons(self, issue: dict, analysis: dict) -> None:
        """Write only the study material. No ticket-note rewrite, no Backlog post."""
        count = len(analysis.get("learning_references", []))
        folder = self.config.get("obsidian_programming_folder", "Work/Programming")
        reply = QMessageBox.question(
            self,
            "Save study material",
            f"Write {count} lesson note{'s' if count != 1 else ''} into {folder}?\n\n"
            "The ticket note is not rewritten and nothing is posted to Backlog. An existing lesson keeps "
            "everything you wrote outside its managed section, and the ticket note only gains a missing link.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            paths, linked = self.obsidian_sync().apply_learning_references(issue, analysis)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not save study material", str(exc))
            return
        self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
        if paths:
            self.obsidian.load_path(paths[0])
        self.ticket_detail.lessons_applied(len(paths), linked)
        self.obsidian.set_ai_status(f"Saved {len(paths)} lesson note{'s' if len(paths) != 1 else ''} to {folder}")
        self.statusBar().showMessage(
            f"Saved {len(paths)} lesson note{'s' if len(paths) != 1 else ''} for {issue_key(issue)}.", 8000,
        )

    def obsidian_ai(self, mode: str) -> None:
        """Run a vault-facing generation against the currently selected ticket."""
        if not self.require_ticket():
            return
        self._load_ticket_detail(self.current)
        if mode == "note_only":
            # Same generation as the full note, but the result never leaves the vault.
            self.generate_ticket_analysis(self.current, "note", target="note_only")
            return
        self.generate_ticket_analysis(self.current, mode, target="obsidian")

    def _apply_note_only(self, issue: dict, analysis: dict) -> None:
        """Write just this ticket's note, after showing exactly what will be written."""
        try:
            sync = self.obsidian_sync()
            path = sync.note_path(issue)
            existed = path.exists()
            preview = sync.render_ai_note(issue, analysis, path.read_text(encoding="utf-8", errors="replace") if existed else "")
        except (ValueError, OSError) as exc:
            self.obsidian.set_ai_busy(False, "Ready")
            QMessageBox.warning(self, "Could not prepare the note", str(exc))
            return
        dialog = NotePreviewDialog(
            self, ticket_key=issue_key(issue), note_path=self._note_label(path), content=preview, exists=existed,
        )
        if not dialog.exec():
            self.obsidian.set_ai_busy(False, "Draft discarded · the note was not changed")
            self.statusBar().showMessage("The note was not changed.", 4000)
            return
        try:
            written, backup = sync.apply_note_only(issue, analysis)
        except (ValueError, OSError) as exc:
            self.obsidian.set_ai_busy(False, "Ready")
            QMessageBox.warning(self, "Could not update the note", str(exc))
            return
        self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
        self.obsidian.load_path(written)
        self.obsidian.set_ai_busy(False, f"Updated {written.name}")
        note = f"Updated {written.name}."
        self.statusBar().showMessage(f"{note} Previous version backed up." if backup else note, 8000)

    def bind_note_ticket(self, key: str) -> None:
        """Opening a ticket note in the vault selects that ticket, without navigating away."""
        issue = next((item for item in self.issues if issue_key(item).strip().upper() == key.strip().upper()), None)
        if not issue:
            return
        if self.current and issue_key(self.current) == issue_key(issue):
            # Already the selected ticket; the vault bar just needs to be re-bound.
            self.obsidian.set_ticket(issue)
            return
        self.show_ticket(issue, navigate=False)

    def obsidian_sync(self) -> ObsidianSync:
        return ObsidianSync(
            self.config.get("obsidian_vault_path", ""),
            self.config.get("obsidian_tickets_folder", "Work/Tickets"),
            self.config.get("obsidian_daily_folder", "Work/Daily"),
            self.config.get("backlog_web_url", "https://mirax.backlog.com"),
            self.config.get("obsidian_programming_folder", "Work/Programming"),
        )

    def _create_note_for(self, issue: dict):
        return self.obsidian_sync().create_note(issue)

    def create_note(self):
        if not self.require_ticket():
            return None
        try:
            path = self._create_note_for(self.current)
            self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
            self.obsidian.load_path(path)
            self.navigate("Obsidian")
            self.statusBar().showMessage(f"Opened Obsidian note: {path.name}", 6000)
            return path
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not sync Obsidian note", str(exc))
            return None

    def change_status(self) -> None:
        status = self.status.currentData()
        if status:
            self._apply_status(status)

    def save_local_context(self) -> None:
        if not self.require_ticket():
            return
        key = issue_key(self.current)
        self.workspace.set_workflow_state(key, self.local_workflow.currentText())
        self.workspace.set_related_tickets(key, self.related_input.text().split(","))
        self.statusBar().showMessage(f"Saved local workflow context for {key}.", 4000)
        self._refresh_workspace_views()

    def _apply_status(self, status: dict, create_note: bool = True) -> bool:
        if not self.require_ticket():
            return False
        old_name = (self.current.get("status") or {}).get("name", "Unknown")
        new_name = status.get("name", "Unknown")
        if old_name == new_name:
            return True
        if not self.confirm_action(f"change the status from “{old_name}” to “{new_name}” for", issue_key(self.current)):
            self.show_ticket(self.current, navigate=False)
            return False
        if status_bucket({"status": status}) == "progress" and create_note:
            try:
                self._create_note_for(self.current)
            except (ValueError, OSError):
                pass
        if backlog_key():
            self.run_action("status", issue_key(self.current), status.get("id"))
        else:
            self.current["status"] = dict(status)
            self.show_ticket(self.current, navigate=False)
            self.tickets.set_issues(self.issues)
            self.refresh_dashboard()
            self.statusBar().showMessage(f"Status set to {status.get('name')} in demo mode.", 4000)
        return True

    def post_comment(self) -> None:
        if not self.require_ticket() or not self.comment.toPlainText().strip():
            return
        current_key = issue_key(self.current)
        if self._comment_editor_ticket != current_key:
            QMessageBox.information(
                self,
                "Comment belongs to another ticket",
                f"This draft belongs to {self._comment_editor_ticket or 'the previous ticket'}, not {current_key}. Reselect the intended ticket before posting.",
            )
            return
        content = self.comment.toPlainText().strip()
        if not backlog_key():
            self.statusBar().showMessage("Comments are disabled in demo mode. Add a Backlog API key in Settings.", 5000)
            self._update_backlog_panel_state()
            return
        if not self.confirm_action("post this comment to", issue_key(self.current)):
            return
        self.run_action("comment", issue_key(self.current), content)

    def run_action(self, action: str, issue_id, value, queued_item: dict | None = None) -> BacklogActionThread | None:
        if not backlog_key():
            self.statusBar().showMessage("Backlog API key is not configured; the action was not sent.", 6000)
            return None
        worker = BacklogActionThread(backlog_key(), action, issue_id, value, self.config)
        self._backlog_action_context[worker] = {
            "action": action, "issue_id": str(issue_id), "value": value, "queued_item": queued_item,
        }
        self.action_workers.append(worker)
        worker.completed.connect(lambda kind, result, current=worker: self._backlog_action_completed(current, kind, result))
        worker.failed.connect(lambda error, retryable, current=worker: self._action_failed(current, error, retryable))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.action_workers.remove(worker) if worker in self.action_workers else None)
        self._update_backlog_panel_state()
        worker.start()
        return worker

    def _backlog_action_completed(self, worker: BacklogActionThread, kind: str, result: object) -> None:
        context = self._backlog_action_context.pop(worker, {})
        key = str(context.get("issue_id") or "")
        queued_item = context.get("queued_item")
        if queued_item:
            self.workspace.remove_pending_action(queued_item)
            self._retrying_pending_action = False
        issue = next((item for item in self.issues if issue_key(item) == key), None)
        if kind == "status" and issue is not None:
            expected = next((
                value for value in issue.get("_availableStatuses", [])
                if value.get("id") == context.get("value")
            ), None)
            returned = result.get("status") if isinstance(result, dict) else None
            issue["status"] = dict(returned or expected or issue.get("status") or {})
            if isinstance(result, dict) and result.get("updated"):
                issue["updated"] = result["updated"]
        elif kind == "comment":
            self._comment_drafts.pop(key, None)
            if self.current and issue_key(self.current) == key and self._comment_editor_ticket == key and self.comment.toPlainText().strip() == str(context.get("value") or "").strip():
                self.comment.clear()
            if issue is not None and isinstance(result, dict):
                issue.setdefault("_comments", []).insert(0, result)
                issue["commentCount"] = int(issue.get("commentCount") or 0) + 1
        if self.current and issue_key(self.current) == key and issue is not None:
            self.show_ticket(issue, navigate=False)
        self.tickets.set_issues(self.issues)
        self.refresh_dashboard()
        self.statusBar().showMessage(f"Backlog {kind} synced. Refreshing ticket context…", 4000)
        self._update_backlog_panel_state()
        self.refresh()
        if queued_item:
            QTimer.singleShot(600, self.retry_pending_actions)

    def _action_failed(self, worker: BacklogActionThread, error: str, retryable: bool) -> None:
        context = self._backlog_action_context.pop(worker, {})
        action = str(context.get("action") or "action")
        issue_id = str(context.get("issue_id") or "")
        value = context.get("value")
        queued_item = context.get("queued_item")
        if queued_item:
            self._retrying_pending_action = False
            if not retryable:
                self.workspace.remove_pending_action(queued_item)
        elif retryable:
            self.workspace.queue_action(action, issue_id, value)
        if not retryable and action == "status" and self.current and issue_key(self.current) == issue_id:
            # The combo may still contain the attempted value. Re-select the
            # server-confirmed status so the inspector cannot imply success.
            self.show_ticket(self.current, navigate=False)
        if not retryable and action == "comment" and queued_item:
            restored = str(value or "")
            if restored.strip():
                self._comment_drafts[issue_id] = restored
                if self.current and issue_key(self.current) == issue_id and self._comment_editor_ticket == issue_id and not self.comment.toPlainText().strip():
                    self.comment.setPlainText(restored)
        if retryable:
            if action == "comment" and self.current and issue_key(self.current) == issue_id and self._comment_editor_ticket == issue_id and self.comment.toPlainText().strip() == str(value or "").strip():
                self._comment_drafts.pop(issue_id, None)
                self.comment.clear()
            self.backlog_sync_state.setText("Backlog unavailable · action queued for automatic retry")
            if not queued_item:
                QMessageBox.warning(self, "Backlog temporarily unavailable", f"{error}\n\nThe action was saved and will retry after a successful Backlog refresh.")
            else:
                self.statusBar().showMessage(f"Queued Backlog {action} still cannot be sent; it remains queued.", 6000)
        else:
            self.backlog_sync_state.setText("Backlog rejected the action · review the error and try again")
            QMessageBox.warning(self, "Backlog update rejected", f"{error}\n\nNothing was changed in the ticket, and this permanent error was not queued.")
        self._update_backlog_panel_state(preserve_message=True)

    def retry_pending_actions(self) -> None:
        if self._closing or not backlog_key() or self._retrying_pending_action or self.action_workers:
            return
        pending = self.workspace.pending_actions()
        if not pending:
            return
        item = pending[0]
        self._retrying_pending_action = True
        if self.run_action(item["action"], item["issue_id"], item.get("value"), queued_item=item) is None:
            self._retrying_pending_action = False
            return
        self.statusBar().showMessage(f"Retrying queued {item['action']} action…", 4000)

    def open_backlog_dashboard(self) -> None:
        webbrowser.open(self.config["backlog_web_url"] + "/dashboard?from_globalbar")

    def open_source_control(self, action: str = "", *, provider: str | None = None) -> None:
        self.navigate("Source Control")
        # Patch shelf actions are explicitly SVN workflows. Generic commands use
        # the chosen provider, so Git works without a selected Backlog ticket.
        if (provider or self.config.get("source_control_provider", "git")) == "git" and str(action) in {"", "status", "diff", "compare", "update"}:
            self.source_workspaces.setCurrentWidget(self.git)
            if str(action) in {"diff", "compare"}:
                self.git.show_diff()
            elif str(action) == "update":
                self.git.remote("pull")
            elif self.config.get("git_repository"):
                self.git.refresh()
            return
        self.source_workspaces.setCurrentWidget(self.source_control)
        if self.current:
            self.source_control.set_ticket(self.current)
        if str(action) in {"create_patch", "shelve"}:
            # Name the patch after the ticket instead of a generic placeholder.
            self.source_control.prepare_patch_context()
        self.navigate("Source Control")
        if str(action) == "patches":
            self.source_control.tabs.setCurrentWidget(self.source_control.shelf_tab)
            QTimer.singleShot(0, self.source_control.refresh_patches)
            return
        if str(action) in {"edit_patch", "renumber_patches"}:
            # Act on the shelf already loaded; a refresh here would race the edit.
            self.source_control.tabs.setCurrentWidget(self.source_control.shelf_tab)
        callbacks = {
            "status": self.source_control.show_status,
            "diff": self.source_control.show_diff,
            "compare": self.source_control.compare_with_base,
            "update": self.source_control.update_working_copy,
            "create_patch": self.source_control.create_patch,
            "shelve": self.source_control.shelve_changes,
            "apply_patch": self.source_control.apply_selected_patch,
            "edit_patch": self.source_control.edit_selected_patch,
            "renumber_patches": self.source_control.renumber_ticket_patches,
        }
        callback = callbacks.get(str(action))
        if callback:
            QTimer.singleShot(0, callback)

    def source_control_action(self, *args) -> None:
        """Open Source Control for a ticket. Accepts (action, issue) or (issue, action)."""
        first, second = args if len(args) == 2 else (args[0], "")
        issue, action = (second, first) if isinstance(first, str) else (first, second)
        if isinstance(issue, dict) and issue:
            self.current = issue
            self.source_control.set_ticket(issue)
        self.open_source_control(str(action or ""), provider="svn")

    def persist_preference(self, key: str, value) -> None:
        self.config[str(key)] = value
        self._save_preferences()

    def set_patch_badges(self, key: str, count: int) -> None:
        """Update the ticket-detail and inspector patch indicators together."""
        total = max(0, int(count))
        self.ticket_detail.set_patch_count(str(key), total)
        current = issue_key(self.current) if self.current else ""
        if key and current and str(key).strip().upper() != current.strip().upper():
            return
        self._current_patch_count = total
        self.patches_button.setText(f"Patches ({total})" if total else "Patches")
        self.patches_button.setEnabled(total > 0)
        self._update_backlog_panel_state()

    def refresh_patch_badges(self, issue: dict | None) -> None:
        """Count shelved patches for a ticket without touching the GUI thread for long.

        `count_for_ticket` globs one folder level, so this stays cheap even with a
        large shelf. Any storage problem simply shows zero patches.
        """
        key = issue_key(issue or {})
        total = 0
        if key:
            try:
                total = PatchStore(self.config.get("svn_patch_root", "")).count_for_ticket(key)
            except (SvnError, OSError):
                total = 0
        self.set_patch_badges(key, total)

    def open_current_ticket(self) -> None:
        if self.require_ticket():
            webbrowser.open(issue_url(self.current, self.config["backlog_web_url"]))

    def prepare_standup(self) -> None:
        day = date.fromisoformat(self.standup.day)
        yesterday = []
        for event in self.calendar.all_events():
            if event.get("kind") != "work":
                continue
            try:
                if self.calendar._parse(event["start"]).date() == day - timedelta(days=1):
                    yesterday.append(f"• {event.get('ticket_key', '')} {event['title']}".strip())
            except (KeyError, TypeError, ValueError):
                continue
        keys = self.workspace.my_day_keys() if day == date.today() else set()
        today = [f"• {issue_key(issue)} — {issue.get('summary', '')}" for issue in self.issues
                 if issue_key(issue) in keys or issue.get("dueDate") == day.isoformat()]
        self.standup.append_plan(yesterday, today)
        self.statusBar().showMessage("Added logged work and planned tasks to your standup." if yesterday or today else "No work sessions or planned tasks found for this date.", 4500)

    def export_standup(self, entry: dict) -> None:
        try:
            path = self.obsidian_sync().create_standup_note(entry)
            self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
            self.obsidian.load_path(path)
            self.statusBar().showMessage(f"Standup exported to {path.name}", 5000)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not export standup", str(exc))

    def calendar_new_event(self) -> None:
        self.navigate("Calendar")
        self.calendar.new_event()

    def set_issue_seen(self, issue: dict, mode: str = "back") -> None:
        key = issue_key(issue)
        if mode == "unseen":
            self.workspace.clear_issue_seen(key)
            message = f"{key} returned to the active queue."
        else:
            self.workspace.mark_issue_seen(issue, mode)
            message = f"{key} marked seen" + (" and hidden from the queue." if mode == "hidden" else " and moved behind active work.")
        self._refresh_workspace_views()
        if self.current and issue_key(self.current) == key:
            self.show_ticket(self.current, navigate=False)
        self.statusBar().showMessage(message, 4500)

    def toggle_current_seen(self) -> None:
        if not self.require_ticket():
            return
        state = self.workspace.issue_attention(self.current)
        self.set_issue_seen(self.current, "unseen" if state in {"back", "hidden"} else "back")

    def add_watched_ticket(self, query: str) -> None:
        if not backlog_key():
            QMessageBox.information(self, "Backlog key required", "Add your Backlog API key in Settings before looking up review tickets.")
            return
        self.statusBar().showMessage(f"Searching Backlog for {query}…", 8000)
        worker = BacklogLookupThread(backlog_key(), query, self.config)
        self.lookup_workers.append(worker)
        worker.found.connect(lambda issues, value=query: self._watched_lookup_found(value, issues))
        worker.failed.connect(lambda error: QMessageBox.warning(self, "Ticket lookup failed", error))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.lookup_workers.remove(worker) if worker in self.lookup_workers else None)
        worker.start()

    def _watched_lookup_found(self, query: str, issues: list[dict]) -> None:
        if not issues:
            QMessageBox.information(self, "No matching ticket", f"Backlog did not return a ticket matching “{query}”.")
            return
        chosen = issues[0]
        if len(issues) > 1:
            labels = [f"{issue_key(issue)} — {issue.get('summary', '')}" for issue in issues]
            selected, accepted = QInputDialog.getItem(self, "Choose a review ticket", "Matches", labels, 0, False)
            if not accepted:
                return
            chosen = issues[labels.index(selected)]
        key = issue_key(chosen)
        if self.workspace.add_watched_ticket(key):
            self.statusBar().showMessage(f"Tracking {key} on the Review Board.", 5000)
        else:
            self.statusBar().showMessage(f"{key} is already on the Review Board.", 4000)
        self.navigate("Review Board")
        self.refresh()

    def remove_watched_ticket(self, key: str) -> None:
        if self.workspace.remove_watched_ticket(key):
            remaining = []
            for issue in self.issues:
                if issue_key(issue).upper() != key.upper():
                    remaining.append(issue)
                    continue
                viewer_id = (issue.get("_viewer") or {}).get("id")
                assignee_id = (issue.get("assignee") or {}).get("id")
                if viewer_id and assignee_id == viewer_id:
                    issue.pop("_watched", None)
                    issue.pop("_review", None)
                    remaining.append(issue)
            self.issues = remaining
            self.tickets.set_issues(self.issues)
            self._refresh_workspace_views()
            self.statusBar().showMessage(f"Stopped tracking {key}.", 4000)

    def toggle_favorite(self, issue: dict) -> None:
        key = issue_key(issue)
        enabled = self.workspace.toggle_favorite(key)
        self._refresh_workspace_views()
        if self.current and issue_key(self.current) == key:
            self.favorite.setText("★ Starred" if enabled else "☆ Star")
        self.statusBar().showMessage(f"{'Starred' if enabled else 'Unstarred'} {key}.", 2500)

    def toggle_current_favorite(self) -> None:
        if self.require_ticket():
            self.toggle_favorite(self.current)

    def toggle_my_day(self, issue: dict) -> None:
        key = issue_key(issue)
        enabled = self.workspace.toggle_my_day(key)
        self._refresh_workspace_views()
        if self.current and issue_key(self.current) == key:
            self.my_day.setText("Remove Day" if enabled else "My Day")
        self.statusBar().showMessage(f"{'Added' if enabled else 'Removed'} {key} {'to' if enabled else 'from'} My Day.", 3000)

    def toggle_current_my_day(self) -> None:
        if self.require_ticket():
            self.toggle_my_day(self.current)

    @staticmethod
    def _format_reminder(value: str) -> str:
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%a %b %d · %H:%M")
        except (TypeError, ValueError):
            return str(value)

    def open_reminder_menu(self) -> None:
        if not self.require_ticket():
            return
        menu = QMenu(self)
        menu.addAction("In 1 hour", lambda: self.set_task_reminder("hour", self.current))
        menu.addAction("Tomorrow at 09:00", lambda: self.set_task_reminder("tomorrow", self.current))
        menu.addAction("Next Monday at 09:00", lambda: self.set_task_reminder("next_week", self.current))
        menu.addAction("Custom...", lambda: self.set_task_reminder("custom", self.current))
        if self.workspace.reminder_for(issue_key(self.current)):
            menu.addSeparator()
            menu.addAction("Clear reminder", lambda: self.set_task_reminder("clear", self.current))
        menu.exec(self.reminder.mapToGlobal(self.reminder.rect().bottomLeft()))

    def set_task_reminder(self, kind: str, issue: dict) -> None:
        key = issue_key(issue)
        if kind == "clear":
            self.workspace.clear_reminder(key)
            self.statusBar().showMessage(f"Cleared the reminder for {key}.", 3000)
        else:
            now = datetime.now().astimezone()
            if kind == "hour":
                target = now + timedelta(hours=1)
            elif kind == "tomorrow":
                target = datetime.combine(date.today() + timedelta(days=1), time(hour=9)).astimezone()
            elif kind == "next_week":
                days = 7 - date.today().weekday()
                target = datetime.combine(date.today() + timedelta(days=days), time(hour=9)).astimezone()
            else:
                current = self.workspace.reminder_for(key)
                initial = datetime.fromisoformat(current["at"]) if current else None
                dialog = ReminderDialog(initial, self)
                if not dialog.exec():
                    return
                target = dialog.value()
            self.workspace.set_reminder(key, target)
            self.statusBar().showMessage(f"Reminder set for {key}: {target.strftime('%a %b %d at %H:%M')}.", 5000)
        self._refresh_workspace_views()
        if self.current and issue_key(self.current) == key:
            self.show_ticket(self.current, navigate=False)

    def setup_notifications(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(APP_ICON)) if APP_ICON.exists() else self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Ty Work Hub reminders")
        self.tray.messageClicked.connect(self._open_last_notification)
        self.tray.show()

    def check_reminders(self) -> None:
        due = self.workspace.due_reminders()
        if not due:
            return
        labels = []
        for reminder in due:
            key = reminder["ticket_key"]
            issue = next((value for value in self.issues if issue_key(value) == key), None)
            labels.append(f"{key}: {issue.get('summary', '')}" if issue else key)
            self.workspace.mark_reminder_notified(key)
        self._notification_issue = due[0]["ticket_key"]
        body = "\n".join(labels[:3])
        if len(labels) > 3:
            body += f"\n+ {len(labels) - 3} more"
        self.notify_user("Task reminder", body, self._notification_issue)

    def notify_user(self, title: str, message: str, ticket_key: str = "") -> None:
        if ticket_key:
            self._notification_issue = ticket_key
        self.statusBar().showMessage(f"{title}: {message.replace(chr(10), ' · ')}", 10000)
        QApplication.alert(self, 5000)
        if self.tray:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _open_last_notification(self) -> None:
        if self._notification_issue:
            self.select_ticket_by_key(self._notification_issue)

    def add_capture(self, text: str) -> None:
        if self.workspace.add_capture(text):
            self.dashboard.set_captures(self.workspace.captures())
            self.statusBar().showMessage("Added to capture inbox.", 2500)

    def toggle_capture(self, capture_id: str) -> None:
        self.workspace.toggle_capture(capture_id)
        self.dashboard.set_captures(self.workspace.captures())

    def _refresh_workspace_views(self) -> None:
        favorites = self.workspace.favorites()
        my_day = self.workspace.my_day_keys()
        reminders = self.workspace.reminders()
        attention = {issue_key(issue): self.workspace.issue_attention(issue) for issue in self.issues}
        self.tickets.set_workspace_state(favorites, my_day, reminders, attention)
        self.dashboard.set_favorites(favorites)
        self.dashboard.set_my_day(my_day)
        self.dashboard.set_attention(attention)
        self.dashboard.set_captures(self.workspace.captures())
        self.review_board.set_data(self.issues, self.workspace.watched_keys(), attention)
        self.calendar.set_personal_planning(my_day, reminders)
        self.set_nav_badge("Personal Tasks", sum(1 for state in attention.values() if state in {"new", "changed"}))
        self.set_nav_badge("Review Board", sum(1 for issue in self.issues if issue.get("_review")))
        self.refresh_dashboard()

    def persist_filter_panel(self, visible: bool) -> None:
        self.config["task_filter_panel"] = visible
        self._save_preferences()

    def persist_task_view(self, value: str) -> None:
        self.config["task_view"] = value
        self._save_preferences()

    def save_task_view(self, definition: dict) -> None:
        name, accepted = QInputDialog.getText(self, "Save task view", "View name:")
        name = name.strip()
        if not accepted or not name:
            return
        saved = [item for item in self.config.get("saved_task_views", []) if isinstance(item, dict) and item.get("name") != name]
        saved.append({"name": name, **definition})
        self.config["saved_task_views"] = saved[-20:]
        self._save_preferences()
        self.tickets.add_saved_view(name, definition)
        self.statusBar().showMessage(f"Saved task view: {name}", 4000)

    def persist_task_columns(self, values: list[str]) -> None:
        self.config["task_columns"] = values
        self._save_preferences()

    def _save_preferences(self) -> None:
        if self._persist_preferences:
            save_config(self.config)

    def set_focus_mode(self, enabled: bool) -> None:
        self._focus_mode = enabled
        self.toolbar.setVisible(not enabled)
        self.dashboard.focus.blockSignals(True)
        self.dashboard.focus.setChecked(enabled)
        self.dashboard.focus.blockSignals(False)
        if enabled:
            self.navigation.hide()
            self.inspector.hide()
            self.statusBar().showMessage("Focus mode enabled. Press Escape to exit.", 5000)
        else:
            self._apply_responsive_layout()

    def toggle_navigation(self) -> None:
        if self._nav_actual_collapsed:
            self._nav_user_collapsed = False
            self._nav_force_expanded = True
        else:
            self._nav_user_collapsed = True
            self._nav_force_expanded = False
        self.config["navigation_collapsed"] = self._nav_user_collapsed
        self._save_preferences()
        self._apply_responsive_layout()


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def focus_task_search(self) -> None:
        self.navigate("Personal Tasks")
        self.tickets.focus_search()

    def open_task_view(self, value: str) -> None:
        self.navigate("Personal Tasks")
        self.tickets.activate_view(value)

    def command_menu(self) -> None:
        commands = [
            {"title": "Search personal tasks", "subtitle": "Ctrl+F", "keywords": "find ticket issue", "callback": self.focus_task_search},
            {"title": "Quick capture", "subtitle": "Command Center inbox", "keywords": "add thought task", "callback": self.focus_dashboard_capture},
            {"title": "New calendar event", "subtitle": "Ctrl+N", "keywords": "schedule meeting", "callback": self.calendar_new_event},
            {"title": "Start selected task", "subtitle": "Ctrl+S", "keywords": "timer work", "callback": self.start_work},
            {"title": "Stop active work", "subtitle": "Timer", "keywords": "timer work", "callback": self.stop_work},
            {"title": "Open selected task note", "subtitle": "Ctrl+Shift+N", "keywords": "obsidian markdown preview note", "callback": self.create_note},
            {"title": "Open full ticket detail", "subtitle": "Ticket Detail", "keywords": "analysis summary local ai llm llama", "callback": self.open_ticket_detail},
            {"title": "AI: Generate study material for selected ticket", "subtitle": "Ctrl+Shift+L", "keywords": "lesson learning obsidian programming reference teach", "callback": lambda: self.generate_ticket_analysis(self.current, "lessons") if self.require_ticket() else None},
            {"title": "AI: Draft the Obsidian note update", "subtitle": "Ticket Detail", "keywords": "template note obsidian vault write", "callback": lambda: self.generate_ticket_analysis(self.current, "note") if self.require_ticket() else None},
            {"title": "AI: Update only this ticket's Obsidian note", "subtitle": "Notes & knowledge", "keywords": "note obsidian vault write only nothing else", "callback": lambda: self.obsidian_ai("note_only")},
            {"title": "Open selected project environment", "subtitle": "検証 / ライブ / ローカル", "keywords": "test site environment credentials username password", "callback": lambda: self.open_ticket_environment(self.current) if self.require_ticket() else None},
            {"title": "Open Source Control", "subtitle": "Ctrl+Alt+S", "keywords": "git svn tortoise repository working copy patch shelf", "callback": self.open_source_control},
            {"title": "Source control: Show diff", "subtitle": "Ctrl+Alt+D", "keywords": "git svn compare base local changes", "callback": lambda: self.open_source_control("diff")},
            {"title": "Source control: Show status", "subtitle": "Configured provider", "keywords": "git svn modified files source control", "callback": lambda: self.open_source_control("status")},
            {"title": "SVN: Create ticket patch", "subtitle": "Patch shelf", "keywords": "save changes patch", "callback": lambda: self.open_source_control("create_patch")},
            {"title": "SVN: Shelve ticket changes", "subtitle": "Patch then revert", "keywords": "switch ticket", "callback": lambda: self.open_source_control("shelve")},
            {"title": "SVN: Apply / unshelve patch", "subtitle": "Patch shelf", "keywords": "resume ticket", "callback": lambda: self.open_source_control("apply_patch")},
            {"title": "SVN: Compare selected changes with base", "subtitle": "Before / after", "keywords": "side by side base revision diff", "callback": lambda: self.open_source_control("compare")},
            {"title": "SVN: Open this ticket's patches", "subtitle": "Patch shelf", "keywords": "patches artifacts shelf list", "callback": lambda: self.open_source_control("patches")},
            {"title": "SVN: Rename or regroup the selected patch", "subtitle": "Patch shelf", "keywords": "label group organise organize edit patch details", "callback": lambda: self.open_source_control("edit_patch")},
            {"title": "SVN: Renumber this ticket's patches", "subtitle": "Patch shelf", "keywords": "sequence order series number patches", "callback": lambda: self.open_source_control("renumber_patches")},
            {"title": "SVN: Update working copy to a revision", "subtitle": "Confirms before updating", "keywords": "update revision head sync", "callback": lambda: self.open_source_control("update")},
            {"title": "Add/remove selected task from My Day", "subtitle": "Daily focus", "keywords": "today plan priority", "callback": self.toggle_current_my_day},
            {"title": "Mark selected ticket seen/unseen", "subtitle": "Priority queue", "keywords": "acknowledge defer hide", "callback": self.toggle_current_seen},
            {"title": "Set reminder for selected task", "subtitle": "Local reminder", "keywords": "snooze notify follow up", "callback": self.open_reminder_menu},
            {"title": "Start or stop the work timer", "subtitle": "Ctrl+Shift+S", "keywords": "timer track time clock", "callback": self.toggle_tracking},
            {"title": "Track general work with no ticket", "subtitle": self.config.get("general_project", "Company General"), "keywords": "timer general company admin meeting", "callback": lambda: self.start_tracking("", self.config.get("general_project", "Company General"))},
            {"title": "Refresh Backlog", "subtitle": "Ctrl+R", "keywords": "sync issues", "callback": self.refresh},
            {"title": "Toggle navigation rail", "subtitle": "Ctrl+B", "keywords": "sidebar collapse", "callback": self.toggle_navigation},
            {"title": "Toggle ticket inspector", "subtitle": "Ctrl+Shift+B", "keywords": "detail panel", "callback": self.toggle_inspector},
            {"title": "Toggle task filter rail", "subtitle": "Display options", "keywords": "filters columns", "callback": self.tickets.toggle_filter_panel},
            {"title": "Next theme", "subtitle": "Ctrl+Shift+T", "keywords": "appearance colour color dark light palette", "callback": self.cycle_theme},
            {"title": "Open settings", "subtitle": "Ctrl+,", "keywords": "preferences theme density font", "callback": self.settings},
            {"title": "Open Backlog dashboard", "subtitle": self.config["backlog_web_url"], "keywords": "browser issues", "callback": self.open_backlog_dashboard},
        ]
        for index, name in enumerate(PAGES, 1):
            commands.append({"title": f"Go to {name}", "subtitle": f"Ctrl+{index}", "keywords": "page navigation", "callback": lambda page=name: self.navigate(page)})
        for key, label, _dark in theme_choices():
            commands.append({"title": f"Theme: {label}", "subtitle": resolve_theme(key).blurb, "keywords": "appearance colour color dark light palette", "callback": lambda name=key: self.apply_theme(name)})
        for label, value in (("All assigned", "all"), ("Needs attention", "attention"), ("Review tickets", "review"), ("Seen / deferred", "seen"), ("My Day", "my_day"), ("In progress", "progress"), ("Due today", "today"), ("Overdue", "overdue"), ("High priority", "high"), ("Starred", "starred"), ("No due date", "none")):
            commands.append({"title": f"Task view: {label}", "subtitle": "Saved view", "keywords": "filter personal tasks", "callback": lambda view=value: self.open_task_view(view)})
        for issue in self.issues:
            commands.append({
                "title": f"{issue_key(issue)}  {issue.get('summary', '')}",
                "subtitle": f"Ticket · {project_name(issue)}",
                "keywords": f"task ticket {(issue.get('assignee') or {}).get('name', '')}",
                "callback": lambda ticket=issue: self.select_ticket(ticket),
            })
        palette = CommandPalette(commands, self)
        palette.exec()

    def focus_dashboard_capture(self) -> None:
        self.navigate("Command Center")
        self.dashboard.focus_capture()

    def settings(self) -> None:
        dialog = SettingsDialog(self.config, self, issues=self.issues)
        if not dialog.exec():
            return
        if dialog.pending_api_key():
            try:
                save_backlog_key(dialog.pending_api_key())
            except RuntimeError as exc:
                QMessageBox.warning(self, "Could not store API key", str(exc))
                return
        self.config.update(dialog.values())
        save_config(self.config)
        self.reset_poll_timer()
        self.apply_schedule()
        self.calendar.config = self.config
        self.calendar.view.setCurrentText(self.config["calendar_default_view"])
        self.calendar.render()
        self.obsidian.set_vault_path(self.config.get("obsidian_vault_path", ""))
        self.ticket_detail.update_config(self.config)
        self.source_control.update_config(self.config)
        self.git.update_config(self.config)
        self.source_workspaces.setCurrentIndex(0 if self.config.get("source_control_provider") == "git" else 1)
        self.insights.update_config(self.config)
        self._nav_user_collapsed = bool(self.config.get("navigation_collapsed", False))
        self._nav_force_expanded = False
        self.build_toolbar()
        self.refresh_theme()
        self._apply_responsive_layout()
        self.statusBar().showMessage("Settings saved.", 3500)
        self.refresh()

    def closeEvent(self, event):
        # Keep Qt worker objects alive until their threads finish; do not block the UI.
        if not self.dashboard.daily_tools.flush():
            self._closing = False
            self.reset_poll_timer()
            self.statusBar().showMessage("Scratchpad could not be saved. Check disk access before closing.")
            event.ignore()
            return
        if not self.standup.save_entry():
            self._closing = False
            self.reset_poll_timer()
            self.statusBar().showMessage("Standup could not be saved. Check disk access before closing.")
            event.ignore()
            return
        self._closing = True
        self.source_control._patch_refresh_pending = False
        self.git._after = None
        workers = [*running_tasks(), self.sync_thread, *self.action_workers, *self.lookup_workers,
                   *self.analysis_workers, *self.comment_workers, self.source_control.worker, self.git.worker]
        running = []
        for worker in workers:
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    running.append(worker)
            except RuntimeError:
                continue
        if running:
            self.poll_timer.stop()
            self._refresh_pending = False
            for worker in running:
                if hasattr(worker, "cancel"):
                    worker.cancel()
            self.statusBar().showMessage("Finishing background work before closing…")
            QTimer.singleShot(150, self.close)
            event.ignore()
            return
        if self.tray:
            self.tray.hide()
        self.poll_timer.stop()
        self.clock_timer.stop()
        super().closeEvent(event)
