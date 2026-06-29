"""Resolve the fully-computed display board for /api/board.

Priority per tap X (1..num_taps):
  1. custom_tap_X.md  -> source "custom" (manual override, always wins)
  2. bf_tap_X.md      -> source "brewfather"
  3. otherwise        -> vacant

The frontend never parses markdown: this module returns name, ABV, IBU, EBC,
computed colour hex + legible text colour, description, a local image URL, and
vacant/hidden flags. Reads tolerate files disappearing mid-cycle.
"""
from __future__ import annotations

from typing import Any

from . import markdown_store as md
from .colors import ebc_to_hex, text_color_for
from .config_store import load_config
from .paths import venue_logo_path


def _num(value: Any) -> float | int | None:
    """Coerce a front-matter value to a number, or None."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _image_url_for(stem: str, ebc: float | int | None = None) -> str:
    """Local image URL for a tap stem.

    Prefers an uploaded photo; otherwise a beer glass tinted to the beer's
    colour (so the placeholder pour matches the SRM/EBC), falling back to a
    neutral amber glass when the colour is unknown.
    """
    img = md.find_image_for(stem)
    if img is not None:
        # Served by the /img/<filename> route which reads from /data/taps.
        return f"/img/{img.name}"
    if ebc is not None:
        return f"/img/beer-glass?ebc={ebc}"
    return "/img/beer-glass"


def resolve_tap(tap: int) -> dict[str, Any]:
    """Resolve a single tap to a display dict."""
    # Manual override first.
    data = md.read_tap_file(md.custom_md_path(tap))
    source = "custom"
    stem = f"custom_tap_{tap}"
    if data is None:
        data = md.read_tap_file(md.bf_md_path(tap))
        source = "brewfather"
        stem = f"bf_tap_{tap}"

    if data is None:
        return {
            "tap": tap,
            "vacant": True,
            "source": None,
            "name": None,
            "abv": None,
            "ibu": None,
            "ebc": None,
            "color_hex": "#222222",
            "text_color": "#f5f5f5",
            "description": "",
            "image_url": None,
        }

    ebc = _num(data.get("ebc"))
    color_hex = ebc_to_hex(ebc)
    return {
        "tap": tap,
        "vacant": False,
        "source": data.get("source", source),
        "name": (data.get("name") or "").strip() or f"Tap {tap}",
        "abv": _num(data.get("abv")),
        "ibu": _num(data.get("ibu")),
        "ebc": ebc,
        "color_hex": color_hex,
        "text_color": text_color_for(color_hex),
        "description": (data.get("description") or "").strip(),
        "image_url": _image_url_for(stem, ebc),
        "updated": data.get("updated"),
    }


def build_board() -> dict[str, Any]:
    """Build the full board payload consumed by the TV display."""
    cfg = load_config()
    num_taps = int(cfg.get("num_taps", 0) or 0)
    hide_vacant = bool(cfg.get("hide_vacant_taps", False))

    taps: list[dict[str, Any]] = []
    for tap in range(1, num_taps + 1):
        resolved = resolve_tap(tap)
        # "hidden" tells the frontend to omit + re-flow; vacant cards are still
        # returned (with the flag) so the admin/preview can reason about them.
        resolved["hidden"] = bool(resolved["vacant"] and hide_vacant)
        taps.append(resolved)

    # Venue logo: only advertise it if the file actually exists. Append the
    # mtime as a cache-buster so the TV reloads when the logo is replaced.
    logo = venue_logo_path()
    logo_height = int(cfg.get("venue_logo_height_vh", 0) or 0)
    venue_logo_url = None
    if logo is not None and logo_height > 0:
        try:
            venue_logo_url = f"/img/venue-logo?v={int(logo.stat().st_mtime)}"
        except OSError:
            venue_logo_url = "/img/venue-logo"

    return {
        "num_taps": num_taps,
        "hide_vacant_taps": hide_vacant,
        "announcement_text": cfg.get("announcement_text", "") or "",
        # Display options consumed by the frontend.
        "color_unit": cfg.get("color_unit", "ebc"),
        "show_abv": bool(cfg.get("show_abv", True)),
        "show_ibu": bool(cfg.get("show_ibu", True)),
        "show_color": bool(cfg.get("show_color", True)),
        "hide_abv_when_empty": bool(cfg.get("hide_abv_when_empty", True)),
        "hide_ibu_when_empty": bool(cfg.get("hide_ibu_when_empty", True)),
        "hide_color_when_empty": bool(cfg.get("hide_color_when_empty", True)),
        "venue_logo_url": venue_logo_url,
        "venue_logo_height_vh": logo_height,
        "taps": taps,
        "last_sync_success": cfg.get("last_sync_success"),
        "last_sync_error": cfg.get("last_sync_error"),
    }
