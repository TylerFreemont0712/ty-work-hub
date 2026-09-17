"""Ticket-aware SVN status, diff, and patch shelf workspace."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from modules.launcher import open_path
from modules.patch_store import PatchStore, safe_component
from modules.svn_models import (
    IGNORE_CHANGELIST, PatchArtifact, PatchFileSummary, SvnChange, SvnInfo,
    SvnWorkingCopyProfile, state_sort_key,
)
from modules.svn_service import SvnValidationError, normalize_revision
from modules.ticket_utils import issue_key, natural_key, project_name
from services.svn_workers import SvnWorker
from ui.change_tree import ChangeTree
from ui.diff_view import CompareView, DiffOutputView
from ui.patch_dialog import PatchDetailsDialog
from ui.tiered_sort import TieredSortHeader


STATE_COLORS = {
    "modified": "#4f9cf9",
    "added": "#22c55e",
    "deleted": "#ff4d6d",
    "replaced": "#a78bfa",
    "conflicted": "#f59e0b",
    "missing": "#f59e0b",
    "unversioned": "#94a3b8",
    "obstructed": "#ff4d6d",
}

USE_COLUMN, STATE_COLUMN, PATH_COLUMN = 0, 1, 2

# (sort key, column title) for the Patch Shelf, in display order.
SHELF_COLUMNS = [
    ("project", "Project"),
    ("ticket", "Ticket"),
    ("sequence", "#"),
    ("label", "Label"),
    ("group", "Group"),
    ("created", "Created"),
    ("state", "State"),
    ("files", "Files"),
    ("revision", "Revision"),
    ("patch", "Patch file"),
]

# Sensible starting widths; every column stays user-resizable.
SHELF_COLUMN_WIDTHS = {0: 110, 1: 118, 2: 38, 3: 210, 4: 104, 5: 138, 6: 84, 7: 50, 8: 72}


class ChangeItem(QTableWidgetItem):
    """Table cell that sorts on a precomputed key instead of its display text."""

    def __init__(self, text: str, sort_value):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other) -> bool:
        if isinstance(other, ChangeItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class SourceControlWidget(QWidget):
    settings_requested = pyqtSignal()
    status_message = pyqtSignal(str, int)
    preference_changed = pyqtSignal(str, object)
    patch_count_changed = pyqtSignal(str, int)

    def __init__(self, config: dict):
        super().__init__()
        self.config = dict(config)
        self.ticket: dict = {}
        self.profiles: list[SvnWorkingCopyProfile] = []
        self.changes: list[SvnChange] = []
        self.worker: SvnWorker | None = None
        self._after_worker = None
        self._after_status = ""
        self._pending_apply: PatchArtifact | None = None
        self._patch_refresh_pending = False
        self._next_sequence = 1
        self._known_groups: list[str] = []
        self._last_group = ""
        self._syncing_selection = False
        self.patch_files: list[PatchFileSummary] = []
        self.patch_artifacts: list[PatchArtifact] = []
        self._shelf_unreadable = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(10, 8, 10, 8)
        heading = QVBoxLayout()
        heading.setSpacing(0)
        title = QLabel("Source Control")
        title.setObjectName("pageTitle")
        subtitle = QLabel("SVN working copies and ticket-scoped patch shelves")
        subtitle.setObjectName("muted")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        hero_layout.addLayout(heading)
        hero_layout.addStretch()
        self.connection = QLabel("Not configured")
        self.connection.setObjectName("badge")
        hero_layout.addWidget(self.connection)
        root.addWidget(hero)

        context = QFrame()
        context.setObjectName("surface")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(8, 6, 8, 6)
        context_layout.setSpacing(5)
        first = QHBoxLayout()
        first.addWidget(QLabel("Working copy"))
        self.profile = QComboBox()
        self.profile.setMinimumWidth(180)
        self.root_path = QLineEdit()
        self.root_path.setReadOnly(True)
        self.root_path.setPlaceholderText("Configure a working-copy profile in Settings")
        self.open_root_button = QPushButton("Open")
        self.open_root_button.setProperty("secondary", True)
        self.open_root_button.setToolTip("Open this working copy in File Explorer")
        self.settings = QPushButton("Configure")
        self.settings.setProperty("secondary", True)
        first.addWidget(self.profile)
        first.addWidget(self.root_path, 1)
        first.addWidget(self.open_root_button)
        first.addWidget(self.settings)
        second = QHBoxLayout()
        second.addWidget(QLabel("Ticket"))
        self.ticket_key = QLineEdit()
        self.ticket_key.setMinimumWidth(240)
        self.ticket_key.setClearButtonEnabled(True)
        self.ticket_key.setPlaceholderText("DEV-123 or selected task")
        self.ticket_key.setToolTip("Ticket key that groups patches in the shelf")
        self.ticket_context = QLineEdit()
        self.ticket_context.setClearButtonEnabled(True)
        self.ticket_context.setPlaceholderText("Name the next patch, e.g. “validation fix”")
        self.ticket_context.setToolTip(
            "Name of the next patch you create. One ticket can hold many patches, so keep this "
            "specific to the change you are shelving rather than to the ticket."
        )
        self.next_patch_hint = QLabel("")
        self.next_patch_hint.setObjectName("badge")
        self.next_patch_hint.setToolTip("Position of the next patch within this ticket's series")
        self.next_patch_caption = QLabel("Next patch")
        second.addWidget(self.ticket_key)
        second.addWidget(self.next_patch_caption)
        second.addWidget(self.next_patch_hint)
        second.addWidget(self.ticket_context, 1)
        context_layout.addLayout(first)
        context_layout.addLayout(second)
        root.addWidget(context)

        actions = QFrame()
        actions.setObjectName("toolbarSurface")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(7, 5, 7, 5)
        action_layout.setSpacing(5)
        self.status_button = QPushButton("Show status")
        self.compare_button = QPushButton("Compare with base")
        self.log_button = QPushButton("Log")
        self.update_button = QPushButton("Update…")
        self.cleanup_button = QPushButton("Cleanup…")
        self.info_button = QPushButton("Info")
        self.more_button = QPushButton("More SVN ▾")
        self.cancel_button = QPushButton("Cancel")
        self.status_button.setToolTip("Refresh the local change list for this working copy")
        self.compare_button.setToolTip("Open the ticked files side by side against their base revision")
        self.log_button.setToolTip("Show recent repository history for this working copy")
        self.update_button.setToolTip("Update the working copy to HEAD or to a specific revision")
        self.cleanup_button.setToolTip("Run SVN cleanup when the working copy is locked by an interrupted operation")
        self.info_button.setToolTip("Show the working-copy URL, repository root, and base revision")
        for button in (self.compare_button, self.log_button, self.update_button, self.cleanup_button,
                       self.info_button, self.more_button, self.cancel_button):
            button.setProperty("secondary", True)
        self.cancel_button.setEnabled(False)
        action_layout.addWidget(self.status_button)
        action_layout.addWidget(self.compare_button)
        action_layout.addWidget(self.log_button)
        action_layout.addWidget(self.update_button)
        action_layout.addWidget(self.cleanup_button)
        action_layout.addWidget(self.info_button)
        action_layout.addStretch()
        action_layout.addWidget(self.more_button)
        action_layout.addWidget(self.cancel_button)
        root.addWidget(actions)

        self.tabs = QTabWidget()

        # ---------------------------------------------------------------- Changes
        self.changes_tab = QWidget()
        changes_layout = QVBoxLayout(self.changes_tab)
        changes_layout.setContentsMargins(5, 6, 5, 5)
        changes_layout.setSpacing(5)

        change_actions = QHBoxLayout()
        self.create_patch_button = QPushButton("Create patch")
        self.create_patch_button.setToolTip("Save a patch containing exactly the ticked files")
        self.revert_button = QPushButton("Revert selected…")
        self.revert_button.setToolTip("Discard the local changes of the ticked files after confirmation")
        self.diff_button = QPushButton("Show diff")
        self.diff_button.setToolTip("Build a unified diff of the ticked files in the Diff / Output tab")
        self.lock_button = QPushButton("Lock")
        self.lock_button.setToolTip(
            "Mark the selected rows ignore-on-commit. Locked files stay visible but are never ticked automatically."
        )
        self.unlock_button = QPushButton("Unlock")
        self.unlock_button.setToolTip("Remove the ignore-on-commit mark from the selected rows")
        for button in (self.revert_button, self.diff_button, self.lock_button, self.unlock_button):
            button.setProperty("secondary", True)
        change_actions.addWidget(self.create_patch_button)
        change_actions.addWidget(self.revert_button)
        change_actions.addWidget(self.diff_button)
        change_actions.addStretch()
        change_actions.addWidget(self.lock_button)
        change_actions.addWidget(self.unlock_button)

        summary_row = QHBoxLayout()
        self.change_summary = QLabel("Run Show status to inspect local changes")
        self.change_summary.setObjectName("muted")
        self.show_unversioned = QCheckBox("Unversioned")
        self.show_unversioned.setToolTip("Show or hide unversioned files. They can never be patched or reverted.")
        self.show_unversioned.setChecked(bool(self.config.get("svn_show_unversioned", True)))
        self.select_patchable = QPushButton("Select patchable")
        self.select_patchable.setToolTip("Tick every patchable file that is not locked")
        self.select_patchable.setProperty("secondary", True)
        self.clear_selection = QPushButton("Clear")
        self.clear_selection.setProperty("secondary", True)
        summary_row.addWidget(self.change_summary)
        summary_row.addStretch()
        summary_row.addWidget(self.show_unversioned)
        summary_row.addWidget(self.select_patchable)
        summary_row.addWidget(self.clear_selection)

        self.change_table = QTableWidget(0, 3)
        self.change_table.setHorizontalHeaderLabels(["Use", "State", "Path"])
        header = self.change_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(USE_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(STATE_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(PATH_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        # The checkbox needs no more room than its indicator, which frees width for the tree.
        self.change_table.setColumnWidth(USE_COLUMN, 38)
        self.change_table.setColumnWidth(STATE_COLUMN, 118)
        self.change_table.verticalHeader().setVisible(False)
        self.change_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.change_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.change_table.setAlternatingRowColors(True)
        self.change_table.setWordWrap(False)
        self.change_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.change_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.change_table.setSortingEnabled(True)
        self.change_table.sortItems(PATH_COLUMN, Qt.SortOrder.AscendingOrder)

        self.change_tree = ChangeTree(STATE_COLORS)
        self.change_split = QSplitter(Qt.Orientation.Horizontal)
        self.change_split.addWidget(self.change_table)
        tree_holder = QWidget()
        tree_layout = QVBoxLayout(tree_holder)
        tree_layout.setContentsMargins(6, 0, 0, 0)
        tree_layout.addWidget(self.change_tree)
        tree_holder.setMinimumWidth(160)
        self.change_split.addWidget(tree_holder)
        self.change_split.setStretchFactor(0, 4)
        self.change_split.setStretchFactor(1, 1)
        self.change_split.setSizes([780, 240])
        self.change_split.setCollapsible(0, False)
        changes_layout.addLayout(change_actions)
        changes_layout.addLayout(summary_row)
        changes_layout.addWidget(self.change_split, 1)

        self.output = DiffOutputView(self.config.get("theme", "dark"))
        self.compare = CompareView(self.config.get("theme", "dark"))

        # ------------------------------------------------------------ Patch Shelf
        self.shelf_tab = QWidget()
        shelf_layout = QVBoxLayout(self.shelf_tab)
        shelf_layout.setContentsMargins(5, 6, 5, 5)
        shelf_layout.setSpacing(5)
        shelf_top = QHBoxLayout()
        self.apply_patch_button = QPushButton("Apply patch…")
        self.apply_patch_button.setToolTip("Dry-run and preview the selected patch, then apply it to the working copy")
        self.edit_patch_button = QPushButton("Edit details…")
        self.edit_patch_button.setProperty("secondary", True)
        self.edit_patch_button.setToolTip(
            "Rename the selected patch or move it to another group. The saved files keep their names."
        )
        self.refresh_shelf_button = QPushButton("Refresh shelf")
        self.refresh_shelf_button.setProperty("secondary", True)
        self.delete_patch_button = QPushButton("Delete patch…")
        self.delete_patch_button.setProperty("secondary", True)
        self.delete_patch_button.setToolTip("Permanently remove the selected patch and its manifest after confirmation")
        self.open_shelf_button = QPushButton("Open shelf folder")
        self.open_shelf_button.setProperty("secondary", True)
        self.shelf_this_ticket = QCheckBox("This ticket only")
        self.shelf_this_ticket.setToolTip("Narrow the shelf to the ticket in the Ticket field instead of the whole shelf")
        self.shelf_this_ticket.setChecked(not bool(self.config.get("svn_shelf_all_tickets", True)))
        self.shelf_filter = QLineEdit()
        self.shelf_filter.setClearButtonEnabled(True)
        self.shelf_filter.setPlaceholderText("Filter by label, group, ticket, or file…")
        self.shelf_filter.setMinimumWidth(200)
        self.shelf_filter.setToolTip("Narrow the shelf without changing which patches it loads")
        self.shelf_summary = QLabel("Loading the patch shelf…")
        self.shelf_summary.setObjectName("muted")
        shelf_top.addWidget(self.apply_patch_button)
        shelf_top.addWidget(self.edit_patch_button)
        shelf_top.addWidget(self.delete_patch_button)
        shelf_top.addWidget(self.refresh_shelf_button)
        shelf_top.addWidget(self.open_shelf_button)
        shelf_top.addWidget(self.shelf_this_ticket)
        shelf_top.addWidget(self.shelf_filter, 1)
        shelf_top.addStretch()
        shelf_top.addWidget(self.shelf_summary)
        self.patch_table = QTableWidget(0, len(SHELF_COLUMNS))
        self.patch_table.setHorizontalHeaderLabels([title for _, title in SHELF_COLUMNS])
        self.patch_table.horizontalHeader().setSectionResizeMode(
            len(SHELF_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )
        for column, width in SHELF_COLUMN_WIDTHS.items():
            self.patch_table.setColumnWidth(column, width)
        self.patch_table.verticalHeader().setVisible(False)
        self.patch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.patch_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.patch_table.setAlternatingRowColors(True)
        self.patch_table.setWordWrap(False)
        self.patch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.patch_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shelf_sort = TieredSortHeader(self.patch_table, SHELF_COLUMNS, fallback="created", fallback_descending=True)
        self.shelf_sort.restore(self.config.get("svn_shelf_sort", []))
        hint_row = QHBoxLayout()
        self.shelf_sort_hint = QLabel("Sort: newest first")
        self.shelf_sort_hint.setToolTip("Columns are compared in this order, most important first")
        shelf_hint = QLabel(
            "Left-click a column to flip ascending / descending · right-click to cycle its priority "
            "(▲ lowest → ▲▲▲ highest)"
        )
        shelf_hint.setObjectName("muted")
        shelf_hint.setWordWrap(True)
        hint_row.addWidget(self.shelf_sort_hint)
        hint_row.addStretch()
        hint_row.addWidget(shelf_hint)
        shelf_layout.addLayout(shelf_top)
        shelf_layout.addWidget(self.patch_table, 1)
        shelf_layout.addLayout(hint_row)

        # ---------------------------------------------------------- Patch Changes
        self.patch_changes_tab = QWidget()
        patch_changes_layout = QVBoxLayout(self.patch_changes_tab)
        patch_changes_layout.setContentsMargins(5, 6, 5, 5)
        patch_changes_layout.setSpacing(5)
        self.patch_changes_summary = QLabel("Select a patch in the Patch Shelf to see what it contains")
        self.patch_changes_summary.setObjectName("muted")
        self.patch_changes_summary.setWordWrap(True)
        self.patch_change_table = QTableWidget(0, 3)
        self.patch_change_table.setHorizontalHeaderLabels(["State", "Lines", "Path"])
        patch_header = self.patch_change_table.horizontalHeader()
        patch_header.setStretchLastSection(True)
        patch_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        patch_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        patch_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        patch_header.setSectionsClickable(True)
        patch_header.setSortIndicatorShown(True)
        self.patch_change_table.setColumnWidth(0, 100)
        self.patch_change_table.setColumnWidth(1, 96)
        self.patch_change_table.verticalHeader().setVisible(False)
        self.patch_change_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.patch_change_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.patch_change_table.setAlternatingRowColors(True)
        self.patch_change_table.setWordWrap(False)
        # Read-only clarification view: this table never mutates anything.
        self.patch_change_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.patch_change_table.setSortingEnabled(True)
        self.patch_change_table.sortItems(2, Qt.SortOrder.AscendingOrder)
        self.patch_change_tree = ChangeTree(STATE_COLORS)
        self.patch_change_tree.caption.setText("Location inside the working copy")
        self.patch_change_split = QSplitter(Qt.Orientation.Horizontal)
        self.patch_change_split.addWidget(self.patch_change_table)
        patch_tree_holder = QWidget()
        patch_tree_layout = QVBoxLayout(patch_tree_holder)
        patch_tree_layout.setContentsMargins(6, 0, 0, 0)
        patch_tree_layout.addWidget(self.patch_change_tree)
        patch_tree_holder.setMinimumWidth(160)
        self.patch_change_split.addWidget(patch_tree_holder)
        self.patch_change_split.setStretchFactor(0, 4)
        self.patch_change_split.setStretchFactor(1, 1)
        self.patch_change_split.setSizes([780, 240])
        self.patch_change_split.setCollapsible(0, False)
        patch_changes_layout.addWidget(self.patch_changes_summary)
        patch_changes_layout.addWidget(self.patch_change_split, 1)

        self.tabs.addTab(self.changes_tab, "Changes")
        self.tabs.addTab(self.shelf_tab, "Patch Shelf")
        self.tabs.addTab(self.patch_changes_tab, "Patch Changes")
        self.tabs.addTab(self.compare, "Before / After")
        self.tabs.addTab(self.output, "Diff / Output")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.operation_state = QLabel("Ready")
        self.operation_state.setObjectName("muted")
        self.copy_output = QPushButton("Copy output")
        self.copy_output.setProperty("secondary", True)
        footer.addWidget(self.operation_state)
        footer.addStretch()
        footer.addWidget(self.copy_output)
        root.addLayout(footer)

        self.profile.currentIndexChanged.connect(self._profile_changed)
        self.settings.clicked.connect(self.settings_requested)
        self.open_root_button.clicked.connect(self.open_working_copy)
        self.ticket_key.textChanged.connect(self._ticket_key_changed)
        self.show_unversioned.toggled.connect(self._unversioned_toggled)
        self.change_table.itemSelectionChanged.connect(self._change_selection_changed)
        self.change_tree.path_activated.connect(self._tree_path_activated)
        self.delete_patch_button.clicked.connect(self.delete_selected_patch)
        self.shelf_this_ticket.toggled.connect(self._shelf_scope_toggled)
        self.shelf_filter.textChanged.connect(self._shelf_filter_changed)
        self.shelf_sort.changed.connect(self._shelf_sort_changed)
        self.status_button.clicked.connect(self.show_status)
        self.diff_button.clicked.connect(self.show_diff)
        self.compare_button.clicked.connect(self.compare_with_base)
        self.info_button.clicked.connect(self.show_info)
        self.log_button.clicked.connect(self.show_log)
        self.update_button.clicked.connect(self.update_working_copy)
        self.cleanup_button.clicked.connect(self.cleanup_working_copy)
        self.more_button.clicked.connect(self._show_more_menu)
        self.create_patch_button.clicked.connect(self.create_patch)
        self.apply_patch_button.clicked.connect(self.apply_selected_patch)
        self.revert_button.clicked.connect(self.revert_selected)
        self.lock_button.clicked.connect(lambda: self.set_locked(True))
        self.unlock_button.clicked.connect(lambda: self.set_locked(False))
        self.change_table.itemChanged.connect(self._change_item_changed)
        self.change_table.customContextMenuRequested.connect(self._show_change_menu)
        self.patch_change_table.itemSelectionChanged.connect(self._patch_change_selected)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.select_patchable.clicked.connect(lambda: self._set_patchable_checked(True))
        self.clear_selection.clicked.connect(lambda: self._set_patchable_checked(False))
        self.refresh_shelf_button.clicked.connect(self.refresh_patches)
        self.open_shelf_button.clicked.connect(self.open_shelf_folder)
        self.patch_table.itemSelectionChanged.connect(self.preview_selected_patch)
        self.patch_table.itemDoubleClicked.connect(lambda _item: self.edit_selected_patch())
        self.patch_table.customContextMenuRequested.connect(self._show_shelf_menu)
        self.edit_patch_button.clicked.connect(self.edit_selected_patch)
        self.copy_output.clicked.connect(lambda: QApplication.clipboard().setText(self.output.toPlainText()))
        self.update_config(config)
        self._refresh_patch_series()

    def update_config(self, config: dict) -> None:
        current = self.profile.currentData()
        current_name = current.name if isinstance(current, SvnWorkingCopyProfile) else ""
        self.config = dict(config)
        self.profiles = [SvnWorkingCopyProfile.from_dict(value) for value in self.config.get("svn_profiles", [])]
        self.profile.blockSignals(True)
        self.profile.clear()
        for profile in self.profiles:
            self.profile.addItem(profile.name, profile)
        index = next((i for i, value in enumerate(self.profiles) if value.name == current_name), 0)
        self.profile.setCurrentIndex(index if self.profiles else -1)
        self.profile.blockSignals(False)
        self.set_theme(self.config.get("theme", "dark"))
        self.show_unversioned.blockSignals(True)
        self.show_unversioned.setChecked(bool(self.config.get("svn_show_unversioned", True)))
        self.show_unversioned.blockSignals(False)
        self._select_project_profile()
        self._profile_changed()

    def set_theme(self, theme: str) -> None:
        """Re-colour the diff and compare views without rebuilding the workspace."""
        self.config["theme"] = theme
        self.output.set_theme(theme)
        self.compare.set_theme(theme)

    def set_ticket(self, ticket: dict | None) -> None:
        self.ticket = dict(ticket or {})
        key = issue_key(self.ticket)
        self.ticket_key.blockSignals(True)
        self.ticket_key.setText(key)
        self.ticket_key.blockSignals(False)
        # The label names one patch, not the ticket, so switching tickets clears it.
        self.set_patch_label("")
        self._select_project_profile()
        self._patch_refresh_pending = True
        self._refresh_patch_series()
        if self.isVisible():
            self.refresh_patches()

    def prepare_patch_context(self, ticket: dict | None = None) -> None:
        """Point the workspace at a ticket before a patch action runs.

        Called from ticket surfaces so a patch created from Ticket Detail or the
        task list lands in the right ticket's series.
        """
        if ticket:
            self.set_ticket(ticket)
        self._refresh_patch_series()

    def patch_label(self) -> str:
        """Name suggested for the next patch. Blank means "use its position"."""
        return self.ticket_context.text().strip()

    def set_patch_label(self, value: str) -> None:
        self.ticket_context.blockSignals(True)
        self.ticket_context.setText(str(value or ""))
        self.ticket_context.blockSignals(False)

    def patch_series(self) -> tuple[int, list[str]]:
        """Next position and known groups for the current ticket.

        Read from the shelf already loaded in memory so opening the patch dialog
        never touches the disk on the GUI thread. `PatchStore` assigns the real
        number when the patch is written, which is why this is only a suggestion.
        """
        key = self.ticket_key.text().strip().upper()
        mine = [item for item in self.patch_artifacts if item.ticket_key == key] if key else []
        highest = max((item.sequence for item in mine), default=0)
        groups = sorted({item.group.strip() for item in mine if item.group.strip()}, key=str.casefold)
        return max(highest, len(mine)) + 1, groups

    def _refresh_patch_series(self) -> None:
        self._next_sequence, self._known_groups = self.patch_series()
        key = self.ticket_key.text().strip().upper()
        # With no ticket there is no series, so the badge would only be noise.
        self.next_patch_caption.setVisible(bool(key))
        self.next_patch_hint.setVisible(bool(key))
        self.next_patch_hint.setText(f"#{self._next_sequence}")
        self.next_patch_hint.setToolTip(f"The next patch for {key} becomes #{self._next_sequence} in its series")

    def _select_project_profile(self) -> None:
        if not self.profiles or not self.ticket:
            return
        project = project_name(self.ticket).casefold()
        index = next((i for i, profile in enumerate(self.profiles) if profile.project and profile.project.casefold() == project), -1)
        if index >= 0 and self.profile.currentIndex() != index:
            self.profile.setCurrentIndex(index)

    def current_profile(self) -> SvnWorkingCopyProfile | None:
        value = self.profile.currentData()
        return value if isinstance(value, SvnWorkingCopyProfile) else None

    def current_root(self) -> str:
        profile = self.current_profile()
        return profile.root if profile else ""

    def current_ticket(self) -> dict:
        typed = self.ticket_key.text().strip().upper()
        if typed and typed == issue_key(self.ticket).upper():
            return dict(self.ticket)
        return {"key": typed, "summary": typed, "project": {"name": "Manual"}}

    def _profile_changed(self, *_args) -> None:
        profile = self.current_profile()
        self.root_path.setText(profile.root if profile else "")
        self.connection.setText(profile.name if profile else "Not configured")
        self.change_tree.set_root_label(Path(profile.root).name if profile else "Working copy")
        self._render_changes([])

    def _ticket_key_changed(self, *_args) -> None:
        self._patch_refresh_pending = True
        self._refresh_patch_series()
        QTimer.singleShot(350, self._refresh_patches_if_pending)

    def _unversioned_toggled(self, checked: bool) -> None:
        self.config["svn_show_unversioned"] = bool(checked)
        self.preference_changed.emit("svn_show_unversioned", bool(checked))
        self._draw_changes(preserve_checks=True)

    def _refresh_patches_if_pending(self) -> None:
        if self._patch_refresh_pending:
            self._patch_refresh_pending = False
            self.refresh_patches()

    def show_status(self) -> None:
        self.tabs.setCurrentWidget(self.changes_tab)
        self._start("status", self._base_payload())

    def show_diff(self) -> None:
        if not self.changes:
            self._after_status = "diff"
            self.show_status()
            return
        paths = self.selected_paths()
        if not paths:
            self._information("No changes selected", "Select one or more changed paths to include in the diff.")
            return
        self._start("diff", {**self._base_payload(), "paths": paths})

    def compare_with_base(self) -> None:
        """Open the side-by-side base-revision / working-copy view."""
        if not self.changes:
            self._after_status = "compare_base"
            self.show_status()
            return
        paths = [change.path for change in self._checked_changes() if change.item_status != "unversioned"]
        if not paths:
            self._information(
                "No versioned changes selected",
                "Tick one or more versioned paths in the Use column to compare them with the base revision.",
            )
            return
        self._start("compare_base", {**self._base_payload(), "paths": paths})

    def show_info(self) -> None:
        self._start("info", self._base_payload())

    def create_patch(self) -> None:
        if not self._require_ticket_key():
            return
        if not self.changes:
            self._after_status = "create_patch"
            self.show_status()
            return
        paths = self.selected_patchable_paths()
        if not paths:
            self._information("No patchable selection", "Select at least one modified, added, deleted, or replaced versioned path.")
            return
        details = self._ask_patch_details("Create patch", len(paths))
        if details is None:
            return
        self._start("create_patch", {**self._base_payload(), "paths": paths, "ticket": self.current_ticket(), **details})

    def _ask_patch_details(self, action: str, file_count: int) -> dict | None:
        """Name the patch before writing it. Returns None when the user cancels."""
        key = self.ticket_key.text().strip().upper()
        dialog = PatchDetailsDialog(
            self,
            action=action,
            ticket_key=key,
            sequence=self._next_sequence,
            file_count=file_count,
            working_copy=self.current_root(),
            suggested_label=self.patch_label(),
            known_groups=tuple(self._known_groups),
            last_group=self._last_group,
        )
        if not dialog.exec():
            return None
        details = dialog.values()
        self._last_group = details["group"]
        return details

    def shelve_changes(self) -> None:
        if not self._require_ticket_key():
            return
        if not self.changes:
            self._after_status = "shelve"
            self.show_status()
            return
        paths = self.selected_patchable_paths()
        if not paths:
            self._information("No patchable selection", "Select at least one versioned path before shelving.")
            return
        sample = "\n".join(f"• {path}" for path in paths[:12])
        if len(paths) > 12:
            sample += f"\n• … and {len(paths) - 12} more"
        answer = QMessageBox.question(
            self,
            "Shelve ticket changes",
            f"Create and verify a patch for {self.ticket_key.text().strip().upper()}, then revert exactly these {len(paths)} path(s)?\n\n"
            f"Working copy: {self.current_root()}\n\n{sample}\n\n"
            "Added versioned files will be removed after the patch is verified. Unversioned files are not included or removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        details = self._ask_patch_details("Shelve changes", len(paths))
        if details is None:
            return
        self._start("shelve", {**self._base_payload(), "paths": paths, "ticket": self.current_ticket(), **details})

    def apply_selected_patch(self) -> None:
        artifact = self.selected_artifact()
        if not artifact:
            self.tabs.setCurrentWidget(self.shelf_tab)
            self._information("Choose a patch", "Select a patch artifact from the Patch Shelf first.")
            return
        self._pending_apply = artifact
        self._start("patch_dry_run", {**self._base_payload(), "manifest_file": artifact.manifest_file})

    def revert_selected(self) -> None:
        paths = self.selected_revertible_paths()
        if not paths:
            self._information("No revertable selection", "Select one or more local SVN changes to revert.")
            return
        sample = "\n".join(f"• {path}" for path in paths[:12])
        answer = QMessageBox.warning(
            self,
            "Revert local changes",
            f"Discard the selected local changes? This action does not create a patch.\n\nWorking copy: {self.current_root()}\n\n{sample}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start("revert", {**self._base_payload(), "paths": paths})

    def edit_selected_patch(self) -> None:
        """Rename or regroup a saved patch, including ones created before labels existed."""
        artifact = self.selected_artifact()
        if not artifact:
            self.tabs.setCurrentWidget(self.shelf_tab)
            self._information("Choose a patch", "Select a patch artifact from the Patch Shelf first.")
            return
        dialog = PatchDetailsDialog(
            self,
            action="Save details",
            ticket_key=artifact.ticket_key,
            sequence=artifact.sequence or self._next_sequence,
            file_count=len(artifact.affected_paths),
            working_copy=artifact.working_copy_root,
            suggested_label=artifact.display_label,
            known_groups=tuple(self._shelf_groups()),
            last_group=artifact.group,
            note=artifact.note,
        )
        if not dialog.exec():
            return
        details = dialog.values()
        self._last_group = details["group"]
        self._start("update_patch", {"manifest_file": artifact.manifest_file, **details})

    def set_selected_patch_group(self) -> None:
        """Fast path for organising an existing shelf: change only the group."""
        artifact = self.selected_artifact()
        if not artifact:
            self._information("Choose a patch", "Select a patch artifact from the Patch Shelf first.")
            return
        groups = self._shelf_groups()
        current = artifact.group
        options = [""] + [name for name in groups if name.casefold() != current.casefold()]
        if current:
            options.insert(1, current)
        value, accepted = QInputDialog.getItem(
            self, "Group patch", f"Group for {artifact.headline}:", options,
            options.index(current) if current in options else 0, True,
        )
        if not accepted:
            return
        self._last_group = str(value).strip()
        self._start("update_patch", {
            "manifest_file": artifact.manifest_file,
            "label": artifact.display_label,
            "group": self._last_group,
            "note": artifact.note,
        })

    def renumber_ticket_patches(self) -> None:
        """Number every patch of the current ticket in creation order."""
        key = self.ticket_key.text().strip().upper()
        if not key:
            self._information("Ticket key needed", "Enter a ticket key before renumbering its patch series.")
            return
        answer = QMessageBox.question(
            self,
            "Renumber patch series",
            f"Give every patch of {key} a position in creation order?\n\n"
            "Only the shelf manifests change; patch contents and file names stay as they are.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start("renumber_patches", {"ticket_key": key})

    def _shelf_groups(self) -> list[str]:
        """Every group name in the loaded shelf, so regrouping can reuse them."""
        names = {artifact.group.strip() for artifact in self.patch_artifacts if artifact.group.strip()}
        return sorted(names, key=str.casefold)

    def _show_shelf_menu(self, position) -> None:
        artifact = self.selected_artifact()
        menu = QMenu(self)
        edit = menu.addAction("Edit details…", self.edit_selected_patch)
        group = menu.addAction("Set group…", self.set_selected_patch_group)
        menu.addSeparator()
        apply_action = menu.addAction("Apply patch…", self.apply_selected_patch)
        delete = menu.addAction("Delete patch…", self.delete_selected_patch)
        menu.addSeparator()
        menu.addAction("Renumber this ticket's patches…", self.renumber_ticket_patches)
        menu.addAction("Open shelf folder", self.open_shelf_folder)
        for action in (edit, group, apply_action, delete):
            action.setEnabled(bool(artifact) and not self.worker)
        menu.exec(self.patch_table.viewport().mapToGlobal(position))

    def delete_selected_patch(self) -> None:
        artifact = self.selected_artifact()
        if not artifact:
            self.tabs.setCurrentWidget(self.shelf_tab)
            self._information("Choose a patch", "Select a patch artifact from the Patch Shelf first.")
            return
        shelved = artifact.state in {"shelved", "revert_failed"}
        caution = (
            "\n\nThis patch is still SHELVED. Its changes were reverted from the working copy, "
            "so deleting it discards the only copy of that work."
            if shelved else ""
        )
        answer = QMessageBox.warning(
            self,
            "Delete patch",
            f"Permanently delete this patch and its manifest?\n\n"
            f"Patch: {artifact.headline}\nFile: {Path(artifact.patch_file).name}\n"
            f"State: {artifact.state}\nFiles: {len(artifact.affected_paths)}\n"
            f"Created: {artifact.created_at}{caution}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start("delete_patch", {"manifest_file": artifact.manifest_file})

    def refresh_patches(self) -> None:
        """Load the shelf. By default that is every project and ticket folder."""
        key = self.ticket_key.text().strip().upper()
        ticket_only = self.shelf_this_ticket.isChecked()
        if ticket_only and not key:
            self._render_patches([])
            self.patch_count_changed.emit("", 0)
            return
        if self.worker:
            self._patch_refresh_pending = True
            return
        self._patch_refresh_pending = False
        self._start("list_patches", {"ticket_key": key, "scope": "ticket" if ticket_only else "all"})

    def _shelf_scope_toggled(self, checked: bool) -> None:
        self.config["svn_shelf_all_tickets"] = not bool(checked)
        self.preference_changed.emit("svn_shelf_all_tickets", not bool(checked))
        self.refresh_patches()

    def _shelf_filter_changed(self, *_args) -> None:
        self._render_patches(self.patch_artifacts, refreshed=False)

    def _shelf_sort_changed(self) -> None:
        state = self.shelf_sort.state()
        self.config["svn_shelf_sort"] = state
        self.preference_changed.emit("svn_shelf_sort", state)
        self._render_patches(self.patch_artifacts, refreshed=False)

    def preview_selected_patch(self) -> None:
        """Read the selected patch so Patch Changes and Diff / Output both refresh."""
        artifact = self.selected_artifact()
        if artifact and not self.worker:
            self._start("read_patch", {"manifest_file": artifact.manifest_file})

    def selected_paths(self) -> list[str]:
        values = []
        for row in range(self.change_table.rowCount()):
            check = self.change_table.item(row, 0)
            change = check.data(Qt.ItemDataRole.UserRole) if check else None
            if isinstance(change, SvnChange) and check.checkState() == Qt.CheckState.Checked:
                values.append(change.path)
        return values

    def selected_patchable_paths(self) -> list[str]:
        return [change.path for change in self._checked_changes() if change.patchable]

    def selected_revertible_paths(self) -> list[str]:
        return [change.path for change in self._checked_changes() if change.revertible]

    def _checked_changes(self) -> list[SvnChange]:
        values = []
        for row in range(self.change_table.rowCount()):
            item = self.change_table.item(row, 0)
            change = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(change, SvnChange) and item.checkState() == Qt.CheckState.Checked:
                values.append(change)
        return values

    def selected_artifact(self) -> PatchArtifact | None:
        row = self.patch_table.currentRow()
        item = self.patch_table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, PatchArtifact) else None

    def cancel_operation(self) -> None:
        if self.worker:
            self.operation_state.setText("Cancelling…")
            self.worker.cancel()

    def open_shelf_folder(self) -> None:
        try:
            store = PatchStore(self.config.get("svn_patch_root", ""))
            store.root.mkdir(parents=True, exist_ok=True)
            open_path(store.root)
        except (OSError, ValueError) as exc:
            self._warning("Could not open patch shelf", str(exc))

    def open_working_copy(self) -> None:
        root = self.current_root()
        if root:
            try:
                open_path(Path(root))
            except OSError as exc:
                self._warning("Could not open working copy", str(exc))

    def _show_more_menu(self) -> None:
        """Secondary actions only: the shelve workflow and TortoiseSVN dialogs."""
        menu = QMenu(self)
        menu.addAction("Shelve changes…", self.shelve_changes)
        menu.addAction("Unshelve (apply selected patch)…", self.apply_selected_patch)
        menu.addSeparator()
        tortoise = menu.addMenu("Open in TortoiseSVN")
        tortoise.addAction("Check for modifications", lambda: self._launch_tortoise("repostatus"))
        tortoise.addAction("Commit…", lambda: self._launch_tortoise("commit", confirm=True))
        tortoise.addAction("Update…", lambda: self._launch_tortoise("update", confirm=True))
        tortoise.addAction("Log", lambda: self._launch_tortoise("log"))
        tortoise.addAction("Revision graph", lambda: self._launch_tortoise("revisiongraph"))
        tortoise.addAction("Repository browser", lambda: self._launch_tortoise("repobrowser"))
        tortoise.addAction("Diff selected", lambda: self._launch_tortoise("diff", selected=True))
        tortoise.addAction("Blame selected", lambda: self._launch_tortoise("blame", selected=True))
        tortoise.addSeparator()
        tortoise.addAction("Switch working copy…", lambda: self._launch_tortoise("switch", confirm=True))
        tortoise.addAction("Merge…", lambda: self._launch_tortoise("merge", confirm=True))
        tortoise.addAction("Create TortoiseSVN patch…", lambda: self._launch_tortoise("createpatch", confirm=True))
        tortoise.addAction("Properties…", lambda: self._launch_tortoise("properties", confirm=True))
        tortoise.addAction("Lock selected…", lambda: self._launch_tortoise("lock", selected=True, confirm=True))
        tortoise.addAction("Unlock selected…", lambda: self._launch_tortoise("unlock", selected=True, confirm=True))
        tortoise.addAction("Resolve selected…", lambda: self._launch_tortoise("resolve", selected=True, confirm=True))
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def _show_change_menu(self, position) -> None:
        """Per-file actions for the rows the user has highlighted."""
        selected = self._selected_table_paths()
        if not selected:
            return
        menu = QMenu(self)
        locked = [change for change in self._selected_table_changes() if change.locked]
        if len(locked) < len(selected):
            menu.addAction(f"Lock {len(selected)} path(s) (ignore-on-commit)", lambda: self.set_locked(True))
        if locked:
            menu.addAction(f"Unlock {len(locked)} path(s)", lambda: self.set_locked(False))
        menu.addSeparator()
        menu.addAction("Blame this file", self._blame_selected)
        menu.addAction("Mark conflicts resolved…", self._resolve_selected)
        menu.addSeparator()
        menu.addAction("Diff in TortoiseSVN", lambda: self._launch_tortoise("diff", selected=True))
        menu.addAction("Copy path", lambda: QApplication.clipboard().setText("\n".join(selected)))
        menu.exec(self.change_table.viewport().mapToGlobal(position))

    def _blame_selected(self) -> None:
        selected = self._selected_table_paths()
        if len(selected) != 1:
            self._information("Choose one file", "Select exactly one changed file row for SVN blame.")
            return
        self._start("blame", {**self._base_payload(), "paths": selected})

    def show_log(self) -> None:
        self._start("log", self._base_payload())

    def update_working_copy(self) -> None:
        """Update to HEAD or to a revision the user names, after confirmation."""
        root = self.current_root()
        if not root:
            self._base_payload()
            return
        revision, accepted = QInputDialog.getText(
            self, "Update to revision",
            "Revision to update to (HEAD, or a revision number):",
            QLineEdit.EchoMode.Normal, "HEAD",
        )
        if not accepted:
            return
        try:
            target = normalize_revision(revision)
        except SvnValidationError as exc:
            self._warning("Unsupported revision", str(exc))
            return
        if self._confirm_mutation("Update working copy", f"Update to revision {target} in", root):
            self._start("update", {**self._base_payload(), "revision": target})

    def cleanup_working_copy(self) -> None:
        if self._confirm_mutation("Cleanup working copy", "Run SVN cleanup on", self.current_root()):
            self._start("cleanup", self._base_payload())

    def set_locked(self, locked: bool) -> None:
        """Add or remove the ignore-on-commit label on the highlighted rows.

        This only labels files in the working copy; no content is touched, and
        TortoiseSVN reads the same label.
        """
        changes = self._selected_table_changes()
        paths = [change.path for change in changes if not change.locked] if locked else [
            change.path for change in changes if change.locked
        ]
        if not paths:
            self._information(
                "Nothing to change",
                "Highlight one or more rows to lock." if locked else "Highlight one or more locked rows to unlock.",
            )
            return
        self._start("changelist", {
            **self._base_payload(),
            "paths": paths,
            "changelist": IGNORE_CHANGELIST if locked else "",
        })

    def _resolve_selected(self) -> None:
        paths = [change.path for change in self._checked_changes() if change.tree_conflicted or change.item_status == "conflicted" or change.property_status == "conflicted"]
        if not paths:
            self._information("No conflicts selected", "Select conflicted paths before marking them resolved.")
            return
        choice, accepted = QInputDialog.getItem(self, "Resolve conflicts", "Accept version", ["working", "mine-full", "theirs-full", "base"], 0, False)
        if accepted and self._confirm_mutation("Mark conflicts resolved", f"Accept “{choice}” for {len(paths)} selected path(s) in", self.current_root()):
            self._start("resolve", {**self._base_payload(), "paths": paths, "accept": choice})

    def _launch_tortoise(self, command: str, selected: bool = False, confirm: bool = False) -> None:
        path = ""
        if selected:
            paths = self._selected_table_paths()
            if len(paths) != 1:
                self._information("Choose one path", "Select exactly one changed path for this TortoiseSVN action.")
                return
            path = paths[0]
        if confirm and not self._confirm_mutation(f"Open TortoiseSVN {command}", f"Open the {command} workflow for", path or self.current_root()):
            return
        self._start("tortoise", {**self._base_payload(), "command": command, "path": path})

    def _selected_table_changes(self) -> list[SvnChange]:
        rows = sorted({index.row() for index in self.change_table.selectionModel().selectedRows()})
        values = []
        for row in rows:
            item = self.change_table.item(row, USE_COLUMN)
            change = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(change, SvnChange):
                values.append(change)
        return values

    def _selected_table_paths(self) -> list[str]:
        rows = sorted({index.row() for index in self.change_table.selectionModel().selectedRows()})
        values = []
        for row in rows:
            item = self.change_table.item(row, 0)
            change = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(change, SvnChange):
                values.append(change.path)
        return values

    def _base_payload(self) -> dict:
        profile = self.current_profile()
        if not profile:
            self._warning("Source control is not configured", "Add a working-copy profile in Settings > Source Control.")
            return {"root": "", "profile_name": ""}
        return {"root": profile.root, "profile_name": profile.name}

    def _start(self, action: str, payload: dict) -> None:
        if self.worker:
            self.status_message.emit("An SVN operation is already running.", 3500)
            return
        if action not in {"list_patches", "read_patch", "delete_patch", "update_patch", "renumber_patches"} and not str(payload.get("root", "")).strip():
            return
        worker = SvnWorker(action, self.config, payload)
        self.worker = worker
        self._set_busy(True, f"Starting {action.replace('_', ' ')}…")
        worker.progress.connect(self.operation_state.setText)
        worker.completed.connect(self._operation_completed)
        worker.failed.connect(self._operation_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._active_worker_finished)
        worker.start()

    def _active_worker_finished(self) -> None:
        self._worker_finished(self.sender())

    def _worker_finished(self, worker: SvnWorker) -> None:
        if self.worker is worker:
            self.worker = None
        self._set_busy(False, self.operation_state.text())
        callback = self._after_worker
        self._after_worker = None
        if callback:
            QTimer.singleShot(0, callback)
        elif self._patch_refresh_pending:
            self._patch_refresh_pending = False
            QTimer.singleShot(0, self.refresh_patches)

    def _operation_completed(self, action: str, result: dict) -> None:
        command = result.get("command")
        if action == "status":
            self.changes = list(result.get("changes", []))
            self._render_changes(self.changes)
            self._show_info(result.get("info"))
            self.tabs.setCurrentWidget(self.changes_tab)
            self.operation_state.setText(f"Status ready · {len(self.changes)} local change(s)")
            followup = self._after_status
            self._after_status = ""
            if followup:
                callbacks = {"diff": self.show_diff, "compare_base": self.compare_with_base, "create_patch": self.create_patch, "shelve": self.shelve_changes}
                self._after_worker = callbacks.get(followup)
        elif action == "compare_base":
            files = [item for item in result.get("files", []) if isinstance(item, dict)]
            self.compare.set_files(files, int(result.get("skipped", 0) or 0))
            self._show_command_output(command, focus=False)
            self.tabs.setCurrentWidget(self.compare)
            self.operation_state.setText(f"Compared {len(files)} file(s) with the base revision")
        elif action in {"diff", "log", "blame", "info"}:
            if action == "info":
                self._show_info(result.get("info"), output=True)
            else:
                self._show_command_output(command)
            self.operation_state.setText(f"{action.replace('_', ' ').title()} complete")
        elif action in {"create_patch", "shelve"}:
            artifact = result.get("artifact")
            self._show_command_output(result.get("patch_command") or command)
            if isinstance(artifact, PatchArtifact):
                self.output.appendPlainText(f"\nPatch artifact: {artifact.patch_file}\nState: {artifact.state}")
                for warning in artifact.warnings:
                    self.output.appendPlainText(f"Warning: {warning}")
            if isinstance(artifact, PatchArtifact):
                self.operation_state.setText(
                    f"{'Patch created' if action == 'create_patch' else 'Changes shelved'} · {artifact.headline}"
                )
            else:
                self.operation_state.setText("Patch created" if action == "create_patch" else "Changes shelved")
            # The name described the patch that was just written, not the next one.
            self.set_patch_label("")
            self._patch_refresh_pending = True
            if action == "shelve":
                self._after_worker = self.show_status
        elif action == "list_patches":
            artifacts = list(result.get("artifacts", []))
            self._shelf_unreadable = int(result.get("unreadable", 0) or 0)
            self._render_patches(artifacts)
            # The badge always counts the selected ticket, whatever the shelf scope is.
            key = self.ticket_key.text().strip().upper()
            self.patch_count_changed.emit(key, sum(1 for item in artifacts if item.ticket_key == key) if key else 0)
            self.operation_state.setText(f"Patch shelf ready · {len(artifacts)} artifact(s)")
            if self._shelf_unreadable:
                self.status_message.emit(
                    f"{self._shelf_unreadable} patch manifest(s) in the shelf could not be read.", 8000
                )
            if artifacts:
                # _render_patches selects the first row with signals blocked, so read it
                # explicitly; otherwise Patch Changes stays empty until another row is clicked.
                self._after_worker = self.preview_selected_patch
        elif action == "update_patch":
            artifact = result.get("artifact")
            if isinstance(artifact, PatchArtifact):
                self.operation_state.setText(f"Patch details saved · {artifact.headline}")
                self._replace_artifact(artifact)
            self._patch_refresh_pending = True
        elif action == "renumber_patches":
            artifacts = [item for item in result.get("artifacts", []) if isinstance(item, PatchArtifact)]
            self.operation_state.setText(
                f"Renumbered {len(artifacts)} patch(es) for {result.get('ticket_key', 'this ticket')}"
            )
            for artifact in artifacts:
                self._replace_artifact(artifact, render=False)
            self._render_patches(self.patch_artifacts, refreshed=False)
            self._patch_refresh_pending = True
        elif action == "delete_patch":
            artifact = result.get("artifact")
            name = Path(artifact.patch_file).name if isinstance(artifact, PatchArtifact) else "patch"
            removed = "\n".join(str(value) for value in result.get("removed", []))
            self.output.setPlainText(f"Deleted patch: {name}\n\n{removed}")
            self.operation_state.setText(f"Deleted {name}")
            self._patch_refresh_pending = True
        elif action == "read_patch":
            artifact = result.get("artifact")
            self.output.setPlainText(self._artifact_header(artifact) + "\n\n" + str(result.get("content", "")))
            self._render_patch_changes(
                artifact if isinstance(artifact, PatchArtifact) else None,
                [item for item in result.get("files", []) if isinstance(item, PatchFileSummary)],
            )
            self.operation_state.setText("Patch integrity verified")
        elif action == "patch_dry_run":
            self._show_command_output(command)
            self.operation_state.setText("Patch preflight complete")
            self._after_worker = lambda data=result: self._confirm_patch_apply(data)
        elif action == "apply_patch":
            self._show_command_output(command)
            artifact = result.get("artifact")
            conflicts = bool(result.get("conflicts"))
            self.operation_state.setText("Patch applied with conflicts" if conflicts else "Patch applied successfully")
            if isinstance(artifact, PatchArtifact):
                self.output.appendPlainText(f"\nArtifact state: {artifact.state}")
            self._patch_refresh_pending = True
            self._after_worker = self.show_status
        elif action == "changelist":
            locked = bool(result.get("changelist"))
            self._show_command_output(command, focus=False)
            self.operation_state.setText("Paths locked (ignore-on-commit)" if locked else "Paths unlocked")
            self._after_worker = self.show_status
        elif action in {"revert", "update", "cleanup", "resolve"}:
            self._show_command_output(command)
            if action == "update":
                self.operation_state.setText(f"Updated to revision {result.get('revision', 'HEAD')}")
            else:
                self.operation_state.setText(f"{action.title()} complete")
            self._after_worker = self.show_status
        elif action == "tortoise":
            self._show_command_output(command)
            self.operation_state.setText("TortoiseSVN opened")
        self.status_message.emit(self.operation_state.text(), 5000)

    def _operation_failed(self, action: str, message: str) -> None:
        self.operation_state.setText(f"{action.replace('_', ' ').title()} failed")
        self.output.setPlainText(message)
        self.tabs.setCurrentWidget(self.output)
        if action in {"shelve", "apply_patch"}:
            self._patch_refresh_pending = True
        self._warning("SVN operation failed", message)

    def _confirm_patch_apply(self, result: dict) -> None:
        artifact = result.get("artifact") or self._pending_apply
        if not isinstance(artifact, PatchArtifact):
            return
        conflicts = bool(result.get("conflicts"))
        warning = "\n\nThe dry run reported conflicts. Apply only if you are ready to resolve them." if conflicts else ""
        answer = QMessageBox.question(
            self,
            "Apply ticket patch",
            f"Apply this verified patch to the recorded working copy?\n\n"
            f"Patch: {artifact.headline}\nFile: {Path(artifact.patch_file).name}\n"
            f"Working copy: {artifact.working_copy_root}\nFiles: {len(artifact.affected_paths)}\nBase revision: {artifact.base_revision or 'unknown'}"
            f"{warning}\n\nThe patch will remain in the shelf and no commit or update will run automatically.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start("apply_patch", {**self._base_payload(), "manifest_file": artifact.manifest_file})

    def _render_changes(self, changes: list[SvnChange]) -> None:
        self.changes = list(changes)
        self._draw_changes()

    def _draw_changes(self, preserve_checks: bool = False) -> None:
        """Fill the table and the location tree from the current filter."""
        keep = {change.path for change in self._checked_changes()} if preserve_checks else None
        visible = self.visible_changes()
        table = self.change_table
        # Rebuilding rows emits selection changes; do not chase them into the tree.
        self._syncing_selection = True
        table.setSortingEnabled(False)
        table.clearContents()
        table.setRowCount(len(visible))
        muted = QColor("#94a3b8")
        for row, change in enumerate(visible):
            use = ChangeItem("", (int(change.locked), 0 if change.patchable else 1, change.sort_key))
            use.setData(Qt.ItemDataRole.UserRole, change)
            use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # A locked file is never ticked automatically; the user can still tick it by hand.
            checked = (change.patchable and not change.locked) if keep is None else change.path in keep
            use.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            use.setToolTip(
                "Locked (ignore-on-commit): never selected automatically"
                if change.locked else "Include this path in patch and revert actions"
            )
            state = ChangeItem(self._state_text(change), state_sort_key(change))
            state.setForeground(muted if change.locked else QColor(STATE_COLORS.get(change.item_status, "#94a3b8")))
            state.setToolTip(self._state_tooltip(change))
            path = ChangeItem(change.path, (int(change.locked), change.sort_key))
            path.setToolTip(change.path)
            if change.locked:
                for item in (state, path):
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                path.setForeground(muted)
            table.setItem(row, USE_COLUMN, use)
            table.setItem(row, STATE_COLUMN, state)
            table.setItem(row, PATH_COLUMN, path)
        table.setSortingEnabled(True)
        self._syncing_selection = False
        self.change_tree.set_changes(visible)
        self._update_change_summary(visible)

    def visible_changes(self) -> list[SvnChange]:
        if self.show_unversioned.isChecked():
            return list(self.changes)
        return [change for change in self.changes if change.item_status != "unversioned"]

    def _update_change_summary(self, visible: list[SvnChange]) -> None:
        patchable = sum(change.patchable for change in visible)
        unversioned = sum(change.item_status == "unversioned" for change in self.changes)
        locked = sum(change.locked for change in self.changes)
        parts = [f"{len(visible)} local change(s)", f"{patchable} patchable"]
        if unversioned and self.show_unversioned.isChecked():
            parts.append(f"{unversioned} unversioned (not patchable)")
        elif unversioned:
            parts.append(f"{unversioned} unversioned hidden")
        if locked:
            parts.append(f"{locked} locked")
        self.change_summary.setText(" · ".join(parts))
        self._update_selection_counts()

    def _change_item_changed(self, _item) -> None:
        if not self._syncing_selection:
            self._update_selection_counts()

    def _update_selection_counts(self) -> None:
        """Keep the action labels honest about how many files they will touch."""
        checked = self._checked_changes()
        patchable = sum(change.patchable for change in checked)
        revertible = sum(change.revertible for change in checked)
        self.create_patch_button.setText(f"Create patch ({patchable})" if patchable else "Create patch")
        self.create_patch_button.setEnabled(bool(patchable) and not self.worker)
        self.revert_button.setText(f"Revert selected ({revertible})…" if revertible else "Revert selected…")
        self.diff_button.setEnabled(bool(checked) and not self.worker)

    @staticmethod
    def _state_text(change: SvnChange) -> str:
        label = change.display_status
        if change.property_status not in {"none", "normal"}:
            label += " +props"
        return f"{label} · locked" if change.locked else label

    @staticmethod
    def _state_tooltip(change: SvnChange) -> str:
        lines = [f"Item status: {change.item_status}", f"Property status: {change.property_status}"]
        if change.revision:
            lines.append(f"Revision: {change.revision}")
        if change.tree_conflicted:
            lines.append("Tree conflict")
        if change.copied:
            lines.append("Copied")
        if change.switched:
            lines.append("Switched")
        if change.changelist:
            lines.append(f"Changelist: {change.changelist}")
        if change.locked:
            lines.append("Locked: never ticked automatically")
        if not change.patchable:
            lines.append("Not representable in an SVN text patch")
        return "\n".join(lines)

    def _change_selection_changed(self) -> None:
        if self._syncing_selection:
            return
        paths = self._selected_table_paths()
        if len(paths) != 1:
            return
        self._syncing_selection = True
        self.change_tree.select_path(paths[0])
        self._syncing_selection = False

    def _tree_path_activated(self, path: str) -> None:
        if self._syncing_selection:
            return
        for row in range(self.change_table.rowCount()):
            item = self.change_table.item(row, USE_COLUMN)
            change = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(change, SvnChange) and change.path == path:
                self._syncing_selection = True
                self.change_table.selectRow(row)
                self.change_table.scrollToItem(self.change_table.item(row, PATH_COLUMN))
                self._syncing_selection = False
                return

    def _render_patches(self, artifacts: list[PatchArtifact], refreshed: bool = True) -> None:
        """Draw the shelf in the order the tiered header asks for."""
        if refreshed:
            self.patch_artifacts = list(artifacts)
        previous = self.selected_artifact()
        needle = self.shelf_filter.text().strip()
        visible = [artifact for artifact in self.patch_artifacts if artifact.matches(needle)]
        ordered = self.shelf_sort.apply(visible, self._shelf_sort_value)
        self.patch_table.blockSignals(True)
        self.patch_table.clearContents()
        self.patch_table.setRowCount(len(ordered))
        for row, artifact in enumerate(ordered):
            values = {
                "project": safe_component(artifact.project, "Manual"),
                "ticket": safe_component(artifact.ticket_key, "MANUAL"),
                "sequence": artifact.series or "—",
                "label": artifact.display_label,
                "group": artifact.group or "—",
                "created": artifact.created_at.replace("T", " ")[:19],
                "state": artifact.state.replace("_", " ").title(),
                "files": str(len(artifact.affected_paths)),
                "revision": artifact.base_revision or "—",
                "patch": Path(artifact.patch_file).name,
            }
            for column, (key, _title) in enumerate(SHELF_COLUMNS):
                item = QTableWidgetItem(values[key])
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, artifact)
                    item.setToolTip(
                        f"Shelf folder: {values['project']}/{values['ticket']}\n\n{self._artifact_header(artifact)}"
                    )
                else:
                    item.setToolTip(self._artifact_header(artifact))
                self.patch_table.setItem(row, column, item)
        self._select_shelf_row(ordered, previous)
        self.patch_table.blockSignals(False)
        self._refresh_patch_series()
        self._update_shelf_summary(ordered)
        self.apply_patch_button.setEnabled(bool(ordered) and not self.worker)
        self.delete_patch_button.setEnabled(bool(ordered) and not self.worker)
        self.edit_patch_button.setEnabled(bool(ordered) and not self.worker)
        if not ordered:
            self._render_patch_changes(None, [])

    def _replace_artifact(self, artifact: PatchArtifact, render: bool = True) -> None:
        """Swap one artifact in the loaded shelf so the table reflects an edit at once.

        Matched on the manifest path rather than the artifact id: manifests
        written before ids existed all share an empty one.
        """
        self.patch_artifacts = [
            artifact if item.manifest_file == artifact.manifest_file else item
            for item in self.patch_artifacts
        ]
        if render:
            self._render_patches(self.patch_artifacts, refreshed=False)

    def _select_shelf_row(self, ordered: list[PatchArtifact], previous: PatchArtifact | None) -> None:
        """Keep the chosen patch selected, else prefer one for the current ticket."""
        if not ordered:
            return
        key = self.ticket_key.text().strip().upper()
        target = 0
        if isinstance(previous, PatchArtifact):
            target = next(
                (index for index, item in enumerate(ordered) if item.artifact_id == previous.artifact_id), -1
            )
        if target < 0 or not isinstance(previous, PatchArtifact):
            target = next((index for index, item in enumerate(ordered) if item.ticket_key == key), 0) if key else 0
        self.patch_table.selectRow(target)

    def _update_shelf_summary(self, ordered: list[PatchArtifact]) -> None:
        key = self.ticket_key.text().strip().upper()
        mine = sum(1 for artifact in ordered if artifact.ticket_key == key) if key else 0
        scope = "this ticket" if self.shelf_this_ticket.isChecked() else "whole shelf"
        parts = [f"{len(ordered)} patch artifact(s) · {scope}"]
        needle = self.shelf_filter.text().strip()
        if needle:
            parts.append(f"filtered by “{needle}” from {len(self.patch_artifacts)}")
        if key and not self.shelf_this_ticket.isChecked():
            parts.append(f"{mine} for {key}")
        if self._shelf_unreadable:
            parts.append(f"{self._shelf_unreadable} manifest(s) unreadable")
        self.shelf_summary.setText(" · ".join(parts))
        self.shelf_sort_hint.setText(f"Sort: {self.shelf_sort.describe()}")

    @staticmethod
    def _shelf_sort_value(artifact: PatchArtifact, key: str):
        """Comparable value per shelf column. Every column keeps one value type."""
        if key == "project":
            return natural_key(artifact.project)
        if key == "ticket":
            return natural_key(artifact.ticket_key)
        if key == "sequence":
            return artifact.sequence
        if key == "label":
            return natural_key(artifact.display_label)
        if key == "group":
            # Ungrouped patches sort after every named group instead of first.
            return natural_key(artifact.group or "￿")
        if key == "state":
            return artifact.state.casefold()
        if key == "files":
            return len(artifact.affected_paths)
        if key == "revision":
            revision = str(artifact.base_revision or "").strip()
            return int(revision) if revision.isdigit() else -1
        if key == "patch":
            return Path(artifact.patch_file).name.casefold()
        return artifact.created_at

    def _render_patch_changes(self, artifact: PatchArtifact | None, files: list[PatchFileSummary]) -> None:
        """Show what a saved patch contains. Read-only: nothing here mutates state."""
        self.patch_files = list(files)
        table = self.patch_change_table
        table.setSortingEnabled(False)
        table.clearContents()
        table.setRowCount(len(self.patch_files))
        for row, summary in enumerate(self.patch_files):
            change = summary.to_change()
            state = ChangeItem(change.display_status, state_sort_key(change))
            state.setForeground(QColor(STATE_COLORS.get(change.item_status, "#94a3b8")))
            lines = ChangeItem(summary.line_summary, (summary.added_lines + summary.removed_lines, change.sort_key))
            path = ChangeItem(summary.path, change.sort_key)
            path.setToolTip(summary.path)
            for column, item in enumerate((state, lines, path)):
                item.setData(Qt.ItemDataRole.UserRole, summary)
                table.setItem(row, column, item)
        table.setSortingEnabled(True)
        self.patch_change_tree.set_changes([summary.to_change() for summary in self.patch_files])
        if not isinstance(artifact, PatchArtifact):
            self.patch_changes_summary.setText("Select a patch in the Patch Shelf to see what it contains")
            self.patch_change_tree.set_root_label("Working copy")
            return
        self.patch_change_tree.set_root_label(Path(artifact.working_copy_root).name or "Working copy")
        added = sum(summary.operation == "added" for summary in self.patch_files)
        deleted = sum(summary.operation == "deleted" for summary in self.patch_files)
        modified = len(self.patch_files) - added - deleted
        total_added = sum(summary.added_lines for summary in self.patch_files)
        total_removed = sum(summary.removed_lines for summary in self.patch_files)
        note = f"\n{artifact.note}" if artifact.note else ""
        self.patch_changes_summary.setText(
            f"{artifact.headline} · state {artifact.state} · "
            f"base r{artifact.base_revision or '?'} · {len(self.patch_files)} file(s) "
            f"({modified} modified, {added} added, {deleted} deleted) · +{total_added} / -{total_removed} lines"
            f"{note}"
        )

    def _patch_change_selected(self) -> None:
        row = self.patch_change_table.currentRow()
        item = self.patch_change_table.item(row, 2) if row >= 0 else None
        summary = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(summary, PatchFileSummary):
            self.patch_change_tree.select_path(summary.path)

    def _set_patchable_checked(self, checked: bool) -> None:
        """Tick every patchable file, skipping locked ones; untick everything."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.change_table.rowCount()):
            item = self.change_table.item(row, USE_COLUMN)
            change = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not isinstance(change, SvnChange) or not change.patchable:
                continue
            if checked and change.locked:
                continue
            item.setCheckState(state)
        self._update_selection_counts()

    def _show_info(self, info: SvnInfo | None, output: bool = False) -> None:
        if not isinstance(info, SvnInfo):
            return
        self.connection.setText(f"r{info.revision or '?'} · connected")
        if output:
            self.output.setPlainText(
                f"Working-copy target: {info.target}\nWorking-copy root: {info.working_copy_root}\n"
                f"URL: {info.url}\nRepository root: {info.repository_root}\nRepository UUID: {info.repository_uuid}\n"
                f"Base revision: {info.revision}"
            )
            self.tabs.setCurrentWidget(self.output)

    def _show_command_output(self, command, focus: bool = True) -> None:
        if command is None:
            return
        text = command.stdout.strip()
        if command.stderr.strip():
            text += ("\n\nSTDERR\n" if text else "") + command.stderr.strip()
        self.output.setPlainText(text or "Command completed without output.")
        if focus:
            self.tabs.setCurrentWidget(self.output)

    @staticmethod
    def _artifact_header(artifact: PatchArtifact | None) -> str:
        if not isinstance(artifact, PatchArtifact):
            return "Patch artifact"
        warnings = "\n".join(f"Warning: {item}" for item in artifact.warnings)
        return (
            f"Ticket: {artifact.ticket_key} — {artifact.ticket_title}\nState: {artifact.state}\nCreated: {artifact.created_at}\n"
            f"Working copy: {artifact.working_copy_root}\nRepository: {artifact.repository_root}\nBase revision: {artifact.base_revision}\n"
            f"Affected paths: {len(artifact.affected_paths)}\nPatch: {artifact.patch_file}\nSHA-256: {artifact.sha256}"
            + (f"\n{warnings}" if warnings else "")
        )

    def _set_busy(self, busy: bool, message: str) -> None:
        for button in (
            self.status_button, self.compare_button, self.log_button, self.update_button,
            self.cleanup_button, self.info_button, self.more_button, self.diff_button,
            self.create_patch_button, self.revert_button, self.lock_button, self.unlock_button,
            self.apply_patch_button, self.refresh_shelf_button, self.delete_patch_button,
            self.edit_patch_button,
        ):
            button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.operation_state.setText(message)
        if not busy:
            self._update_selection_counts()
            has_patch = self.patch_table.rowCount() > 0
            self.apply_patch_button.setEnabled(has_patch)
            self.delete_patch_button.setEnabled(has_patch)
            self.edit_patch_button.setEnabled(has_patch)

    def _require_ticket_key(self) -> bool:
        if self.ticket_key.text().strip():
            return True
        self._information("Ticket key needed", "Select a Backlog ticket or enter a ticket key before creating a patch shelf.")
        self.ticket_key.setFocus()
        return False

    def _confirm_mutation(self, title: str, action: str, target: str) -> bool:
        answer = QMessageBox.question(
            self, title, f"{action}:\n\n{target}\n\nReview the current status first and continue only if this scope is correct.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _information(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def _warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
        self.status_message.emit(message.replace("\n", " · "), 7000)

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._patch_refresh_pending and not self.worker:
            QTimer.singleShot(0, self.refresh_patches)
