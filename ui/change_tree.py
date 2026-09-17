"""Working-copy location tree for the Source Control changes table.

Built entirely from the SVN status result, so it never touches the disk and
always agrees with the rows the table is showing.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from modules.svn_models import SvnChange


PATH_ROLE = Qt.ItemDataRole.UserRole


class ChangeTree(QWidget):
    """Shows where the selected change lives, with its siblings for context."""

    path_activated = pyqtSignal(str)

    def __init__(self, state_colors: dict[str, str] | None = None):
        super().__init__()
        self.state_colors = dict(state_colors or {})
        self._changes: list[SvnChange] = []
        self._root_label = "Working copy"
        self._items: dict[str, QTreeWidgetItem] = {}
        self._emitting = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.caption = QLabel("File location")
        self.caption.setObjectName("muted")
        self.tree = QTreeWidget()
        self.tree.setObjectName("changeTree")
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels(["Working copy"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setMinimumWidth(150)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.caption)
        layout.addWidget(self.tree, 1)

        self.tree.currentItemChanged.connect(self._current_changed)

    def set_root_label(self, label: str) -> None:
        self._root_label = label or "Working copy"
        self.tree.setHeaderLabels([self._root_label])

    def set_changes(self, changes: list[SvnChange]) -> None:
        self._changes = list(changes)
        self._rebuild()

    def clear(self) -> None:
        self._changes = []
        self._rebuild()

    def select_path(self, path: str) -> None:
        """Reveal and select the node for a working-copy-relative path."""
        item = self._items.get(str(path or "").replace("\\", "/"))
        if not item:
            self.caption.setText("File location")
            return
        self._emitting = True
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._emitting = False
        change = next((value for value in self._changes if value.path == item.data(0, PATH_ROLE)), None)
        folder = change.folder if change else ""
        self.caption.setText(f"In: {folder or self._root_label}")

    def _rebuild(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = {}
        folders: dict[str, QTreeWidgetItem] = {}
        bold = QFont()
        bold.setBold(True)
        for change in sorted(self._changes, key=lambda value: value.sort_key):
            parent = self._folder_item(change.folder_parts, folders)
            item = QTreeWidgetItem([change.name])
            item.setData(0, PATH_ROLE, change.path)
            item.setToolTip(0, f"{change.path} · {change.display_status}")
            color = self.state_colors.get(change.item_status)
            if color:
                item.setForeground(0, QColor(color))
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._items[change.path] = item
        for path, item in folders.items():
            item.setFont(0, bold)
            item.setToolTip(0, path)
            item.setText(0, f"{item.text(0)}  ({self._descendant_files(item)})")
        self.tree.expandToDepth(1)
        self.tree.blockSignals(False)
        if not self._changes:
            self.caption.setText("Run Show status to see where changes live")

    def _folder_item(self, parts: tuple[str, ...], folders: dict[str, QTreeWidgetItem]) -> QTreeWidgetItem | None:
        parent: QTreeWidgetItem | None = None
        walked: list[str] = []
        for part in parts:
            walked.append(part)
            key = "/".join(walked)
            existing = folders.get(key)
            if existing is None:
                existing = QTreeWidgetItem([part])
                existing.setData(0, PATH_ROLE, "")
                if parent is None:
                    self.tree.addTopLevelItem(existing)
                else:
                    parent.addChild(existing)
                folders[key] = existing
            parent = existing
        return parent

    def _descendant_files(self, item: QTreeWidgetItem) -> int:
        total = 0
        for index in range(item.childCount()):
            child = item.child(index)
            total += 1 if child.data(0, PATH_ROLE) else self._descendant_files(child)
        return total

    def _current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        if self._emitting or current is None:
            return
        path = str(current.data(0, PATH_ROLE) or "")
        if path:
            self.path_activated.emit(path)
