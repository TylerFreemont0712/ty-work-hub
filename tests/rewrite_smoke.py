"""Behavioral checks for the rewritten UI, failure recovery, and large task sets.

Run with python -m tests.rewrite_smoke. All data uses temporary directories.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date, datetime
from time import perf_counter
from unittest.mock import patch
import unittest

_home = TemporaryDirectory(prefix="ty-rewrite-tests-")
os.environ["TY_WORK_APP_HOME"] = _home.name
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6 import sip
from PyQt6.QtCore import QDate, QItemSelectionModel, QThread, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QPushButton
from config import DEFAULTS
from modules.backlog_api import demo_issues
from modules.event_store import EventStore
from modules.workspace_store import WorkspaceStore
from modules.work_insights import format_minutes, logged_minutes, summarize
from modules.work_schedule import WorkSchedule
from services.background import running_tasks
from services.time_tracking import TimeTracker
from ui.calendar_widget import CalendarWidget
from ui.command_palette import CommandPalette
from ui.dashboard import Dashboard
from ui.settings_dialog import SettingsDialog
from ui.standup_widget import StandupWidget
from ui.styles import theme_styles
from ui import theme as theme_module
from ui.ticket_list import TicketList
from ui.typography import configure_typography

APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")
configure_typography(APP)
APP.setStyleSheet(theme_styles("dark", "comfortable", 12))
METRICS = {}


def drain_until(predicate, milliseconds=5000):
    end = perf_counter() + milliseconds / 1000
    while not predicate() and perf_counter() < end:
        APP.processEvents()
        QTest.qWait(5)
    if not predicate():
        raise AssertionError("Background operation did not finish")


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory(prefix="ty-rewrite-case-")
        self.root = Path(self.folder.name)
        self.widgets = []

    def tearDown(self):
        drain_until(lambda: not running_tasks())
        for widget in reversed(self.widgets):
            if not sip.isdeleted(widget):
                sip.delete(widget)
        self.folder.cleanup()

    def track(self, widget):
        self.widgets.append(widget)
        return widget

    def test_overnight_work_totals_and_malformed_events(self):
        zone = datetime.now().astimezone().tzinfo
        now = datetime(2026, 9, 7, 9, 40, tzinfo=zone)
        events = [
            {"kind": "work", "start": "2026-09-06T23:30:00", "end": "2026-09-07T01:15:00"},
            {"kind": "work", "start": "bad", "end": "bad"},
            # A session that is still running counts up to now, so the visible
            # timer and the day's total can never disagree.
            {"kind": "work", "start": "2026-09-07T09:00:00", "end": "2026-09-07T09:00:00", "active": True},
        ]
        days = [date(2026, 9, 6), date(2026, 9, 7)]
        self.assertEqual(logged_minutes(events, days, now=now), [30, 115])

    def test_totals_deduct_evenings_weekends_and_breaks(self):
        """Only time inside the schedule counts; the raw session is untouched."""
        zone = datetime.now().astimezone().tzinfo
        schedule = WorkSchedule.from_config({})
        self.assertEqual(schedule.daily_minutes, 480)
        monday, saturday = date(2026, 9, 7), date(2026, 9, 12)
        events = [
            # 08:00-20:00 on a Monday is 12 raw hours but 8 counted.
            {"kind": "work", "start": "2026-09-07T08:00:00", "end": "2026-09-07T20:00:00",
             "ticket_key": "DEV-1", "project": "3TS"},
            # Straddles lunch: only 11:30-12:00 and 13:00-13:30 count.
            {"kind": "work", "start": "2026-09-07T11:30:00", "end": "2026-09-07T13:30:00",
             "ticket_key": "DEV-2", "project": "CrossTalk"},
            # A whole Saturday counts for nothing at all.
            {"kind": "work", "start": "2026-09-12T09:00:00", "end": "2026-09-12T17:00:00",
             "ticket_key": "DEV-1", "project": "3TS"},
        ]
        days = [monday, saturday]
        self.assertEqual(logged_minutes(events, days), [720 + 120, 480], "Raw totals keep every minute")
        self.assertEqual(logged_minutes(events, days, schedule), [480 + 60, 0])

        summary = summarize(events, days, schedule)
        self.assertEqual(summary.total_minutes, 540)
        self.assertEqual(dict(summary.ranked_tickets()), {"DEV-1": 480, "DEV-2": 60})
        self.assertEqual(dict(summary.ranked_projects()), {"3TS": 480, "CrossTalk": 60})
        self.assertEqual(format_minutes(summary.total_minutes), "9h 00m")

        # A session with no ticket lands under the configured general project.
        general = summarize(
            [{"kind": "work", "start": "2026-09-07T14:00:00", "end": "2026-09-07T15:00:00"}],
            [monday], schedule, fallback_project="Company General",
        )
        self.assertEqual(general.ranked_projects(), [("Company General", 60)])
        self.assertEqual(general.ranked_tickets(), [("No ticket", 60)])

        # Turning enforcement off counts everything, including the weekend.
        relaxed = WorkSchedule.from_config({"track_business_hours_only": False})
        self.assertEqual(logged_minutes(events, days, relaxed), [840, 480])
        self.assertTrue(relaxed.counts_now(datetime(2026, 9, 12, 3, 0, tzinfo=zone)))

    def test_schedule_windows_breaks_and_resume_hint(self):
        zone = datetime.now().astimezone().tzinfo
        schedule = WorkSchedule.from_config({})
        windows = schedule.windows(date(2026, 9, 7), zone)
        self.assertEqual([(start.hour, end.hour) for start, end in windows], [(9, 12), (13, 18)])
        self.assertEqual(schedule.windows(date(2026, 9, 13), zone), [], "Sunday has no countable window")
        self.assertFalse(schedule.counts_now(datetime(2026, 9, 7, 12, 30, tzinfo=zone)))
        self.assertTrue(schedule.counts_now(datetime(2026, 9, 7, 14, 0, tzinfo=zone)))
        resumes = schedule.next_window_start(datetime(2026, 9, 12, 10, 0, tzinfo=zone))
        self.assertEqual((resumes.date(), resumes.hour), (date(2026, 9, 14), 9))
        self.assertIsNone(schedule.next_window_start(datetime(2026, 9, 7, 14, 0, tzinfo=zone)))
        self.assertIn("Mon", schedule.describe())
        # A reversed or unparsable schedule falls back instead of counting nothing.
        broken = WorkSchedule.from_config({"work_schedule": {"start": "18:00", "end": "09:00"}})
        self.assertEqual((broken.start.hour, broken.end.hour), (9, 18))

    def test_event_save_failure_keeps_active_session_and_disk_consistent(self):
        store = EventStore(self.root / "events.json")
        tracker = TimeTracker(store)
        event = tracker.start(demo_issues()[0], 25)
        revision = store.revision
        active = tracker.active()
        active["active"] = False
        self.assertTrue(tracker.active()["active"])
        with patch.object(store, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                tracker.stop()
        self.assertEqual(store.revision, revision)
        self.assertTrue(EventStore(store.path).active_work()["active"])
        self.assertEqual(tracker.active()["id"], event["id"])
        tracker.stop()
        self.assertIsNone(tracker.active())
        self.assertIsNone(EventStore(store.path).active_work())

    def test_malformed_saved_events_do_not_break_timer_startup(self):
        path = self.root / "events.json"
        path.write_text(json.dumps([
            {"id": [], "title": "Bad id", "start": "2026-01-01", "end": "2026-01-02"},
            {"id": "bad", "title": "Bad date", "start": "oops", "end": "oops", "kind": "work", "active": True},
        ]))
        self.assertIsNone(EventStore(path).active_work())

    def test_workspace_failed_save_rolls_back_and_can_retry(self):
        store = WorkspaceStore(self.root / "workspace.json")
        store.set_scratchpad("Keep this")
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.set_scratchpad("New draft")
        self.assertEqual(store.scratchpad(), "Keep this")
        self.assertEqual(WorkspaceStore(store.path).scratchpad(), "Keep this")
        store.set_scratchpad("New draft")
        self.assertEqual(WorkspaceStore(store.path).scratchpad(), "New draft")

    def test_scratchpad_flush_routines_and_failure_feedback(self):
        dashboard = self.track(Dashboard())
        desk = dashboard.daily_tools
        store = WorkspaceStore(self.root / "workspace.json")
        desk.bind(store)
        desk.scratchpad.setPlainText("Unfinished thought 日本語")
        self.assertTrue(desk.flush())
        self.assertEqual(WorkspaceStore(store.path).scratchpad(), "Unfinished thought 日本語")
        desk.routine_input.setText("Review the queue")
        desk.add_routine()
        desk.routines.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(store.routine_is_current(store.routines()[0]))
        desk.scratchpad.setPlainText("Another draft")
        with patch.object(store, "set_scratchpad", side_effect=OSError("disk full")):
            self.assertFalse(desk.flush())
        self.assertIn("Could not save", desk.save_state.text())
        self.assertTrue(desk.flush())

    def test_task_selection_and_actions_survive_sort_and_local_update(self):
        tasks = self.track(TicketList(dict(DEFAULTS, task_filter_panel=False)))
        issues = demo_issues()
        for issue, key in zip(issues, ("DEV-10", "DEV-2", "DEV-1")):
            issue["key"] = issue["issueKey"] = key
        tasks.set_issues(issues)
        tasks.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        tasks.table.selectRow(0)
        selection = tasks.table.selectionModel()
        selection.select(tasks.sort_model.index(1, 1), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        tasks.set_workspace_state({"DEV-2"}, set(), {}, {})
        self.assertEqual({issue["key"] for issue in tasks.selected_issues()}, {"DEV-1", "DEV-2"})
        spy = QSignalSpy(tasks.action_requested)
        tasks._cell_clicked(1, 9)
        self.assertEqual(spy[0][1]["key"], "DEV-2")
        tasks.search.setText("no possible matching task")
        tasks.render()
        self.assertEqual(tasks.model.rowCount(), 0)
        self.assertIsNone(tasks.current_issue())
        self.assertFalse(tasks.empty_state.isHidden())

    def test_ten_thousand_tickets_use_constant_widget_count(self):
        tasks = self.track(TicketList(dict(DEFAULTS, task_filter_panel=False)))
        seed = demo_issues()[0]
        issues = [dict(seed, key=f"PERF-{index}", summary=f"Task number {index}") for index in range(10000)]
        tasks.set_issues(issues[:10])
        widget_count = len(tasks.findChildren(QPushButton))
        start = perf_counter()
        tasks.set_issues(issues)
        METRICS["load_10000_tickets_ms"] = round((perf_counter() - start) * 1000, 1)
        self.assertEqual(tasks.model.rowCount(), 10000)
        self.assertEqual(len(tasks.findChildren(QPushButton)), widget_count)
        self.assertEqual(len(tasks.table.findChildren(QPushButton)), 0)
        start = perf_counter()
        tasks.search.setText("PERF-9999")
        tasks.render()
        METRICS["filter_10000_tickets_ms"] = round((perf_counter() - start) * 1000, 1)
        self.assertEqual(tasks.model.rowCount(), 1)
        self.assertEqual(tasks.model.rows[0]["key"], "PERF-9999")
        METRICS["task_table_row_widgets"] = 0
        resets = QSignalSpy(tasks.model.modelReset)
        tasks.set_workspace_state(set(), set(), {}, {})
        self.assertEqual(len(resets), 1)

    def test_calendar_overnight_and_multihour_blocks(self):
        calendar = self.track(CalendarWidget(dict(DEFAULTS)))
        calendar.store = EventStore(self.root / "events.json")
        calendar.store.upsert({"title": "Overnight", "start": "2026-09-06T23:30:00", "end": "2026-09-07T01:15:00"})
        calendar.current_date = date(2026, 9, 7)
        calendar.view.setCurrentText("Day")
        calendar.render()
        self.assertEqual(calendar.day.item(0, 0).data(Qt.ItemDataRole.UserRole)[0]["title"], "Overnight")
        self.assertEqual(calendar.day.item(1, 0).data(Qt.ItemDataRole.UserRole)[0]["title"], "Overnight")
        self.assertEqual(calendar.day.item(2, 0).data(Qt.ItemDataRole.UserRole), [])

    def test_standup_history_preserves_text_and_failed_date_switch(self):
        from modules.standup_store import StandupStore
        standup = self.track(StandupWidget())
        standup.store = StandupStore(self.root / "standup.json")
        original = standup.day
        standup.today.setPlainText("My own plan")
        standup.append_plan([], ["Added task"])
        standup.append_plan([], ["Added task"])
        self.assertEqual(standup.today.toPlainText(), "My own plan\nAdded task")
        tomorrow = QDate.currentDate().addDays(1)
        standup.date_picker.setDate(tomorrow)
        standup.today.setPlainText("Tomorrow's plan")
        with patch.object(standup.store, "save_entry", side_effect=OSError("disk full")):
            standup.date_picker.setDate(QDate.fromString(original, "yyyy-MM-dd"))
        self.assertEqual(standup.day, tomorrow.toString("yyyy-MM-dd"))
        standup.date_picker.setDate(QDate.fromString(original, "yyyy-MM-dd"))
        self.assertEqual(standup.today.toPlainText(), "My own plan\nAdded task")

    def test_settings_probe_runs_off_gui_thread_and_recovers(self):
        settings = self.track(SettingsDialog(dict(DEFAULTS)))
        thread_ids = []
        class Client:
            def models(self):
                thread_ids.append(QThread.currentThread())
                return ["Test model"]
        settings._llm_client = lambda: Client()
        settings.refresh_llm_models()
        self.assertFalse(settings.load_models_button.isEnabled())
        drain_until(lambda: settings._probe is None)
        self.assertIsNot(thread_ids[0], APP.thread())
        self.assertEqual(settings.local_llm_model.currentText(), "Test model")
        self.assertTrue(settings.load_models_button.isEnabled())

    def test_every_theme_is_complete_and_applies_everywhere(self):
        """A theme is only usable if all of its tokens are real colours."""
        from PyQt6.QtGui import QColor

        tokens = [name for name in theme_module.token_names() if name not in {"key", "label", "blurb", "dark"}]
        self.assertGreaterEqual(len(theme_module.THEMES), 4)
        for key, palette in theme_module.THEMES.items():
            self.assertEqual(palette.key, key)
            self.assertTrue(palette.label and palette.blurb, key)
            for token in tokens:
                value = getattr(palette, token)
                self.assertTrue(QColor(value).isValid(), f"{key}.{token} = {value!r}")
            self.assertEqual(set(palette.status_colors), {"open", "progress", "resolved", "closed"})
            self.assertEqual(palette.event_color("work"), palette.success)
            self.assertEqual(palette.event_color("event", readonly=True), palette.danger)
            stylesheet = theme_styles(key, "compact", 12)
            self.assertIs(theme_module.active(), palette)
            self.assertIn(palette.bg, stylesheet)
            self.assertIn(palette.accent, stylesheet)
        # An unknown or missing key must never leave the app unstyled.
        self.assertEqual(theme_module.resolve("no-such-theme").key, theme_module.DEFAULT_THEME)
        self.assertEqual(theme_module.resolve(None).key, theme_module.DEFAULT_THEME)
        cycled = {theme_module.next_key(key) for key in theme_module.THEME_KEYS}
        self.assertEqual(cycled, set(theme_module.THEME_KEYS), "Cycling reaches every theme exactly once")
        theme_styles("dark", "comfortable", 12)

    def test_theme_change_repaints_glyphs_and_workspaces(self):
        from ui.design import button, glyph_color, retint_icons
        from PyQt6.QtWidgets import QWidget, QVBoxLayout

        holder = self.track(QWidget())
        layout = QVBoxLayout(holder)
        nav = button("Tasks", glyph="tasks")
        nav.setProperty("nav", True)
        nav.setCheckable(True)
        primary = button("New", glyph="plus", primary=True)
        layout.addWidget(nav)
        layout.addWidget(primary)
        theme_styles("dark", "compact", 12)
        self.assertEqual(glyph_color(nav), theme_module.THEMES["dark"].muted)
        self.assertEqual(glyph_color(primary), theme_module.THEMES["dark"].primary_text)
        nav.setChecked(True)
        self.assertEqual(glyph_color(nav), theme_module.THEMES["dark"].accent)
        theme_styles("ember", "compact", 12)
        retint_icons(holder)
        self.assertEqual(glyph_color(nav), theme_module.THEMES["ember"].accent)
        self.assertFalse(nav.icon().isNull())
        theme_styles("dark", "comfortable", 12)

    def test_command_results_are_bounded_but_all_tickets_searchable(self):
        palette = self.track(CommandPalette([{"title": f"Ticket {i}", "callback": lambda: None} for i in range(10000)]))
        self.assertEqual(palette.results.count(), 60)
        palette.search.setText("Ticket 9999")
        self.assertEqual(palette.results.count(), 1)
        self.assertEqual(palette.results.item(0).text(), "Ticket 9999")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RewriteTests))
    artifact = Path(__file__).resolve().parents[1] / "artifacts" / "performance.json"
    artifact.write_text(json.dumps(METRICS, indent=2), encoding="utf-8")
    print(json.dumps(METRICS), flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
