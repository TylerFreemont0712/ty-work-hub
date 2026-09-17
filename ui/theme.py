"""Named appearance themes.

One palette definition per theme drives both the Qt stylesheet and every widget
that paints itself. Adding a theme means adding a single `Theme` entry to
`THEMES`; nothing else in the application hard-codes a colour.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Theme:
    """The complete colour vocabulary of one appearance."""

    key: str
    label: str
    blurb: str
    dark: bool
    # Surfaces, from the window backdrop to raised inputs.
    bg: str
    surface: str
    raised: str
    border: str
    # Text.
    text: str
    muted: str
    # Interaction states and the identity/focus colour.
    hover: str
    selected: str
    accent: str
    # Filled primary buttons.
    primary: str
    primary_text: str
    primary_hover: str
    primary_pressed: str
    # The dashboard focus card, which carries its own contained palette.
    focus_bg: str
    focus_border: str
    focus_text: str
    focus_muted: str
    # Brand mark in the navigation rail.
    brand_bg: str
    brand_text: str
    # Semantic colours. Used for status, priority, and risk, never decoration.
    success: str
    warning: str
    danger: str
    info: str
    neutral: str
    # Painted data surfaces: week chart and focus ring.
    chart: str
    chart_today: str
    chart_axis: str
    ring_track: str
    ring_hand: str

    @property
    def status_colors(self) -> dict[str, str]:
        """Colour per `modules.ticket_utils.status_bucket` value."""
        return {"open": self.warning, "progress": self.info, "resolved": self.success, "closed": self.neutral}

    @property
    def priority_colors(self) -> dict[str, str]:
        return {"High": self.danger, "Normal": self.neutral, "Low": self.muted}

    def event_color(self, kind: str, readonly: bool = False) -> str:
        """Calendar block colour: deadlines read as risk, work sessions as progress."""
        if readonly:
            return self.danger
        return self.success if kind == "work" else self.info


SAGE_DARK = Theme(
    key="dark", label="Sage Dark", blurb="Charcoal and sage. The original workspace.", dark=True,
    bg="#111816", surface="#19211e", raised="#202b26", border="#2c3931",
    text="#e5eee8", muted="#91a49a",
    hover="#25382e", selected="#294536", accent="#a0dbb8",
    primary="#a6d9b2", primary_text="#183322", primary_hover="#bce9c6", primary_pressed="#83bb91",
    focus_bg="#1b352b", focus_border="#375645", focus_text="#dbefdf", focus_muted="#a4beae",
    brand_bg="#aed6b4", brand_text="#173829",
    success="#72ae87", warning="#bb9561", danger="#ce8278", info="#699dc6", neutral="#8a9690",
    chart="#5c8066", chart_today="#90c49f", chart_axis="#819890",
    ring_track="#355444", ring_hand="#b3d7bf",
)

SAGE_LIGHT = Theme(
    key="light", label="Sage Light", blurb="Paper white with a sage accent.", dark=False,
    bg="#f4f6f3", surface="#ffffff", raised="#eef2ee", border="#dce5dd",
    text="#233a30", muted="#64786e",
    hover="#edf4ef", selected="#dcefe2", accent="#287357",
    primary="#8fcfa4", primary_text="#10301f", primary_hover="#a5dcb6", primary_pressed="#74b78b",
    focus_bg="#e4f0e7", focus_border="#c3ddc9", focus_text="#1e3d2d", focus_muted="#5b7767",
    brand_bg="#9ed3ad", brand_text="#123526",
    success="#2f8757", warning="#96682a", danger="#b4503f", info="#2f6ea6", neutral="#6b7a72",
    chart="#8cc0a0", chart_today="#3f8f63", chart_axis="#7a8d83",
    ring_track="#cfe4d5", ring_hand="#3f7f5f",
)

MIDNIGHT = Theme(
    key="midnight", label="Midnight", blurb="Deep navy with a clear blue accent.", dark=True,
    bg="#0d1220", surface="#141b2d", raised="#1b2438", border="#26314a",
    text="#e3e9f7", muted="#8b98b8",
    hover="#1d283f", selected="#25355a", accent="#8cc4ff",
    primary="#6ea8fe", primary_text="#08101f", primary_hover="#8bbcff", primary_pressed="#5a8ee0",
    focus_bg="#16233c", focus_border="#2c3f66", focus_text="#dbe7ff", focus_muted="#93a5c8",
    brand_bg="#6ea8fe", brand_text="#07182b",
    success="#5fc98d", warning="#e0b263", danger="#f2777f", info="#6ea8fe", neutral="#8593b0",
    chart="#3f5f9c", chart_today="#6ea8fe", chart_axis="#7b89a8",
    ring_track="#23314e", ring_hand="#a9c7ff",
)

EMBER = Theme(
    key="ember", label="Ember", blurb="Warm low light with an amber accent.", dark=True,
    bg="#14100d", surface="#1c1815", raised="#241f1b", border="#352d26",
    text="#f0e7de", muted="#a89787",
    hover="#2a231d", selected="#3a2c1f", accent="#f0b26b",
    primary="#e8a75c", primary_text="#2a1a08", primary_hover="#f4bb78", primary_pressed="#c98d47",
    focus_bg="#2a1f15", focus_border="#4a3620", focus_text="#f6e6d2", focus_muted="#c0a68c",
    brand_bg="#f0b26b", brand_text="#35210b",
    success="#8bbd76", warning="#e5b25c", danger="#e57a63", info="#6fb3a8", neutral="#9c8b7c",
    chart="#7a5b3a", chart_today="#e8a75c", chart_axis="#9c8b7c",
    ring_track="#40301f", ring_hand="#f2cf9c",
)

NORDIC = Theme(
    key="nordic", label="Nordic Light", blurb="Cool daylight with a solid blue accent.", dark=False,
    bg="#eff2f7", surface="#ffffff", raised="#e6ebf2", border="#d3dbe6",
    text="#1f2937", muted="#64748b",
    hover="#e8eef6", selected="#dbe7f6", accent="#2559bd",
    primary="#2f6fe4", primary_text="#ffffff", primary_hover="#4a83f0", primary_pressed="#2559bd",
    focus_bg="#e7eefb", focus_border="#c6d8f5", focus_text="#17324f", focus_muted="#55708f",
    brand_bg="#2f6fe4", brand_text="#ffffff",
    success="#157f4a", warning="#a06a12", danger="#c0392b", info="#2563eb", neutral="#6b7280",
    chart="#9fbfe8", chart_today="#2f6fe4", chart_axis="#7b8794",
    ring_track="#d5e2f5", ring_hand="#2f6fe4",
)

GRAPHITE = Theme(
    key="graphite", label="Graphite", blurb="Neutral high contrast with no colour cast.", dark=True,
    bg="#0f0f10", surface="#171718", raised="#1e1e20", border="#2b2b2e",
    text="#ededee", muted="#9a9a9f",
    hover="#212124", selected="#2f2f34", accent="#e4e4e7",
    primary="#e4e4e7", primary_text="#18181b", primary_hover="#f4f4f5", primary_pressed="#c6c6cb",
    focus_bg="#1b1b1e", focus_border="#37373d", focus_text="#f2f2f3", focus_muted="#a5a5aa",
    brand_bg="#e4e4e7", brand_text="#18181b",
    success="#7bc98e", warning="#d4b26a", danger="#e08585", info="#8ab4e8", neutral="#9a9a9f",
    chart="#4a4a50", chart_today="#b8b8bd", chart_axis="#86868b",
    ring_track="#303035", ring_hand="#d4d4d8",
)

# Declaration order is the order shown in the theme menu and in Settings.
THEMES: dict[str, Theme] = {theme.key: theme for theme in (SAGE_DARK, MIDNIGHT, EMBER, GRAPHITE, SAGE_LIGHT, NORDIC)}
THEME_KEYS: tuple[str, ...] = tuple(THEMES)
DEFAULT_THEME = SAGE_DARK.key

_active: Theme = SAGE_DARK


def resolve(key: str | None) -> Theme:
    """Return a known theme; an unknown or missing key falls back to the default."""
    return THEMES.get(str(key or ""), THEMES[DEFAULT_THEME])


def active() -> Theme:
    """The theme currently applied to the application, for widgets that paint."""
    return _active


def set_active(key: str | None) -> Theme:
    """Record the applied theme. `ui.styles.theme_styles` calls this for callers."""
    global _active
    _active = resolve(key)
    return _active


def next_key(key: str | None) -> str:
    """The following theme in declaration order, wrapping at the end."""
    return THEME_KEYS[(THEME_KEYS.index(resolve(key).key) + 1) % len(THEME_KEYS)]


def choices() -> tuple[tuple[str, str, bool], ...]:
    """(key, label, dark) for every theme, for menus and settings."""
    return tuple((theme.key, theme.label, theme.dark) for theme in THEMES.values())


def token_names() -> tuple[str, ...]:
    """Field names of `Theme`; used by the design-system self-check in tests."""
    return tuple(field.name for field in fields(Theme))
