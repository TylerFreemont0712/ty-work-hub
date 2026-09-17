"""Readable diff rendering: coloured unified output and a before/after compare pane.

Both widgets are presentation only. They never run SVN; callers hand them text
that a background worker already produced.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from ui.theme import resolve as resolve_theme


# Distinct hues so consecutive files in one patch never share a header colour.
FILE_COLORS = ("#2dd4bf", "#c084fc", "#38bdf8", "#fb923c", "#f472b6", "#a3e635")

DARK_PALETTE = {
    "added": "#4ade80",
    "added_bg": "#0f2a1b",
    "removed": "#fb7185",
    "removed_bg": "#2c1119",
    "hunk": "#fbbf24",
    "hunk_bg": "#2e2410",
    "meta": "#8b9bb1",
    # The compare panes carry no per-line +/- marker, so their fills are the
    # only signal and are deliberately stronger than the inline diff tints.
    "compare_added_bg": "#15532f",
    "compare_removed_bg": "#5e1c2b",
    "compare_added_focus": "#1f7d45",
    "compare_removed_focus": "#8d2740",
}

LIGHT_PALETTE = {
    "added": "#15803d",
    "added_bg": "#e3f7e9",
    "removed": "#b91c1c",
    "removed_bg": "#fdeaea",
    "hunk": "#a16207",
    "hunk_bg": "#fdf3d7",
    "meta": "#66758b",
    "compare_added_bg": "#a7e9bd",
    "compare_removed_bg": "#f9bcbc",
    "compare_added_focus": "#6fd695",
    "compare_removed_focus": "#f28a8a",
}

LIGHT_FILE_COLORS = ("#0d9488", "#7c3aed", "#0369a1", "#c2410c", "#be185d", "#4d7c0f")

FILE_START_PREFIXES = ("Index: ", "diff --git ", "diff -", "Property changes on: ")
META_PREFIXES = ("===", "___", "\\ No newline", "Added: ", "Deleted: ", "Modified: ", "Name: ")


def monospace_font() -> QFont:
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class DiffHighlighter(QSyntaxHighlighter):
    """Colour unified-diff text: additions green, removals red, hunks yellow.

    Each file in a multi-file patch gets its own header colour. The running file
    index is carried in the Qt block state so editing or re-highlighting a single
    block keeps the same colour as a full re-parse.
    """

    def __init__(self, document, theme: str = "dark"):
        super().__init__(document)
        self._formats: dict[str, QTextCharFormat] = {}
        self._file_formats: list[QTextCharFormat] = []
        self.set_theme(theme)

    def set_theme(self, theme: str) -> None:
        dark = resolve_theme(theme).dark
        palette = DARK_PALETTE if dark else LIGHT_PALETTE
        file_colors = FILE_COLORS if dark else LIGHT_FILE_COLORS
        self._formats = {
            "added": self._format(palette["added"], palette["added_bg"]),
            "removed": self._format(palette["removed"], palette["removed_bg"]),
            "hunk": self._format(palette["hunk"], palette["hunk_bg"], bold=True),
            "meta": self._format(palette["meta"]),
        }
        self._file_formats = [self._format(color, bold=True) for color in file_colors]
        self.rehighlight()

    @staticmethod
    def _format(foreground: str, background: str = "", bold: bool = False) -> QTextCharFormat:
        value = QTextCharFormat()
        value.setForeground(QColor(foreground))
        if background:
            value.setBackground(QColor(background))
        if bold:
            value.setFontWeight(QFont.Weight.Bold)
        return value

    def highlightBlock(self, text: str) -> None:
        file_index = max(0, self.previousBlockState())
        if text.startswith(FILE_START_PREFIXES):
            file_index += 1
        self.setCurrentBlockState(file_index)
        if not text:
            return
        length = len(text)
        if text.startswith(FILE_START_PREFIXES):
            self.setFormat(0, length, self._file_formats[file_index % len(self._file_formats)])
        elif text.startswith("@@"):
            self.setFormat(0, length, self._formats["hunk"])
        elif text.startswith(("+++ ", "--- ")):
            self.setFormat(0, length, self._file_formats[file_index % len(self._file_formats)])
        elif text.startswith("+"):
            self.setFormat(0, length, self._formats["added"])
        elif text.startswith("-"):
            self.setFormat(0, length, self._formats["removed"])
        elif text.startswith(META_PREFIXES):
            self.setFormat(0, length, self._formats["meta"])


class DiffOutputView(QPlainTextEdit):
    """Read-only monospace output with unified-diff colouring."""

    def __init__(self, theme: str = "dark"):
        super().__init__()
        self.setReadOnly(True)
        self.setObjectName("diffOutput")
        self.setFont(monospace_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setPlaceholderText("SVN command output, diffs, previews, and errors appear here.")
        self.highlighter = DiffHighlighter(self.document(), theme)

    def set_theme(self, theme: str) -> None:
        self.highlighter.set_theme(theme)


class CompareView(QWidget):
    """Side-by-side base revision (before) and working copy (after).

    Line-level highlighting comes from difflib and is skipped for very large
    files so switching files never blocks the GUI thread noticeably.
    """

    MAX_MARKED_LINES = 8000

    def __init__(self, theme: str = "dark"):
        super().__init__()
        self.files: list[dict] = []
        self._theme = theme
        self._blocks: list[tuple[int, int]] = []
        self._block_index = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 6, 5, 5)
        layout.setSpacing(5)

        chooser = QHBoxLayout()
        self.file_picker = QComboBox()
        self.file_picker.setMinimumWidth(280)
        self.summary = QLabel("Select changed files and run Compare with base")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        chooser.addWidget(QLabel("File"))
        chooser.addWidget(self.file_picker, 1)
        chooser.addWidget(self.summary, 2)
        layout.addLayout(chooser)

        navigation = QHBoxLayout()
        self.first_change = QPushButton("⏮ First")
        self.previous_change = QPushButton("◀ Previous")
        self.next_change = QPushButton("Next ▶")
        self.last_change = QPushButton("Last ⏭")
        self.change_position = QLabel("No changes")
        self.change_position.setObjectName("muted")
        for button in (self.first_change, self.previous_change, self.next_change, self.last_change):
            button.setProperty("secondary", True)
            button.setEnabled(False)
        self.first_change.setToolTip("Jump to the first changed block in this file")
        self.previous_change.setToolTip("Jump to the previous changed block (Shift+F7)")
        self.next_change.setToolTip("Jump to the next changed block (F7)")
        self.last_change.setToolTip("Jump to the last changed block in this file")
        navigation.addWidget(self.first_change)
        navigation.addWidget(self.previous_change)
        navigation.addWidget(self.next_change)
        navigation.addWidget(self.last_change)
        navigation.addWidget(self.change_position)
        navigation.addStretch()
        layout.addLayout(navigation)

        headings = QHBoxLayout()
        self.before_label = QLabel("Before · base revision")
        self.before_label.setObjectName("muted")
        self.after_label = QLabel("After · working copy")
        self.after_label.setObjectName("muted")
        headings.addWidget(self.before_label, 1)
        headings.addWidget(self.after_label, 1)
        layout.addLayout(headings)

        self.before = QPlainTextEdit()
        self.after = QPlainTextEdit()
        for pane in (self.before, self.after):
            pane.setReadOnly(True)
            pane.setFont(monospace_font())
            pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.before.setPlaceholderText("The base revision of the selected file appears here.")
        self.after.setPlaceholderText("The working-copy version of the selected file appears here.")
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.before)
        self.splitter.addWidget(self.after)
        self.splitter.setSizes([500, 500])
        layout.addWidget(self.splitter, 1)

        self.file_picker.currentIndexChanged.connect(self._render_selected)
        self.first_change.clicked.connect(lambda: self.go_to_change(0))
        self.previous_change.clicked.connect(lambda: self.step_change(-1))
        self.next_change.clicked.connect(lambda: self.step_change(1))
        self.last_change.clicked.connect(lambda: self.go_to_change(len(self._blocks) - 1))
        QShortcut(QKeySequence("F7"), self, activated=lambda: self.step_change(1))
        QShortcut(QKeySequence("Shift+F7"), self, activated=lambda: self.step_change(-1))
        # Keep both sides on the same line while reading.
        self.before.verticalScrollBar().valueChanged.connect(self.after.verticalScrollBar().setValue)
        self.after.verticalScrollBar().valueChanged.connect(self.before.verticalScrollBar().setValue)
        self.before.horizontalScrollBar().valueChanged.connect(self.after.horizontalScrollBar().setValue)
        self.after.horizontalScrollBar().valueChanged.connect(self.before.horizontalScrollBar().setValue)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._render_selected()

    def set_files(self, files: list[dict], skipped: int = 0) -> None:
        self.files = [item for item in files if isinstance(item, dict)]
        self.file_picker.blockSignals(True)
        self.file_picker.clear()
        for item in self.files:
            self.file_picker.addItem(str(item.get("path", "")))
        self.file_picker.setCurrentIndex(0 if self.files else -1)
        self.file_picker.blockSignals(False)
        if not self.files:
            self.summary.setText("No files were compared.")
            self.before.clear()
            self.after.clear()
            self._blocks = []
            self._block_index = -1
            self._removed_rows = []
            self._added_rows = []
            self._update_navigation()
            return
        extra = f" · {skipped} more not compared (select fewer files)" if skipped else ""
        self.summary.setText(f"{len(self.files)} file(s) compared with the base revision{extra}")
        self._render_selected()

    def _render_selected(self, *_args) -> None:
        index = self.file_picker.currentIndex()
        if index < 0 or index >= len(self.files):
            return
        item = self.files[index]
        before_text = str(item.get("before") or "")
        after_text = str(item.get("after") or "")
        before_note = str(item.get("before_note") or "")
        after_note = str(item.get("after_note") or "")
        self.before.setPlainText(before_text if before_text else before_note)
        self.after.setPlainText(after_text if after_text else after_note)
        self.before_label.setText(f"Before · base revision{'  ·  ' + before_note if before_note else ''}")
        self.after_label.setText(f"After · working copy{'  ·  ' + after_note if after_note else ''}")
        self._mark_differences(before_text.splitlines(), after_text.splitlines())

    def _mark_differences(self, before_lines: list[str], after_lines: list[str]) -> None:
        self.before.setExtraSelections([])
        self.after.setExtraSelections([])
        self._blocks = []
        self._block_index = -1
        self._removed_rows: list[int] = []
        self._added_rows: list[int] = []
        if len(before_lines) + len(after_lines) > self.MAX_MARKED_LINES:
            self.change_position.setText("File too large to mark individual lines")
            self._update_navigation()
            return
        for tag, before_start, before_end, after_start, after_end in SequenceMatcher(
            None, before_lines, after_lines, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if tag in {"replace", "delete"}:
                self._removed_rows.extend(range(before_start, before_end))
            if tag in {"replace", "insert"}:
                self._added_rows.extend(range(after_start, after_end))
            self._blocks.append((before_start, after_start))
        # Set the current block before painting so its rows get the focus shade.
        self._block_index = 0 if self._blocks else -1
        self._paint_lines()
        self._update_navigation()

    def _paint_lines(self) -> None:
        palette = DARK_PALETTE if resolve_theme(self._theme).dark else LIGHT_PALETTE
        focus_before, focus_after = self._focus_rows()
        self.before.setExtraSelections(self._line_selections(
            self.before, self._removed_rows, palette["compare_removed_bg"],
            focus_before, palette["compare_removed_focus"],
        ))
        self.after.setExtraSelections(self._line_selections(
            self.after, self._added_rows, palette["compare_added_bg"],
            focus_after, palette["compare_added_focus"],
        ))

    def _focus_rows(self) -> tuple[set[int], set[int]]:
        """Rows of the currently selected block, drawn in the stronger shade."""
        if not (0 <= self._block_index < len(self._blocks)):
            return set(), set()
        before_start, after_start = self._blocks[self._block_index]
        end = self._blocks[self._block_index + 1] if self._block_index + 1 < len(self._blocks) else None
        before_end = end[0] if end else max(self._removed_rows, default=before_start) + 1
        after_end = end[1] if end else max(self._added_rows, default=after_start) + 1
        before = {row for row in self._removed_rows if before_start <= row < before_end}
        after = {row for row in self._added_rows if after_start <= row < after_end}
        return before, after

    def step_change(self, delta: int) -> None:
        if not self._blocks:
            return
        self.go_to_change((self._block_index + delta) % len(self._blocks))

    def go_to_change(self, index: int) -> None:
        """Scroll both panes to a changed block and highlight it."""
        if not self._blocks or not 0 <= index < len(self._blocks):
            return
        self._block_index = index
        before_row, after_row = self._blocks[index]
        self._scroll_to(self.before, before_row)
        self._scroll_to(self.after, after_row)
        self._paint_lines()
        self._update_navigation()

    @staticmethod
    def _scroll_to(pane: QPlainTextEdit, row: int) -> None:
        block = pane.document().findBlockByNumber(max(0, row))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        pane.setTextCursor(cursor)
        pane.centerCursor()

    def _update_navigation(self) -> None:
        total = len(self._blocks)
        for button in (self.first_change, self.previous_change, self.next_change, self.last_change):
            button.setEnabled(total > 1 or (total == 1 and button in (self.first_change, self.last_change)))
        if not total:
            if "too large" not in self.change_position.text():
                self.change_position.setText("No differences in this file")
            return
        self.change_position.setText(f"Change {self._block_index + 1} of {total}")

    @staticmethod
    def _line_selections(
        pane: QPlainTextEdit, rows: list[int], background: str,
        focus_rows: set[int] | None = None, focus_background: str = "",
    ):
        selections = []
        document = pane.document()
        for row in rows:
            block = document.findBlockByNumber(row)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            highlighted = bool(focus_rows and row in focus_rows and focus_background)
            selection.format.setBackground(QColor(focus_background if highlighted else background))
            selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
            cursor = QTextCursor(block)
            cursor.clearSelection()
            selection.cursor = cursor
            selections.append(selection)
        return selections
