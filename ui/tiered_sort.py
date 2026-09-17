"""Multi-column table sorting driven from the header.

Left-click flips a column between ascending and descending. Right-click cycles
its priority tier, shown as up arrows to the left of the column title:

    (none)  not part of the tiered sort
    ▲       lowest priority
    ▲▲      middle priority
    ▲▲▲     highest priority

Rows are compared by the highest tier first. Columns sharing a tier are compared
in the order their tiers were assigned, so adding a tier never silently reorders
the columns already set up.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem


MAX_TIER = 3
TIER_GLYPH = "▲"
ASCENDING_GLYPH = "↑"
DESCENDING_GLYPH = "↓"
TIER_COLOR = "#2dd4bf"

HEADER_HINT = (
    "Left-click: ascending / descending"
    "\nRight-click: cycle sort priority (none → ▲ → ▲▲ → ▲▲▲)"
    "\nHigher priority columns are compared first."
)


@dataclass
class SortRule:
    key: str
    title: str
    tier: int = 0
    descending: bool = False
    assigned: int = 0


class TieredSortHeader(QObject):
    """Decorates an existing table's horizontal header; not a widget itself."""

    changed = pyqtSignal()

    def __init__(
        self,
        table: QTableWidget,
        columns: list[tuple[str, str]],
        fallback: str = "",
        fallback_descending: bool = True,
        parent: QObject | None = None,
    ):
        super().__init__(parent or table)
        self.table = table
        self.rules = [SortRule(key=key, title=title) for key, title in columns]
        self.fallback = fallback or (self.rules[0].key if self.rules else "")
        self.fallback_descending = fallback_descending
        self._assignments = 0
        # Give the fallback column its default direction, otherwise an untiered
        # table sorts ascending no matter what the caller asked for.
        default_rule = self.rule(self.fallback)
        if default_rule is not None:
            default_rule.descending = fallback_descending

        table.setSortingEnabled(False)
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.sectionClicked.connect(self._section_clicked)
        header.customContextMenuRequested.connect(self._section_right_clicked)
        self.refresh_labels()

    # ------------------------------------------------------------------ state
    def rule(self, key: str) -> SortRule | None:
        return next((rule for rule in self.rules if rule.key == key), None)

    def sort_order(self) -> list[tuple[str, bool]]:
        """Effective comparison order, most significant first."""
        tiered = [rule for rule in self.rules if rule.tier > 0]
        tiered.sort(key=lambda rule: (-rule.tier, rule.assigned))
        order = [(rule.key, rule.descending) for rule in tiered]
        if not order:
            rule = self.rule(self.fallback)
            if rule:
                return [(rule.key, rule.descending)]
            return [(self.fallback, self.fallback_descending)]
        return order

    def apply(self, rows: list, value_for) -> list:
        """Sort rows using every active column.

        Successive stable sorts from least to most significant give the same
        result as a compound key while letting each column keep its own
        direction and its own value type.
        """
        ordered = list(rows)
        for key, descending in reversed(self.sort_order()):
            ordered.sort(key=lambda row, name=key: value_for(row, name), reverse=descending)
        return ordered

    def _is_default(self, rule: SortRule) -> bool:
        expected = self.fallback_descending if rule.key == self.fallback else False
        return rule.tier == 0 and rule.descending == expected

    def state(self) -> list[dict]:
        """Only what differs from the defaults, so a saved state stays small."""
        return [
            {"key": rule.key, "tier": rule.tier, "descending": rule.descending, "assigned": rule.assigned}
            for rule in self.rules
            if not self._is_default(rule)
        ]

    def restore(self, value) -> None:
        """Apply a saved configuration, ignoring anything unrecognised."""
        if not isinstance(value, list):
            return
        for entry in value:
            if not isinstance(entry, dict):
                continue
            rule = self.rule(str(entry.get("key", "")))
            if rule is None:
                continue
            try:
                rule.tier = max(0, min(MAX_TIER, int(entry.get("tier", 0))))
                rule.assigned = max(0, int(entry.get("assigned", 0)))
            except (TypeError, ValueError):
                rule.tier, rule.assigned = 0, 0
            rule.descending = bool(entry.get("descending", False))
            self._assignments = max(self._assignments, rule.assigned)
        self.refresh_labels()

    def describe(self) -> str:
        """Human-readable summary of the effective sort, most significant first."""
        labels = []
        for key, descending in self.sort_order():
            rule = self.rule(key)
            if rule is None:
                continue
            arrows = TIER_GLYPH * rule.tier
            direction = DESCENDING_GLYPH if descending else ASCENDING_GLYPH
            labels.append(" ".join(part for part in (arrows, rule.title, direction) if part))
        return "  →  ".join(labels) if labels else "unsorted"

    def clear_tiers(self) -> None:
        for rule in self.rules:
            rule.tier = 0
            rule.assigned = 0
        self._assignments = 0
        self.refresh_labels()
        self.changed.emit()

    # ----------------------------------------------------------------- events
    def _section_clicked(self, index: int) -> None:
        rule = self._rule_at(index)
        if rule is None:
            return
        rule.descending = not rule.descending
        self.refresh_labels()
        self.changed.emit()

    def _section_right_clicked(self, position) -> None:
        index = self.table.horizontalHeader().logicalIndexAt(position)
        rule = self._rule_at(index)
        if rule is None:
            return
        rule.tier = 0 if rule.tier >= MAX_TIER else rule.tier + 1
        if rule.tier:
            self._assignments += 1
            rule.assigned = self._assignments
        else:
            rule.assigned = 0
        self.refresh_labels()
        self.changed.emit()

    def _rule_at(self, index: int) -> SortRule | None:
        return self.rules[index] if 0 <= index < len(self.rules) else None

    # ------------------------------------------------------------------ view
    def refresh_labels(self) -> None:
        """Redraw the header titles with their tier arrows and direction."""
        active = {key for key, _ in self.sort_order()}
        for index, rule in enumerate(self.rules):
            arrows = TIER_GLYPH * rule.tier
            direction = ""
            if rule.key in active:
                direction = DESCENDING_GLYPH if rule.descending else ASCENDING_GLYPH
            label = " ".join(part for part in (arrows, rule.title, direction) if part)
            item = self.table.horizontalHeaderItem(index)
            if item is None:
                item = QTableWidgetItem()
                self.table.setHorizontalHeaderItem(index, item)
            item.setText(label)
            item.setToolTip(f"{rule.title}\n\n{HEADER_HINT}")
            font = item.font()
            font.setBold(rule.tier > 0)
            item.setFont(font)
            if rule.tier > 0:
                item.setForeground(QColor(TIER_COLOR))
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)


def stretch_last(table: QTableWidget) -> None:
    table.horizontalHeader().setSectionResizeMode(
        table.columnCount() - 1, QHeaderView.ResizeMode.Stretch
    )
