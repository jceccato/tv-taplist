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

from .beer import Beer, TapPresentation
from .beer_glass import DEFAULT_GLASS, normalize_glass
from .colors import ResolvedColor, resolve_color
from .config_store import DEFAULT_CONFIG, load_config
from .mapping import status_rank_for_label
from .paths import venue_logo_path
from .tap_store import resolve as resolve_tap_file
from .theme import resolve_theme
from .upcoming_store import UpcomingEntry, list_all as list_upcoming


def _image_url_for(image: Path | None, color: ResolvedColor | None = None,
                   glass: str | None = None, img_prefix: str = "/img") -> str:
    """Local image URL for a beer's photo, if it has one.

    The store hands out a Path and knows nothing about web routes, so building
    the URL stays here. `image` is the photo paired with the *winning* Tap file
    (or, for an Upcoming Beer, the Upcoming store's own cached copy) only -
    never borrowed from elsewhere, so a Beer with no photo shows the
    placeholder rather than another Beer's picture.

    `img_prefix` names which store the photo lives in: `/img` for a Tap's
    photo (`/img/<filename>`, served from TAPS_DIR) or `/img/upcoming` for an
    Upcoming Beer's (served from UPCOMING_DIR, its own store - ADR-0006). The
    two directories can hold files with colliding names, so pointing a teaser
    at the wrong route would risk serving - or 404ing on - the wrong photo.

    With no photo: a beer glass tinted to the beer's Colour. The URL carries the
    **resolved** colour rather than the EBC and saturation that produced it, so
    the renderer tints what it is told instead of running the precedence chain a
    second time. Unknown is expressed by sending no colour at all, which is what
    selects the renderer's amber (ADR-0004). This URL is built here and nowhere
    else - no template references it - so its shape is free to change.
    """
    if image is not None:
        return f"{img_prefix}/{image.name}"
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
    absent reading, and hiding it would be wrong.

    None is the *only* absence, because the Tap file store coerces a blank field
    to None when it builds the Beer (app/beer.py). This used to also test for
    the empty string, once per Attribute per Tap per poll, because the value
    arrived straight out of an untyped front-matter dict.
    """
    return value is None


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


def resolve_beer_card(beer: Beer, cfg: dict[str, Any],
                      image: Path | None = None,
                      default_glass: str = DEFAULT_GLASS,
                      presentation: TapPresentation = TapPresentation(),
                      img_prefix: str = "/img",
                      ) -> dict[str, Any]:
    """Resolve a Beer to the card fields shared by every surface that shows one.

    This is the one implementation of the Beer-to-card resolution (issue #34):
    the Attribute values, the six Visibility answers and the resolved Colour,
    plus the image URL that is built from that Colour. `resolve_tap` calls this
    and adds what is specific to a Tap - the Slot number, vacancy, the Source,
    the description and the updated timestamp. An Upcoming Beer (issue #4) has
    no Slot and no per-Tap override, so it calls this with the default
    `presentation` and adds nothing beyond what the caller needs.

    `presentation` carries the per-Slot OG/FG tri-state overrides. It defaults
    to "no override" (all fields None) rather than requiring every caller to
    construct one - an Upcoming Beer has no Slot to hold an override, so this
    is also the value that call site should use.

    `image` is the photo paired with whichever Tap file (or, for an Upcoming
    Beer, whichever cached record) the caller resolved - never borrowed from
    elsewhere - so a Beer with no photo of its own shows the Placeholder rather
    than another Beer's picture. It is a parameter here rather than something
    this function looks up, because *which* photo belongs to a Beer is a
    question for the caller's own store, not for this resolution.

    `img_prefix` says which store `image` was resolved from, so the URL routes
    back to the right one - see `_image_url_for`. A Tap's caller leaves it at
    the default; the Upcoming resolution below passes `/img/upcoming`.
    """
    def setting(key: str) -> Any:
        # Fall back to the schema rather than repeating the literals here: a
        # default changed in config_store must not need changing twice.
        return cfg.get(key, DEFAULT_CONFIG[key])

    # Colour precedence lives in colors.resolve_color, not here. It answers with
    # a colour or with Unknown (None); the board forwards that answer to both
    # surfaces rather than each of them re-deriving it.
    color = resolve_color(beer.ebc, beer.saturation, beer.color_override)
    glass = normalize_glass(beer.glass or default_glass)
    return {
        # The caller decides the display name's fallback (a Vacant-less Tap
        # falls back to "Tap N"; an Upcoming Beer may have a different one, or
        # none) - this resolution only ever reports what the Beer itself holds.
        "name": beer.name,
        "abv": beer.abv,
        "ibu": beer.ibu,
        "ebc": beer.ebc,
        "og": beer.og,
        "fg": beer.fg,
        # Null when Colour is Unknown. The display's `|| grey` is then the
        # swatch's own declared fallback rather than a copy of a server value.
        "color_hex": color.color_hex if color else None,
        "text_color": color.text_color if color else None,
        "image_url": _image_url_for(image, color, glass, img_prefix),
        # Visibility, already resolved. Named for the question each one answers,
        # not for the Settings that fed it: the display has no business knowing
        # *why* a stat is hidden, only that it is. OG and FG are the two that
        # carry a per-Tap override; the rest have only the global toggle.
        "abv_visible": resolve_visibility(
            beer.abv, setting("show_abv"), setting("hide_abv_when_empty")),
        "ibu_visible": resolve_visibility(
            beer.ibu, setting("show_ibu"), setting("hide_ibu_when_empty")),
        "ebc_visible": resolve_visibility(
            beer.ebc, setting("show_color"), setting("hide_color_when_empty")),
        "og_visible": resolve_visibility(
            beer.og, setting("show_og"), setting("hide_og_when_empty"),
            presentation.show_og),
        "fg_visible": resolve_visibility(
            beer.fg, setting("show_fg"), setting("hide_fg_when_empty"),
            presentation.show_fg),
        # The swatch shares the EBC toggle but asks whether *Colour* is known -
        # an EBC value OR an override - so passing the resolved Colour instead of
        # the EBC number is the entire special case. A Beer with only an override
        # therefore shows a swatch and no EBC number, which is the intent
        # (ADR-0004), and `color_known` no longer has to travel to explain it.
        "swatch_visible": resolve_visibility(
            color, setting("show_color"), setting("hide_color_when_empty")),
    }


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

    # The Beer arrives typed and already coerced - every Attribute is a number
    # or None, never a blank string - so the board reads attributes instead of
    # re-coercing a front-matter dict on every poll from every TV.
    beer = tap_file.beer
    # The Beer-to-card resolution (issue #34) is the one implementation shared
    # with whatever later resolves an Upcoming Beer: Attributes, the six
    # Visibility answers, Colour and the image URL. Everything below is what is
    # specific to a Tap rather than to a Beer.
    card = resolve_beer_card(beer, cfg, tap_file.image, default_glass,
                             tap_file.presentation)
    return {
        "tap": tap,
        "vacant": False,
        # The filename decides the Source. A front-matter `source:` key is
        # written for a human reading the file and is never read back as truth,
        # so a mislabelled file cannot make sync and the display disagree.
        "source": str(tap_file.source),
        # The description is the markdown body, a named field on the TapFile
        # rather than a synthesised front-matter key.
        "description": (tap_file.body or "").strip(),
        "updated": tap_file.updated,
        **card,
        # A Tap with no name falls back to its Slot number; that fallback is
        # specific to a Tap card (an Upcoming Beer has no Slot to fall back to)
        # so it is applied here rather than inside the shared resolution.
        "name": card["name"] or f"Tap {tap}",
    }


# Customer-facing spelling of each Batch status (issue #39). A Batch's own
# status word ("completed") is written for Brewfather, not for someone at the
# bar deciding whether to come back this week or next - "how soon" is the
# question a customer actually has. This is the one dictionary; #45's on-tap
# conditioning marker reuses it rather than inventing its own wording a
# second time. Keys mirror mapping.STATUS_PRECEDENCE; "unknown" is
# deliberately absent; see resolve_upcoming for what that means on the wire.
STATUS_DISPLAY_LABELS: dict[str, str] = {
    "completed": "Ready",
    "conditioning": "Conditioning",
    "fermenting": "Fermenting",
    "brewing": "Brewing",
    "planning": "Planned",
}


def _order_upcoming(entries: list[UpcomingEntry], cap: int) -> list[UpcomingEntry]:
    """Order Upcoming Beers for display, and truncate to the cap (issue #37).

    Both steps are display-time, not sync-time (ADR-0006), so changing
    `max_upcoming_previews` takes effect on the very next poll without a
    sync - the cap is applied here, against whatever the store already
    holds, rather than by the store limiting what it writes.

    Sort key: status rank ascending (`mapping.STATUS_PRECEDENCE` - Completed
    first, unknown last), then recency descending (the newer Batch shown
    first when two share a status), then Batch id ascending as a final,
    purely mechanical tie-break. That last term is load-bearing: without it,
    two entries with identical status and recency sort in whatever order the
    directory glob happens to return them, which is not guaranteed stable
    across polls and would make the board's order flicker for no reason a
    customer could see.
    """
    ordered = sorted(
        entries,
        key=lambda e: (status_rank_for_label(e.status), -e.revision, str(e.batch_id)),
    )
    return ordered[:max(cap, 0)]


def resolve_upcoming(entry: UpcomingEntry, cfg: dict[str, Any],
                     default_glass: str, num_taps: int,
                     taps: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve one Upcoming Beer to the teaser fields the wire carries (issue #37).

    Reuses `resolve_beer_card` for the Attributes, the six Visibility answers
    and the Colour - the identical chain a Tap uses (ADR-0004, CLAUDE.md's
    "resolved answers, not inputs"). What is added here is specific to a
    teaser: the Batch identity, the bound Slot (or none), the description,
    `pinned`, and (issue #39) the teaser's own words - `status_label` (a
    customer-facing status word, or null), `subtitle` (resolved text or null;
    see below) and `abv_estimated` (drives the '~' marker, true only when
    `abv_visible` is). `show_upcoming_abv` also re-gates `abv_visible` here,
    on top of the ordinary Visibility answer `resolve_beer_card` already gave
    it.

    A teaser's Slot resolves to None - becoming unbound - when it names a Slot
    beyond the *configured* tap count. Sync accepts `tap:1..MAX_NUM_TAPS`
    regardless of `num_taps` (the tap count must never destroy Beer data), so
    a cached entry can point past the board the operator has actually built;
    printing "coming up on tap 12" on an eight-tap board is worse than saying
    nothing. This is resolved here, at display time, rather than at
    sync/write time, so raising `num_taps` re-binds a previously
    out-of-range teaser on the very next poll with no sync needed - the same
    display-time contract ADR-0006 gives the ordering and the cap.

    `pinned` is true only when the teaser is bound AND that Slot is Vacant
    (`taps[slot - 1]["vacant"]`): there is no pouring beer for the teaser to
    defer to, so it shows permanently instead of only while its Batch would
    otherwise be invisible. This is board logic, not a presentation choice
    (CLAUDE.md), so it is decided here and travels as an answer - the display
    never re-derives it from the Slot's own vacancy.

    `cross_fade` (issue #40) answers whether the in-place baseline may cycle
    this teaser over its Slot. False for a pinned teaser (it already owns its
    Slot outright, nothing to fade over); false for an unbound teaser (no
    Slot exists to fade over); false for every teaser when the operator has
    turned `upcoming_rotate_occupied` off. Otherwise true - a teaser bound to
    an occupied Slot with rotation on. Resolved here rather than on the
    display, same as `pinned`: `upcoming_rotate_occupied` never reaches the
    wire (CLAUDE.md), only this resolved answer does.

    `on_surfaces` (issue #41) answers whether this teaser belongs on the
    overflow surfaces (the on-deck page, and #42's half-board panel). Under
    the default `"overflow"` scope it is exactly the teaser the baseline
    cannot already reach: `not pinned and not cross_fade` - true for an
    unbound teaser (both are always false there) and for a bound-and-occupied
    teaser only when rotation is off (cross_fade is false there too), false
    for a pinned teaser (it already owns its Slot, the strongest presentation
    available, so it is not overflow) and for a bound-and-occupied teaser the
    cross-fade can already cycle. Under `"all"` scope it is unconditionally
    true, pinned teasers included - the named regression from the prototype
    (CLAUDE.md), which is what makes "all upcoming" actually mean all rather
    than silently omitting a beer already sitting in a Vacant Slot.
    """
    slot = entry.slot if entry.slot is not None and 1 <= entry.slot <= num_taps else None
    pinned = slot is not None and bool(taps[slot - 1]["vacant"])
    rotate_occupied = bool(cfg.get("upcoming_rotate_occupied",
                                   DEFAULT_CONFIG["upcoming_rotate_occupied"]))
    # Occupied means bound and NOT pinned: pinned already covers the
    # bound-and-Vacant case, so anything left that is bound is occupied.
    cross_fade = slot is not None and not pinned and rotate_occupied
    scope = cfg.get("upcoming_surface_scope", DEFAULT_CONFIG["upcoming_surface_scope"])
    on_surfaces = True if scope == "all" else (not pinned and not cross_fade)
    # The Upcoming store's own photo, never a Tap's - see _image_url_for.
    card = resolve_beer_card(entry.beer, cfg, entry.image, default_glass,
                             img_prefix="/img/upcoming")

    # show_upcoming_abv layers ON TOP of the ordinary ABV Visibility answer
    # (issue #39), rather than being folded into resolve_beer_card: a Tap's
    # ABV has no such extra gate, so this stays specific to a teaser instead
    # of growing a parameter that every Tap call site would have to pass
    # `True` past. Off forces abv_visible false regardless of the global
    # show_abv toggle. abv_estimated - the '~' marker - travels true only
    # when the ABV actually renders; there is nothing to mark on a hidden
    # stat, and it is true whatever the source (a hydrometer reading on an
    # unfinished beer is an estimate too), so it is never conditioned on
    # anything about the Batch itself.
    card["abv_visible"] = card["abv_visible"] and bool(
        cfg.get("show_upcoming_abv", DEFAULT_CONFIG["show_upcoming_abv"]))
    card["abv_estimated"] = card["abv_visible"]

    # The customer word for the Batch status, or null when the Setting is
    # off - resolved here, once, so #45's on-tap conditioning marker can
    # reuse STATUS_DISPLAY_LABELS without re-deriving this null-when-off rule.
    status_label = (
        STATUS_DISPLAY_LABELS.get(entry.status)
        if bool(cfg.get("show_upcoming_status", DEFAULT_CONFIG["show_upcoming_status"]))
        else None
    )

    # The subtitle text is the resolved answer, never the Setting that
    # produced it (CLAUDE.md, CONTEXT.md): boundness decides half the
    # question, so sending the toggle alone would leave the display re-running
    # that half itself. An unbound teaser (slot is None, including a teaser
    # bound past num_taps - CLAUDE.md's "treated as unbound") has no tap
    # number anywhere else on the card, so it ALWAYS gets a subtitle,
    # whatever show_upcoming_subtitle says. A bound teaser gets one only when
    # the Setting is on, because the ribbon already says "coming up" and the
    # tap number is already in the card head.
    if slot is None:
        subtitle = "no tap assigned yet"
    elif bool(cfg.get("show_upcoming_subtitle", DEFAULT_CONFIG["show_upcoming_subtitle"])):
        label = str(cfg.get("upcoming_label") or DEFAULT_CONFIG["upcoming_label"])
        subtitle = f"{label} on tap {slot}"
    else:
        subtitle = None

    return {
        "batch_id": entry.batch_id,
        "slot": slot,
        "pinned": pinned,
        "cross_fade": cross_fade,
        "on_surfaces": on_surfaces,
        "description": (entry.body or "").strip(),
        "status_label": status_label,
        "subtitle": subtitle,
        **card,
    }


def build_board() -> dict[str, Any]:
    """Build the full board payload consumed by the TV display."""
    cfg = load_config()
    num_taps = int(cfg.get("num_taps", 0) or 0)
    hide_vacant = bool(cfg.get("hide_vacant_taps", False))
    default_glass = cfg.get("glass_type", DEFAULT_GLASS)

    taps: list[dict[str, Any]] = []
    for tap in range(1, num_taps + 1):
        resolved = resolve_tap(tap, default_glass, cfg)
        # "hidden" tells the frontend to omit + re-flow; vacant cards are still
        # returned (with the flag) so the admin/preview can reason about them.
        resolved["hidden"] = bool(resolved["vacant"] and hide_vacant)
        taps.append(resolved)

    # Upcoming Beers (issue #37): resolved answers, exactly like the Taps
    # above, and built from the Taps just resolved so `pinned` can read each
    # bound Slot's own `vacant` flag rather than re-deriving it.
    #
    # This whole block is gated on the toggle, and the "upcoming" key is only
    # added to the payload when it is on - not sent as an empty list when off.
    # That is the toggle's contract as CLAUDE.md and issue #37 state it: with
    # `show_upcoming_previews` off the payload must be identical to what this
    # function produced before the feature existed, for the same data, and an
    # extra key would not be identical even if it were always empty.
    upcoming: list[dict[str, Any]] | None = None
    if bool(cfg.get("show_upcoming_previews", False)):
        cap = int(cfg.get("max_upcoming_previews",
                          DEFAULT_CONFIG["max_upcoming_previews"]) or 0)
        upcoming = []
        for entry in _order_upcoming(list_upcoming(), cap):
            teaser = resolve_upcoming(entry, cfg, default_glass, num_taps, taps)
            if teaser["pinned"]:
                # A Vacant Slot carrying a pinned teaser has something to
                # show, so hide_vacant_taps must not hide it - resolved here,
                # not forwarded, and answered through the Tap's own already-
                # resolved `hidden` rather than a new flag (issue #37).
                taps[teaser["slot"] - 1]["hidden"] = False
            upcoming.append(teaser)

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

    board: dict[str, Any] = {
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
    if upcoming is not None:
        board["upcoming"] = upcoming
        # The ribbon's own text: the one input from this ticket's four
        # Settings that legitimately travels raw, because it IS the answer -
        # there is no chain to resolve, only a string to draw (CLAUDE.md).
        # Absent whenever "upcoming" itself is, for the same reason: with the
        # feature off there is no ribbon to letter.
        board["upcoming_label"] = str(
            cfg.get("upcoming_label", DEFAULT_CONFIG["upcoming_label"]))
        # The one cadence driving every upcoming animation (issue #40): a
        # scheduling fact the display must execute, in the same category as
        # rotation_seconds - not an input like upcoming_rotate_occupied, which
        # stays off the wire because the display never decides whether to
        # rotate, only how fast to run what it is already told to.
        board["upcoming_interval_seconds"] = int(cfg.get(
            "upcoming_interval_seconds", DEFAULT_CONFIG["upcoming_interval_seconds"]))
        # The on-deck page's own scheduling facts (issue #41): whether it is
        # enabled at all, and its multiple of the one interval - the same
        # category as upcoming_interval_seconds, something the display must
        # execute rather than decide. `upcoming_surface_scope` is deliberately
        # NOT here: it is fully consumed into each teaser's `on_surfaces`
        # answer above, and CLAUDE.md/CONTEXT.md require it stay off the wire.
        # Whether the page actually has anything to draw is left to the
        # display filtering `upcoming` by `on_surfaces` itself - with nothing
        # to carry the page is not rendered at all (issue #41), which needs no
        # extra flag here.
        board["upcoming_deck_enabled"] = bool(cfg.get(
            "show_upcoming_deck_page", DEFAULT_CONFIG["show_upcoming_deck_page"]))
        board["upcoming_deck_multiple"] = int(cfg.get(
            "upcoming_deck_multiple", DEFAULT_CONFIG["upcoming_deck_multiple"]))
    return board
