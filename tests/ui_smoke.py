"""Offscreen regression checks for the primary desktop workflows.

Run after installing requirements with:
  $env:QT_QPA_PLATFORM = "offscreen"
  python -m tests.ui_smoke
"""
from datetime import date, datetime, timedelta
from json import dumps
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
import requests

from PyQt6 import sip
from PyQt6.QtCore import QDate, QItemSelectionModel, QPoint, Qt, QThread
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

import config as config_module
from config import load_config
from modules.backlog_api import BacklogClient, BacklogError, demo_issues
from modules.event_store import EventStore
from modules.obsidian_sync import ObsidianSync
from modules.patch_store import PatchStore
from modules.standup_store import StandupStore
from modules.svn_models import IGNORE_CHANGELIST, PatchArtifact, PatchFileSummary, SvnChange, SvnInfo
from modules.ticket_utils import project_name
from modules.workspace_store import WorkspaceStore
from services.issue_cache import IssueCache
from services.local_llm import (
    LocalLLMClient, LocalLLMError, format_backlog_comment, normalize_analysis,
    normalize_learning_references, ticket_patch_context,
)
from services.time_tracking import TimeTracker
from ui.command_palette import CommandPalette
from ui.event_dialog import EventDialog
from ui.environment_dialog import EnvironmentDialog
import ui.environment_dialog as environment_dialog_module
import ui.main_window as main_window_module
from ui.main_window import MainWindow
from ui.note_preview_dialog import NotePreviewDialog
from ui.patch_dialog import PatchDetailsDialog
from ui.settings_dialog import SettingsDialog


@contextmanager
def patch_details(label: str = "", group: str = "", accepted: bool = True):
    """Answer the patch naming dialog without showing it.

    `create_patch` and `shelve_changes` are modal, so any test path that reaches
    them has to stub the dialog or the offscreen run blocks forever.
    """
    def stub(dialog):
        if label:
            dialog.label.setText(label)
        if group:
            dialog.group.setCurrentText(group)
        return 1 if accepted else 0

    # Only remove what this helper added. Re-assigning the inherited QDialog.exec
    # onto the subclass leaves an unbound method PyQt6 refuses to call.
    own = PatchDetailsDialog.__dict__.get("exec")
    PatchDetailsDialog.exec = stub
    try:
        yield
    finally:
        if own is None:
            del PatchDetailsDialog.exec
        else:
            PatchDetailsDialog.exec = own


@contextmanager
def isolated_workspace(app):
    # Destroy Qt filesystem watchers before Windows removes the temporary vault.
    with TemporaryDirectory() as folder:
        try:
            yield folder
        finally:
            for widget in list(app.topLevelWidgets()):
                if isinstance(widget, MainWindow):
                    widget.poll_timer.stop()
                    workers = [widget.sync_thread, *widget.action_workers, *widget.lookup_workers,
                               *widget.analysis_workers, *widget.comment_workers, widget.source_control.worker]
                    for worker in workers:
                        if worker is not None and not sip.isdeleted(worker):
                            if hasattr(worker, "cancel"):
                                worker.cancel()
                            worker.wait(5000)
                    sip.delete(widget)



def main():
    app = QApplication([])
    with isolated_workspace(app) as folder:
        root = Path(folder)
        vault = root / "Vault"
        vault.mkdir()
        working_copy = root / "Working Copy 日本"
        working_copy.mkdir()
        patch_root = root / "Patch Shelf"
        config = load_config()
        config.update({
            "custom_launchers": [], "navigation_collapsed": False, "task_filter_panel": True,
            "obsidian_vault_path": str(vault), "svn_patch_root": str(patch_root),
            "svn_profiles": [{"name": "Main app", "project": "3TS", "root": str(working_copy)}],
            # Pin every remembered preference so the run does not depend on the
            # developer's saved config.
            "svn_show_unversioned": True, "theme": "dark", "density": "compact", "source_control_provider": "svn",
            "svn_shelf_all_tickets": True, "svn_shelf_sort": [],
            "local_llm_url": "http://llama.test", "local_llm_model": "", "local_llm_thinking": False,
            "local_llm_max_tokens": 8192, "local_llm_context_tokens": 32768,
        })
        workspace = WorkspaceStore(root / "workspace.json")

        # Config migration keeps invalid old values safe and saves atomically.
        original_config_path = config_module.CONFIG_PATH
        config_module.CONFIG_PATH = root / "config.json"
        config_module.CONFIG_PATH.write_text('{"workday_start": 22, "workday_end": 8, "svn_profiles": "invalid"}', encoding="utf-8")
        migrated = config_module.load_config()
        # A reversed legacy workday cannot produce a schedule that counts nothing.
        assert migrated["work_schedule"]["start"] == "09:00" and migrated["work_schedule"]["end"] == "18:00"
        assert migrated["work_schedule"]["breaks"] == [{"label": "Lunch", "start": "12:00", "end": "13:00"}]
        assert migrated["svn_profiles"] == []
        config_module.CONFIG_PATH.write_text('{"workday_start": 8, "workday_end": 17}', encoding="utf-8")
        legacy = config_module.load_config()
        assert (legacy["work_schedule"]["start"], legacy["work_schedule"]["end"]) == ("08:00", "17:00"), (
            "The pre-schedule workday hours seed the new schedule once"
        )
        config_module.save_config(migrated)
        assert config_module.CONFIG_PATH.exists() and not config_module.CONFIG_PATH.with_suffix(".tmp").exists()
        config_module.CONFIG_PATH = original_config_path
        main_window_module.backlog_key = lambda: ""
        window = MainWindow(config, workspace, persist_preferences=False)
        assert not window.windowIcon().isNull()
        for _ in range(100):
            app.processEvents()
            if window.sync_thread is None:
                break
            QThread.msleep(5)
        assert window.sync_thread is None
        lookup_ticket = demo_issues()[0]
        window._watched_lookup_found(lookup_ticket["key"], [lookup_ticket])
        for _ in range(100):
            app.processEvents()
            if window.sync_thread is None:
                break
            QThread.msleep(5)
        assert lookup_ticket["key"] in workspace.watched_keys()
        assert window.sync_thread is None
        # A second refresh after Qt deletes the first worker reproduces the
        # review-ticket crash path and must not touch a stale C++ wrapper.
        window.refresh()
        for _ in range(100):
            app.processEvents()
            if window.sync_thread is None:
                break
            QThread.msleep(5)
        assert window.sync_thread is None
        window.calendar.store = EventStore(root / "events.json")
        window.calendar.tracker = TimeTracker(window.calendar.store)

        issues = demo_issues() + [{
            "id": 9,
            "key": "DEV-9",
            "summary": "Invalid deadline is ignored",
            "dueDate": "invalid",
            "status": {"name": "Open"},
            "project": {"name": "Test"},
            "priority": {"name": "Low"},
        }]
        issues[0]["dueDate"] = date.today().isoformat()
        issues[1]["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
        window.synced(issues, True)

        # Saved views, tokenized search, favorites, and extended selection.
        window.tickets.activate_view("today")
        assert [issue["key"] for issue in window.tickets.filtered()] == [issues[0]["key"]]
        workspace.toggle_favorite(issues[1]["key"])
        workspace.toggle_my_day(issues[1]["key"])
        window._refresh_workspace_views()
        window.tickets.activate_view("starred")
        assert [issue["key"] for issue in window.tickets.filtered()] == [issues[1]["key"]]
        window.tickets.activate_view("all")
        window.tickets.search.setText("translated chat")
        assert [issue["key"] for issue in window.tickets.filtered()] == [issues[1]["key"]]
        window.tickets.search.clear()
        window.tickets.table.selectRow(0)
        selection = window.tickets.table.selectionModel()
        selection.select(
            window.tickets.table.model().index(1, 1),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        assert len(window.tickets.selected_issues()) == 2
        window.tickets.activate_view("my_day")
        assert {issue["key"] for issue in window.tickets.filtered()} == {issues[0]["key"], issues[1]["key"]}
        assert window.tickets.sort_model.index(0, 9).data() == "MD"

        # Seen tickets remain in Tasks, move behind active queue work, and resurface on change.
        workspace.mark_issue_seen(issues[0], "back")
        window._refresh_workspace_views()
        assert workspace.issue_attention(issues[0]) == "back"
        assert any(issue["key"] == issues[0]["key"] for issue in window.tickets.filtered())
        changed = dict(issues[0]); changed["updated"] = "2099-01-01T00:00:00Z"
        assert workspace.issue_attention(changed) == "changed"
        workspace.mark_issue_seen(changed, "hidden")
        assert workspace.issue_attention(changed) == "hidden"
        workspace.clear_issue_seen(changed["key"])

        # Watched review tickets persist independently.
        assert workspace.add_watched_ticket("DEV_3TS-1479") is True
        assert "DEV_3TS-1479" in workspace.watched_keys()

        # Local reminders feed both the task list and the calendar planning rail.
        reminder = workspace.set_reminder(issues[1]["key"], datetime.now().astimezone() + timedelta(hours=1))
        assert workspace.reminder_for(issues[1]["key"])["at"] == reminder["at"]
        window._refresh_workspace_views()
        assert issues[1]["key"] in window.calendar.reminders
        assert window.calendar.upcoming_tasks.count() >= 1

        # Local capture inbox remains functional without a network connection.
        capture = workspace.add_capture("Follow up with the design team")
        assert capture and not capture["done"]
        assert workspace.toggle_capture(capture["id"]) is True
        window.dashboard.set_captures(workspace.captures())
        assert window.dashboard.captures.count() == 1

        # Command palette filtering is deterministic and keyboard-ready.
        palette = CommandPalette([
            {"title": "Task view: Overdue", "keywords": "filter tasks", "callback": lambda: None},
            {"title": "Open Calendar", "keywords": "schedule", "callback": lambda: None},
        ])
        palette.search.setText("overdue")
        assert palette.results.count() == 1
        palette.close()

        # Every primary page renders, and narrow layouts collapse instead of overflowing.
        window.show()
        for width, height in ((1360, 800), (1100, 680), (900, 600)):
            window.resize(width, height)
            app.processEvents()
            for page in ("Command Center", "Personal Tasks", "Review Board", "Calendar", "Obsidian", "Morning Standup", "Source Control", "Ticket Detail"):
                window.navigate(page)
                app.processEvents()
        assert window.navigation.width() <= 64
        window.show_ticket(issues[0])
        window.resize(900, 600)
        app.processEvents()
        assert window.inspector.isVisible()
        assert not window.tickets.filter_panel.isVisible()
        assert window.inspector_tabs.count() == 3
        assert [window.inspector_tabs.tabText(index) for index in range(3)] == ["Work", "Backlog", "Context"]
        # Manual and AI drafts belong to a ticket; selection changes cannot
        # accidentally carry a comment into another Backlog issue.
        window.comment.setPlainText("draft for the first ticket")
        window.show_ticket(issues[1])
        assert window.comment.toPlainText() == ""
        window.show_ticket(issues[0])
        assert window.comment.toPlainText() == "draft for the first ticket"
        original_information = QMessageBox.information
        ownership_warnings = []
        QMessageBox.information = lambda *args, **_kwargs: ownership_warnings.append(args[1]) or QMessageBox.StandardButton.Ok
        window.current = issues[1]
        try:
            window.post_comment()
        finally:
            QMessageBox.information = original_information
        assert ownership_warnings == ["Comment belongs to another ticket"]
        assert window.comment.toPlainText() == "draft for the first ticket"
        window.show_ticket(issues[0])
        window.comment.clear()
        # A real Backlog status is not painted optimistically; the returned
        # issue owns the visible state after the worker succeeds.
        original_confirm = window.confirm_action
        original_run_action = window.run_action
        original_backlog_key = main_window_module.backlog_key
        sent_actions = []
        window.run_action = lambda action, key, value, **_kwargs: sent_actions.append((action, key, value))
        main_window_module.backlog_key = lambda: "configured"
        window.confirm_action = lambda *_args, **_kwargs: True
        original_status = dict(window.current["status"])
        try:
            assert window._apply_status(window.current["_availableStatuses"][1], create_note=False)
        finally:
            window.run_action = original_run_action
            main_window_module.backlog_key = original_backlog_key
            window.confirm_action = original_confirm
        assert window.current["status"] == original_status
        assert sent_actions == [("status", issues[0]["key"], issues[0]["_availableStatuses"][1]["id"])]
        # Cancelling the in-progress status prompt cancels the entire start flow.
        window.confirm_action = lambda *_args, **_kwargs: False
        try:
            window.start_work()
        finally:
            window.confirm_action = original_confirm
        assert window.calendar.active_work() is None
        # A permanent queued-comment rejection restores the draft, and a
        # rejected status snaps the combo back to the confirmed issue state.
        original_warning = QMessageBox.warning
        QMessageBox.warning = lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok
        try:
            queued_comment = workspace.queue_action("comment", issues[0]["key"], "restore this comment")
            comment_worker = object()
            window._backlog_action_context[comment_worker] = {
                "action": "comment", "issue_id": issues[0]["key"],
                "value": "restore this comment", "queued_item": queued_comment,
            }
            window._action_failed(comment_worker, "[7] Invalid request", False)
            assert window.comment.toPlainText() == "restore this comment"
            assert queued_comment not in workspace.pending_actions()
            window.comment.clear()

            window.status.setCurrentIndex(1)
            status_worker = object()
            window._backlog_action_context[status_worker] = {
                "action": "status", "issue_id": issues[0]["key"],
                "value": issues[0]["_availableStatuses"][1]["id"], "queued_item": None,
            }
            window._action_failed(status_worker, "[7] Invalid transition", False)
            assert window.status.currentData()["id"] == issues[0]["status"]["id"]
        finally:
            QMessageBox.warning = original_warning
        window.open_ticket_detail()
        assert window.stack.currentWidget() is window.ticket_detail
        assert issues[0]["key"] in window.ticket_detail.title.text()

        # Source Control preserves ticket context and renders patch-safe status and shelf metadata.
        window.source_control.set_ticket(issues[0])
        window.navigate("Source Control")
        assert window.stack.currentWidget() is window.source_workspaces
        assert window.source_workspaces.currentWidget() is window.source_control
        assert window.source_control.ticket_key.text() == issues[0]["key"]
        assert window.source_control.current_root() == str(working_copy)
        source_changes = [
            SvnChange("src/日本 file.txt", "modified", revision="42"),
            SvnChange("notes/local.txt", "unversioned"),
        ]
        window.source_control._render_changes(source_changes)
        assert window.source_control.change_table.rowCount() == 2
        assert window.source_control.selected_patchable_paths() == ["src/日本 file.txt"]
        patch_info = SvnInfo(
            target=str(working_copy), working_copy_root=str(working_copy),
            url="https://svn.example.test/repos/app/trunk", repository_root="https://svn.example.test/repos/app",
            repository_uuid="test-uuid", revision="42",
        )
        artifact = PatchStore(patch_root).create(
            issues[0], "Main app", working_copy, patch_info,
            b"Index: src/file.txt\n--- src/file.txt\t(revision 42)\n+++ src/file.txt\t(working copy)\n",
            ["src/file.txt"],
        )
        window.source_control._render_patches([artifact])
        assert window.source_control.patch_table.rowCount() == 1
        assert isinstance(window.source_control.selected_artifact(), PatchArtifact)

        source = window.source_control
        # The label names one patch, not the ticket, so it starts empty per ticket.
        assert source.patch_label() == ""
        source.ticket_context.setText("Login regression fix")
        assert source.patch_label() == "Login regression fix"
        manual = dict(source.current_ticket())
        source.ticket_key.setText("MANUAL-7")
        assert source.current_ticket()["key"] == "MANUAL-7"
        source.set_ticket(issues[0])
        assert source.patch_label() == "", "Switching tickets clears the previous patch name"
        assert manual["key"] == issues[0]["key"]

        # A ticket's patches are numbered, so the next one is suggested as #2.
        source._render_patches([artifact])
        sequence, groups = source.patch_series()
        assert artifact.sequence == 1 and artifact.display_label == "Part 1"
        assert sequence == 2 and groups == [], "The shelf drives the next position"
        assert source.next_patch_hint.text() == "#2"
        labelled = PatchStore(patch_root).create(
            issues[0], "Main app", working_copy, patch_info,
            b"Index: src/two.txt\n--- src/two.txt\t(revision 42)\n+++ src/two.txt\t(working copy)\n",
            ["src/two.txt"], label="Validation fix", group="Backend", note="Second half of the change",
        )
        assert labelled.sequence == 2 and labelled.display_label == "Validation fix"
        assert "#2" in labelled.headline and "[Backend]" in labelled.headline
        assert PatchStore(patch_root).groups_for_ticket(issues[0]["key"]) == ["Backend"]
        source._render_patches([artifact, labelled])
        assert source.patch_table.rowCount() == 2 and source.next_patch_hint.text() == "#3"

        # The shelf filter narrows what is listed without reloading the shelf.
        source.shelf_filter.setText("backend")
        assert source.patch_table.rowCount() == 1, "Only the grouped patch matches"
        assert "filtered by" in source.shelf_summary.text()
        source.shelf_filter.setText("no-such-patch")
        assert source.patch_table.rowCount() == 0
        source.shelf_filter.clear()
        assert source.patch_table.rowCount() == 2

        # Naming happens in a dialog, and cancelling it creates nothing.
        source._render_changes([SvnChange("src/file.txt", "modified", revision="42")])
        source._set_patchable_checked(True)
        started_patches = []
        original_patch_start = source._start
        source._start = lambda action, payload: started_patches.append((action, payload))
        try:
            with patch_details(accepted=False):
                source.create_patch()
            assert not started_patches, "Cancelling the patch dialog starts no work"
            with patch_details(label="Parser rewrite", group="Backend"):
                source.create_patch()
        finally:
            source._start = original_patch_start
        assert started_patches and started_patches[0][0] == "create_patch"
        assert started_patches[0][1]["label"] == "Parser rewrite"
        assert started_patches[0][1]["group"] == "Backend"
        source._render_patches([artifact, labelled])

        # Patches saved before labels existed can still be renamed and grouped.
        store = PatchStore(patch_root)
        renamed = store.update_details(artifact, label="Initial spike", group="Backend", note="Kept for reference")
        assert renamed.display_label == "Initial spike" and renamed.group == "Backend"
        assert Path(renamed.patch_file).name == Path(artifact.patch_file).name, "Editing never renames files"
        assert store.load(renamed.manifest_file).note == "Kept for reference"
        assert store.update_details(renamed, label="").display_label == "Initial spike", (
            "Clearing the label keeps the name the patch already showed"
        )
        source._replace_artifact(renamed)
        assert source._shelf_groups() == ["Backend"]

        # Renumbering gives a legacy series positions in creation order.
        legacy = PatchArtifact.from_dict(
            {**renamed.to_dict(), "sequence": 0}, renamed.patch_file, renamed.manifest_file
        )
        store._write_manifest(legacy)
        assert store.load(legacy.manifest_file).sequence == 0
        ordered = store.renumber_ticket(issues[0]["key"])
        assert [item.sequence for item in ordered] == [2, 1], "Newest first, numbered oldest first"
        assert store.load(legacy.manifest_file).sequence == 1

        # Unversioned files can be hidden, and hiding them keeps the versioned rows.
        tree_changes = [
            SvnChange("src/app/service/Order.java", "modified", revision="42"),
            SvnChange("src/app/Main.java", "added"),
            SvnChange("notes/local.txt", "unversioned"),
        ]
        source._render_changes(tree_changes)
        assert source.change_table.rowCount() == 3
        source.show_unversioned.setChecked(False)
        assert source.change_table.rowCount() == 2
        assert "unversioned hidden" in source.change_summary.text()
        assert config["svn_show_unversioned"] is False
        assert source.selected_patchable_paths() == ["src/app/Main.java", "src/app/service/Order.java"]
        source.show_unversioned.setChecked(True)
        assert source.change_table.rowCount() == 3

        # Sorting by a header groups siblings; sorting by state surfaces attention first.
        source.change_table.sortItems(2, Qt.SortOrder.AscendingOrder)
        sorted_paths = [source.change_table.item(row, 2).text() for row in range(source.change_table.rowCount())]
        assert sorted_paths == ["notes/local.txt", "src/app/Main.java", "src/app/service/Order.java"]
        source.change_table.sortItems(1, Qt.SortOrder.AscendingOrder)
        assert source.change_table.item(source.change_table.rowCount() - 1, 1).text() == "Unversioned"
        source.change_table.sortItems(2, Qt.SortOrder.AscendingOrder)

        # Selecting a row reveals the file in the location tree, and the tree selects back.
        source.change_table.selectRow(2)
        assert source.change_tree.caption.text() == "In: src/app/service"
        source.change_tree.path_activated.emit("src/app/Main.java")
        assert source._selected_table_paths() == ["src/app/Main.java"]

        # The before/after compare view renders base and working-copy content.
        source.compare.set_files([
            {"path": "src/app/Main.java", "before": "one\ntwo\n", "after": "one\nTWO\n"},
            {"path": "src/app/new.java", "before": "", "after": "new\n", "before_note": "No base revision"},
        ])
        assert source.compare.file_picker.count() == 2
        assert "two" in source.compare.before.toPlainText()
        assert len(source.compare.after.extraSelections()) == 1, "The changed line should be marked"
        source.compare.file_picker.setCurrentIndex(1)
        assert "No base revision" in source.compare.before_label.text()

        # The shelf shows every project folder, and the Project column comes first.
        other_ticket = {"key": "OPS-9", "summary": "Ops patch", "project": {"name": "Operations"}}
        other_artifact = PatchStore(patch_root).create(
            other_ticket, "Main app", working_copy, patch_info,
            b"Index: ops.txt\n--- ops.txt\t(revision 42)\n+++ ops.txt\t(working copy)\n", ["ops.txt"],
        )
        assert [source.patch_table.horizontalHeaderItem(index).text().split()[-2:][0]
                for index in range(2)][0] in {"Project", "Ticket"}
        shelf_all, unreadable = PatchStore(patch_root).scan()
        assert unreadable == 0 and len(shelf_all) == 3, "Both tickets' patches live in one shelf"
        source._render_patches(shelf_all)
        assert source.patch_table.rowCount() == 3
        projects = {source.patch_table.item(row, 0).text() for row in range(3)}
        assert projects == {"Operations", project_name(issues[0])}
        assert "whole shelf" in source.shelf_summary.text()
        assert other_artifact.sequence == 1, "A new ticket starts its own patch series"

        # Tiered sorting: right-click cycles a column's priority, left-click flips direction.
        header = source.shelf_sort
        assert header.describe() == "Created ↓", "Newest first until the user changes it"
        assert header.rule("project").tier == 0
        for expected in (1, 2, 3, 0):
            header._section_right_clicked(QPoint(header.table.horizontalHeader().sectionViewportPosition(0) + 4, 4))
            assert header.rule("project").tier == expected
        header.rule("project").tier, header.rule("project").assigned = 3, 1
        header.rule("ticket").tier, header.rule("ticket").assigned = 1, 2
        header.refresh_labels()
        assert source.patch_table.horizontalHeaderItem(0).text() == "▲▲▲ Project ↑"
        assert source.patch_table.horizontalHeaderItem(1).text() == "▲ Ticket ↑"
        assert [key for key, _ in header.sort_order()] == ["project", "ticket"], "Highest tier is compared first"
        header.changed.emit()
        assert source.patch_table.item(0, 0).text() == project_name(issues[0]), (
            "Project now decides the order instead of the creation date"
        )
        assert "▲▲▲ Project" in source.shelf_sort_hint.text()
        assert config["svn_shelf_sort"], "The tier layout is remembered"
        header._section_clicked(0)
        assert source.patch_table.item(0, 0).text() == "Operations", "Left-click flips the direction"
        header.clear_tiers()
        assert header.describe() == "Created ↓"

        # Narrowing to one ticket still works.
        source.shelf_this_ticket.setChecked(True)
        assert config["svn_shelf_all_tickets"] is False
        source.shelf_this_ticket.setChecked(False)
        assert config["svn_shelf_all_tickets"] is True
        PatchStore(patch_root).delete(other_artifact)

        # Tabs read in workflow order: inspect, shelve, explain, compare, raw output.
        assert [source.tabs.tabText(index) for index in range(source.tabs.count())] == [
            "Changes", "Patch Shelf", "Patch Changes", "Before / After", "Diff / Output",
        ]

        # A locked file is visible and patchable but never selected automatically.
        locked_changes = [
            SvnChange("src/app/Order.java", "modified", revision="42"),
            SvnChange("web.config", "modified", revision="42", changelist=IGNORE_CHANGELIST),
        ]
        source._render_changes(locked_changes)
        assert source.change_table.rowCount() == 2
        assert source.selected_patchable_paths() == ["src/app/Order.java"]
        assert "1 locked" in source.change_summary.text()
        assert source.create_patch_button.text() == "Create patch (1)"
        source._set_patchable_checked(True)
        assert source.selected_patchable_paths() == ["src/app/Order.java"], "Select patchable skips locked files"
        # The user can still tick a locked file by hand.
        for row in range(source.change_table.rowCount()):
            item = source.change_table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole).path == "web.config":
                item.setCheckState(Qt.CheckState.Checked)
        assert sorted(source.selected_patchable_paths()) == ["src/app/Order.java", "web.config"]
        assert source.create_patch_button.text() == "Create patch (2)"
        # Locked rows sink to the bottom under both sort orders.
        source.change_table.sortItems(2, Qt.SortOrder.AscendingOrder)
        assert source.change_table.item(1, 2).text() == "web.config"

        # Locking routes through the changelist command with the right scope.
        started = []
        original_start = source._start
        source._start = lambda action, payload: started.append((action, payload))
        try:
            source.change_table.selectRow(0)
            source.set_locked(True)
            source.change_table.selectRow(1)
            source.set_locked(False)
        finally:
            source._start = original_start
        assert started[0] == ("changelist", {**started[0][1], "changelist": IGNORE_CHANGELIST})
        assert started[0][1]["paths"] == ["src/app/Order.java"]
        assert started[1][1] == {**started[1][1], "changelist": ""}
        assert started[1][1]["paths"] == ["web.config"]

        # Patch Changes explains the selected patch without touching anything.
        source._render_patch_changes(artifact, [
            PatchFileSummary("src/app/Order.java", "modified", 4, 2),
            PatchFileSummary("src/app/New.java", "added", 9, 0),
            PatchFileSummary("src/app/Old.java", "deleted", 0, 7),
        ])
        assert source.patch_change_table.rowCount() == 3
        assert "1 added" in source.patch_changes_summary.text()
        assert "+13 / -9" in source.patch_changes_summary.text()
        assert source.patch_change_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        source.patch_change_table.selectRow(0)
        source._render_patch_changes(None, [])
        assert source.patch_change_table.rowCount() == 0

        # Before/After navigation walks the changed blocks and marks the current one.
        source.compare.set_files([{"path": "n.txt", "before": "a\nb\nc\nd\ne\n", "after": "a\nB\nc\nd\nE\n"}])
        assert source.compare.change_position.text() == "Change 1 of 2"
        source.compare.step_change(1)
        assert source.compare.change_position.text() == "Change 2 of 2"
        source.compare.step_change(1)
        assert source.compare.change_position.text() == "Change 1 of 2", "Navigation wraps around"
        source.compare.go_to_change(1)
        focused = {selection.format.background().color().name() for selection in source.compare.after.extraSelections()}
        assert len(focused) == 2, "The current block uses a stronger shade than the others"

        # Diff colouring is applied by the highlighter, not by the caller.
        source.output.setPlainText("Index: a.txt\n@@ -1 +1 @@\n-old\n+new\n")
        assert source.output.highlighter.document() is source.output.document()
        source.output.set_theme("light")
        source.output.set_theme(config.get("theme", "dark"))

        # Patch badges reach Ticket Detail and the inspector.
        window.set_patch_badges(issues[0]["key"], 3)
        assert window.ticket_detail.view_patches.text() == "Patches (3)"
        assert window.patches_button.isEnabled()
        window.set_patch_badges(issues[0]["key"], 0)
        assert not window.patches_button.isEnabled()
        window.refresh_patch_badges(issues[0])
        assert window.ticket_detail.patch_count == 2, "Both of this ticket's patches are counted"
        assert window.ai_comment.isEnabled()
        assert window.ai_comment.text() == "AI Generate Ticket Comment"

        # Ticket Detail can drive the SVN workspace, naming the patch after the ticket.
        window.ticket_detail.set_ticket(issues[0])
        source.ticket_context.clear()
        with patch_details(accepted=False):
            window.ticket_detail._source_control("create_patch")
            # "Make patch" with no status loaded runs status first and asks for the
            # patch name afterwards; drain that follow-up while the stub is installed.
            for _ in range(20):
                app.processEvents()
            source._after_status, source._after_worker = "", None
        assert window.stack.currentWidget() is window.source_workspaces
        assert window.source_workspaces.currentWidget() is source
        assert source.ticket_key.text().strip().upper() == issues[0]["key"].upper()
        window.ticket_detail._source_control("patches")
        assert source.tabs.currentWidget() is source.shelf_tab

        # Deleting a patch asks first and then removes both files.
        source._render_patches([artifact])
        original_delete_warning = QMessageBox.warning
        QMessageBox.warning = lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel
        try:
            source.delete_selected_patch()
        finally:
            QMessageBox.warning = original_delete_warning
        assert Path(artifact.patch_file).is_file(), "Cancelling the confirmation must keep the patch"
        assert PatchStore(patch_root).count_for_ticket(issues[0]["key"]) == 2
        assert PatchStore(patch_root).next_sequence(issues[0]["key"]) == 3
        target_day = date.today() + timedelta(days=8)
        window.calendar.mini_date_selected(QDate(target_day.year, target_day.month, target_day.day))
        assert window.calendar.current_date == target_day and window.calendar.view.currentText() == "Week"

        now = datetime.now().astimezone()
        window.calendar.store.upsert({
            "title": "Test event",
            "start": now.isoformat(),
            "end": (now + timedelta(minutes=30)).isoformat(),
            "kind": "event",
            "color": "#8b5cf6",
        })
        for mode in ("Month", "Week", "Day", "Agenda"):
            window.calendar.view.setCurrentText(mode)
        window.calendar.start_work(issues[0], project_name(issues[0]))
        window.refresh_dashboard()
        window.current = issues[0]
        window.tick()
        session = window.calendar.active_work()
        assert session["ticket_key"] == issues[0]["key"] and session["project"] == project_name(issues[0])
        assert session["active"] and window.timer_chip.action.text() == "Stop"
        assert not window.dashboard.start.isEnabled(), "One session at a time"
        window.stop_work()
        assert window.calendar.active_work() is None and window.timer_chip.action.text() == "Start"

        # The timer works with no ticket at all, filed under the general project.
        window.start_tracking("", config["general_project"])
        general = window.calendar.active_work()
        assert window.timer_chip.subject.text().startswith(config["general_project"])
        assert general["ticket_key"] == "" and general["project"] == config["general_project"]
        window.toggle_tracking()
        assert window.calendar.active_work() is None, "The shortcut stops a running session"

        # The Today card drives the same path through its two dropdowns.
        assert window.dashboard.work_ticket.itemData(0) == "", "General work is always offered first"
        window.dashboard.work_ticket.setCurrentIndex(1)
        chosen = window.dashboard.selected_ticket_key()
        assert chosen and window.dashboard.selected_project() == project_name(
            next(issue for issue in issues if issue["key"] == chosen)
        ), "The project follows the chosen ticket"
        window.dashboard._request_start()
        assert window.calendar.active_work()["ticket_key"] == chosen
        window.stop_work()

        reloaded_workspace = WorkspaceStore(root / "workspace.json")
        assert issues[1]["key"] in reloaded_workspace.my_day_keys()
        assert reloaded_workspace.reminder_for(issues[1]["key"])

        # Time insights reads the same sessions through the schedule.
        window.navigate("Time Insights")
        window.insights.set_events(window.calendar.store.all())
        assert "Mon" in window.insights.subtitle.text()
        assert window.insights.sessions.rowCount() >= 1
        settings_schedule = SettingsDialog(config).values()["work_schedule"]
        assert settings_schedule["start"] == "09:00" and settings_schedule["end"] == "18:00"
        assert settings_schedule["breaks"][0]["label"] == "Lunch"
        dialog = EventDialog(default_start=now)
        assert dialog.start.dateTime().isValid()
        settings = SettingsDialog(config)
        settings.add_launcher("Terminal", "cmd.exe")
        assert any(value["label"] == "Terminal" for value in settings.values()["custom_launchers"])
        assert settings.values()["density"] in {"compact", "comfortable"}
        assert settings.values()["local_llm_url"].startswith("http://")
        assert settings.values()["local_llm_thinking"] is False
        assert 512 <= settings.values()["local_llm_max_tokens"] <= 32768
        assert settings.values()["local_llm_context_tokens"] >= 2048
        assert settings.values()["obsidian_programming_folder"] == "Work/Programming"
        assert settings.values()["svn_patch_root"] == str(patch_root)
        assert settings.values()["svn_profiles"][0]["root"] == str(working_copy)
        project_settings = SettingsDialog(config, issues=issues)
        before_rows = project_settings.svn_profiles.rowCount()
        project_settings.add_backlog_project_profiles()
        assert project_settings.svn_profiles.rowCount() > before_rows
        assert project_settings.backlog_projects(), "Loaded tickets should expose their projects"
        # A project that already has a profile is not duplicated; the user is told why.
        again = project_settings.svn_profiles.rowCount()
        original_information = QMessageBox.information
        shown = []
        QMessageBox.information = lambda *args, **_kwargs: shown.append(args[1]) or QMessageBox.StandardButton.Ok
        try:
            project_settings.add_backlog_project_profiles()
        finally:
            QMessageBox.information = original_information
        assert project_settings.svn_profiles.rowCount() == again and shown == ["Nothing to add"]
        # Rows without a root are dropped on save, so the import cannot create junk profiles.
        assert all(entry["root"] for entry in project_settings.values()["svn_profiles"])
        assert window.stop_work() is None

        event_path = root / "corrupt-events.json"
        event_path.write_text('[{"id": "bad"}]', encoding="utf-8")
        assert EventStore(event_path).all() == []
        sync = ObsidianSync(str(vault))
        ticket = dict(issues[0])
        ticket["created"] = "2026-08-03T12:34:56+09:00"
        note = sync.create_note(ticket)
        assert note.parent.name == "8-3-2026"
        original = note.read_text(encoding="utf-8") + "\nUser-owned line\n"
        note.write_text(original, encoding="utf-8")
        ticket["status"] = ticket["_availableStatuses"][1]
        sync.create_note(ticket)
        assert "User-owned line" in note.read_text(encoding="utf-8")
        analysis = normalize_analysis({
            "situation": "The core change is implemented and awaits review.",
            "previous_work": ["Added the parser."],
            "next_steps": ["Verify the display toggle."],
            "risks": ["The learner screen is not yet verified."],
            "approach": "Keep generation optional and require review before applying output.",
            "status_items": [{"requirement": "Generate readings", "current": "Implemented", "status": "Done"}],
            "work_plan": [{"task": "Verify learner screen", "done": False}],
            "implementation_flow": ["Collect missing words", "Generate structured readings", "Apply after review"],
            "design_decisions": [{"decision": "Do not auto-apply", "rationale": "Protect manual data"}],
            "changed_files": [{"file": "QuizEdit.aspx", "change": "Added editor", "reason": "Support review"}],
            "error_cases": [{"scenario": "Invalid JSON", "handling": "Show an error"}],
            "scope_notes": ["Learner view remains to be checked."],
            "verification": ["Focused parser checks passed."],
            "learning_references": [{"title": "Validated AI Application", "category": "01-Core-Concepts", "slug": "Validated-AI-Application", "summary": "Review generated data before mutation.", "principles": ["Validate structure", "Preserve manual values"], "example": "Generate a draft and apply it only after confirmation.", "when_to_use": ["AI-assisted editors"]}],
        })
        preview = sync.render_ai_note(ticket, analysis, note.read_text(encoding="utf-8"))
        assert "## AI Brief" in preview and "## 対応状況 / Requirement status" in preview
        updated_note, references, backup = sync.apply_ai_update(ticket, analysis)
        assert updated_note == note and references[0].exists() and backup and backup.exists()
        assert "User-owned line" in backup.read_text(encoding="utf-8")
        assert "Work/Programming/01-Core-Concepts/Validated-AI-Application" in updated_note.read_text(encoding="utf-8")
        environment_dialog = EnvironmentDialog("Test", {"\u691c\u8a3c": {"url": "https://verify.example.test", "username": "tester"}})
        environment_dialog.password.setText("secret")
        environment_dialog._copy_details()
        assert "Username: tester" in QApplication.clipboard().text()
        assert "Password: secret" in QApplication.clipboard().text()
        saved_passwords = []
        original_password_save = environment_dialog_module.save_environment_password
        environment_dialog_module.save_environment_password = lambda project, environment, password: saved_passwords.append((project, environment, password))
        try:
            environment_dialog._profiles["ライブ"] = {"url": "https://live.example.test", "username": "live-user"}
            environment_dialog._passwords["検証"] = "verify-secret"
            environment_dialog._passwords["ライブ"] = "live-secret"
            environment_dialog._save()
        finally:
            environment_dialog_module.save_environment_password = original_password_save
        assert ("Test", "検証", "secret") in saved_passwords
        assert ("Test", "ライブ", "live-secret") in saved_passwords
        window.ticket_detail.set_ticket(ticket, note.read_text(encoding="utf-8"), str(note))
        window.ticket_detail.set_analysis(analysis, preview)
        assert "current situation" in window.ticket_detail.brief.toPlainText().casefold()
        assert window.ticket_detail.generate.isEnabled() and not window.ticket_detail.cancel_generation.isEnabled()
        flat_analysis = normalize_analysis({
            "situation": "Ready for review.", "previous_work": [], "next_steps": [], "risks": [], "approach": "Review before applying.",
            "status_items": ["Preview\tImplemented\tDone"], "work_plan": ["[x] Add preview", "[ ] Run review"],
            "implementation_flow": [], "design_decisions": ["Require confirmation\tProtect manual values"],
            "changed_files": [], "error_cases": [], "scope_notes": [], "verification": [],
            "learning_references": ["01-Core-Concepts\tSafe AI Drafts\tReview before mutation.\tValidate; confirm\tPreview then apply.\tAI-assisted editing"],
        })
        assert flat_analysis["status_items"][0]["current"] == "Implemented"
        assert flat_analysis["work_plan"] == [{"task": "Add preview", "done": True}, {"task": "Run review", "done": False}]
        assert flat_analysis["learning_references"][0]["slug"] == "Safe-AI-Drafts"

        # The local AI transport speaks the llama.cpp OpenAI-compatible API and streams.
        BRIEF_JSON = (
            '{"situation":"Waiting for review.","previous_work":["Implemented validation."],'
            '"next_steps":["Run review."],"risks":["Coverage is unknown."]}'
        )

        class StreamResponse:
            def __init__(self, chunks, status=200, body=None):
                self.status_code = status
                self._chunks = chunks
                self._body = body or {}
                self.closed = False

            def close(self):
                self.closed = True

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise AssertionError("raise_for_status should not be reached for streamed calls")

            def json(self):
                return self._body

            def iter_lines(self, decode_unicode=False):
                for piece in self._chunks:
                    yield "data: " + dumps({"choices": [{"delta": {"content": piece}}]})
                yield "data: " + dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                yield "data: [DONE]"

        class LlamaSession:
            def __init__(self, response):
                self.payload = None
                self.url = ""
                self.response = response

            def post(self, url, json, timeout, stream=False):
                self.payload, self.url = json, url
                assert timeout == 60 and stream is True
                return self.response

            def get(self, url, timeout):
                self.url = url
                return StreamResponse([], body={
                    "model_path": "/models/Qwen3-27B.gguf", "total_slots": 4,
                    "default_generation_settings": {"n_ctx": 48128},
                })

        chunks = [BRIEF_JSON[index:index + 12] for index in range(0, len(BRIEF_JSON), 12)]
        session = LlamaSession(StreamResponse(chunks))
        client = LocalLLMClient("http://llama.test", "test-model", 60, session)
        reported = []
        brief = client.brief(ticket, on_progress=lambda count, elapsed: reported.append(count))
        assert brief["_mode"] == "brief" and brief["next_steps"] == ["Run review."]
        assert session.url == "http://llama.test/v1/chat/completions"
        assert session.payload["stream"] is True and session.payload["model"] == "test-model"
        schema = session.payload["response_format"]["json_schema"]
        assert session.payload["response_format"]["type"] == "json_schema"
        assert schema["schema"]["required"] == ["situation", "previous_work", "next_steps", "risks"]
        assert session.payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert reported, "Streaming must report progress so a slow model does not look stuck"

        # Thinking is opt-in, and the server description is read from /props.
        thinking_client = LocalLLMClient("http://llama.test", "", 60, LlamaSession(StreamResponse(chunks)), thinking=True)
        thinking_client.brief(ticket)
        info = client.health()
        assert info.model_label == "Qwen3-27B" and info.context_tokens == 48128 and info.slots == 4

        # A reply cut off by the token budget explains itself instead of raising a JSON error.
        truncated = LlamaSession(StreamResponse(['{"situation":"partial']))

        class TruncatedResponse(StreamResponse):
            def iter_lines(self, decode_unicode=False):
                yield "data: " + dumps({"choices": [{"delta": {"content": '{"situation":"partial'}}]})
                yield "data: " + dumps({"choices": [{"delta": {}, "finish_reason": "length"}]})

        truncated.response = TruncatedResponse([])
        try:
            LocalLLMClient("http://llama.test", "", 60, truncated).brief(ticket)
        except LocalLLMError as exc:
            assert "budget" in str(exc).casefold()
        else:
            raise AssertionError("A truncated reply was accepted as valid JSON")

        # Cancellation stops the stream and closes the response.
        cancel_response = StreamResponse(chunks)
        cancelled = LocalLLMClient("http://llama.test", "", 60, LlamaSession(cancel_response))
        try:
            cancelled.brief(ticket, should_cancel=lambda: True)
        except LocalLLMError as exc:
            assert "cancelled" in str(exc).casefold() and cancel_response.closed
        else:
            raise AssertionError("Cancellation was ignored")

        # A server error surfaces the server's own message.
        failing = LlamaSession(StreamResponse([], status=500, body={"error": {"message": "model not loaded"}}))
        try:
            LocalLLMClient("http://llama.test", "", 60, failing).brief(ticket)
        except LocalLLMError as exc:
            assert "model not loaded" in str(exc)
        else:
            raise AssertionError("An HTTP error was treated as success")

        # The prompt carries the ticket's saved patches so changed files are not guessed.
        patch_context = ticket_patch_context(config, issues[0]["key"])
        assert patch_context and patch_context[0]["files"], "The shelf entry should describe its files"
        context_session = LlamaSession(StreamResponse(chunks))
        LocalLLMClient("http://llama.test", "", 60, context_session).brief(ticket, patches=patch_context)
        prompt = context_session.payload["messages"][1]["content"]
        assert "SOURCE-CONTROL PATCHES" in prompt and "src/file.txt" in prompt

        # Japanese Backlog comments use the fixed template and the verified patch
        # owns the file list even if the model proposes an unrelated path.
        comment_json = dumps({
            "response_items": ["件数バッジ表示を追加"],
            "files": ["invented/file.py"],
            "detail_items": ["表示対象の件数を既存処理から取得して反映"],
        }, ensure_ascii=False)
        comment_chunks = [comment_json[index:index + 12] for index in range(0, len(comment_json), 12)]

        class Utf8ByteStreamResponse(StreamResponse):
            def iter_lines(self, decode_unicode=False):
                assert decode_unicode is False, "The transport must decode llama.cpp SSE as UTF-8 itself"
                for piece in self._chunks:
                    line = "data: " + dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False)
                    yield line.encode("utf-8")
                yield ("data: " + dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})).encode("utf-8")
                yield b"data: [DONE]"

        comment_session = LlamaSession(Utf8ByteStreamResponse(comment_chunks))
        diff_context = ticket_patch_context(config, issues[0]["key"], include_diff=True)
        assert any(item.get("diff", "").startswith("Index: src/file.txt") for item in diff_context)
        drafted = LocalLLMClient("http://llama.test", "", 60, comment_session).draft_comment(ticket, patches=diff_context)
        assert drafted["comment"].startswith("【対応内容】\n・")
        assert "\n【実施箇所】\n・" in drafted["comment"] and "・src/file.txt" in drafted["comment"]
        assert "invented/file.py" not in drafted["comment"]
        assert "\n【詳細】\n・" in drafted["comment"]
        assert "SVNパッチ" in comment_session.payload["messages"][0]["content"]
        assert format_backlog_comment([], [], []).count("（要確認）") == 3

        # A note larger than the window is trimmed rather than rejected by the server.
        trim_session = LlamaSession(StreamResponse(chunks))
        LocalLLMClient("http://llama.test", "", 60, trim_session, context_tokens=4096).brief(ticket, "x" * 500000)
        trimmed = trim_session.payload["messages"][1]["content"]
        assert "[Note truncated to fit the model context window.]" in trimmed
        assert len(trimmed) < 40000, "The prompt must fit a small context window"
        window.obsidian.load_path(note)
        assert ticket["summary"] in window.obsidian.preview.toPlainText()
        assert window.obsidian.document_stack.currentWidget() is window.obsidian.preview
        outside_note = root / "outside.md"
        outside_note.write_text("outside", encoding="utf-8")
        original_warning = QMessageBox.warning
        QMessageBox.warning = lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok
        try:
            window.obsidian.load_path(outside_note)
        finally:
            QMessageBox.warning = original_warning
        assert window.obsidian.current_path == note
        window.obsidian.copy_current_path()
        assert QApplication.clipboard().text() == note.relative_to(vault).as_posix()
        window.ticket_action("open_note", ticket)
        assert window.stack.currentWidget() is window.obsidian and window.obsidian.current_path == note

        # The rail keeps Calendar and Morning Standup last, and every grouped page
        # is still reachable and shortcut-numbered.
        assert main_window_module.PAGES[-3:] == ("Calendar", "Time Insights", "Morning Standup")
        grouped = [name for _, names in main_window_module.NAV_SECTIONS for name in names]
        assert grouped == list(main_window_module.PAGES) == list(window.nav_buttons)
        assert "Source Control" in main_window_module.PAGES
        window.set_nav_badge("Personal Tasks", 3)
        assert window._nav_text("Personal Tasks", False).endswith("3")
        assert window._nav_text("Personal Tasks", True).endswith("•")
        assert window.nav_buttons["Personal Tasks"].text() == window._nav_text("Personal Tasks", window._nav_actual_collapsed)
        window.set_nav_badge("Personal Tasks", 0)
        assert window._nav_text("Personal Tasks", False) == "Tasks"
        assert window._nav_text("Personal Tasks", True) == ""

        # Source Control is a real page, so config validation must keep it.
        saved_page_path = config_module.CONFIG_PATH
        config_module.CONFIG_PATH = root / "page.json"
        config_module.CONFIG_PATH.write_text('{"default_page": "Source Control"}', encoding="utf-8")
        assert config_module.load_config()["default_page"] == "Source Control"
        config_module.CONFIG_PATH.write_text('{"default_page": "Nowhere"}', encoding="utf-8")
        assert config_module.load_config()["default_page"] == "Command Center"
        config_module.CONFIG_PATH = saved_page_path

        # Opening a ticket note in the vault binds the AI bar to that ticket.
        window.obsidian.set_ticket(None)
        assert not window.obsidian.ai_update_note.isEnabled()
        window.obsidian.load_path(note)
        app.processEvents()
        assert window.obsidian.ticket is not None and window.obsidian.ai_update_note.isEnabled()
        assert ticket["key"] in window.obsidian.ticket_context.text()

        # The vault filter narrows the tree without touching the open note.
        window.obsidian.filter.setText("nothing-matches-this")
        assert window.obsidian.model.nameFilters() == ["*nothing-matches-this*.md"]
        window.obsidian.filter.setText("")
        assert window.obsidian.model.nameFilters() == ["*.md"]
        assert window.obsidian.current_path == note

        # Long labels elide instead of clipping mid-word or overflowing the row.
        long_ticket = dict(ticket, summary="A deliberately long ticket summary that would otherwise overflow the row")
        window.obsidian.set_ticket(long_ticket)
        assert window.obsidian.ticket_context.text().endswith("\u2026")
        assert long_ticket["summary"] in window.obsidian.ticket_context.toolTip()
        window.ticket_detail.set_ticket(long_ticket, "", "Work/Tickets/x.md", [])
        assert window.ticket_detail.title.toolTip().endswith(long_ticket["summary"])
        window.obsidian.set_ai_status("x" * 300)
        assert len(window.obsidian.ai_status.text()) < 300

        # The note path is reported relative to the vault, not as an absolute path.
        assert window._note_label(note) == note.relative_to(vault).as_posix()
        assert window._note_label(root / "elsewhere.md") == str(root / "elsewhere.md")

        # A newly selected ticket drops the previous ticket's patch caption.
        window.ticket_detail.set_patch_count(long_ticket["key"], 4)
        assert "4" in window.ticket_detail.patch_state.text()
        window.ticket_detail.set_ticket(dict(ticket, key="DEV-OTHER", issueKey="DEV-OTHER"), "", "", [])
        assert window.ticket_detail.patch_state.text() == "No patches for this ticket"
        window.ticket_detail.set_ticket(ticket, "", str(note), [])

        # Study material is generated and saved on its own: no ticket-note rewrite,
        # no Backlog post. The ticket note only gains the missing lesson link.
        lesson_json = dumps({"learning_references": [{
            "category": "02-JavaScript", "title": "Debouncing input", "slug": "debounce",
            "summary": "Collapse bursts of events into one call.", "why_it_matters": "Avoids redundant work.",
            "principles": ["Delay until quiet"], "when_to_use": ["Typeahead search"], "when_not_to_use": [],
            "prerequisites": [], "exercises": [], "lesson_path": [],
            "examples": [{"title": "Minimal", "code": "const d = fn => fn", "explanation": "Illustrative only."}],
            "alternatives": [{"name": "Throttle", "when_to_choose": "Steady updates", "pros": ["Predictable"], "cons": ["Drops input"], "example": "-"}],
        }]})
        lesson_session = LlamaSession(StreamResponse([lesson_json[i:i + 40] for i in range(0, len(lesson_json), 40)]))
        lessons = LocalLLMClient("http://llama.test", "", 60, lesson_session).lessons(ticket)
        assert lessons["_mode"] == "lessons" and len(lessons["learning_references"]) == 1
        assert "study material" in lesson_session.payload["messages"][0]["content"]

        window.ticket_detail.set_ticket(ticket, "", str(note), [])
        window.ticket_detail.set_lessons(lessons)
        assert window.ticket_detail.apply_lessons.isEnabled()
        assert not window.ticket_detail.apply.isEnabled(), "Lessons alone must not unlock the note rewrite"
        assert window.ticket_detail.tabs.tabText(window.ticket_detail.tabs.currentIndex()) == "Study material"

        note_before = note.read_text(encoding="utf-8")
        original_question = QMessageBox.question
        QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes
        try:
            window.apply_lessons(ticket, window.ticket_detail.study_material)
        finally:
            QMessageBox.question = original_question
        lesson_note = vault / "Work" / "Programming" / "02-JavaScript" / "debounce.md"
        assert lesson_note.exists() and "Debouncing input" in lesson_note.read_text(encoding="utf-8")
        note_after = note.read_text(encoding="utf-8")
        assert "[[Work/Programming/02-JavaScript/debounce|Debouncing input]]" in note_after
        assert "## Backlog Context" in note_after, "The ticket note must not be re-rendered"
        assert note_before.split("## Learning references")[0] in note_after

        # An empty lesson set is refused instead of writing an empty note.
        try:
            sync.apply_learning_references(ticket, {"learning_references": []})
        except ValueError as exc:
            assert "study material" in str(exc)
        else:
            raise AssertionError("Expected empty study material to be refused")

        # The note-only action rewrites the ticket note and nothing else.
        lessons_before = sorted(path.name for path in (vault / "Work" / "Programming").rglob("*.md"))
        note_only_analysis = {
            "situation": "Only the note should change",
            "next_steps": ["Confirm the parser fix"],
            "learning_references": [{"title": "Ignored lesson", "slug": "ignored", "category": "01-Core-Concepts"}],
        }
        cancelled = NotePreviewDialog.__dict__.get("exec")
        NotePreviewDialog.exec = lambda dialog: 0
        try:
            window._apply_note_only(ticket, note_only_analysis)
        finally:
            if cancelled is None:
                del NotePreviewDialog.exec
        assert "Only the note should change" not in note.read_text(encoding="utf-8"), (
            "Cancelling the preview must leave the note untouched"
        )
        NotePreviewDialog.exec = lambda dialog: 1
        try:
            window._apply_note_only(ticket, note_only_analysis)
        finally:
            del NotePreviewDialog.exec
        rewritten = note.read_text(encoding="utf-8")
        assert "Only the note should change" in rewritten
        assert sorted(path.name for path in (vault / "Work" / "Programming").rglob("*.md")) == lessons_before, (
            "A note-only update must never write study material"
        )
        assert not (vault / "Work" / "Programming" / "01-Core-Concepts" / "ignored.md").exists()
        history = list((vault / "Work" / "Tickets" / ".history" / ticket["key"]).glob("*.md"))
        assert history, "The previous note is backed up before it is replaced"
        assert "Debouncing input" in history[-1].read_text(encoding="utf-8")
        assert window.obsidian.ai_note_only.isEnabled()

        # Both generation paths normalize lessons through one code path.
        assert normalize_learning_references(None) == []
        assert normalize_analysis({"learning_references": lessons["learning_references"]})["learning_references"][0]["slug"] == "debounce"
        assert (vault / "Work" / "Daily" / f"{datetime.now().date().isoformat()}.md").exists()
        assert sync.create_standup_note({"date": "2026-07-14", "what_i_will_say": "Ready"}).exists()
        roster = StandupStore(root / "standup.json")
        first_id = roster.people()[0]["id"]
        roster.toggle_person(first_id)
        assert roster.people()[-1]["id"] == first_id
        cache = IssueCache(root / "issues.json")
        cache.save(issues[:2])
        assert len(cache.load()) == 2
        normalized = BacklogClient._normalize(
            {"id": 1, "issueKey": "DEV-1", "projectId": 9, "summary": "Test", "status": {"id": 1, "name": "未対応"}},
            {9: {"id": 9, "projectKey": "DEV", "name": "Development"}},
            {9: [{"id": 1, "name": "未対応"}]},
            {"id": 7, "name": "Ty"},
        )
        assert normalized["key"] == "DEV-1" and normalized["project"]["name"] == "Development"

        class Response:
            content = b"{}"
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {}

        class Session:
            def __init__(self):
                self.kwargs = None

            def request(self, *args, **kwargs):
                self.kwargs = kwargs
                return Response()

        api = BacklogClient("test-key")
        api.session = Session()
        api.update_status("DEV-1", 2)
        assert api.session.kwargs["data"] == {"statusId": 2}
        assert api.session.kwargs["params"]["apiKey"] == "test-key"

        class RejectedResponse:
            content = b'{"errors": [{"message": "Invalid status transition", "code": 7}]}'
            status_code = 400

            def raise_for_status(self):
                raise requests.HTTPError("400 url?apiKey=test-key", response=self)

            def json(self):
                return {"errors": [{"message": "Invalid status transition", "code": 7}]}

        class RejectedSession:
            def request(self, *_args, **_kwargs):
                return RejectedResponse()

        rejected_api = BacklogClient("test-key")
        rejected_api.session = RejectedSession()
        try:
            rejected_api.update_status("DEV/unsafe", 2)
        except BacklogError as exc:
            assert exc.status_code == 400 and not exc.retryable
            assert "[7] Invalid status transition" in str(exc)
            assert "test-key" not in str(exc)
        else:
            raise AssertionError("A permanent Backlog rejection was accepted")

        class SecretSession:
            def request(self, *_args, **_kwargs):
                raise requests.RequestException("Request failed for https://example.test?apiKey=do-not-leak")

        secret_api = BacklogClient("do-not-leak")
        secret_api.session = SecretSession()
        try:
            secret_api.own_user()
        except BacklogError as exc:
            assert "do-not-leak" not in str(exc) and "[redacted]" in str(exc) and exc.retryable
        else:
            raise AssertionError("Expected a redacted Backlog error")

        # Offline status retries collapse to the newest intended status; an
        # identical comment is never queued twice.
        first_status = workspace.queue_action("status", "DEV-1", 2)
        workspace.queue_action("status", "DEV-1", 3)
        workspace.queue_action("comment", "DEV-1", "same comment")
        workspace.queue_action("comment", "DEV-1", "same comment")
        queued = workspace.pending_actions_for("DEV-1")
        assert [item["value"] for item in queued if item["action"] == "status"] == [3]
        assert len([item for item in queued if item["action"] == "comment"]) == 1
        assert first_status not in workspace.pending_actions()
        for item in queued:
            workspace.remove_pending_action(item)
        window.close()
    print("expanded UI smoke ok")


if __name__ == "__main__":
    main()
