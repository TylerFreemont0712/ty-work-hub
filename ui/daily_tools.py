"""Small local utilities with debounced autosave and explicit failure feedback."""
from __future__ import annotations
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget
from modules.workspace_store import WorkspaceStore
from ui.design import button


class DailyTools(QTabWidget):
    def __init__(self, captures: QListWidget, capture_input: QLineEdit, capture_add):
        super().__init__()
        self.store: WorkspaceStore | None = None
        self._loading = False
        self._dirty = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(650)
        self.save_timer.timeout.connect(self.flush)
        inbox = QWidget()
        layout = QVBoxLayout(inbox)
        row = QHBoxLayout()
        row.addWidget(capture_input, 1)
        row.addWidget(capture_add)
        layout.addLayout(row)
        layout.addWidget(captures, 1)
        self.addTab(inbox, "Inbox")

        routine_page = QWidget()
        layout = QVBoxLayout(routine_page)
        row = QHBoxLayout()
        self.routine_input = QLineEdit()
        self.routine_input.setPlaceholderText("A small habit to repeat…")
        self.cadence = QComboBox()
        self.cadence.addItem("Daily", "daily")
        self.cadence.addItem("Weekly", "weekly")
        row.addWidget(self.routine_input, 1)
        row.addWidget(self.cadence)
        row.addWidget(button("Add", self.add_routine))
        self.routines = QListWidget()
        self.routines.setObjectName("quietList")
        self.routines.itemChanged.connect(self.toggle_routine)
        self.routines.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.routines.customContextMenuRequested.connect(self.routine_menu)
        self.routine_input.returnPressed.connect(self.add_routine)
        layout.addLayout(row)
        layout.addWidget(self.routines)
        self.routine_status = QLabel("Check off a routine for this day or week.")
        self.routine_status.setObjectName("muted")
        layout.addWidget(self.routine_status)
        self.addTab(routine_page, "Routines")

        scratch_page = QWidget()
        layout = QVBoxLayout(scratch_page)
        self.scratchpad = QPlainTextEdit()
        self.scratchpad.setPlaceholderText("A little room to think. Notes are saved on this device.\n\nIdeas, links, the thing you don't want to forget…")
        self.save_state = QLabel("Saved on this device")
        self.save_state.setObjectName("muted")
        self.scratchpad.textChanged.connect(self.schedule_save)
        layout.addWidget(self.scratchpad)
        layout.addWidget(self.save_state)
        self.addTab(scratch_page, "Scratchpad")

    def bind(self, store: WorkspaceStore):
        self.store = store
        self._loading = True
        self.scratchpad.setPlainText(store.scratchpad())
        self._loading = False
        self.render_routines()

    def schedule_save(self):
        if not self._loading and self.store:
            self._dirty = True
            self.save_state.setText("Saving…")
            self.save_timer.start()

    def flush(self) -> bool:
        if not self.store or not self._dirty:
            return True
        self.save_timer.stop()
        if len(self.scratchpad.toPlainText()) > 100000:
            self.save_state.setText("Scratchpad limit is 100,000 characters. Shorten it or copy the text to a note before closing.")
            return False
        try:
            self.store.set_scratchpad(self.scratchpad.toPlainText())
        except OSError:
            self.save_state.setText("Could not save. Keep this window open and check disk access.")
            return False
        self.save_state.setText("Saved on this device")
        self._dirty = False
        return True

    def routine_menu(self, point):
        item = self.routines.itemAt(point)
        if item is None or self.store is None:
            return
        menu = QMenu(self)
        remove = menu.addAction("Remove routine…")
        if menu.exec(self.routines.viewport().mapToGlobal(point)) == remove:
            if QMessageBox.question(self, "Remove routine", f"Remove {item.text()} from your checklist?") == QMessageBox.StandardButton.Yes:
                try:
                    self.store.remove_routine(item.data(Qt.ItemDataRole.UserRole))
                except OSError:
                    self.routine_status.setText("Could not remove routine. Check disk access.")
                self.render_routines()

    def add_routine(self):
        if not self.store or not self.routine_input.text().strip():
            return
        try:
            self.store.add_routine(self.routine_input.text(), self.cadence.currentData())
        except OSError:
            self.routine_status.setText("Could not save. Check disk access and try again.")
            return
        self.routine_input.clear()
        self.render_routines()

    def toggle_routine(self, item):
        if self._loading or not self.store:
            return
        try:
            self.store.toggle_routine(item.data(Qt.ItemDataRole.UserRole))
        except OSError:
            self.routine_status.setText("Could not save routine. Check disk access.")
        self.render_routines()

    def render_routines(self):
        self._loading = True
        self.routines.clear()
        for routine in self.store.routines() if self.store else []:
            item = QListWidgetItem(f"{routine['title']}   ·   {routine['cadence']}")
            item.setData(Qt.ItemDataRole.UserRole, routine["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if self.store.routine_is_current(routine) else Qt.CheckState.Unchecked)
            self.routines.addItem(item)
        self._loading = False
