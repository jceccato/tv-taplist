"""Resolve the fully-computed display board for /api/board.

Source precedence per Slot lives in the Tap file store, not here: this module
asks `tap_store.resolve(slot)` for the winning Tap and is told which Source it
came from. Everything left in here is presentation - number coercion, Colour,
the image URL, and the tri-state Visibility flags.

Two consequences of resolving through the store are worth spelling out, because
they look like bugs to a reader who remembers the old walk:

* The **filename decides the Source**, so a hand-edited `bf_tap_3.md` whose
  front matter claims `source: custom` is still reported as Brewfather. The
  front-matter key is written by every writer and never read back as truth.
* An **existing-but-unreadable Manual Tap file does not fall through** to the
  Brewfather Tap; the Slot renders as an empty card under the Manual Source
  rather than showing another brewery's beer. A file that has *vanished* still
  falls through, because it genuinely is not there. See tap_store's docstring.

The frontend never parses markdown: this module returns name, ABV, IBU, EBC,
computed colour hex + legible text colour, description, a local image URL, and
vacant/hidden flags. Reads tolerate files disappearing mid-cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .beer_glass import DEFAULT_GLASS, normalize_glass
from .colors import ebc_to_hex, parse_hex_color, parse_saturation, text_color_for
from .config_store import load_config
from .paths import venue_logo_path
from .tap_store import resolve as resolve_tap_file
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


def _image_url_for(image: Path | None, ebc: float | int | None = None,
                   saturation: float | None = None, glass: str | None = None,
                   color_override: str | None = None) -> str:
    """Local image URL for a tap's photo, if it has one.

    The store hands out a Path and knows nothing about web routes, so building
    the URL stays here. `image` is the photo paired with the *winning* Tap file
    only - never borrowed from the other Source, so a Manual Tap with no photo
    shows the placeholder rather than the Brewfather beer's picture.

    With no photo: a beer glass tinted to the beer's colour (so the placeholder
    pour matches the SRM/EBC or the exact colour override), falling back to a
    neutral amber glass when the colour is unknown. The per-tap saturation and
    glassware are forwarded so the placeholder matches the swatch.
    """
    if image is not None:
        # Served by the /img/<filename> route which reads from /data/taps.
        return f"/img/{image.name}"
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
    # Source precedence (Manual beats Brewfather beats Vacant) belongs to the
    # store; the board only asks for a Slot and is told which Source won.
    tap_file = resolve_tap_file(tap)

    if tap_file is None:
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

    data = tap_file.front_matter
    ebc = _num(data.get("ebc"))
    saturation = parse_saturation(data.get("saturation"))
    color_override = parse_hex_color(data.get("color_override"))
    # An exact colour override wins over the computed EBC colour, everywhere.
    color_hex = color_override or ebc_to_hex(ebc, saturation)
    glass = normalize_glass(data.get("glass") or default_glass)
    return {
        "tap": tap,
        "vacant": False,
        # The filename decides the Source. A front-matter `source:` key is
        # written for a human reading the file and is never read back as truth,
        # so a mislabelled file cannot make sync and the display disagree.
        "source": str(tap_file.source),
        "name": (data.get("name") or "").strip() or f"Tap {tap}",
        "abv": _num(data.get("abv")),
        "ibu": _num(data.get("ibu")),
        "ebc": ebc,
        "og": _num(data.get("og")),
        "fg": _num(data.get("fg")),
        "color_hex": color_hex,
        "text_color": text_color_for(color_hex),
        # The swatch shows whenever the colour is known - from an EBC value OR an
        # explicit override - even if the EBC *stat* itself is hidden/empty.
        "color_known": ebc is not None or color_override is not None,
        # The description is the markdown body, a named field on the TapFile
        # rather than a synthesised front-matter key.
        "description": (tap_file.body or "").strip(),
        "image_url": _image_url_for(tap_file.image, ebc, saturation, glass, color_override),
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
        # Card sizing, already resolved to plain numbers. The preset key stays
        # behind in Settings: the display only needs to know how big to draw,
        # not which button produced the number.
        "tap_image_scale": float(cfg.get("tap_image_scale", 1.0) or 1.0),
        "tap_text_scale": float(cfg.get("tap_text_scale", 1.0) or 1.0),
        # Pagination / carousel.
        "paginate": bool(cfg.get("paginate", False)),
        "page_size": int(cfg.get("page_size", 6) or 6),
        "rotation_seconds": int(cfg.get("rotation_seconds", 30) or 30),
        "venue_logo_url": venue_logo_url,
        "venue_logo_height_vh": logo_height,
        "taps": taps,
        # Status is deliberately NOT exposed here: /api/board is public and
        # unauthenticated, the display never consumes it, and last_sync_error can
        # carry upstream API error text. It lives in status.json - a file this
        # module never opens - and is shown only on the authenticated admin page.
    }
