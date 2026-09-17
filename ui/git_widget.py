"""Personal-project Git workspace, independent of Backlog and the SVN shelf."""
from __future__ import annotations

import hashlib
from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from services.git_worker import GitWorker
from ui.design import button


class GitWidget(QWidget):
    preference_changed = pyqtSignal(str, object)
    settings_requested = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = dict(config)
        self.worker = None
        self._after = None
        self._operation = ""
        self._patch_destination = ""
        self._commit_message = ""
        self._staged_digest = ""
        self.controls = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel("Your code, in motion.")
        title.setObjectName("pageTitle")
        heading.addWidget(title, 1)
        heading.addWidget(button("Git settings", self.settings_requested))
        layout.addLayout(heading)
        self.location = QLabel()
        self.location.setWordWrap(True)
        self.location.setObjectName("muted")
        layout.addWidget(self.location)
        actions = QHBoxLayout()
        for text, callback in (("Open repository", self.choose_repository), ("Refresh", self.refresh),
                               ("Fetch", lambda: self.remote("fetch")), ("Pull", lambda: self.remote("pull")),
                               ("Push", lambda: self.remote("push"))):
            control = button(text, callback)
            self.controls.append(control)
            actions.addWidget(control)
        actions.addStretch()
        self.branch = QLabel("No repository loaded")
        actions.addWidget(self.branch)
        layout.addLayout(actions)
        self.status = QLabel("Open a local clone to inspect changes. Git authentication uses your installed Git configuration.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Staged", "Working tree", "File"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 78)
        self.table.setColumnWidth(1, 98)
        split.addWidget(self.table)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        split.addWidget(self.output)
        split.setSizes([250, 220])
        layout.addWidget(split, 1)
        row = QHBoxLayout()
        self.diff_mode = QComboBox()
        self.diff_mode.addItems(["Working tree", "Staged"])
        row.addWidget(self.diff_mode)
        self.controls.append(self.diff_mode)
        for text, callback in (("View diff", self.show_diff), ("Stage selected", lambda: self.run("stage", self.selected())),
                               ("Unstage selected", lambda: self.run("unstage", self.selected())), ("Export patch", self.export_patch)):
            control = button(text, callback)
            row.addWidget(control)
            self.controls.append(control)
        row.addStretch()
        layout.addLayout(row)
        commit_row = QHBoxLayout()
        self.message = QLineEdit()
        self.message.setPlaceholderText("Commit message — commits only the staged changes")
        commit_row.addWidget(self.message, 1)
        commit_button = button("Review & commit", self.review_commit, primary=True)
        commit_row.addWidget(commit_button)
        self.controls += [self.message, commit_button]
        layout.addLayout(commit_row)
        stash_row = QHBoxLayout()
        self.stashes = QComboBox()
        self.stashes.setMinimumWidth(120)
        stash_row.addWidget(self.stashes, 1)
        save = button("Stash work", self.stash_work)
        restore = button("Apply stash", self.apply_stash)
        stash_row.addWidget(save)
        stash_row.addWidget(restore)
        self.controls += [self.stashes, save, restore]
        layout.addLayout(stash_row)
        self.cancel = button("Cancel operation", self.cancel_operation)
        self.cancel.setVisible(False)
        layout.addWidget(self.cancel)
        self.table.doubleClicked.connect(self.show_diff)
        self.update_config(config)

    def update_config(self, config):
        if self.worker:
            self.cancel_operation()
        previous = self.config.get("git_repository", "")
        self.config = dict(config)
        self.location.setText(self.config.get("git_repository") or "Choose a local Git repository to get started.")
        if previous != self.config.get("git_repository", ""):
            self.table.setRowCount(0)
            self.stashes.clear()
            self.branch.setText("Refresh to load repository")
            self.output.clear()

    def choose_repository(self):
        path = QFileDialog.getExistingDirectory(self, "Open Git repository", self.config.get("git_repository", ""))
        if path:
            config = dict(self.config, git_repository=path)
            self.update_config(config)
            self.preference_changed.emit("git_repository", path)
            self.refresh()

    def selected(self):
        return [self.table.item(index.row(), 2).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def run(self, operation, *args):
        if self.worker:
            return
        self._operation = operation
        self.status.setText(f"Git: {operation.replace('_', ' ')}…")
        self.worker = GitWorker(self.config.get("git_repository", ""), self.config.get("git_executable", ""), operation, *args, parent=self)
        self.worker.ready.connect(self._ready)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.table.setEnabled(False)
        for control in self.controls:
            control.setEnabled(False)
        self.cancel.setVisible(True)
        self.worker.start()

    def cancel_operation(self):
        if self.worker:
            self._after = None
            self.worker.cancel()

    def _ready(self, result):
        if self.worker.isInterruptionRequested():
            return
        operation = self._operation
        self.status.setText(f"Git {operation.replace('_', ' ')} completed.")
        if operation == "snapshot":
            self.table.setRowCount(len(result["changes"]))
            for row, item in enumerate(result["changes"]):
                for column, value in enumerate((item.index, item.worktree, item.path)):
                    cell = QTableWidgetItem(value)
                    cell.setData(Qt.ItemDataRole.UserRole, item.path)
                    self.table.setItem(row, column, cell)
            self.branch.setText(result["branch"])
            self.stashes.clear()
            for entry in result["stashes"]:
                oid, _, description = entry.partition(" ")
                self.stashes.addItem(description, oid)
            self.output.setPlainText(result["history"])
            self.status.setText(f"{len(result['changes'])} changed files · {len(result['stashes'])} saved stashes")
        elif operation == "staged_patch":
            if not result:
                self.status.setText("Stage at least one change before committing.")
                return
            self._staged_digest = hashlib.sha256(result).hexdigest()
            self._review_patch = result.decode("utf-8", "replace")
            self._after = self._confirm_commit
        elif operation == "patch":
            if not result:
                self.status.setText("No tracked changes to export. Stage new files first.")
                return
            try:
                Path(self._patch_destination).write_bytes(result)
                self.status.setText(f"Patch exported to {self._patch_destination}. Untracked files are excluded; stage them first to include them.")
            except OSError as exc:
                self._failed(str(exc))
        elif operation == "diff":
            self.output.setPlainText(result)
        else:
            if operation == "commit":
                self.message.clear()
            self._after = self.refresh

    def _failed(self, message):
        self._after = None
        self.status.setText("Git operation failed. Review the output, then refresh before retrying.")
        self.output.setPlainText(message)

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.table.setEnabled(True)
        for control in self.controls:
            control.setEnabled(True)
        self.cancel.setVisible(False)
        callback, self._after = self._after, None
        if callback:
            callback()

    def refresh(self):
        self.run("snapshot")

    def show_diff(self):
        self.run("diff", self.selected(), self.diff_mode.currentIndex() == 1)

    def review_commit(self):
        self._commit_message = self.message.text().strip()
        if not self._commit_message:
            self.status.setText("Enter a commit message first.")
            return
        self.run("staged_patch")

    def _confirm_commit(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Review staged changes")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        summary = QLabel(f"Repository: {self.config.get('git_repository', '')}\nCommit: {self._commit_message}")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        preview = QPlainTextEdit(self._review_patch)
        preview.setReadOnly(True)
        layout.addWidget(preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Commit staged changes")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.run("commit", self._commit_message, self._staged_digest)

    def confirm(self, title, explanation):
        return QMessageBox.question(self, title, f"{explanation}\n\nRepository: {self.config.get('git_repository', '')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def remote(self, action):
        explanations = {"pull": "Pull the configured upstream with fast-forward only? Diverged branches will be left for manual resolution.",
                        "push": "Push the current branch using this repository's configured remote and push settings?"}
        if action == "fetch" or self.confirm(f"Git {action}", explanations[action]):
            self.run("remote_action", action)

    def stash_work(self):
        if self.confirm("Stash working changes", "Save ALL tracked and untracked changes in a Git stash and clear them from the working tree? Ignored files stay in place."):
            self.run("stash", self.message.text().strip() or "Ty Work Hub")

    def apply_stash(self):
        oid = self.stashes.currentData()
        if oid and self.confirm("Apply stash", f"Apply {self.stashes.currentText()} to the working tree? The stash stays saved. Conflicts may require manual resolution."):
            self.run("apply_stash", oid)

    def export_patch(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export tracked changes (stage new files first)", "changes.patch", "Git patch (*.patch)")
        if path:
            self._patch_destination = path
            self.run("patch")
