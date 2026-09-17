from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout)

from services.credentials import (delete_environment_password,
                                  get_environment_password,
                                  save_environment_password)

ENVIRONMENT_NAMES = ("検証", "ライブ", "ローカル")


class EnvironmentDialog(QDialog):
    def __init__(self, project: str, profiles: dict, parent=None):
        super().__init__(parent)
        self.project = str(project).strip() or "Unassigned"
        self._profiles = {
            str(name): {
                "url": str(profile.get("url", "")).strip(),
                "username": str(profile.get("username", "")).strip(),
            }
            for name, profile in (profiles or {}).items()
            if isinstance(profile, dict)
        }
        self._passwords: dict[str, str] = {}
        self._current_environment = ""

        self.setWindowTitle(f"Project environments · {self.project}")
        self.resize(560, 300)
        root = QVBoxLayout(self)
        intro = QLabel("Save a URL and username for this project. Passwords are stored in your system keyring, not in the app config file.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.environment = QComboBox()
        names = list(ENVIRONMENT_NAMES)
        names.extend(sorted(name for name in self._profiles if name not in names))
        self.environment.addItems(names)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://example.test or http://localhost:3000")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Stored securely in your system keyring")
        self.show_password = QCheckBox("Show password")
        self.show_password.toggled.connect(self._toggle_password)
        password_row = QHBoxLayout()
        password_row.addWidget(self.password, 1)
        password_row.addWidget(self.show_password)
        form.addRow("Environment", self.environment)
        form.addRow("Site URL", self.url)
        form.addRow("Username", self.username)
        form.addRow("Password", password_row)
        root.addLayout(form)

        self.status = QLabel("Choose an environment, then save its details.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        actions = QHBoxLayout()
        self.open_site = QPushButton("Open site")
        self.copy_details = QPushButton("Copy login details")
        self.delete_profile = QPushButton("Delete")
        self.close_button = QPushButton("Close")
        self.save = QPushButton("Save")
        self.copy_details.setProperty("secondary", True)
        self.delete_profile.setProperty("secondary", True)
        self.close_button.setProperty("secondary", True)
        actions.addWidget(self.open_site)
        actions.addWidget(self.copy_details)
        actions.addWidget(self.delete_profile)
        actions.addStretch()
        actions.addWidget(self.close_button)
        actions.addWidget(self.save)
        root.addLayout(actions)

        self.environment.currentTextChanged.connect(self._environment_changed)
        self.open_site.clicked.connect(self._open_site)
        self.copy_details.clicked.connect(self._copy_details)
        self.delete_profile.clicked.connect(self._delete_profile)
        self.close_button.clicked.connect(self.reject)
        self.save.clicked.connect(self._save)
        self._current_environment = self.environment.currentText().strip()
        self._load_profile(self._current_environment)

    def _environment_name(self) -> str:
        return self.environment.currentText().strip()

    def _environment_changed(self, name: str) -> None:
        name = str(name).strip()
        if name == self._current_environment:
            return
        self._store_current()
        self._current_environment = name
        self._load_profile(name)

    def _load_profile(self, name: str) -> None:
        profile = self._profiles.get(name, {})
        self.url.setText(profile.get("url", ""))
        self.username.setText(profile.get("username", ""))
        password = self._passwords.get(name)
        if password is None:
            password = get_environment_password(self.project, name)
            self._passwords[name] = password
        self.password.setText(password)
        self.status.setText(f"Editing {name or 'a new environment'} for {self.project}.")

    def _store_current(self) -> None:
        name = self._environment_name()
        if not name:
            return
        self._profiles[name] = {
            "url": self.url.text().strip(),
            "username": self.username.text().strip(),
        }
        self._passwords[name] = self.password.text()

    def _toggle_password(self, visible: bool) -> None:
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def _open_site(self) -> None:
        self._store_current()
        url = self.url.text().strip()
        if not url:
            QMessageBox.information(self, "Site URL needed", "Enter a site URL before opening it.")
            return
        target = QUrl.fromUserInput(url)
        if not target.isValid() or target.scheme().casefold() not in {"http", "https"}:
            QMessageBox.warning(self, "Invalid site URL", "Use an http:// or https:// URL.")
            return
        QDesktopServices.openUrl(target)

    def _copy_details(self) -> None:
        self._store_current()
        name = self._environment_name()
        details = "\n".join((
            f"Project: {self.project}",
            f"Environment: {name}",
            f"URL: {self.url.text().strip()}",
            f"Username: {self.username.text().strip()}",
            f"Password: {self.password.text()}",
        ))
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(details)
        self.status.setText("Login details copied to the clipboard.")

    def _delete_profile(self) -> None:
        name = self._environment_name()
        if not name or name not in self._profiles:
            return
        reply = QMessageBox.question(
            self,
            "Delete environment profile",
            f"Delete the saved {name} profile for {self.project}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_environment_password(self.project, name)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Could not remove saved password", str(exc))
            return
        self._profiles.pop(name, None)
        self._passwords.pop(name, None)
        self.url.clear()
        self.username.clear()
        self.password.clear()
        self.status.setText(f"Deleted the {name} profile. Click Save to persist the change.")

    def _save(self) -> None:
        self._store_current()
        name = self._environment_name()
        if not name:
            QMessageBox.warning(self, "Environment name needed", "Choose or enter an environment name.")
            return
        try:
            for environment, password in self._passwords.items():
                if environment in self._profiles:
                    save_environment_password(self.project, environment, password)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Could not save environment password", str(exc))
            return
        self.accept()

    def profiles(self) -> dict:
        self._store_current()
        return {
            name: dict(profile)
            for name, profile in self._profiles.items()
            if profile.get("url") or profile.get("username") or self._passwords.get(name)
        }
