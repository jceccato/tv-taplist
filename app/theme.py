"""Display theme presets and resolution.

A theme is a set of colours for the TV display, exposed to the frontend as a flat
dict that display.js writes onto the document root as CSS custom properties
(``--bg``, ``--bg-card`` ...). The display CSS ships the "default" palette as its
:root fallback, so an un-themed board (or one loading before the first poll) looks
exactly as before.

Presets cover a few panel types and styles; "custom" lets the operator pick every
colour by hand. The colour keys here map 1:1 to the CSS variables in display.css.
"""
from __future__ import annotations

from typing import Any

from .colors import parse_hex_color

# Ordered colour keys -> the CSS custom property each drives on the display.
THEME_KEYS: dict[str, str] = {
    "bg": "--bg",
    "bg_card": "--bg-card",
    "bg_card_2": "--bg-card-2",
    "border": "--border",
    "text": "--text",
    "text_dim": "--text-dim",
    "accent": "--accent",
    "vacant": "--vacant",
}

# Human labels for the custom-theme colour pickers, in display order.
THEME_FIELD_LABELS: list[tuple[str, str]] = [
    ("bg", "Background"),
    ("bg_card", "Card background"),
    ("bg_card_2", "Card gradient / panels"),
    ("border", "Borders"),
    ("text", "Primary text"),
    ("text_dim", "Dim text"),
    ("accent", "Accent (tap number, ticker)"),
    ("vacant", "Vacant tap fill"),
]

# The shipped default palette — identical to the :root values in display.css.
DEFAULT_THEME: dict[str, str] = {
    "bg": "#0d0f14",
    "bg_card": "#181b23",
    "bg_card_2": "#1f2330",
    "border": "#2a2f3d",
    "text": "#f3f4f7",
    "text_dim": "#aeb4c2",
    "accent": "#ffb627",
    "vacant": "#232633",
}

# Preset palettes. Each entry: key -> (label, hint, colours).
THEMES: dict[str, dict[str, Any]] = {
    "default": {
        "label": "Default (dark)",
        "hint": "The standard balanced dark palette.",
        "colors": DEFAULT_THEME,
    },
    "oled": {
        "label": "OLED true black",
        "hint": "Pure-black background — best contrast and lowest power on OLED panels.",
        "colors": {
            "bg": "#000000",
            "bg_card": "#0a0a0c",
            "bg_card_2": "#131318",
            "border": "#26262e",
            "text": "#ffffff",
            "text_dim": "#b6b9c2",
            "accent": "#ffb627",
            "vacant": "#0c0c10",
        },
    },
    "local_dimming": {
        "label": "Local-dimming LCD",
        "hint": "Slightly-lifted blacks to avoid blooming/haloing around bright text on FALD/edge-lit LCDs.",
        "colors": {
            "bg": "#14171d",
            "bg_card": "#1d212a",
            "bg_card_2": "#262b36",
            "border": "#343b49",
            "text": "#f3f4f7",
            "text_dim": "#b2b9c6",
            "accent": "#ffc24d",
            "vacant": "#20242e",
        },
    },
    "midnight": {
        "label": "Midnight blue",
        "hint": "Deep navy with warm-gold accents.",
        "colors": {
            "bg": "#0a0f1f",
            "bg_card": "#121a30",
            "bg_card_2": "#19233f",
            "border": "#283357",
            "text": "#eef2ff",
            "text_dim": "#a6b2d4",
            "accent": "#ffd166",
            "vacant": "#141d33",
        },
    },
    "daylight": {
        "label": "Daylight (light)",
        "hint": "Light background for bright rooms / daytime venues.",
        "colors": {
            "bg": "#eef1f6",
            "bg_card": "#ffffff",
            "bg_card_2": "#f3f5fa",
            "border": "#d2d8e2",
            "text": "#1a1e28",
            "text_dim": "#5b6475",
            "accent": "#c8741a",
            "vacant": "#e3e8f0",
        },
    },
    "custom": {
        "label": "Custom",
        "hint": "Pick every colour by hand below.",
        "colors": DEFAULT_THEME,  # placeholder; custom values come from config
    },
}

DEFAULT_THEME_NAME = "default"


def normalize_theme_name(name: Any) -> str:
    """Coerce a theme name to a known preset key (falling back to default)."""
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME_NAME


def coerce_custom_theme(value: Any) -> dict[str, str]:
    """Validate a custom-theme dict, filling any missing/invalid colour from the default."""
    out: dict[str, str] = {}
    src = value if isinstance(value, dict) else {}
    for key in THEME_KEYS:
        out[key] = parse_hex_color(src.get(key)) or DEFAULT_THEME[key]
    return out


def resolve_theme(cfg: dict[str, Any]) -> dict[str, str]:
    """Return the flat colour dict for the board's configured theme.

    For "custom", the per-colour overrides in ``cfg['theme_custom']`` win; every
    other preset returns its fixed palette. Always returns a full set of colours.
    """
    name = normalize_theme_name(cfg.get("theme"))
    if name == "custom":
        return coerce_custom_theme(cfg.get("theme_custom"))
    return dict(THEMES[name]["colors"])
