"""Resolve the fully-computed display board for /api/board.

Source precedence per Slot lives in the Tap file store, not here: this module
asks `tap_store.resolve(slot)` for the winning Tap and is told which Source it
came from. Everything left in here is presentation - number coercion, Colour,
the image URL, and Visibility.

**Visibility is resolved here, not on the TV.** The board sends one boolean per
Attribute per Tap plus one for the colour swatch, the same way Occupancy already
sends a resolved `hidden` rather than the vacancy and the hide-vacant setting
that produced it. The display renders what it is told. The alternative - shipping
the ten raw toggles and re-running the chain in JavaScript - put the only
implementation of a documented precedence rule (CONTEXT.md, Visibility) in the
one language this project has no test harness for.

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
from .colors import ResolvedColor, parse_saturation, resolve_color
from .config_store import DEFAULT_CONFIG, load_config
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


def _image_url_for(image: Path | None, color: ResolvedColor | None = None,
                   glass: str | None = None) -> str:
    """Local image URL for a tap's photo, if it has one.

    The store hands out a Path and knows nothing about web routes, so building
    the URL stays here. `image` is the photo paired with the *winning* Tap file
    only - never borrowed from the other Source, so a Manual Tap with no photo
    shows the placeholder rather than the Brewfather beer's picture.

    With no photo: a beer glass tinted to the beer's Colour. The URL carries the
    **resolved** colour rather than the EBC and saturation that produced it, so
    the renderer tints what it is told instead of running the precedence chain a
    second time. Unknown is expressed by sending no colour at all, which is what
    selects the renderer's amber (ADR-0004). This URL is built here and nowhere
    else - no template references it - so its shape is free to change.
    """
    if image is not None:
        # Served by the /img/<filename> route which reads from /data/taps.
        return f"/img/{image.name}"
    params: list[str] = []
    if color is not None:
        params.append("hex=" + color.color_hex.lstrip("#"))
    g = normalize_glass(glass)
    if g != DEFAULT_GLASS:
        params.append(f"glass={g}")
    return "/img/beer-glass" + ("?" + "&".join(params) if params else "")


def _tri(value: Any) -> bool | None:
    """A per-Tap tri-state Visibility override: True / False / None (inherit).

    Still read from the Tap file - it stays an editable value the operator sets
    per Slot - but it is now an *input* to resolution rather than something the
    board forwards for someone else to apply.
    """
    if value is None or value == "":
        return None
    return bool(value)


def _is_missing(value: Any) -> bool:
    """Whether an Attribute has no value to show.

    Deliberately not a falsiness test: 0 IBU is a fact about a lager, not an
    absent reading, and hiding it would be wrong. Only None and the empty string
    count as missing, matching what the display used to ask.
    """
    return value is None or value == ""


def resolve_visibility(value: Any, global_show: Any, hide_when_empty: Any,
                       per_tap: Any = None) -> bool:
    """Answer whether one Attribute renders on one Tap.

    The single expression of Visibility (CONTEXT.md), in its fixed order:

    1. a **per-Tap override** wins outright - True or False is a deliberate
       instruction for this Slot; None or "" means "inherit";
    2. otherwise the **global toggle** decides whether the Attribute is enabled;
    3. **Empty suppression** then hides an already-enabled Attribute whose value
       is missing.

    Step 3 runs last on purpose. It is a per-beer refinement of an enabled
    Attribute, not a toggle of its own, so it can only ever take something away -
    it can never reveal an Attribute the operator switched off.

    `value` is whatever the Attribute would print. The colour swatch reuses this
    function by passing the **resolved Colour** in place of the EBC number: the
    swatch asks whether Colour is known (an EBC *or* an override), the EBC
    Attribute asks whether EBC is present, and passing a different value is the
    whole of that difference. One operator toggle, two answers - see ADR-0004 and
    CONTEXT.md's note that the swatch is Presentation of Colour, not an Attribute.

    Inputs are coerced defensively because the toggles arrive from Settings and
    the override from front matter, either of which a human may have hand-edited.
    """
    override = _tri(per_tap)
    show = override if override is not None else bool(global_show)
    if not show:
        return False
    return not (_is_missing(value) and bool(hide_when_empty))


def resolve_tap(tap: int, default_glass: str = DEFAULT_GLASS,
                cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a single tap to a display dict.

    `cfg` is the Settings the Visibility toggles come from; it is loaded here
    when omitted so a caller resolving one Slot in isolation still gets the same
    answers the board would give. `build_board` passes its own copy so a full
    board reads config once rather than once per Slot.
    """
    if cfg is None:
        cfg = load_config()

    def setting(key: str) -> Any:
        # Fall back to the schema rather than repeating the literals here: a
        # default changed in config_store must not need changing twice.
        return cfg.get(key, DEFAULT_CONFIG[key])

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
            # No colour fields and no Visibility answers: a Vacant card has no
            # Beer, so there is nothing to resolve and nothing renders. The
            # display styles those cards from a CSS custom property and never
            # read the near-black hex that used to be sent here; it only fed the
            # change-detection signature. Sending six booleans for Attributes
            # that cannot appear would be a claim, not an answer - with Empty
            # suppression off they would all read "visible" on a card with no
            # stats block at all.
            "description": "",
            "image_url": None,
        }

    data = tap_file.front_matter
    ebc = _num(data.get("ebc"))
    abv = _num(data.get("abv"))
    ibu = _num(data.get("ibu"))
    og = _num(data.get("og"))
    fg = _num(data.get("fg"))
    # Colour precedence lives in colors.resolve_color, not here. It answers with
    # a colour or with Unknown (None); the board forwards that answer to both
    # surfaces rather than each of them re-deriving it.
    color = resolve_color(ebc, parse_saturation(data.get("saturation")),
                          data.get("color_override"))
    glass = normalize_glass(data.get("glass") or default_glass)
    return {
        "tap": tap,
        "vacant": False,
        # The filename decides the Source. A front-matter `source:` key is
        # written for a human reading the file and is never read back as truth,
        # so a mislabelled file cannot make sync and the display disagree.
        "source": str(tap_file.source),
        "name": (data.get("name") or "").strip() or f"Tap {tap}",
        "abv": abv,
        "ibu": ibu,
        "ebc": ebc,
        "og": og,
        "fg": fg,
        # Null when Colour is Unknown. The display's `|| grey` is then the
        # swatch's own declared fallback rather than a copy of a server value.
        "color_hex": color.color_hex if color else None,
        "text_color": color.text_color if color else None,
        # The description is the markdown body, a named field on the TapFile
        # rather than a synthesised front-matter key.
        "description": (tap_file.body or "").strip(),
        "image_url": _image_url_for(tap_file.image, color, glass),
        # Visibility, already resolved. Named for the question each one answers,
        # not for the Settings that fed it: the display has no business knowing
        # *why* a stat is hidden, only that it is. OG and FG are the two that
        # carry a per-Tap override; the rest have only the global toggle.
        "abv_visible": resolve_visibility(
            abv, setting("show_abv"), setting("hide_abv_when_empty")),
        "ibu_visible": resolve_visibility(
            ibu, setting("show_ibu"), setting("hide_ibu_when_empty")),
        "ebc_visible": resolve_visibility(
            ebc, setting("show_color"), setting("hide_color_when_empty")),
        "og_visible": resolve_visibility(
            og, setting("show_og"), setting("hide_og_when_empty"),
            data.get("show_og")),
        "fg_visible": resolve_visibility(
            fg, setting("show_fg"), setting("hide_fg_when_empty"),
            data.get("show_fg")),
        # The swatch shares the EBC toggle but asks whether *Colour* is known -
        # an EBC value OR an override - so passing the resolved Colour instead of
        # the EBC number is the entire special case. A Beer with only an override
        # therefore shows a swatch and no EBC number, which is the intent
        # (ADR-0004), and `color_known` no longer has to travel to explain it.
        "swatch_visible": resolve_visibility(
            color, setting("show_color"), setting("hide_color_when_empty")),
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
        resolved = resolve_tap(tap, default_glass, cfg)
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
        # Display options consumed by the frontend. The ten Visibility toggles
        # are deliberately absent: they were resolved per Tap above, and sending
        # them as well would leave two implementations of the chain in play.
        #
        # These two stay raw because neither is Visibility. The colour unit is a
        # unit conversion of a number the display already has, and the source
        # badge is a plain global boolean - no per-Tap override, no Empty
        # suppression, so there is no chain to run.
        "color_unit": cfg.get("color_unit", "ebc"),
        "show_source_badge": bool(cfg.get("show_source_badge", False)),
        # Theme colours (display.js writes these onto the document root).
        "theme": resolve_theme(cfg),
        # Card sizing, already resolved to plain numbers. The preset keys stay
        # behind in Settings: the display only needs to know how big to draw,
        # not which button produced the number. The photo scale is applied by
        # display.js against each photo's measured height, not by CSS - see the
        # `.card .thumb` rule for why.
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
