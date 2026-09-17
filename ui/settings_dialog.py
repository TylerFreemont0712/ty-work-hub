from __future__ import annotations

from PyQt6.QtCore import QTime, Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from services.background import BackgroundTask
from services.credentials import backlog_key_configured
from services.local_llm import DEFAULT_LLM_URL, LocalLLMClient
from modules.svn_service import discover_svn_executable, discover_tortoise_executable
from modules.ticket_utils import project_name
from modules.work_schedule import DAY_NAMES, WorkSchedule, format_clock, parse_clock
from ui.theme import choices as theme_choices, resolve as resolve_theme
from ui.git_settings import GitSettings

class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None, issues: list[dict] | None = None):
        super().__init__(parent); self.config = config; self.issues = list(issues or []); self.setWindowTitle("Ty Work Hub settings"); self.resize(700, 500)
        self._probe = None
        root = QVBoxLayout(self); tabs = QTabWidget(); root.addWidget(tabs)
        self.git_settings = GitSettings(config)
        tabs.addTab(self.git_settings, "Git")
        tabs.addTab(self.general_tab(), "General"); tabs.addTab(self.schedule_tab(), "Schedule"); tabs.addTab(self.backlog_tab(), "Backlog"); tabs.addTab(self.obsidian_tab(), "Obsidian"); tabs.addTab(self.local_ai_tab(), "Local AI"); tabs.addTab(self.calendar_tab(), "Calendar"); tabs.addTab(self.source_control_tab(), "Source Control"); tabs.addTab(self.launchers_tab(), "Launchers")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def general_tab(self):
        tab = QWidget(); form = QFormLayout(tab); self.poll = QSpinBox(); self.poll.setRange(1, 120); self.poll.setSuffix(" minutes"); self.poll.setValue(self.config.get("polling_interval_minutes", 10))
        self.theme = QComboBox()
        for key, label, dark in theme_choices():
            self.theme.addItem(f"{label}  ·  {'dark' if dark else 'light'}", key)
            self.theme.setItemData(self.theme.count() - 1, resolve_theme(key).blurb, Qt.ItemDataRole.ToolTipRole)
        self.theme.setCurrentIndex(max(0, self.theme.findData(resolve_theme(self.config.get("theme")).key)))
        self.theme.setToolTip("Appearance. Ctrl+Shift+T steps through these without opening settings.")
        self.default_page = QComboBox(); self.default_page.addItems(["Command Center", "Personal Tasks", "Ticket Detail", "Review Board", "Source Control", "Obsidian", "Calendar", "Morning Standup"]); self.default_page.setCurrentText(self.config.get("default_page", "Command Center"))
        self.density = QComboBox(); self.density.addItems(["compact", "comfortable"]); self.density.setCurrentText(self.config.get("density", "compact"))
        self.font_size = QSpinBox(); self.font_size.setRange(9, 14); self.font_size.setSuffix(" px"); self.font_size.setValue(self.config.get("font_size", 11))
        self.navigation_collapsed = QCheckBox("Start with the navigation rail collapsed"); self.navigation_collapsed.setChecked(self.config.get("navigation_collapsed", False))
        form.addRow("Backlog refresh", self.poll); form.addRow("Theme", self.theme); form.addRow("Interface density", self.density); form.addRow("Base font size", self.font_size); form.addRow("Open on", self.default_page); form.addRow("", self.navigation_collapsed); return tab

    def backlog_tab(self):
        tab = QWidget(); form = QFormLayout(tab); configured = "Available through your system keyring or environment" if backlog_key_configured() else "Not configured — demo tasks will be shown"
        status = QLabel(configured); status.setWordWrap(True); self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.EchoMode.Password); self.api_key.setPlaceholderText("Enter a new key, or leave blank to keep the current key")
        self.base_url = QLineEdit(self.config.get("backlog_base_url", "https://mirax.backlog.com/api/v2")); self.web_url = QLineEdit(self.config.get("backlog_web_url", "https://mirax.backlog.com"))
        self.assignee = QSpinBox(); self.assignee.setRange(0, 2147483647); self.assignee.setSpecialValueText("All assignees"); self.assignee.setValue(self.config.get("backlog_assignee_id", 0)); self.hide_closed = QCheckBox("Hide completed/closed tasks"); self.hide_closed.setChecked(self.config.get("backlog_hide_closed", True))
        form.addRow("Connection", status); form.addRow("API key", self.api_key); form.addRow("API base URL", self.base_url); form.addRow("Web URL", self.web_url); form.addRow("Assignee ID", self.assignee); form.addRow("", self.hide_closed); return tab

    def obsidian_tab(self):
        tab = QWidget(); form = QFormLayout(tab); self.vault = QLineEdit(self.config.get("obsidian_vault_path", "")); browse = QPushButton("Browse"); browse.clicked.connect(self.choose_vault)
        row = QHBoxLayout(); row.addWidget(self.vault); row.addWidget(browse); self.tickets_folder = QLineEdit(self.config.get("obsidian_tickets_folder", "Work/Tickets")); self.daily_folder = QLineEdit(self.config.get("obsidian_daily_folder", "Work/Daily")); self.programming_folder = QLineEdit(self.config.get("obsidian_programming_folder", "Work/Programming"))
        explanation = QLabel("AI ticket updates use a fixed template, create a history backup, and write reusable learning references under the Programming folder."); explanation.setWordWrap(True)
        form.addRow("Vault", row); form.addRow("Ticket notes folder", self.tickets_folder); form.addRow("Daily notes folder", self.daily_folder); form.addRow("Programming references", self.programming_folder); form.addRow("", explanation); return tab

    def local_ai_tab(self):
        tab = QWidget(); form = QFormLayout(tab)
        self.local_llm_url = QLineEdit(self.config.get("local_llm_url", DEFAULT_LLM_URL))
        self.local_llm_url.setPlaceholderText(DEFAULT_LLM_URL)
        self.local_llm_url.setToolTip("Base URL of the llama.cpp server, without a trailing path")
        self.local_llm_model = QComboBox(); self.local_llm_model.setEditable(True)
        self.local_llm_model.addItem(self.config.get("local_llm_model", ""))
        self.local_llm_model.setToolTip(
            "Leave blank to use whatever model the server has loaded. llama.cpp serves one model at a time."
        )
        refresh = QPushButton("Load models"); refresh.clicked.connect(self.refresh_llm_models); self.load_models_button = refresh
        test = QPushButton("Test connection"); test.clicked.connect(self.test_llm_connection); self.test_connection_button = test
        model_row = QHBoxLayout(); model_row.addWidget(self.local_llm_model, 1); model_row.addWidget(refresh); model_row.addWidget(test)

        self.local_llm_timeout = QSpinBox(); self.local_llm_timeout.setRange(30, 1800); self.local_llm_timeout.setSuffix(" seconds")
        self.local_llm_timeout.setValue(self.config.get("local_llm_timeout_seconds", 600))
        self.local_llm_max_tokens = QSpinBox(); self.local_llm_max_tokens.setRange(512, 32768); self.local_llm_max_tokens.setSingleStep(512)
        self.local_llm_max_tokens.setSuffix(" tokens"); self.local_llm_max_tokens.setValue(self.config.get("local_llm_max_tokens", 8192))
        self.local_llm_max_tokens.setToolTip(
            "Ceiling on the reply. A full note template needs several thousand tokens; too low truncates the JSON."
        )
        self.local_llm_context_tokens = QSpinBox(); self.local_llm_context_tokens.setRange(2048, 262144); self.local_llm_context_tokens.setSingleStep(1024)
        self.local_llm_context_tokens.setSuffix(" tokens"); self.local_llm_context_tokens.setValue(self.config.get("local_llm_context_tokens", 32768))
        self.local_llm_context_tokens.setToolTip(
            "The server's context window. The app trims the note and comment history to fit inside it. "
            "Use Test connection to read the real value from the server."
        )
        self.local_llm_thinking = QCheckBox("Let the model think before answering (slower, uses the reply budget)")
        self.local_llm_thinking.setChecked(self.config.get("local_llm_thinking", False))
        self.local_llm_thinking.setToolTip(
            "Reasoning models such as Qwen3 spend reply tokens on thinking. Off keeps structured output fast and reliable."
        )

        self.local_llm_status = QLabel("Not tested yet.")
        self.local_llm_status.setObjectName("muted"); self.local_llm_status.setWordWrap(True)
        explanation = QLabel(
            "Ticket content, recent Backlog comments, the associated note, and the file list of this ticket's saved "
            "SVN patches are sent only to this server. The app requires structured JSON and never writes the result "
            "until you review the preview and apply it."
        )
        explanation.setWordWrap(True)
        form.addRow("Server URL", self.local_llm_url); form.addRow("Model", model_row)
        form.addRow("Generation timeout", self.local_llm_timeout)
        form.addRow("Reply budget", self.local_llm_max_tokens)
        form.addRow("Context window", self.local_llm_context_tokens)
        form.addRow("", self.local_llm_thinking)
        form.addRow("Server", self.local_llm_status)
        form.addRow("", explanation)
        return tab

    def _llm_client(self, timeout: int = 8):
        return LocalLLMClient(self.local_llm_url.text().strip(), self.local_llm_model.currentText().strip(), timeout)

    def _start_probe(self, kind):
        if self._probe is not None:
            return
        client = self._llm_client()
        self._probe_kind = kind
        self._probe_model = self.local_llm_model.currentText().strip()
        self.local_llm_status.setText("Checking the local AI server…")
        self.load_models_button.setEnabled(False)
        self.test_connection_button.setEnabled(False)
        self._probe = BackgroundTask(client.models if kind == "models" else client.health)
        self._probe.ready.connect(self._probe_ready)
        self._probe.failed.connect(self._probe_failed)
        self._probe.finished.connect(self._probe_finished)
        self._probe.start()

    def refresh_llm_models(self):
        self._start_probe("models")

    def test_llm_connection(self):
        self._start_probe("health")

    def _probe_ready(self, result):
        if self._probe_kind == "models":
            current = self._probe_model
            self.local_llm_model.clear()
            self.local_llm_model.addItems(result)
            if current and current not in result:
                self.local_llm_model.addItem(current)
            self.local_llm_model.setCurrentText(current or (result[0] if result else ""))
            self.local_llm_status.setText(f"{len(result)} model(s) available." if result else "The server responded but reported no models.")
        else:
            if result.context_tokens:
                self.local_llm_context_tokens.setValue(max(2048, min(262144, result.context_tokens)))
            slots = f" · {result.slots} slot(s)" if result.slots else ""
            window = f" · {result.context_tokens} token context" if result.context_tokens else ""
            self.local_llm_status.setText(f"Connected · {result.model_label or 'model unnamed'}{window}{slots}")

    def _probe_failed(self, message):
        self.local_llm_status.setText(f"Connection failed: {message}")

    def _probe_finished(self):
        self._probe = None
        self.load_models_button.setEnabled(True)
        self.test_connection_button.setEnabled(True)

    def calendar_tab(self):
        tab = QWidget(); form = QFormLayout(tab); self.default_view = QComboBox(); self.default_view.addItems(["Month", "Week", "Day", "Agenda"]); self.default_view.setCurrentText(self.config.get("calendar_default_view", "Week"))
        self.default_minutes = QSpinBox(); self.default_minutes.setRange(5, 480); self.default_minutes.setSingleStep(5); self.default_minutes.setSuffix(" minutes"); self.default_minutes.setValue(self.config.get("calendar_default_minutes", 30))
        self.weekends = QCheckBox("Show Saturday and Sunday in week view"); self.weekends.setChecked(self.config.get("show_weekends", True))
        hint = QLabel("Working hours and breaks live on the Schedule tab."); hint.setObjectName("muted"); hint.setWordWrap(True)
        form.addRow("Opening view", self.default_view); form.addRow("New work block", self.default_minutes); form.addRow("", self.weekends); form.addRow("", hint); return tab

    def schedule_tab(self):
        """When tracked time counts. Everything outside it is deducted from totals."""
        schedule = WorkSchedule.from_config(self.config)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        explanation = QLabel(
            "The work timer always records when you started and stopped. Only time inside this schedule "
            "counts towards your totals, so evenings, weekends, and breaks are deducted automatically "
            "instead of having to be remembered."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.track_business_hours = QCheckBox("Only count time inside the schedule")
        self.track_business_hours.setChecked(bool(self.config.get("track_business_hours_only", True)))
        self.track_business_hours.setToolTip("Turn this off to count every tracked minute, whenever it happens")
        self.day_start = QTimeEdit(QTime(schedule.start.hour, schedule.start.minute))
        self.day_end = QTimeEdit(QTime(schedule.end.hour, schedule.end.minute))
        for editor in (self.day_start, self.day_end):
            editor.setDisplayFormat("HH:mm")
        self.general_project = QLineEdit(str(self.config.get("general_project", "Company General")))
        self.general_project.setToolTip("Used for work that has no Backlog project of its own")
        days_row = QHBoxLayout()
        days_row.setContentsMargins(0, 0, 0, 0)
        self.work_days = []
        for index, name in enumerate(DAY_NAMES):
            box = QCheckBox(name)
            box.setChecked(index in schedule.days)
            self.work_days.append(box)
            days_row.addWidget(box)
        days_row.addStretch()
        days_holder = QWidget()
        days_holder.setLayout(days_row)
        form.addRow("", self.track_business_hours)
        form.addRow("Day starts", self.day_start)
        form.addRow("Day ends", self.day_end)
        form.addRow("Working days", days_holder)
        form.addRow("General project", self.general_project)
        layout.addLayout(form)

        layout.addWidget(QLabel("Breaks deducted from every working day"))
        self.breaks = QTableWidget(0, 3)
        self.breaks.setHorizontalHeaderLabels(["Label", "From", "To"])
        self.breaks.horizontalHeader().setStretchLastSection(True)
        self.breaks.setColumnWidth(0, 180)
        self.breaks.setColumnWidth(1, 90)
        self.breaks.verticalHeader().setVisible(False)
        self.breaks.setMinimumHeight(110)
        for item in schedule.breaks:
            self._add_break_row(item.label, format_clock(item.start), format_clock(item.end))
        layout.addWidget(self.breaks)
        buttons = QHBoxLayout()
        add = QPushButton("Add break")
        add.clicked.connect(lambda: self._add_break_row("Break", "12:00", "13:00"))
        remove = QPushButton("Remove selected")
        remove.setProperty("secondary", True)
        remove.clicked.connect(self._remove_break_row)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        layout.addLayout(buttons)
        return tab

    def _add_break_row(self, label: str, start: str, end: str) -> None:
        row = self.breaks.rowCount()
        self.breaks.insertRow(row)
        for column, value in enumerate((label, start, end)):
            self.breaks.setItem(row, column, QTableWidgetItem(value))

    def _remove_break_row(self) -> None:
        row = self.breaks.currentRow()
        if row >= 0:
            self.breaks.removeRow(row)

    def _schedule_values(self) -> dict:
        """Read the schedule back, dropping any break whose times are reversed."""
        breaks = []
        for row in range(self.breaks.rowCount()):
            cells = [self.breaks.item(row, column) for column in range(3)]
            label, start, end = (cell.text().strip() if cell else "" for cell in cells)
            first, last = parse_clock(start, "12:00"), parse_clock(end, "13:00")
            if first >= last:
                continue
            breaks.append({"label": label or "Break", "start": format_clock(first), "end": format_clock(last)})
        return {
            "start": self.day_start.time().toString("HH:mm"),
            "end": self.day_end.time().toString("HH:mm"),
            "days": [index for index, box in enumerate(self.work_days) if box.isChecked()],
            "breaks": breaks,
        }

    def source_control_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        explanation = QLabel(
            "Configure local SVN working copies and the ticket patch shelf. Authentication remains in SVN/TortoiseSVN; "
            "Ty Work Hub never stores repository passwords."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        form = QFormLayout()
        self.svn_executable = QLineEdit(self.config.get("svn_executable", ""))
        self.tortoise_executable = QLineEdit(self.config.get("tortoise_executable", ""))
        self.svn_patch_root = QLineEdit(self.config.get("svn_patch_root", ""))
        self.svn_timeout = QSpinBox()
        self.svn_timeout.setRange(10, 1800)
        self.svn_timeout.setSuffix(" seconds")
        self.svn_timeout.setValue(self.config.get("svn_timeout_seconds", 120))

        svn_row = QHBoxLayout()
        svn_row.addWidget(self.svn_executable, 1)
        svn_browse = QPushButton("Browse")
        svn_browse.clicked.connect(lambda: self._choose_executable(self.svn_executable, "Choose SVN executable"))
        svn_row.addWidget(svn_browse)
        tortoise_row = QHBoxLayout()
        tortoise_row.addWidget(self.tortoise_executable, 1)
        tortoise_browse = QPushButton("Browse")
        tortoise_browse.clicked.connect(lambda: self._choose_executable(self.tortoise_executable, "Choose TortoiseProc.exe"))
        tortoise_row.addWidget(tortoise_browse)
        patch_row = QHBoxLayout()
        patch_row.addWidget(self.svn_patch_root, 1)
        patch_browse = QPushButton("Browse")
        patch_browse.clicked.connect(self._choose_patch_root)
        patch_row.addWidget(patch_browse)
        form.addRow("SVN executable", svn_row)
        form.addRow("TortoiseSVN executable", tortoise_row)
        form.addRow("Patch shelf", patch_row)
        form.addRow("Command timeout", self.svn_timeout)
        layout.addLayout(form)

        tools_row = QHBoxLayout()
        detect = QPushButton("Find installed tools")
        detect.clicked.connect(self.detect_svn_tools)
        self.svn_detection = QLabel("Executable paths can be detected without saving them.")
        self.svn_detection.setObjectName("muted")
        self.svn_detection.setWordWrap(True)
        tools_row.addWidget(detect)
        tools_row.addWidget(self.svn_detection, 1)
        layout.addLayout(tools_row)

        profiles_label = QLabel("Working-copy profiles")
        profiles_label.setObjectName("sectionTitle")
        layout.addWidget(profiles_label)
        self.svn_profiles = QTableWidget(0, 3)
        self.svn_profiles.setHorizontalHeaderLabels(["Name", "Backlog project", "Working-copy root"])
        self.svn_profiles.horizontalHeader().setStretchLastSection(True)
        for profile in self.config.get("svn_profiles", []):
            self.add_svn_profile(profile.get("name", ""), profile.get("project", ""), profile.get("root", ""))
        layout.addWidget(self.svn_profiles, 1)
        profile_actions = QHBoxLayout()
        add = QPushButton("Add profile")
        from_backlog = QPushButton("Add from Backlog projects")
        from_backlog.setToolTip("Create one empty profile per project in the loaded Backlog tickets, then set each root")
        remove = QPushButton("Remove selected")
        browse = QPushButton("Browse working copy")
        manual = QPushButton("Enter root manually")
        manual.setToolTip("Type or paste a working-copy path, including UNC and mapped-drive paths")
        for button in (from_backlog, remove, browse, manual):
            button.setProperty("secondary", True)
        add.clicked.connect(lambda: self.add_svn_profile("", "", ""))
        from_backlog.clicked.connect(self.add_backlog_project_profiles)
        remove.clicked.connect(self.remove_svn_profile)
        browse.clicked.connect(self.browse_svn_profile)
        manual.clicked.connect(self.enter_svn_root)
        profile_actions.addWidget(add)
        profile_actions.addWidget(from_backlog)
        profile_actions.addWidget(browse)
        profile_actions.addWidget(manual)
        profile_actions.addWidget(remove)
        profile_actions.addStretch()
        layout.addLayout(profile_actions)
        self.svn_profile_hint = QLabel(
            "A profile is selected automatically when its Backlog project matches the current ticket. "
            "A root is only validated when an SVN command runs, so an offline or disconnected path can be saved now."
        )
        self.svn_profile_hint.setObjectName("muted")
        self.svn_profile_hint.setWordWrap(True)
        layout.addWidget(self.svn_profile_hint)
        return tab

    def backlog_projects(self) -> list[str]:
        """Distinct project names from the tickets currently loaded in the app."""
        names = []
        for issue in self.issues:
            name = project_name(issue)
            if name and name != "Unassigned" and name not in names:
                names.append(name)
        return sorted(names)

    def add_backlog_project_profiles(self):
        projects = self.backlog_projects()
        if not projects:
            QMessageBox.information(
                self, "No Backlog projects",
                "No projects were found in the loaded tickets. Refresh Personal Tasks first, "
                "or add a profile manually.",
            )
            return
        existing = set()
        for row in range(self.svn_profiles.rowCount()):
            item = self.svn_profiles.item(row, 1)
            if item and item.text().strip():
                existing.add(item.text().strip().casefold())
        added = [name for name in projects if name.casefold() not in existing]
        for name in added:
            self.add_svn_profile(name, name, "")
        skipped = len(projects) - len(added)
        message = f"Added {len(added)} project profile(s)."
        if skipped:
            message += f" {skipped} already had a profile."
        if added:
            message += " Set a working-copy root for each new row before saving."
        self.svn_detection.setText(message)
        if not added:
            QMessageBox.information(self, "Nothing to add", message)

    def enter_svn_root(self):
        row = self.svn_profiles.currentRow()
        if row < 0:
            self.add_svn_profile("", "", "")
            row = self.svn_profiles.currentRow()
        current = self.svn_profiles.item(row, 2)
        value, accepted = QInputDialog.getText(
            self, "Working-copy root", "Path to the SVN working copy:",
            QLineEdit.EchoMode.Normal, current.text() if current else "",
        )
        if accepted and value.strip():
            self.svn_profiles.setItem(row, 2, QTableWidgetItem(value.strip().strip('"')))

    def detect_svn_tools(self):
        svn = discover_svn_executable(self.svn_executable.text())
        tortoise = discover_tortoise_executable(self.tortoise_executable.text())
        if svn:
            self.svn_executable.setText(svn)
        if tortoise:
            self.tortoise_executable.setText(tortoise)
        labels = []
        if svn:
            labels.append("SVN found")
        if tortoise:
            labels.append("TortoiseSVN found")
        self.svn_detection.setText(" · ".join(labels) if labels else "No SVN executables were found. Choose them manually.")

    def add_svn_profile(self, name: str, project: str, root: str):
        row = self.svn_profiles.rowCount()
        self.svn_profiles.insertRow(row)
        for column, value in enumerate((name, project, root)):
            self.svn_profiles.setItem(row, column, QTableWidgetItem(value))
        self.svn_profiles.setCurrentCell(row, 0)

    def remove_svn_profile(self):
        if self.svn_profiles.currentRow() >= 0:
            self.svn_profiles.removeRow(self.svn_profiles.currentRow())

    def browse_svn_profile(self):
        row = self.svn_profiles.currentRow()
        if row < 0:
            self.add_svn_profile("", "", "")
            row = self.svn_profiles.currentRow()
        current = self.svn_profiles.item(row, 2)
        path = QFileDialog.getExistingDirectory(self, "Choose SVN working copy", current.text() if current else "")
        if path:
            self.svn_profiles.setItem(row, 2, QTableWidgetItem(path))

    def _choose_executable(self, target: QLineEdit, title: str):
        path, _ = QFileDialog.getOpenFileName(self, title, target.text(), "Executables (*.exe);;All files (*)")
        if path:
            target.setText(path)

    def _choose_patch_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose patch shelf", self.svn_patch_root.text())
        if path:
            self.svn_patch_root.setText(path)

    def launchers_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); note = QLabel("Add commands to the command-center toolbar. They run only when clicked."); note.setWordWrap(True); layout.addWidget(note)
        self.launchers = QTableWidget(0, 2); self.launchers.setHorizontalHeaderLabels(["Label", "Command"]); self.launchers.horizontalHeader().setStretchLastSection(True)
        for entry in self.config.get("custom_launchers", []): self.add_launcher(entry.get("label", ""), entry.get("command", ""))
        actions = QHBoxLayout(); add = QPushButton("Add launcher"); remove = QPushButton("Remove selected"); add.clicked.connect(lambda: self.add_launcher("", "")); remove.clicked.connect(self.remove_launcher); actions.addWidget(add); actions.addWidget(remove); actions.addStretch(); layout.addWidget(self.launchers); layout.addLayout(actions); return tab

    def add_launcher(self, label: str, command: str):
        row = self.launchers.rowCount(); self.launchers.insertRow(row); self.launchers.setItem(row, 0, QTableWidgetItem(label)); self.launchers.setItem(row, 1, QTableWidgetItem(command))
    def remove_launcher(self):
        if self.launchers.currentRow() >= 0: self.launchers.removeRow(self.launchers.currentRow())
    def choose_vault(self):
        path = QFileDialog.getExistingDirectory(self, "Choose Obsidian vault", self.vault.text())
        if path: self.vault.setText(path)

    def values(self) -> dict:
        launchers = []
        for row in range(self.launchers.rowCount()):
            label, command = self.launchers.item(row, 0), self.launchers.item(row, 1)
            if label and command and label.text().strip() and command.text().strip(): launchers.append({"label": label.text().strip(), "command": command.text().strip()})
        svn_profiles = []
        for row in range(self.svn_profiles.rowCount()):
            values = [self.svn_profiles.item(row, column) for column in range(3)]
            name, project, root = (item.text().strip() if item else "" for item in values)
            if root:
                svn_profiles.append({"name": name or f"Working copy {row + 1}", "project": project, "root": root})
        return {
            **self.git_settings.values(),
            "polling_interval_minutes": self.poll.value(), "theme": self.theme.currentData(), "density": self.density.currentText(), "font_size": self.font_size.value(), "navigation_collapsed": self.navigation_collapsed.isChecked(), "default_page": self.default_page.currentText(),
            "backlog_base_url": self.base_url.text().strip(), "backlog_web_url": self.web_url.text().strip(), "backlog_assignee_id": self.assignee.value(), "backlog_hide_closed": self.hide_closed.isChecked(),
            "obsidian_vault_path": self.vault.text().strip(), "obsidian_tickets_folder": self.tickets_folder.text().strip(), "obsidian_daily_folder": self.daily_folder.text().strip(), "obsidian_programming_folder": self.programming_folder.text().strip(),
            "local_llm_url": self.local_llm_url.text().strip(), "local_llm_model": self.local_llm_model.currentText().strip(), "local_llm_timeout_seconds": self.local_llm_timeout.value(),
            "local_llm_thinking": self.local_llm_thinking.isChecked(), "local_llm_max_tokens": self.local_llm_max_tokens.value(), "local_llm_context_tokens": self.local_llm_context_tokens.value(),
            "calendar_default_view": self.default_view.currentText(), "calendar_default_minutes": self.default_minutes.value(), "show_weekends": self.weekends.isChecked(), "custom_launchers": launchers,
            "work_schedule": self._schedule_values(), "track_business_hours_only": self.track_business_hours.isChecked(), "general_project": self.general_project.text().strip(),
            "svn_executable": self.svn_executable.text().strip(), "tortoise_executable": self.tortoise_executable.text().strip(), "svn_patch_root": self.svn_patch_root.text().strip(), "svn_timeout_seconds": self.svn_timeout.value(), "svn_profiles": svn_profiles,
        }

    def pending_api_key(self) -> str: return self.api_key.text().strip()
