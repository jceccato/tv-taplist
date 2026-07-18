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
from .beer_glass import DEFAULT_GLASS, normalize_glass
from .colors import ebc_to_hex, parse_hex_color, parse_saturation, text_color_for
from .config_store import load_config
from .paths import venue_logo_path
from .theme import resolve_theme


def _num(value: Any) -> float | int | None:
    """Coerce a front-matter value to a number, or None."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _image_url_for(stem: str, ebc: float | int | None = None,
                   saturation: float | None = None, glass: str | None = None,
                   color_override: str | None = None) -> str:
    """Local image URL for a tap stem.

    Prefers an uploaded photo; otherwise a beer glass tinted to the beer's colour
    (so the placeholder pour matches the SRM/EBC or the exact colour override),
    falling back to a neutral amber glass when the colour is unknown. The per-tap
    saturation and glassware are forwarded so the placeholder matches the swatch.
    """
    img = md.find_image_for(stem)
    if img is not None:
        # Served by the /img/<filename> route which reads from /data/taps.
        return f"/img/{img.name}"
    params: list[str] = []
    if color_override:
        params.append("hex=" + color_override.lstrip("#"))
    elif ebc is not None:
        params.append(f"ebc={ebc}")
        if saturation is not None:
            params.append(f"sat={saturation}")
    g = normalize_glass(glass)
    if g != DEFAULT_GLASS:
        params.append(f"glass={g}")
    return "/img/beer-glass" + ("?" + "&".join(params) if params else "")


def _tri(value: Any) -> bool | None:
    """A per-tap tri-state visibility flag: True / False / None (inherit global)."""
    if value is None or value == "":
        return None
    return bool(value)


def resolve_tap(tap: int, default_glass: str = DEFAULT_GLASS) -> dict[str, Any]:
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
            "og": None,
            "fg": None,
            "color_hex": "#222222",
            "text_color": "#f5f5f5",
            "color_known": False,
            "description": "",
            "image_url": None,
            "show_og": None,
            "show_fg": None,
        }

    ebc = _num(data.get("ebc"))
    saturation = parse_saturation(data.get("saturation"))
    color_override = parse_hex_color(data.get("color_override"))
    # An exact colour override wins over the computed EBC colour, everywhere.
    color_hex = color_override or ebc_to_hex(ebc, saturation)
    glass = normalize_glass(data.get("glass") or default_glass)
    return {
        "tap": tap,
        "vacant": False,
        "source": data.get("source", source),
        "name": (data.get("name") or "").strip() or f"Tap {tap}",
        "abv": _num(data.get("abv")),
        "ibu": _num(data.get("ibu")),
        "ebc": ebc,
        "og": _num(data.get("og")),
        "fg": _num(data.get("fg")),
        "color_hex": color_hex,
        "text_color": text_color_for(color_hex),
        # The swatch shows whenever the colour is known — from an EBC value OR an
        # explicit override — even if the EBC *stat* itself is hidden/empty.
        "color_known": ebc is not None or color_override is not None,
        "description": (data.get("description") or "").strip(),
        "image_url": _image_url_for(stem, ebc, saturation, glass, color_override),
        # Per-tap stat-visibility overrides (None -> follow the global toggle).
        "show_og": _tri(data.get("show_og")),
        "show_fg": _tri(data.get("show_fg")),
        "updated": data.get("updated"),
    }


def build_board() -> dict[str, Any]:
    """Build the full board payload consumed by the TV display."""
    cfg = load_config()
    num_taps = int(cfg.get("num_taps", 0) or 0)
    hide_vacant = bool(cfg.get("hide_vacant_taps", False))
    default_glass = cfg.get("glass_type", "default")

    taps: list[dict[str, Any]] = []
    for tap in range(1, num_taps + 1):
        resolved = resolve_tap(tap, default_glass)
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
        "show_og": bool(cfg.get("show_og", False)),
        "show_fg": bool(cfg.get("show_fg", False)),
        "hide_abv_when_empty": bool(cfg.get("hide_abv_when_empty", True)),
        "hide_ibu_when_empty": bool(cfg.get("hide_ibu_when_empty", True)),
        "hide_color_when_empty": bool(cfg.get("hide_color_when_empty", True)),
        "hide_og_when_empty": bool(cfg.get("hide_og_when_empty", True)),
        "hide_fg_when_empty": bool(cfg.get("hide_fg_when_empty", True)),
        "show_source_badge": bool(cfg.get("show_source_badge", False)),
        # Theme colours (display.js writes these onto the document root).
        "theme": resolve_theme(cfg),
        # Pagination / carousel.
        "paginate": bool(cfg.get("paginate", False)),
        "page_size": int(cfg.get("page_size", 6) or 6),
        "rotation_seconds": int(cfg.get("rotation_seconds", 30) or 30),
        "venue_logo_url": venue_logo_url,
        "venue_logo_height_vh": logo_height,
        "taps": taps,
        # Sync status (last_sync_*) is deliberately NOT exposed here: /api/board is
        # public and unauthenticated, the display never consumes it, and
        # last_sync_error can carry upstream API error text. It stays in
        # config.json and is shown only on the authenticated admin page.
    }
