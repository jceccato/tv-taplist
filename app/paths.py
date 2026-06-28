"""Filesystem layout for the appliance.

All persistent state lives under DATA_DIR, which is the Docker volume mount
point (/data by default). Everything here is derived from environment so the
same image can run with a different data path in development.
"""
from __future__ import annotations

import os
from pathlib import Path

# Root of all persistent state. Mounted as a Docker volume in production.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()

# Tap markdown + image pairs currently on display.
TAPS_DIR = DATA_DIR / "taps"

# Archive of removed beers (md + image pairs with a datetime suffix).
OLD_BEERS_DIR = DATA_DIR / "old_beers"

# Single settings file (atomic-written).
CONFIG_PATH = DATA_DIR / "config.json"

# Bundled assets shipped inside the image (read-only, no external origins).
# __file__ is .../app/paths.py, so the project root is two parents up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Placeholder shipped with the image; copied into DATA_DIR on first run so the
# admin can swap it for a venue logo if desired.
BUNDLED_PLACEHOLDER = STATIC_DIR / "placeholder.svg"


def ensure_dirs() -> None:
    """Create the data directory tree if it does not exist yet (first run)."""
    for d in (DATA_DIR, TAPS_DIR, OLD_BEERS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def placeholder_path() -> Path | None:
    """Return the on-disk placeholder image, preferring the one under /data.

    Allows an operator to drop their own /data/placeholder.(svg|png|jpg) in to
    override the bundled default without rebuilding the image.
    """
    for name in ("placeholder.svg", "placeholder.png", "placeholder.jpg"):
        p = DATA_DIR / name
        if p.exists():
            return p
    if BUNDLED_PLACEHOLDER.exists():
        return BUNDLED_PLACEHOLDER
    return None
