"""Application chrome, responsive navigation, and workspace-level presentation."""
from __future__ import annotations
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QVBoxLayout
from modules.launcher import open_codex, run_custom
from ui import theme
from ui.design import button, retint_icons, ElidedLabel
from ui.styles import theme_styles
from ui.timer_chip import TimerChip

NAV_SECTIONS = (
    ("YOUR WORK", ("Command Center", "Personal Tasks", "Ticket Detail")),
    ("WORKSPACE", ("Review Board", "Source Control", "Obsidian")),
    ("YOUR DAY", ("Calendar", "Time Insights", "Morning Standup")),
)
PAGES = tuple(name for _, names in NAV_SECTIONS for name in names)
PAGE_INFO = {
    "Command Center": ("Today", "today"), "Personal Tasks": ("Tasks", "tasks"),
    "Ticket Detail": ("Ticket studio", "detail"), "Review Board": ("Review board", "review"),
    "Source Control": ("Source control", "code"), "Obsidian": ("Notes & knowledge", "notes"),
    "Calendar": ("Calendar", "calendar"), "Time Insights": ("Time insights", "clock"),
    "Morning Standup": ("Standup", "team"),
}
NAV_WIDTH, NAV_COLLAPSED_WIDTH = 200, 56


class ShellMixin:
    def build_navigation(self):
        frame = QFrame()
        frame.setObjectName("navigation")
        self.nav_layout = QVBoxLayout(frame)
        self.nav_layout.setContentsMargins(12, 14, 12, 12)
        self.nav_layout.setSpacing(3)
        brand = QHBoxLayout()
        self.nav_mark = QLabel("ty")
        self.nav_mark.setObjectName("brandMark")
        self.nav_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nav_mark.setFixedSize(30, 30)
        self.nav_brand = QLabel("work hub")
        self.nav_brand.setObjectName("brand")
        brand.addWidget(self.nav_mark)
        brand.addWidget(self.nav_brand)
        brand.addStretch()
        self.nav_layout.addLayout(brand)
        self.nav_subtitle = QLabel("A little space for better work.")
        self.nav_subtitle.setObjectName("muted")
        self.nav_layout.addWidget(self.nav_subtitle)
        self.nav_layout.addSpacing(12)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons, self.nav_section_labels = {}, []
        for section, names in NAV_SECTIONS:
            heading = QLabel(section)
            heading.setObjectName("navSection")
            self.nav_layout.addWidget(heading)
            self.nav_section_labels.append(heading)
            for name in names:
                control = button(self._nav_text(name, False), lambda _, page=name: self.navigate(page), glyph=PAGE_INFO[name][1])
                control.setProperty("nav", True)
                control.setCheckable(True)
                control.setToolTip(f"{PAGE_INFO[name][0]}  ·  Ctrl+{PAGES.index(name)+1}")
                control.setAccessibleName(PAGE_INFO[name][0])
                control.setIconSize(QSize(17, 17))
                self.nav_buttons[name] = control
                self.nav_group.addButton(control)
                self.nav_layout.addWidget(control)
            self.nav_layout.addSpacing(6)
        self.nav_layout.addStretch()
        self.nav_connection = QLabel("Connecting to your workspace…")
        self.nav_connection.setWordWrap(True)
        self.nav_connection.setObjectName("muted")
        self.nav_layout.addWidget(self.nav_connection)
        self.nav_layout.addSpacing(6)
        self.nav_settings = button("Settings", self.settings, glyph="settings")
        self.nav_settings.setProperty("nav", True)
        self.nav_settings.setToolTip("Settings · Ctrl+,")
        self.nav_layout.addWidget(self.nav_settings)
        return frame

    def _nav_text(self, name, collapsed):
        count = self.nav_badges.get(name, 0)
        if collapsed:
            return "•" if count else ""
        return PAGE_INFO[name][0].replace("&", "&&") + (f"    {count}" if count else "")

    def build_toolbar(self):
        previous = self.last_synced.text() if hasattr(self, "last_synced") else "Not synced"
        layout = self.toolbar.layout()
        if layout is None:
            layout = QHBoxLayout(self.toolbar)
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.setContentsMargins(16, 7, 16, 7)
        layout.setSpacing(8)
        collapse = button("", self.toggle_navigation, glyph="menu")
        collapse.setToolTip("Toggle sidebar · Ctrl+B")
        collapse.setAccessibleName("Toggle sidebar")
        self.breadcrumb = ElidedLabel("Workspace  /  Today")
        self.breadcrumb.setMinimumWidth(100)
        self.breadcrumb.setObjectName("muted")
        search = button("Search tasks or commands…     Ctrl+K", self.command_menu, glyph="search")
        search.setObjectName("commandSearch")
        search.setMinimumWidth(175)
        search.setMaximumWidth(340)
        search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.timer_chip = TimerChip()
        self.timer_chip.toggle_requested.connect(self.toggle_tracking)
        self.timer_chip.open_requested.connect(lambda: self.navigate("Command Center"))
        self.last_synced = QLabel(previous)
        self.last_synced.setObjectName("muted")
        self.theme_button = button("", glyph="palette")
        self.theme_button.setToolTip(f"Appearance: {theme.resolve(self.config['theme']).label} · Ctrl+Shift+T for the next one")
        self.theme_button.setAccessibleName("Choose a theme")
        self.theme_button.setMenu(self.theme_menu())
        create = button("New", glyph="plus", primary=True)
        menu = QMenu(create)
        menu.addAction("Capture a thought", self.focus_dashboard_capture)
        menu.addAction("Calendar event", self.calendar_new_event)
        menu.addAction("Ticket note", self.create_note)
        menu.addSeparator()
        menu.addAction("Open Codex", open_codex)
        menu.addAction("Open Backlog", self.open_backlog_dashboard)
        for launcher in self.config.get("custom_launchers", []):
            menu.addAction(launcher["label"], lambda checked=False, command=launcher["command"]: run_custom(command))
        create.setMenu(menu)
        layout.addWidget(collapse)
        layout.addWidget(self.breadcrumb, 1)
        layout.addWidget(search, 2)
        layout.addWidget(self.timer_chip)
        layout.addWidget(self.last_synced)
        layout.addWidget(self.theme_button)
        layout.addWidget(create)

    def theme_menu(self) -> QMenu:
        """Every registered appearance, grouped by dark and light, current one checked."""
        menu = QMenu(self.theme_button)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self.theme_actions = {}
        for heading, dark in (("DARK", True), ("LIGHT", False)):
            section = menu.addAction(heading)
            section.setEnabled(False)
            for key, label, is_dark in theme.choices():
                if is_dark != dark:
                    continue
                action = menu.addAction(label, lambda _=False, name=key: self.apply_theme(name))
                action.setCheckable(True)
                action.setToolTip(theme.resolve(key).blurb)
                group.addAction(action)
                self.theme_actions[key] = action
        self._check_theme_action()
        return menu

    def _check_theme_action(self) -> None:
        current = theme.resolve(self.config["theme"]).key
        for key, action in getattr(self, "theme_actions", {}).items():
            action.setChecked(key == current)

    def apply_theme(self, key: str) -> None:
        """Switch appearance everywhere: stylesheet, glyphs, and painted workspaces."""
        self.config["theme"] = theme.resolve(key).key
        self._save_preferences()
        self.refresh_theme()
        self.statusBar().showMessage(f"Theme: {theme.active().label}", 2500)

    def cycle_theme(self) -> None:
        self.apply_theme(theme.next_key(self.config["theme"]))

    def refresh_theme(self) -> None:
        """Re-apply the configured theme to widgets that cannot read the stylesheet."""
        QApplication.instance().setStyleSheet(theme_styles(self.config["theme"], self.config["density"], self.config["font_size"]))
        retint_icons(self)
        self.theme_button.setToolTip(f"Appearance: {theme.active().label} · Ctrl+Shift+T for the next one")
        self._check_theme_action()
        self.source_control.set_theme(self.config["theme"])
        self.tickets.refresh_theme()
        self.calendar.render()

    def _apply_responsive_layout(self):
        if not hasattr(self, "navigation"):
            return
        inspector_visible = self.inspector.isVisible()
        auto = self.width() < 1120 or (inspector_visible and self.width() < 1480)
        collapsed = self._nav_user_collapsed or (auto and not self._nav_force_expanded)
        self._nav_actual_collapsed = collapsed
        self.navigation.setFixedWidth(NAV_COLLAPSED_WIDTH if collapsed else NAV_WIDTH)
        self.nav_layout.setContentsMargins(8 if collapsed else 12, 14, 8 if collapsed else 12, 12)
        self.nav_brand.setVisible(not collapsed)
        self.nav_subtitle.setVisible(not collapsed)
        self.nav_connection.setVisible(not collapsed)
        for name, control in self.nav_buttons.items():
            control.setText(self._nav_text(name, collapsed))
        for heading in self.nav_section_labels:
            heading.setVisible(not collapsed)
        self.nav_settings.setText("" if collapsed else "Settings")
        self.navigation.setVisible(not self._focus_mode)
        self.last_synced.setVisible(self.width() >= 1280)
        # The chip never hides: an unnoticed running timer is the problem it solves.
        self.timer_chip.subject.setVisible(self.width() >= 1000)
        if inspector_visible:
            width = 310 if self.width() < 1100 else 345
            self.content_splitter.setSizes([max(420, self.content_splitter.width() - width), width])
