"""Standup preparation, daily history, and a keyboard-accessible team roll call."""
from __future__ import annotations
from datetime import date
from PyQt6.QtCore import QDate, QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QApplication, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QSplitter, QTextEdit, QVBoxLayout, QWidget)
from modules.standup_store import StandupStore
from ui.design import button
from ui.theme import active


class MemberDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add team member")
        form = QFormLayout(self)
        self.name, self.reading = QLineEdit(), QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Reading", self.reading)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        form.addRow(controls)


class StandupWidget(QWidget):
    export_requested = pyqtSignal(dict)
    preparation_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.day = date.today().isoformat()
        self.store = StandupStore()
        self._loading = False
        self._dirty = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(700)
        self.save_timer.timeout.connect(self.save_entry)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Start the day together.")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.date_picker = QDateEdit(QDate.currentDate())
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("yyyy-MM-dd")
        self.date_picker.setToolTip("Browse or prepare another day's standup")
        self.date_label = QLabel(self.day)
        self.date_label.hide()
        header.addWidget(self.date_picker)
        root.addLayout(header)
        controls = QHBoxLayout()
        self.save_state = QLabel("Saved on this device")
        self.save_state.setObjectName("muted")
        controls.addWidget(self.save_state)
        controls.addStretch()
        controls.addWidget(button("Use my day", self.preparation_requested, glyph="tasks"))
        controls.addWidget(button("Copy summary", self.copy_summary))
        controls.addWidget(button("Export to Obsidian", lambda: self.export_requested.emit(self.current_entry()), primary=True))
        root.addLayout(controls)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        roster = QFrame()
        roster.setObjectName("surface")
        roster.setMinimumWidth(228)
        roster.setMaximumWidth(350)
        roster_layout = QVBoxLayout(roster)
        roster_layout.setContentsMargins(11, 11, 11, 9)
        heading = QLabel("Around the room")
        heading.setObjectName("sectionTitle")
        self.progress = QLabel()
        self.progress.setObjectName("muted")
        roster_layout.addWidget(heading)
        roster_layout.addWidget(self.progress)
        self.people = QListWidget()
        self.people.setObjectName("quietList")
        self.people.setUniformItemSizes(True)
        roster_layout.addWidget(self.people, 1)
        actions = QHBoxLayout()
        actions.addWidget(button("Add", self.add_member))
        actions.addWidget(button("Remove", self.remove_member))
        actions.addWidget(button("Reset", self.reset_people))
        roster_layout.addLayout(actions)
        hint = QLabel("Click or press Enter after someone speaks.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        roster_layout.addWidget(hint)
        split.addWidget(roster)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        prep = QWidget()
        layout = QVBoxLayout(prep)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(9)
        self.say = self._section(layout, "Your talking points", "What is the one thing the team should know?", 130)
        pair = QHBoxLayout()
        pair.setSpacing(9)
        yesterday_box, today_box = QVBoxLayout(), QVBoxLayout()
        self.yesterday = self._section(yesterday_box, "01  Yesterday", "What moved forward? Outcomes, not just activity.", 155)
        self.today = self._section(today_box, "02  Today", "What will you focus on next?", 155)
        pair.addLayout(yesterday_box)
        pair.addLayout(today_box)
        layout.addLayout(pair)
        self.blockers = self._section(layout, "03  Anything in the way?", "Dependencies, risks, or help you need.", 110)
        self.notes = self._section(layout, "Keep the useful details", "Decisions, announcements, and follow-ups from the meeting.", 155)
        layout.addStretch()
        scroll.setWidget(prep)
        split.addWidget(scroll)
        split.setSizes([300, 850])
        root.addWidget(split, 1)
        self.people.itemClicked.connect(self.toggle_person)
        self.people.installEventFilter(self)
        self.date_picker.dateChanged.connect(self.change_day)
        for editor in (self.say, self.yesterday, self.today, self.blockers, self.notes):
            editor.textChanged.connect(self.schedule_save)
        self.load_entry()
        self.render_people()

    @staticmethod
    def _section(layout, title, placeholder, height):
        card = QFrame()
        card.setObjectName("surface")
        inside = QVBoxLayout(card)
        inside.setContentsMargins(11, 9, 11, 9)
        inside.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        editor = QTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setAcceptRichText(False)
        editor.setMinimumHeight(height)
        inside.addWidget(heading)
        inside.addWidget(editor, 1)
        layout.addWidget(card)
        return editor

    def render_people(self):
        self.people.clear()
        values = self.store.people(self.day)
        checked = sum(bool(person["checked"]) for person in values)
        for index, person in enumerate(values, 1):
            marker = "✓" if person["checked"] else "○"
            reading = f"  ({person.get('reading')})" if person.get("reading") else ""
            item = QListWidgetItem(f"{marker}   {index:02d}   {person['name']}{reading}")
            item.setData(Qt.ItemDataRole.UserRole, int(person["id"]))
            item.setSizeHint(QSize(0, 36))
            if person["checked"]:
                item.setForeground(QColor(active().muted))
            self.people.addItem(item)
        self.progress.setText(f"{checked} of {len(values)} shared their update")

    def _persist(self, operation):
        try:
            operation()
            return True
        except OSError:
            self.save_state.setText("Could not save. Check disk access and try again.")
            return False

    def eventFilter(self, watched, event):
        if watched is self.people and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            item = self.people.currentItem()
            if item:
                self.toggle_person(item)
            return True
        return super().eventFilter(watched, event)

    def toggle_person(self, item):
        if self._persist(lambda: self.store.toggle_person(int(item.data(Qt.ItemDataRole.UserRole)), self.day)):
            self.render_people()

    def add_member(self):
        dialog = MemberDialog(self)
        if dialog.exec() and dialog.name.text().strip():
            if self._persist(lambda: self.store.add_person(dialog.name.text(), dialog.reading.text())):
                self.render_people()

    def remove_member(self):
        item = self.people.currentItem()
        if item and QMessageBox.question(self, "Remove team member", f"Remove this person from the roster?\n\n{item.text()}") == QMessageBox.StandardButton.Yes:
            if self._persist(lambda: self.store.remove_person(int(item.data(Qt.ItemDataRole.UserRole)))):
                self.render_people()

    def reset_people(self):
        if self._persist(lambda: self.store.reset_people(self.day)):
            self.render_people()

    def schedule_save(self):
        if not self._loading:
            self._dirty = True
            self.save_state.setText("Saving…")
            self.save_timer.start()

    def change_day(self, selected):
        if not self.save_entry():
            self.date_picker.blockSignals(True)
            self.date_picker.setDate(QDate.fromString(self.day, "yyyy-MM-dd"))
            self.date_picker.blockSignals(False)
            return
        self.day = selected.toString("yyyy-MM-dd")
        self.date_label.setText(self.day)
        self.load_entry()
        self.render_people()

    def load_entry(self):
        self._loading = True
        entry = self.store.entry(self.day)
        for editor, key in ((self.say, "what_i_will_say"), (self.yesterday, "yesterday"), (self.today, "today"), (self.blockers, "blockers"), (self.notes, "notes")):
            editor.setPlainText(entry[key])
        self._loading = self._dirty = False
        self.save_state.setText("Saved on this device")

    def current_entry(self):
        return {"date": self.day, "what_i_will_say": self.say.toPlainText().strip(),
                "yesterday": self.yesterday.toPlainText().strip(), "today": self.today.toPlainText().strip(),
                "blockers": self.blockers.toPlainText().strip(), "notes": self.notes.toPlainText().strip()}

    def save_entry(self):
        self.save_timer.stop()
        if not self._dirty:
            return True
        if not self._persist(lambda: self.store.save_entry(self.day, self.current_entry())):
            return False
        self._dirty = False
        self.save_state.setText("Saved on this device")
        return True

    def append_plan(self, yesterday, today):
        for editor, lines in ((self.yesterday, yesterday), (self.today, today)):
            current = editor.toPlainText()
            additions = [line for line in dict.fromkeys(lines) if line not in current]
            if additions:
                editor.setPlainText(current + ("\n" if current else "") + "\n".join(additions))

    def copy_summary(self):
        entry = self.current_entry()
        text = f"Morning standup — {self.day}\n\n"
        for title, key in (("Talking points", "what_i_will_say"), ("Yesterday", "yesterday"), ("Today", "today"), ("Blockers", "blockers"), ("Meeting notes", "notes")):
            text += f"{title}:\n{entry[key] or '—'}\n\n"
        QApplication.clipboard().setText(text.strip())
        self.save_state.setText("Summary copied")
