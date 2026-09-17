from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QTabWidget, QTextBrowser,
                             QVBoxLayout, QWidget)

from modules.ticket_utils import issue_key, project_name


class TicketDetailWidget(QWidget):
    analyze_requested = pyqtSignal(dict, str)
    apply_note_requested = pyqtSignal(dict, dict)
    apply_lessons_requested = pyqtSignal(dict, dict)
    open_note_requested = pyqtSignal(dict)
    open_backlog_requested = pyqtSignal(dict)
    environment_requested = pyqtSignal(dict)
    draft_comment_requested = pyqtSignal(dict)
    source_control_requested = pyqtSignal(dict, str)
    cancel_requested = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.ticket: dict | None = None
        self.analysis: dict | None = None
        # Study material is tracked apart from the note template so it can be saved
        # on its own, without committing to a full ticket-note rewrite.
        self.study_material: dict | None = None
        self.note_content = ""
        self.comments: list[dict] = []
        self.patch_count = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(9)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel("Ticket Detail")
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel("Select a ticket to inspect its full context and create a local-AI brief.")
        self.subtitle.setObjectName("muted")
        self._full_title = self.title.text()
        self._full_subtitle = self.subtitle.text()
        for label in (self.title, self.subtitle):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        self.model_badge = QLabel(self._model_text())
        self.model_badge.setObjectName("badge")
        self.model_badge.setToolTip(self._model_parts()[1])
        self.open_backlog = QPushButton("Open Backlog")
        self.open_backlog.setProperty("secondary", True)
        self.environments = QPushButton("Environments")
        self.environments.setProperty("secondary", True)
        self.open_note = QPushButton("Open note")
        self.open_note.setProperty("secondary", True)
        header.addLayout(titles, 1)
        header.addWidget(self.model_badge)
        header.addWidget(self.environments)
        header.addWidget(self.open_backlog)
        header.addWidget(self.open_note)
        root.addLayout(header)

        self.meta = QLabel("No ticket selected")
        self.meta.setObjectName("muted")
        self.meta.setWordWrap(True)
        root.addWidget(self.meta)

        # Two labelled rows: what the model produces, then where the result is written.
        # A single row of six controls could not stay readable at the 900px minimum.
        controls = QFrame()
        controls.setObjectName("toolbarSurface")
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(8, 5, 8, 5)
        control_layout.setSpacing(5)

        generate_row = QHBoxLayout()
        generate_row.setSpacing(6)
        generate_heading = QLabel("Generate")
        generate_heading.setObjectName("eyebrow")
        self.generate = QPushButton("English brief")
        self.generate.setToolTip(
            "Uses the local AI server, Backlog comments, the existing Obsidian note, and this ticket's saved SVN patches"
        )
        self.prepare_note = QPushButton("Obsidian note draft")
        self.prepare_note.setProperty("secondary", True)
        self.prepare_note.setToolTip("Generates the full structured ticket template together with its study material")
        self.lessons = QPushButton("Study material")
        self.lessons.setProperty("secondary", True)
        self.lessons.setToolTip(
            "Generate reusable programming lessons from this ticket alone, without drafting a ticket-note rewrite"
        )
        self.draft_comment = QPushButton("Backlog comment (JP)")
        self.draft_comment.setProperty("secondary", True)
        self.draft_comment.setToolTip("Draft the fixed Japanese comment template from this ticket's verified SVN patch")
        self.cancel_generation = QPushButton("Stop")
        self.cancel_generation.setProperty("secondary", True)
        self.cancel_generation.setToolTip("Stop the running generation")
        self.cancel_generation.setEnabled(False)
        self.ai_status = QLabel("Ready")
        self.ai_status.setObjectName("muted")
        generate_row.addWidget(generate_heading)
        generate_row.addWidget(self.generate)
        generate_row.addWidget(self.prepare_note)
        generate_row.addWidget(self.lessons)
        generate_row.addWidget(self.draft_comment)
        generate_row.addWidget(self.cancel_generation)
        generate_row.addStretch()
        control_layout.addLayout(generate_row)

        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        save_heading = QLabel("Save")
        save_heading.setObjectName("eyebrow")
        self.apply = QPushButton("Apply note + study material")
        self.apply.setEnabled(False)
        self.apply.setToolTip("Preview first; applying creates a history backup before updating the ticket note")
        self.apply_lessons = QPushButton("Save study material only")
        self.apply_lessons.setProperty("secondary", True)
        self.apply_lessons.setEnabled(False)
        self.apply_lessons.setToolTip(
            "Write only the lesson notes to the vault. The ticket note is not rewritten and nothing is posted to Backlog."
        )
        self.copy = QPushButton("Copy brief")
        self.copy.setProperty("secondary", True)
        self.copy.setEnabled(False)
        save_row.addWidget(save_heading)
        save_row.addWidget(self.apply)
        save_row.addWidget(self.apply_lessons)
        save_row.addWidget(self.copy)
        save_row.addStretch()
        save_row.addWidget(self.ai_status)
        control_layout.addLayout(save_row)
        root.addWidget(controls)

        source = QFrame()
        source.setObjectName("toolbarSurface")
        source_layout = QHBoxLayout(source)
        source_layout.setContentsMargins(8, 5, 8, 5)
        heading = QLabel("Source control")
        heading.setObjectName("eyebrow")
        self.make_patch = QPushButton("Make patch for this ticket")
        self.make_patch.setToolTip(
            "Open the SVN workspace with this ticket already set as the patch context, then create the patch"
        )
        self.view_patches = QPushButton("Patches")
        self.view_patches.setProperty("secondary", True)
        self.view_patches.setToolTip("Open this ticket's patch shelf")
        self.shelve_changes = QPushButton("Shelve changes")
        self.shelve_changes.setProperty("secondary", True)
        self.shelve_changes.setToolTip("Create and verify a ticket patch, then revert exactly those paths")
        self.open_source_control = QPushButton("SVN workspace")
        self.open_source_control.setProperty("secondary", True)
        self.open_source_control.setToolTip("Open Source Control for this ticket (Ctrl+Alt+S)")
        self.patch_state = QLabel("No patches for this ticket")
        self.patch_state.setObjectName("muted")
        source_layout.addWidget(heading)
        source_layout.addWidget(self.make_patch)
        source_layout.addWidget(self.shelve_changes)
        source_layout.addWidget(self.view_patches)
        source_layout.addWidget(self.open_source_control)
        source_layout.addStretch()
        source_layout.addWidget(self.patch_state)
        root.addWidget(source)

        self.tabs = QTabWidget()
        self.overview = QTextBrowser()
        self.overview.setOpenLinks(False)
        self.current_note = QTextBrowser()
        self.comments_view = QTextBrowser()
        self.activity_view = QTextBrowser()
        self.brief = QTextBrowser()
        self.note_preview = QTextBrowser()
        self.learning = QTextBrowser()
        for view in (self.overview, self.current_note, self.comments_view, self.activity_view, self.brief, self.note_preview, self.learning):
            view.setObjectName("markdownPreview")
        self.tabs.addTab(self.overview, "Ticket context")
        self.tabs.addTab(self.current_note, "Current note")
        self.tabs.addTab(self.comments_view, "Backlog comments")
        self.tabs.addTab(self.activity_view, "Activity")
        self.tabs.addTab(self.brief, "AI brief")
        self.tabs.addTab(self.note_preview, "Obsidian update preview")
        self.tabs.addTab(self.learning, "Study material")
        root.addWidget(self.tabs, 1)

        footer = QLabel("AI output is a draft. Nothing is written to Obsidian until you review the preview and click Apply; the previous ticket note is backed up first.")
        footer.setObjectName("muted")
        footer.setWordWrap(True)
        root.addWidget(footer)

        self.generate.clicked.connect(self._generate)
        self.draft_comment.clicked.connect(lambda: self.ticket and self.draft_comment_requested.emit(self.ticket))
        self.prepare_note.clicked.connect(self._prepare_note)
        self.lessons.clicked.connect(self._generate_lessons)
        self.copy.clicked.connect(self._copy)
        self.apply.clicked.connect(self._apply)
        self.apply_lessons.clicked.connect(self._apply_lessons)
        self.open_note.clicked.connect(lambda: self.ticket and self.open_note_requested.emit(self.ticket))
        self.open_backlog.clicked.connect(lambda: self.ticket and self.open_backlog_requested.emit(self.ticket))
        self.environments.clicked.connect(lambda: self.ticket and self.environment_requested.emit(self.ticket))
        self.cancel_generation.clicked.connect(self.cancel_requested)
        self.make_patch.clicked.connect(lambda: self._source_control("create_patch"))
        self.shelve_changes.clicked.connect(lambda: self._source_control("shelve"))
        self.view_patches.clicked.connect(lambda: self._source_control("patches"))
        self.open_source_control.clicked.connect(lambda: self._source_control("open"))
        self._set_enabled(False)
        self.overview.setMarkdown("## A workspace for the whole ticket.\n\nSelect a task to bring its context, notes, comments, and patches together.\n\n**Understand** the current situation with a local AI brief.\n\n**Work** with linked notes and ticket-specific source control.\n\n**Capture** the outcome as an Obsidian note or study material.\n\nOpen Tasks from the sidebar, or press **Ctrl+K** to find a ticket.")

    def _render_titles(self) -> None:
        """Elide the heading rather than let it run under the model badge."""
        for label, text in ((self.title, self._full_title), (self.subtitle, self._full_subtitle)):
            width = max(160, label.width())
            label.setText(QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight, width))
            label.setToolTip(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_titles()

    def _status(self, message: str) -> None:
        """Set the AI status, trimmed so a long message cannot widen the row."""
        text = " ".join(str(message).split())
        self.ai_status.setText(text if len(text) <= 64 else text[:63] + "…")
        self.ai_status.setToolTip(text)

    def _model_parts(self) -> tuple[str, str]:
        """Badge text plus the full detail for its tooltip."""
        model = str(self.config.get("local_llm_model") or "").strip()
        name = model.replace("\\", "/").rsplit("/", 1)[-1]
        if name.casefold().endswith(".gguf"):
            name = name[:-5]
        url = str(self.config.get("local_llm_url") or "").strip()
        host = url.split("//", 1)[-1] or "not configured"
        badge = f"Local AI · {name}" if name else f"Local AI · {host}"
        detail = f"Server: {url or 'not configured'}\nModel: {name or 'whatever the server has loaded'}"
        return badge, detail

    def _model_text(self) -> str:
        return self._model_parts()[0]

    def update_config(self, config: dict) -> None:
        self.config = config
        badge, detail = self._model_parts()
        self.model_badge.setText(badge)
        self.model_badge.setToolTip(detail)

    def _set_enabled(self, enabled: bool) -> None:
        self.generate.setEnabled(enabled)
        self.cancel_generation.setEnabled(False)
        self.draft_comment.setEnabled(enabled and self.patch_count > 0)
        self.prepare_note.setEnabled(enabled)
        self.lessons.setEnabled(enabled)
        self.apply_lessons.setEnabled(enabled and bool(self.study_material))
        self.open_note.setEnabled(enabled)
        self.open_backlog.setEnabled(enabled)
        self.environments.setEnabled(enabled)
        self.make_patch.setEnabled(enabled)
        self.shelve_changes.setEnabled(enabled)
        self.open_source_control.setEnabled(enabled)
        self.view_patches.setEnabled(enabled and self.patch_count > 0)

    def _source_control(self, action: str) -> None:
        if self.ticket:
            self.source_control_requested.emit(self.ticket, action)

    def set_patch_count(self, key: str, count: int) -> None:
        """Show how many shelved patches exist for the displayed ticket."""
        current = issue_key(self.ticket) if self.ticket else ""
        if key and current and key.strip().upper() != current.strip().upper():
            return
        self.patch_count = max(0, int(count))
        self.view_patches.setText(f"Patches ({self.patch_count})" if self.patch_count else "Patches")
        self.view_patches.setEnabled(bool(self.ticket) and self.patch_count > 0)
        self.draft_comment.setEnabled(bool(self.ticket) and self.patch_count > 0 and not self.cancel_generation.isEnabled())
        if not self.ticket:
            self.patch_state.setText("No ticket selected")
        elif self.patch_count:
            self.patch_state.setText(f"{self.patch_count} patch{'es' if self.patch_count != 1 else ''} in the shelf")
        else:
            self.patch_state.setText("No patches for this ticket")

    def set_ticket(self, ticket: dict, note_content: str = "", note_path: str = "", comments: list[dict] | None = None) -> None:
        key = issue_key(ticket)
        if not self.ticket or issue_key(self.ticket) != key:
            self.analysis = None
            self.study_material = None
            self.copy.setEnabled(False)
            self.apply.setEnabled(False)
            self.apply_lessons.setEnabled(False)
            self.brief.setMarkdown("_Generate a local-AI brief to summarize this ticket._")
            self.note_preview.setMarkdown("_The template update preview will appear after analysis._")
            self.learning.setMarkdown("_Generate study material to turn this ticket into reusable programming lessons._")
        changed = not self.ticket or issue_key(self.ticket) != key
        self.ticket = ticket
        if changed:
            # The real count arrives right after from the patch-shelf lookup.
            self.set_patch_count(key, 0)
        self.note_content = note_content
        self.comments = list(comments or [])
        self._full_title = f"{key} · {ticket.get('summary', '')}"
        assignee = (ticket.get("assignee") or {}).get("name") or "Unassigned"
        self._full_subtitle = f"{project_name(ticket)} · {(ticket.get('status') or {}).get('name', '')} · Assigned to {assignee}"
        self._render_titles()
        self.meta.setText(f"Priority: {(ticket.get('priority') or {}).get('name', 'Not set')}   ·   Due: {ticket.get('dueDate') or 'Not set'}   ·   Updated: {str(ticket.get('updated') or 'Unknown')[:19]}   ·   Note: {note_path or 'Not created'}")
        description = ticket.get("description") or "_No description provided._"
        custom = []
        for field in ticket.get("customFields", []):
            if field.get("value") not in (None, "", []):
                custom.append(f"- **{field.get('name', 'Field')}:** {field.get('value')}")
        status = (ticket.get("status") or {}).get("name", "Unknown")
        priority = (ticket.get("priority") or {}).get("name", "Not set")
        issue_type = (ticket.get("issueType") or {}).get("name", "Not set")
        categories = ", ".join(item.get("name", "") for item in ticket.get("category", []) if item.get("name")) or "None"
        self.overview.setMarkdown(
            f"## Ticket metadata\n\n| Field | Value |\n| --- | --- |\n| Key | {key} |\n| Status | {status} |\n| Priority | {priority} |\n"
            f"| Assignee | {assignee} |\n| Issue type | {issue_type} |\n| Categories | {categories} |\n| Created | {str(ticket.get('created') or 'Unknown')[:19]} |\n"
            f"| Updated | {str(ticket.get('updated') or 'Unknown')[:19]} |\n| Due | {ticket.get('dueDate') or 'Not set'} |\n\n"
            f"## Description\n\n{description}\n\n## Additional fields\n\n" + ("\n".join(custom) or "_None._")
        )
        self.current_note.setMarkdown(note_content or "_No associated Obsidian note exists yet._")
        self.set_comments(self.comments)
        self._set_enabled(True)
        self._status("Ready")

    def set_comments(self, comments: list[dict]) -> None:
        self.comments = list(comments or [])
        if not self.comments:
            self.comments_view.setMarkdown("_No Backlog comments were loaded._")
            self.activity_view.setMarkdown("_No activity has been loaded for this ticket._")
            return
        blocks = []
        for comment in self.comments:
            author = (comment.get("createdUser") or {}).get("name") or "Unknown author"
            created = comment.get("created") or "Unknown date"
            content = comment.get("content") or "_Empty comment._"
            blocks.append(f"### {author} · {created}\n\n{content}")
        self.comments_view.setMarkdown("## Backlog comments\n\n" + "\n\n---\n\n".join(blocks))
        if self.ticket:
            key = issue_key(self.ticket)
            status = (self.ticket.get("status") or {}).get("name", "Unknown")
            timeline = [f"### Ticket created\n\n{self.ticket.get('created') or 'Unknown date'}", f"### Current status\n\n**{status}**\n\nLast updated: {self.ticket.get('updated') or 'Unknown date'}"]
            timeline.extend(f"### Comment · {(comment.get('createdUser') or {}).get('name') or 'Unknown author'}\n\n{comment.get('created') or 'Unknown date'}\n\n{comment.get('content') or '_Empty comment._'}" for comment in self.comments)
            self.activity_view.setMarkdown(f"## Activity for {key}\n\n" + "\n\n---\n\n".join(timeline))

    def comments_loading(self) -> None:
        self.comments_view.setMarkdown("_Loading the full Backlog comment history…_")

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.generate.setEnabled(bool(self.ticket) and not busy)
        self.draft_comment.setEnabled(bool(self.ticket) and not busy)
        self.prepare_note.setEnabled(bool(self.ticket) and not busy)
        self.lessons.setEnabled(bool(self.ticket) and not busy)
        self.apply.setEnabled(bool(self.analysis) and not busy)
        self.apply_lessons.setEnabled(bool(self.study_material) and not busy)
        self.cancel_generation.setEnabled(busy)
        self._status(message or ("Generating…" if busy else "Ready"))
        if busy:
            self.tabs.setCurrentWidget(self.brief)
            self.brief.setMarkdown(
                "_The local AI server is reviewing the ticket, recent Backlog comments, the existing note, "
                "and this ticket's saved SVN patches…_"
            )

    def set_progress(self, message: str) -> None:
        """Live status while a generation runs, so a slow model does not look stuck."""
        if self.cancel_generation.isEnabled():
            self._status(message)

    @staticmethod
    def _bullets(values: list[str], empty: str = "- Not documented") -> str:
        return "\n".join(f"- {value}" for value in values) or empty

    def _learning_preview(self, item: dict) -> str:
        examples = []
        for example in item.get("examples", [])[:5]:
            if not isinstance(example, dict):
                continue
            code = str(example.get("code") or "").replace("```", "~~~").strip()
            block = f"### {example.get('title') or 'Example'}\n\n"
            if code:
                block += f"```text\n{code}\n```\n\n"
            block += str(example.get("explanation") or "_No explanation provided._")
            examples.append(block)
        if not examples and item.get("example"):
            examples.append(f"### Original example\n\n{item.get('example')}")
        alternatives = []
        for alternative in item.get("alternatives", [])[:4]:
            if not isinstance(alternative, dict):
                continue
            alternatives.append(
                f"- **{alternative.get('name') or 'Alternative'}:** {alternative.get('when_to_choose') or 'Compare it against the default.'} "
                f"Pros: {', '.join(alternative.get('pros', [])) or 'Not documented'}. "
                f"Cons: {', '.join(alternative.get('cons', [])) or 'Not documented'}."
            )
        examples_text = "\n\n".join(examples) or "_No examples generated._"
        alternatives_text = "\n".join(alternatives) or "_No alternatives generated._"
        return f"""## {item.get('title') or item.get('slug') or 'Learning reference'}

**Folder:** `{item.get('category')}/{item.get('slug')}.md`

{item.get('summary') or '_No summary provided._'}

### Why this matters

{item.get('why_it_matters') or '_Not documented._'}

### When to use

{self._bullets(item.get('when_to_use', []))}

### When not to use

{self._bullets(item.get('when_not_to_use', []), '- Compare the trade-offs before choosing')}

### Core principles

{self._bullets(item.get('principles', []))}

### Examples

{examples_text}

### Alternatives and trade-offs

{alternatives_text}

### Practice exercises

{self._bullets(item.get('exercises', []), '- Recreate the minimal example and test one edge case')}
"""

    def set_analysis(self, analysis: dict, note_content: str) -> None:
        self.analysis = analysis
        self.note_content = note_content
        brief = f"""## Current situation

{analysis.get('situation') or '_Unknown._'}

## What was done before

{self._bullets(analysis.get('previous_work', []))}

## Next steps

{self._bullets(analysis.get('next_steps', []))}

## Risks / unknowns

{self._bullets(analysis.get('risks', []), '- None identified')}
"""
        self.brief.setMarkdown(brief)
        self.note_preview.setMarkdown(note_content)
        references = analysis.get("learning_references", [])
        learning_text = [self._learning_preview(item) for item in references]
        self.learning.setMarkdown("\n\n".join(learning_text) or "_No reusable learning references were generated._")
        self.study_material = {"learning_references": references} if references else None
        self.set_busy(False, "Draft ready · review before applying")
        self.copy.setEnabled(True)
        self.apply.setEnabled(True)
        self.tabs.setCurrentWidget(self.brief)

    def set_brief(self, analysis: dict) -> None:
        self.analysis = None
        brief = f"""## Current situation

{analysis.get('situation') or '_Unknown._'}

## What was done before

{self._bullets(analysis.get('previous_work', []))}

## Next steps

{self._bullets(analysis.get('next_steps', []))}

## Risks / unknowns

{self._bullets(analysis.get('risks', []), '- None identified')}
"""
        self.brief.setMarkdown(brief)
        self.set_busy(False, "Brief ready · use Prepare Obsidian update for the full template")
        self.copy.setEnabled(True)
        self.apply.setEnabled(False)
        self.tabs.setCurrentWidget(self.brief)

    def set_lessons(self, analysis: dict) -> None:
        """Show generated study material on its own, with no ticket-note preview."""
        references = analysis.get("learning_references", [])
        self.analysis = None
        self.study_material = {"learning_references": references} if references else None
        self.learning.setMarkdown(
            "\n\n".join(self._learning_preview(item) for item in references)
            or "_No reusable study material was generated._"
        )
        self.set_busy(False, f"{len(references)} lesson{'s' if len(references) != 1 else ''} ready · review before saving")
        self.apply.setEnabled(False)
        self.tabs.setCurrentWidget(self.learning)

    def lessons_applied(self, count: int, linked: bool) -> None:
        suffix = " · linked from the ticket note" if linked else ""
        self._status(f"Saved {count} lesson note{'s' if count != 1 else ''} to the vault{suffix}")

    def set_draft_comment(self, analysis: dict) -> None:
        self.analysis = None
        self.brief.setMarkdown(f"## Draft Backlog comment\n\n{analysis.get('comment') or '_No draft was returned._'}\n\n_Review and post this manually from the Ticket Inspector._")
        self.set_busy(False, "Comment draft ready · review before posting")
        self.copy.setEnabled(bool(analysis.get("comment")))
        self.apply.setEnabled(False)
        self.tabs.setCurrentWidget(self.brief)

    def analysis_failed(self, message: str) -> None:
        self.set_busy(False, "Generation failed")
        self.brief.setMarkdown(f"## Could not generate the brief\n\n{message}")
        self.tabs.setCurrentWidget(self.brief)

    def set_source_control_context(self, patch_count: int) -> None:
        """Tell the user what the model was actually given to work from."""
        if patch_count:
            self.patch_state.setText(f"{patch_count} patch{'es' if patch_count != 1 else ''} · in AI context")

    def note_applied(self, path: str, learning_count: int, backup: str = "") -> None:
        extra = f" · {learning_count} learning reference{'s' if learning_count != 1 else ''}"
        if backup:
            extra += " · previous note backed up"
        self._status(f"Updated {Path(path).name}{extra}")

    def _generate(self) -> None:
        if self.ticket:
            self.analyze_requested.emit(self.ticket, "brief")

    def _prepare_note(self) -> None:
        if self.ticket:
            self.analyze_requested.emit(self.ticket, "note")

    def _generate_lessons(self) -> None:
        if self.ticket:
            self.analyze_requested.emit(self.ticket, "lessons")

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.brief.toPlainText())
        self._status("Brief copied")

    def _apply(self) -> None:
        if self.ticket and self.analysis:
            self.apply_note_requested.emit(self.ticket, self.analysis)

    def _apply_lessons(self) -> None:
        if self.ticket and self.study_material:
            self.apply_lessons_requested.emit(self.ticket, self.study_material)
