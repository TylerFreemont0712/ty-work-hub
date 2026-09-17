"""Work Hub design system: every rule is derived from the active `ui.theme.Theme`."""
from __future__ import annotations

from ui.design import checkmark
from ui.theme import Theme, set_active


def theme_styles(theme: str | Theme, density: str = "compact", font_size: int = 12) -> str:
    """Build the application stylesheet and record the theme for painted widgets."""
    palette = set_active(theme.key if isinstance(theme, Theme) else theme)
    size = max(9, min(14, font_size))
    check_icon = checkmark(palette.primary_text)
    # Comfortable stays usable for touch and accessibility; both steps are one
    # pixel tighter than the original scale so a workspace fits more content.
    pad = 4 if density == "compact" else 6
    return f'''
QWidget {{ background: {palette.bg}; color: {palette.text}; font-family: "Segoe UI Variable", "Segoe UI", "Noto Sans", "DejaVu Sans", "Yu Gothic UI"; font-size: {size}px; }}
QLabel {{ background: transparent; border: none; }}
QToolTip {{ background: {palette.raised}; color: {palette.text}; border: 1px solid {palette.border}; padding: 4px; }}
QFrame#navigation {{ background: {palette.surface}; border-right: 1px solid {palette.border}; }}
QFrame#topbar {{ background: {palette.bg}; border-bottom: 1px solid {palette.border}; }}
QFrame#inspector, QFrame#filterPanel {{ background: {palette.surface}; border: 1px solid {palette.border}; border-radius: 10px; }}
QFrame#surface, QFrame#metricCard, QFrame#hero, QFrame#toolbarSurface {{ background: {palette.surface}; border: 1px solid {palette.border}; border-radius: 10px; }}
QFrame#focusCard {{ background: {palette.focus_bg}; border: 1px solid {palette.focus_border}; border-radius: 12px; }}
QFrame#focusCard QLabel {{ color: {palette.focus_text}; }}
QFrame#focusCard QLabel#muted, QFrame#focusCard QLabel#eyebrow {{ color: {palette.focus_muted}; }}
QFrame#focusCard QPushButton[secondary="true"] {{ color: {palette.focus_text}; border-color: {palette.focus_border}; }}
QFrame#focusCard QPushButton[secondary="true"]:hover {{ background: {palette.focus_border}; }}
QFrame#focusCard QPushButton:disabled {{ color: {palette.focus_muted}; border-color: {palette.focus_border}; background: transparent; }}
QLabel#brand {{ font-size: 18px; font-weight: 700; letter-spacing: -.4px; }}
QLabel#brandMark {{ font-size: 17px; font-weight: 800; color: {palette.brand_text}; background: {palette.brand_bg}; border-radius: 8px; padding: 2px; }}
QLabel#pageTitle {{ font-size: {size + 11}px; font-weight: 650; letter-spacing: -.5px; }}
QLabel#heroTitle {{ font-size: {size + 6}px; font-weight: 650; }}
QLabel#sectionTitle {{ font-size: {size + 1}px; font-weight: 650; }}
QLabel#inspectorTitle {{ font-size: {size + 4}px; font-weight: 650; }}
QLabel#metricValue {{ font-size: {size + 10}px; font-weight: 600; }}
QLabel#muted {{ color: {palette.muted}; }}
QLabel#eyebrow, QLabel#navSection {{ color: {palette.muted}; font-size: 10px; font-weight: 650; letter-spacing: 1.4px; padding: 3px 1px; }}
QLabel#badge {{ color: {palette.accent}; background: {palette.selected}; border-radius: 6px; padding: 2px 6px; font-size: 10px; }}
QWidget#timerChip {{ background: {palette.surface}; border: 1px solid {palette.border}; border-radius: 7px; }}
QWidget#timerChip:hover {{ border-color: {palette.muted}; }}
QLabel#timerClock {{ font-size: {size + 1}px; font-weight: 650; }}
QPushButton {{ background: {palette.primary}; color: {palette.primary_text}; border: 1px solid {palette.primary}; border-radius: 6px; padding: {pad}px 9px; font-weight: 600; }}
QPushButton:hover {{ background: {palette.primary_hover}; border-color: {palette.primary_hover}; }}
QPushButton:pressed {{ background: {palette.primary_pressed}; }}
QPushButton:disabled {{ background: {palette.raised}; color: {palette.muted}; border-color: {palette.border}; }}
QPushButton:focus {{ border: 1px solid {palette.accent}; }}
QPushButton[secondary="true"] {{ background: transparent; color: {palette.text}; border-color: {palette.border}; }}
QPushButton[secondary="true"]:hover {{ background: {palette.hover}; border-color: {palette.muted}; }}
QPushButton[secondary="true"]:checked {{ background: {palette.selected}; border-color: {palette.accent}; color: {palette.accent}; }}
QPushButton[nav="true"] {{ background: transparent; color: {palette.muted}; border: 1px solid transparent; text-align: left; padding: 6px 8px; font-weight: 500; }}
QPushButton[nav="true"]:hover {{ background: {palette.hover}; color: {palette.text}; }}
QPushButton[nav="true"]:checked {{ background: {palette.selected}; color: {palette.accent}; font-weight: 600; }}
QPushButton[chip="true"] {{ background: transparent; color: {palette.muted}; border: 1px solid transparent; padding: 5px 8px; }}
QPushButton[chip="true"]:checked {{ background: {palette.selected}; color: {palette.accent}; border-color: {palette.border}; }}
QPushButton#commandSearch {{ text-align: left; color: {palette.muted}; background: {palette.surface}; border-color: {palette.border}; padding: 5px 9px; font-weight: 400; }}
QPushButton#textLink {{ background: transparent; border: none; color: {palette.accent}; padding: 3px; }}
QPushButton#aiCommentButton {{ color: {palette.accent}; border-color: {palette.accent}; }}
QPushButton[tableAction="true"] {{ padding: 2px 6px; min-height: 0; }}
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox, QDateEdit, QDateTimeEdit, QSpinBox {{ background: {palette.surface}; color: {palette.text}; border: 1px solid {palette.border}; border-radius: 6px; padding: {pad}px; selection-background-color: {palette.selected}; selection-color: {palette.text}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {palette.accent}; }}
QLineEdit#captureInput {{ background: {palette.raised}; border-color: transparent; padding: 6px; }}
QComboBox {{ padding-right: 20px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {palette.surface}; color: {palette.text}; selection-background-color: {palette.selected}; }}
QTextBrowser#markdownPreview {{ padding: 10px 14px; background: {palette.surface}; }}
QTableWidget, QTableView, QListWidget, QTreeView, QTreeWidget {{ background: {palette.surface}; alternate-background-color: {palette.surface}; border: 1px solid {palette.border}; border-radius: 8px; gridline-color: {palette.border}; outline: none; selection-background-color: {palette.selected}; selection-color: {palette.text}; }}
QListWidget#quietList {{ border: none; background: transparent; }}
QTableWidget::item, QTableView::item, QTreeView::item, QTreeWidget::item {{ padding: 3px; }}
QListWidget::item {{ padding: 6px; border-bottom: 1px solid {palette.border}; }}
QTableWidget::item:selected, QTableView::item:selected, QListWidget::item:selected, QTreeView::item:selected {{ background: {palette.selected}; color: {palette.text}; }}
QTableWidget::item:hover, QTableView::item:hover, QListWidget::item:hover, QTreeView::item:hover {{ background: {palette.hover}; }}
QHeaderView {{ background: {palette.surface}; border: none; }}
QHeaderView::section {{ background: {palette.surface}; color: {palette.muted}; border: none; border-bottom: 1px solid {palette.border}; font-size: 11px; font-weight: 500; padding: 6px 7px; }}
QHeaderView::section:hover {{ background: {palette.hover}; }}
QTableCornerButton::section {{ background: {palette.surface}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {palette.border}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {palette.muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 7px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {palette.border}; border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {palette.muted}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTabWidget::pane {{ border: 1px solid {palette.border}; border-radius: 8px; background: {palette.surface}; }}
QTabBar::tab {{ background: transparent; color: {palette.muted}; padding: 6px 11px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {palette.accent}; border-bottom-color: {palette.accent}; }}
QTabBar::tab:hover {{ background: {palette.hover}; }}
QWidget#inspectorTab {{ background: {palette.surface}; }}
QToolBar {{ background: {palette.surface}; border-bottom: 1px solid {palette.border}; spacing: 4px; padding: 3px; }}
QToolButton {{ background: transparent; color: {palette.text}; border: none; padding: 3px; }}
QToolButton:hover {{ background: {palette.hover}; }}
QMenu {{ background: {palette.surface}; border: 1px solid {palette.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px 5px 8px; border-radius: 4px; }}
QMenu::item:selected {{ background: {palette.selected}; color: {palette.accent}; }}
QMenu::separator {{ background: {palette.border}; height: 1px; margin: 4px; }}
QSplitter::handle {{ background: transparent; width: 7px; height: 7px; }}
QStatusBar {{ background: {palette.bg}; color: {palette.muted}; border-top: 1px solid {palette.border}; font-size: 11px; }}
QStatusBar::item {{ border: none; }}
QCalendarWidget {{ background: {palette.surface}; border: none; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {palette.surface}; }}
QCalendarWidget QAbstractItemView {{ background: {palette.surface}; alternate-background-color: {palette.surface}; selection-background-color: {palette.selected}; selection-color: {palette.text}; }}
QCheckBox {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {palette.muted}; border-radius: 4px; background: {palette.surface}; }}
QCheckBox::indicator:checked {{ background: {palette.primary}; border-color: {palette.primary}; image: url("{check_icon}"); }}
QListView::indicator {{ width: 13px; height: 13px; border: 1px solid {palette.muted}; border-radius: 4px; background: {palette.surface}; }}
QListView::indicator:checked {{ background: {palette.primary}; border-color: {palette.primary}; image: url("{check_icon}"); }}
QProgressBar {{ border: none; border-radius: 3px; background: {palette.raised}; text-align: center; }}
QProgressBar::chunk {{ border-radius: 3px; background: {palette.primary}; }}
'''


APP_STYLES = theme_styles("dark")
