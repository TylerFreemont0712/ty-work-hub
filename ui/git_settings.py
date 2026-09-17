"""Git preferences and explicit, asynchronous dependency installation."""
from __future__ import annotations
import shlex
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                            QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)
from modules.git_install import execute_install, install_plan
from modules.git_service import discover_git
from services.background import BackgroundTask
from ui.design import button


class GitSettings(QWidget):
    def __init__(self, config):
        super().__init__()
        self.worker = None
        layout = QVBoxLayout(self)
        explanation = QLabel("Use Git for personal projects, or keep the SVN ticket shelf. Both workspaces stay available under Source control.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("Git", "git")
        self.provider.addItem("SVN", "svn")
        self.provider.setCurrentIndex(max(0, self.provider.findData(config.get("source_control_provider", "git"))))
        form.addRow("Default source control", self.provider)
        self.executable = QLineEdit(config.get("git_executable", ""))
        self.executable.setPlaceholderText("Detect from PATH")
        row = QHBoxLayout()
        row.addWidget(self.executable, 1)
        row.addWidget(button("Browse", self.browse_executable))
        form.addRow("Git executable", row)
        self.repository = QLineEdit(config.get("git_repository", ""))
        row = QHBoxLayout()
        row.addWidget(self.repository, 1)
        row.addWidget(button("Browse", self.browse_repository))
        form.addRow("Repository root", row)
        layout.addLayout(form)
        actions = QHBoxLayout()
        actions.addWidget(button("Detect Git", self.detect))
        self.install = button("Install Git", self.install_git, primary=True)
        actions.addWidget(self.install)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setPlainText("Detect an existing Git installation, or install it with your system package manager. Authentication for remotes uses your Git credentials or SSH agent.")
        layout.addWidget(self.status, 1)

    def values(self):
        return {"source_control_provider": self.provider.currentData(), "git_executable": self.executable.text().strip(), "git_repository": self.repository.text().strip()}

    def browse_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Git executable", self.executable.text())
        if path:
            self.executable.setText(path)

    def browse_repository(self):
        path = QFileDialog.getExistingDirectory(self, "Choose repository root", self.repository.text())
        if path:
            self.repository.setText(path)

    def detect(self):
        path = discover_git(self.executable.text())
        if path:
            self.executable.setText(path)
            self.status.setPlainText(f"Git found: {path}")
        else:
            self.status.setPlainText("Git was not found. Clear an outdated executable path to search PATH, or use Install Git.")

    def install_git(self):
        if self.worker:
            return
        if discover_git(self.executable.text()):
            self.detect()
            self.status.appendPlainText("Git is already installed.")
            return
        command, explanation = install_plan()
        self.status.setPlainText(explanation)
        if not command:
            return
        if QMessageBox.question(self, "Install Git", f"{explanation}\n\nCommand: {shlex.join(command)}\n\nInstall now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.install.setEnabled(False)
        self.status.appendPlainText("Installing… Complete any system authorization prompt. You can continue using the app.")
        self.worker = BackgroundTask(lambda: execute_install(command))
        self.worker.ready.connect(self._installed)
        self.worker.failed.connect(self.status.setPlainText)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _installed(self, output):
        self.status.setPlainText(output)
        path = discover_git()
        if path:
            self.executable.setText(path)
            self.status.appendPlainText(f"Git is ready: {path}")
        else:
            self.status.appendPlainText("Restart the app or browse to the Git executable to refresh PATH.")

    def _finished(self):
        self.worker = None
        self.install.setEnabled(True)
