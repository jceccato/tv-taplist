"""Read/write tap markdown files (YAML front matter + body) and naming helpers.

File naming:
  custom_tap_<X>.md / custom_tap_<X>.<ext>   -> manual override (wins)
  bf_tap_<X>.md     / bf_tap_<X>.<ext>        -> Brewfather-sourced

Front matter keys: name, abv, ibu, ebc, saturation, source, batch_id, image,
updated. The body (after the closing '---') holds the description / tasting notes.

All writes go through atomic.atomic_write_text. Reads tolerate a file being
renamed or deleted mid-cycle (return None instead of raising).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .atomic import atomic_write_text
from .paths import TAPS_DIR

log = logging.getLogger("taplist.md")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


# ---- naming ---------------------------------------------------------------

def custom_md_path(tap: int) -> Path:
    return TAPS_DIR / f"custom_tap_{tap}.md"


def bf_md_path(tap: int) -> Path:
    return TAPS_DIR / f"bf_tap_{tap}.md"


def is_manual_override(tap: int) -> bool:
    """A tap is a manual override iff its custom_tap_X.md exists."""
    return custom_md_path(tap).exists()


def find_image_for(stem: str) -> Path | None:
    """Find an existing image file matching a stem (e.g. 'bf_tap_5'), any ext."""
    for ext in IMAGE_EXTS:
        p = TAPS_DIR / f"{stem}{ext}"
        if p.exists():
            return p
    return None


# ---- front matter parse / serialise --------------------------------------

def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown string into (front_matter_dict, body)."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        # No front matter: treat whole thing as body.
        return {}, text.strip()
    fm_raw, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(fm_raw) or {}
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError as exc:
        log.warning("bad YAML front matter: %s", exc)
        data = {}
    return data, body.strip()


def serialise_markdown(front_matter: dict[str, Any], body: str) -> str:
    """Build a markdown string from front matter + body."""
    fm = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    body = (body or "").strip()
    return f"---\n{fm}\n---\n{body}\n"


def read_tap_file(path: Path) -> dict[str, Any] | None:
    """Read a tap markdown file into a dict, or None if missing/unreadable.

    Tolerates the file being renamed/removed mid-read (atomic rename races).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)
        return None
    fm, body = parse_markdown(text)
    fm["description"] = body
    return fm


def write_tap_file(path: Path, front_matter: dict[str, Any], body: str) -> None:
    """Atomically write a tap markdown file."""
    atomic_write_text(path, serialise_markdown(front_matter, body))
