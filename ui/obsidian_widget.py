from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import quote, unquote, urlencode

from PyQt6.QtCore import QDir, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFileSystemModel, QFontMetrics
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
                             QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                             QTextBrowser, QTextEdit, QTreeView, QVBoxLayout,
                             QWidget)

from modules.ticket_utils import issue_key


# Widest the selected-ticket badge may grow, so a long summary cannot push the
# AI actions off the right edge at the 900px minimum window width.
TICKET_BADGE_WIDTH = 200


class ObsidianWidget(QWidget):
    settings_requested = pyqtSignal()
    file_saved = pyqtSignal(str)
    # The vault surface asks for AI work; MainWindow owns the worker and the vault
    # write, exactly as it does for the Ticket Detail page.
    ai_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    open_ticket_note_requested = pyqtSignal()
    note_ticket_detected = pyqtSignal(str)

    def __init__(self, vault_path: str = ""):
        super().__init__()
        self.vault_path = Path()
        self.current_path: Path | None = None
        self.ticket: dict | None = None
        self._loading = False
        self._tree_selection_timer = QTimer(self)
        self._tree_selection_timer.setSingleShot(True)
        self._tree_selection_timer.timeout.connect(self._apply_tree_selection)
        self._tree_selection_path = None

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Obsidian Workspace")
        title.setObjectName("pageTitle")
        self.location = QLabel("Vault not configured")
        self.location.setObjectName("muted")
        self.location.setMaximumWidth(240)
        self.new_note = QPushButton("New note")
        self.open_external = QPushButton("Open in Obsidian")
        self.save = QPushButton("Save")
        self.configure_vault = QPushButton("Connect a vault")
        self.configure_vault.clicked.connect(self.settings_requested)
        self.copy_path = QPushButton("Copy path")
        self.copy_path.setProperty("secondary", True)
        self.copy_path.setToolTip("Copy the vault-relative Markdown path to the clipboard")
        self.copy_path.setEnabled(False)
        header.addWidget(title)
        header.addWidget(self.location)
        header.addStretch()
        header.addWidget(self.configure_vault)
        header.addWidget(self.new_note)
        header.addWidget(self.open_external)
        header.addWidget(self.save)
        header.addWidget(self.copy_path)
        root.addLayout(header)

        # Ticket-aware AI bar. The vault is where notes are read, so the actions that
        # write notes belong here too, not only on the Ticket Detail page.
        ai_bar = QFrame()
        ai_bar.setObjectName("toolbarSurface")
        ai_layout = QHBoxLayout(ai_bar)
        ai_layout.setContentsMargins(8, 5, 8, 5)
        ai_layout.setSpacing(6)
        ai_heading = QLabel("Ticket AI")
        ai_heading.setObjectName("eyebrow")
        self.ticket_context = QLabel("No ticket selected")
        self.ticket_context.setObjectName("badge")
        self.ticket_context.setMaximumWidth(TICKET_BADGE_WIDTH + 20)
        self.ticket_context.setToolTip("Select a Personal Task, or open a ticket note, to bind these actions to it")
        self.open_ticket_note = QPushButton("Open ticket note")
        self.open_ticket_note.setProperty("secondary", True)
        self.open_ticket_note.setToolTip("Jump to the vault note for the selected ticket, creating it if needed")
        self.ai_note_only = QPushButton("Update note only")
        self.ai_note_only.setToolTip(
            "Regenerate this ticket's Obsidian note with the local AI and nothing else. "
            "You review the note before it is written, the previous version is backed up, "
            "and no study material or Backlog comment is touched."
        )
        self.ai_update_note = QPushButton("Draft note & lessons")
        self.ai_update_note.setProperty("secondary", True)
        self.ai_update_note.setToolTip(
            "Draft the full structured note plus study material. The preview opens in Ticket Detail; nothing is written until you apply it."
        )
        self.ai_lessons = QPushButton("Study material")
        self.ai_lessons.setProperty("secondary", True)
        self.ai_lessons.setToolTip("Turn the selected ticket into reusable programming lessons, without rewriting its note")
        self.ai_cancel = QPushButton("Stop")
        self.ai_cancel.setProperty("secondary", True)
        self.ai_cancel.setToolTip("Stop the running generation")
        self.ai_cancel.setEnabled(False)
        self.ai_status = QLabel("Ready")
        self.ai_status.setObjectName("muted")
        self.ai_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Ignored width plus a stretch factor: the message takes whatever is left
        # over instead of forcing the row wider than the window.
        self.ai_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._ai_status_text = "Ready"
        ai_layout.addWidget(ai_heading)
        ai_layout.addWidget(self.ticket_context)
        ai_layout.addWidget(self.open_ticket_note)
        ai_layout.addWidget(self.ai_note_only)
        ai_layout.addWidget(self.ai_update_note)
        ai_layout.addWidget(self.ai_lessons)
        ai_layout.addWidget(self.ai_cancel)
        ai_layout.addWidget(self.ai_status, 1)
        root.addWidget(ai_bar)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilters(["*.md"])
        self.model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(180)
        for column in range(1, 4):
            self.tree.hideColumn(column)

        # A real vault is too large to scroll; name filtering is the fast way in.
        browser = QWidget()
        self.browser_panel = browser
        browser_layout = QVBoxLayout(browser)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(4)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter notes by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip("Show only Markdown notes whose file name contains this text")
        browser_layout.addWidget(self.filter)
        browser_layout.addWidget(self.tree, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(5, 0, 0, 0)
        right_layout.setSpacing(5)
        note_header = QHBoxLayout()
        self.file_label = QLabel("Select a Markdown note")
        self.file_label.setObjectName("sectionTitle")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._file_label_text = self.file_label.text()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.preview_mode = QPushButton("Preview")
        self.edit_mode = QPushButton("Edit Markdown")
        for button in (self.preview_mode, self.edit_mode):
            button.setCheckable(True)
            button.setProperty("secondary", True)
            self.mode_group.addButton(button)
        self.preview_mode.setChecked(True)
        note_header.addWidget(self.file_label, 1)
        note_header.addWidget(self.preview_mode)
        note_header.addWidget(self.edit_mode)
        right_layout.addLayout(note_header)

        self.metadata_frame = QFrame()
        self.metadata_frame.setObjectName("surface")
        metadata_layout = QVBoxLayout(self.metadata_frame)
        metadata_layout.setContentsMargins(5, 4, 5, 5)
        metadata_layout.setSpacing(3)
        metadata_title = QLabel("Properties")
        metadata_title.setObjectName("muted")
        self.metadata = QTableWidget(0, 2)
        self.metadata.setHorizontalHeaderLabels(["Property", "Value"])
        self.metadata.verticalHeader().setVisible(False)
        self.metadata.setMaximumHeight(112)
        self.metadata.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.metadata.horizontalHeader().setStretchLastSection(True)
        metadata_layout.addWidget(metadata_title)
        metadata_layout.addWidget(self.metadata)
        right_layout.addWidget(self.metadata_frame)

        self.document_stack = QStackedWidget()
        self.preview = QTextBrowser()
        self.preview.setObjectName("markdownPreview")
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.setPlaceholderText("Select a Markdown note to see a rendered preview.")
        self.preview.document().setDefaultStyleSheet(
            "h1 { font-size: 22px; margin: 8px 0 10px; } "
            "h2 { font-size: 17px; margin: 14px 0 6px; } "
            "h3 { font-size: 14px; margin: 10px 0 4px; } "
            "blockquote { border-left: 3px solid #2dd4bf; margin-left: 4px; padding-left: 10px; color: #8b9bb1; } "
            "table { border-collapse: collapse; margin: 6px 0; } "
            "th, td { border: 1px solid #64748b; padding: 4px 7px; } "
            "code, pre { font-family: Consolas, monospace; padding: 2px 4px; } "
            "a { color: #2dd4bf; text-decoration: none; }"
        )
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Select or create a Markdown note...")
        self.editor.setAcceptRichText(False)
        self.document_stack.addWidget(self.preview)
        self.document_stack.addWidget(self.editor)
        right_layout.addWidget(self.document_stack, 1)

        split.addWidget(browser)
        split.addWidget(right)
        split.setSizes([245, 850])
        root.addWidget(split, 1)

        self.tree.clicked.connect(self.load_index)
        self.tree.doubleClicked.connect(self.load_index)
        self.save.clicked.connect(self.save_current)
        self.copy_path.clicked.connect(self.copy_current_path)
        self.open_external.clicked.connect(self.open_current)
        self.new_note.clicked.connect(self.create_note)
        self.preview_mode.clicked.connect(lambda: self.set_mode("preview"))
        self.edit_mode.clicked.connect(lambda: self.set_mode("edit"))
        self.editor.textChanged.connect(self._editor_changed)
        self.preview.anchorClicked.connect(self._open_preview_link)
        self.filter.textChanged.connect(self._apply_filter)
        self.open_ticket_note.clicked.connect(self.open_ticket_note_requested)
        self.ai_note_only.clicked.connect(lambda: self.ai_requested.emit("note_only"))
        self.ai_update_note.clicked.connect(lambda: self.ai_requested.emit("note"))
        self.ai_lessons.clicked.connect(lambda: self.ai_requested.emit("lessons"))
        self.ai_cancel.clicked.connect(self.cancel_requested)
        self.set_ticket(None)
        self.set_vault_path(vault_path)

    def _apply_filter(self, text: str) -> None:
        value = text.strip()
        self.model.setNameFilters([f"*{value}*.md"] if value else ["*.md"])

    def set_ticket(self, ticket: dict | None) -> None:
        """Bind the AI actions to the ticket the rest of the app has selected."""
        self.ticket = ticket
        if ticket:
            summary = str(ticket.get("summary") or "").strip()
            label = f"{issue_key(ticket)} · {summary}" if summary else issue_key(ticket)
            metrics = QFontMetrics(self.ticket_context.font())
            self.ticket_context.setText(metrics.elidedText(label, Qt.TextElideMode.ElideRight, TICKET_BADGE_WIDTH))
            self.ticket_context.setToolTip(label)
        else:
            self.ticket_context.setText("No ticket selected")
            self.ticket_context.setToolTip("Select a Personal Task, or open a ticket note, to bind these actions to it")
        running = self.ai_cancel.isEnabled()
        for button in (self.open_ticket_note, self.ai_note_only, self.ai_update_note, self.ai_lessons):
            button.setEnabled(bool(ticket) and not running)

    def set_ai_busy(self, busy: bool, message: str = "") -> None:
        for button in (self.open_ticket_note, self.ai_note_only, self.ai_update_note, self.ai_lessons):
            button.setEnabled(bool(self.ticket) and not busy)
        self.ai_cancel.setEnabled(busy)
        self.set_ai_status(message or ("Generating…" if busy else "Ready"))

    def set_ai_status(self, message: str) -> None:
        self._ai_status_text = " ".join(str(message).split())
        self.ai_status.setToolTip(self._ai_status_text)
        self._render_ai_status()

    def _render_file_label(self) -> None:
        """Elide the note path from the left: the file name matters most."""
        metrics = QFontMetrics(self.file_label.font())
        width = max(120, self.file_label.width())
        self.file_label.setText(metrics.elidedText(self._file_label_text, Qt.TextElideMode.ElideLeft, width))

    def _render_ai_status(self) -> None:
        """Fit the message to the space the row actually has left."""
        metrics = QFontMetrics(self.ai_status.font())
        width = max(40, self.ai_status.width())
        self.ai_status.setText(metrics.elidedText(self._ai_status_text, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_ai_status()
        self._render_file_label()

    @staticmethod
    def _frontmatter_ticket(content: str) -> str:
        """The ticket key a note declares, so browsing the vault can drive selection."""
        if not content.startswith("---"):
            return ""
        match = re.search(r"(?m)^ticket_id:\s*\"?([^\"\n]+)\"?\s*$", content.split("---", 2)[1] if content.count("---") >= 2 else "")
        return match.group(1).strip() if match else ""

    def set_vault_path(self, value: str) -> None:
        self.vault_path = Path(value).expanduser() if value else Path()
        valid = bool(value and self.vault_path.is_dir())
        self.configure_vault.setVisible(not valid)
        self.browser_panel.setVisible(valid)
        self.metadata_frame.setVisible(valid)
        self.preview_mode.setVisible(valid)
        self.edit_mode.setVisible(valid)
        self.file_label.setVisible(valid)
        self.tree.setVisible(valid)
        for control in (self.new_note, self.open_external, self.save, self.copy_path):
            control.setVisible(valid)
        if valid:
            root_index = self.model.setRootPath(str(self.vault_path))
            self.tree.setRootIndex(root_index)
            self.location.setText(str(self.vault_path))
            self.location.setToolTip(str(self.vault_path))
            self.setEnabled(True)
        else:
            self._loading = True
            self.current_path = None
            self.copy_path.setEnabled(False)
            self.location.setText("Choose a vault in Settings")
            self.model.setRootPath("")
            self.tree.setRootIndex(self.model.index(""))
            self.editor.clear()
            self.preview.setMarkdown("## Your knowledge, close at hand.\n\nConnect an Obsidian vault to browse Markdown notes, keep ticket context together, and turn your work into reusable learning.\n\nUse **Connect a vault** to choose a folder in Settings.")
            self._loading = False
            self.save.setText("Save")
            self.setEnabled(True)

    def load_index(self, index) -> None:
        path = Path(self.model.filePath(index))
        if path.is_file() and path.suffix.casefold() == ".md":
            self.load_path(path)

    def load_path(self, path: Path) -> None:
        path = path.expanduser().resolve()
        vault = self.vault_path.expanduser().resolve()
        if path != vault and vault not in path.parents:
            QMessageBox.warning(self, "Invalid note path", "The note must be inside the configured Obsidian vault.")
            return
        if path.suffix.casefold() != ".md":
            QMessageBox.warning(self, "Invalid note type", "Only Markdown notes can be opened in the Obsidian workspace.")
            return
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "Could not read note", str(exc))
            return
        self.current_path = path
        try:
            relative = path.relative_to(self.vault_path)
        except ValueError:
            relative = path
        self._file_label_text = str(relative)
        self.file_label.setToolTip(str(path))
        self._render_file_label()
        self._loading = True
        self.editor.setPlainText(content)
        self._loading = False
        self._show_metadata(content)
        self._render_preview(content)
        self.save.setText("Save")
        self.copy_path.setEnabled(True)
        self.set_mode("preview")
        self._select_tree_path(path)
        key = self._frontmatter_ticket(content)
        if key and (not self.ticket or issue_key(self.ticket) != key):
            self.note_ticket_detected.emit(key)

    def _select_tree_path(self, path: Path) -> None:
        self._tree_selection_path = path
        self._tree_selection_timer.start(0)

    def _apply_tree_selection(self) -> None:
        index = self.model.index(str(self._tree_selection_path))
        if index.isValid():
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)

    def set_mode(self, mode: str) -> None:
        previewing = mode == "preview"
        if previewing:
            self._render_preview(self.editor.toPlainText())
        self.document_stack.setCurrentWidget(self.preview if previewing else self.editor)
        self.preview_mode.setChecked(previewing)
        self.edit_mode.setChecked(not previewing)

    @staticmethod
    def _body_without_frontmatter(content: str) -> str:
        if content.startswith("---\n"):
            parts = content.split("---", 2)
            if len(parts) == 3:
                return parts[2].lstrip()
        return content

    def _render_preview(self, content: str) -> None:
        body = self._body_without_frontmatter(content)

        def wiki_link(match: re.Match) -> str:
            target = match.group(1).strip()
            label = (match.group(2) or target.split("/")[-1]).strip()
            return f"[{label}](tynote:{quote(target)})"

        body = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", wiki_link, body)
        self.preview.setMarkdown(body or "_This note is empty._")

    def _show_metadata(self, content: str) -> None:
        self.metadata.setRowCount(0)
        if not content.startswith("---\n"):
            self.metadata_frame.hide()
            return
        try:
            frontmatter = content.split("---", 2)[1]
        except IndexError:
            self.metadata_frame.hide()
            return
        for line in frontmatter.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            row = self.metadata.rowCount()
            self.metadata.insertRow(row)
            self.metadata.setItem(row, 0, QTableWidgetItem(key.strip()))
            self.metadata.setItem(row, 1, QTableWidgetItem(value.strip().strip('"')))
        self.metadata_frame.setVisible(self.metadata.rowCount() > 0)

    def _editor_changed(self) -> None:
        if not self._loading:
            self.save.setText("Save *")

    def save_current(self) -> None:
        if not self.current_path:
            return
        try:
            temporary = self.current_path.with_suffix(".md.tmp")
            temporary.write_text(self.editor.toPlainText(), encoding="utf-8")
            temporary.replace(self.current_path)
            self._show_metadata(self.editor.toPlainText())
            self._render_preview(self.editor.toPlainText())
            self.save.setText("Save")
            self.file_saved.emit(str(self.current_path))
        except OSError as exc:
            QMessageBox.warning(self, "Could not save note", str(exc))

    def copy_current_path(self) -> None:
        if not self.current_path:
            return
        try:
            relative = self.current_path.relative_to(self.vault_path).as_posix()
        except ValueError:
            relative = self.current_path.as_posix()
        QApplication.clipboard().setText(relative)

    def create_note(self) -> None:
        if not self.vault_path.exists():
            QMessageBox.information(self, "Vault needed", "Choose an Obsidian vault in Settings first.")
            return
        relative, accepted = QInputDialog.getText(self, "New Markdown note", "Path inside vault", text="Inbox/New note.md")
        if not accepted or not relative.strip():
            return
        relative_path = Path(relative.strip())
        if relative_path.suffix.casefold() != ".md":
            relative_path = relative_path.with_suffix(".md")
        path = (self.vault_path / relative_path).resolve()
        if self.vault_path.resolve() not in path.parents:
            QMessageBox.warning(self, "Invalid path", "The note must be inside the configured vault.")
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
            self.load_path(path)
            self.set_mode("edit")
        except FileExistsError:
            self.load_path(path)
        except OSError as exc:
            QMessageBox.warning(self, "Could not create note", str(exc))

    def _open_preview_link(self, url: QUrl) -> None:
        if url.scheme() == "tynote":
            target = unquote(url.path() or url.toString().split(":", 1)[-1])
            path = (self.vault_path / target).with_suffix(".md").resolve()
            if path.exists():
                self.load_path(path)
            return
        if url.scheme() in {"http", "https", "mailto", "obsidian"}:
            QDesktopServices.openUrl(url)
            return
        if self.current_path:
            path = (self.current_path.parent / unquote(url.path())).resolve()
            if path.suffix.casefold() != ".md":
                path = path.with_suffix(".md")
            if path.exists() and self.vault_path.resolve() in (path, *path.parents):
                self.load_path(path)

    def open_current(self) -> None:
        if self.current_path and self.vault_path.exists():
            relative = self.current_path.relative_to(self.vault_path).with_suffix("").as_posix()
            query = urlencode({"vault": self.vault_path.name, "file": relative})
            QDesktopServices.openUrl(QUrl(f"obsidian://open?{query}"))
        elif self.vault_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.vault_path)))
