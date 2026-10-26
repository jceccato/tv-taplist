"""config.json load/save with first-run bootstrap and atomic writes.

config.json holds **Settings**: operator configuration, deliberate and rarely
changed. It is the single source of truth for credentials, tap count, display
toggles, and cleanup limits. Secrets (the Brewfather key) live here in
plaintext; this is a documented, conscious choice for the appliance scope
(see README).

Machine-written **Status** - the sync timestamps, the last sync error, and what
the daily update check found - used to live here too, and no longer does. It
sits in status.json and is owned by `app/status_store.py`, so the scheduled jobs
no longer rewrite the file holding the credential on every cycle. See
`docs/adr/0002-config-status-separation.md`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, NamedTuple

from . import upcoming_store
from .atomic import JOB_LOCK, atomic_write_text
from .beer_glass import DEFAULT_GLASS, normalize_glass
from .colors import parse_hex_color
from .paths import CONFIG_PATH, ensure_dirs
from .photo_library import (
    FORMAT_WEBP, FORMATS as PHOTO_FORMATS, ORDER_SHUFFLE, ORDERS as ALBUM_ORDERS,
    SIZE_4K, SIZES as PHOTO_SIZES, invalidate_pipeline as _invalidate_photo_pipeline,
)
from .theme import DEFAULT_THEME, coerce_custom_theme, normalize_theme_name

log = logging.getLogger("taplist.config")

DEFAULT_CONFIG: dict[str, Any] = {
    "brewfather_user_id": "",
    "brewfather_api_key": "",
    # Brewfather sync scope: by default only Completed batches sync. When on, also
    # pull Conditioning batches (a beer that is on tap but still lagering/maturing).
    "include_conditioning": False,
    # Widens the scope one stage earlier: Fermenting Batches (still in primary),
    # so an upcoming Beer can be shown before it is ready. Independent of
    # include_conditioning - all four combinations are valid. Either way a Batch
    # still needs a `tap:X` note token to reach a Slot; this only decides which
    # Batches are fetched, never which are displayed.
    "include_fermenting": False,
    # Master toggle for the Upcoming Beer feature (issue #4). Off means
    # today's behaviour exactly: nothing is fetched differently (that is
    # still entirely include_conditioning/include_fermenting's business,
    # see brewfather.py), and /data/upcoming/ is not written at all. This is
    # the ONE Setting that deletes files when it goes from on to off - see
    # config_store.apply_settings and brewfather.run_sync, the two places
    # that clear it (docs/adr/0006).
    "show_upcoming_previews": False,
    # How many Upcoming Beers the board shows at once, after ordering by status
    # then recency then Batch id (app/board.py). Resolved at DISPLAY time, per
    # ADR-0006, so lowering or raising it takes effect on the very next poll -
    # no sync required, because the cap never touches what the store caches.
    "max_upcoming_previews": 3,
    # The teaser card's words (issue #39). All four are resolved at board time
    # (app/board.py) into wire answers - status_label, subtitle, abv_estimated
    # and the ribbon's own text - never forwarded as raw toggles; see
    # CLAUDE.md's "resolved answers, not inputs" and GLOSSARY.md's Visibility.
    "upcoming_label": "Coming up",       # ribbon text; capped, see MAX_UPCOMING_LABEL_LEN
    "show_upcoming_status": True,        # "Ready" / "Conditioning" / ... under the head
    "show_upcoming_subtitle": False,     # "<label> on tap N" under a BOUND teaser's head;
                                          # an unbound teaser always gets one regardless
    "show_upcoming_abv": False,          # layered on top of show_abv; off wins either way
    # Scheduling (issue #40). One Setting drives every upcoming animation, now
    # (the cross-fade) and later (the surfaces in #41/#42): an operator tuning
    # "how often do I see upcoming beers" is asking one question, not three.
    # The hold (how long a teaser stays up) is DERIVED from this, not a second
    # Setting - see display.js's holdMs().
    "upcoming_interval_seconds": 20,
    # May a pouring beer be cross-faded out at all? On by default. Off pushes
    # every occupied-slot teaser into the overflow (a later surface's
    # business) - this stays OFF the board payload; only the per-teaser
    # resolved `cross_fade` answer travels (CLAUDE.md, board.resolve_upcoming).
    "upcoming_rotate_occupied": True,
    # The overflow surfaces (issue #41/#42): homes for a teaser the baseline
    # cross-fade cannot reach. The on-deck page is an ordinary carousel page
    # shown by the normal rotation (issue #4 close-out: its old scheduled
    # jump-and-return turn flicked instead of flowing, so the page needs no
    # cadence of its own - only this on/off).
    "show_upcoming_deck_page": False,
    # The half-board panel (issue #42): a second, independent overflow
    # surface, off by default like the on-deck page. Unlike the page it
    # overlays the board on a schedule, so it carries a multiplier of
    # upcoming_interval_seconds - the multiplier is not decoration, it is
    # what stops the panel taking so many ticks that the cross-fade (whose
    # turn shows every bound teaser on the page at once) rarely gets one.
    # 2x reads well as the default (the prototype's own default): the panel
    # is a cheap
    # interruption - the top half of the board stays readable underneath it -
    # so it can afford to take its turn fairly often.
    "show_upcoming_panel": False,
    "upcoming_panel_multiple": 2,
    # What a surface carries. "overflow" (default) is only what the baseline
    # cannot reach - a pinned teaser already owns its Vacant Slot outright, the
    # strongest presentation available, so it is correctly excluded. "all"
    # means ALL, pinned teasers included: the named regression from the
    # prototype, which excluded them under both scopes and so made "all
    # upcoming" quietly untrue (issue #41). Unrecognised values coerce to
    # "overflow" below, never rejected.
    "upcoming_surface_scope": "overflow",
    "num_taps": 0,
    "hide_vacant_taps": False,
    "announcement_text": "",
    "max_archive_age_days": 180,
    "max_archive_storage_mb": 2048,
    # Days a removed photo waits in Removed before the daily cleanup purges it
    # (ADR-0008). Global: there is one bin. Set on the Photos tab, not the
    # Settings form.
    "photo_removed_retention_days": 30,
    # How Display copies are built (ADR-0008). Global: one cache serves every
    # screen. "webp" or "jpeg" (compatibility mode, for TVs that cannot show
    # WebP); "4k" or "1080p". Set on the Photos tab with their own route, not
    # the Settings form. Changing either rebuilds every copy in the background.
    "photo_format": FORMAT_WEBP,
    "photo_size": SIZE_4K,
    # Display options.
    "color_unit": "ebc",            # "ebc" or "srm" - colour stat display unit
    "show_abv": True,               # global show/hide for each stat
    "show_ibu": True,
    "show_color": True,
    "show_og": False,               # original / final gravity (off by default)
    "show_fg": False,
    "hide_abv_when_empty": True,    # when shown, hide per-beer if value missing
    "hide_ibu_when_empty": True,
    "hide_color_when_empty": True,
    "hide_og_when_empty": True,
    "hide_fg_when_empty": True,
    "show_source_badge": False,     # the "Custom"/"BF" badge on each card
    # The conditioning-on-tap status marker (issue #45): mark a beer that is
    # POURING but not finished - Conditioning, or Fermenting when the operator
    # has widened the sync scope and it occupies a Slot. Deliberately its own
    # Setting rather than part of show_upcoming_status, and deliberately OFF by
    # default: that one renders inside a feature that is itself off, while this
    # one changes a live board on a box that may never enable upcoming previews
    # at all. A Completed beer is never marked whatever this says - the board
    # resolves that, see board.resolve_status_marker.
    "show_conditioning_status": False,
    # Theme (display colours).
    "theme": "default",             # preset key, or "custom"
    "theme_custom": dict(DEFAULT_THEME),  # per-colour overrides when theme == "custom"
    "glass_type": DEFAULT_GLASS,    # default glassware for the no-photo placeholder
    # Card sizing. A preset is remembered only so the admin can re-open the
    # picker where the operator left it; the two scales are what actually reach
    # the board, so a preset never needs re-resolving at render time.
    # The two axes are chosen independently: a photo preset never moves the text
    # and vice versa, because on this layout they trade against each other and an
    # operator taming a photo should not have their text resized underneath them.
    "tap_photo_preset": "default",  # "tiny" | "small" | "medium" | "default" | "custom"
    "tap_text_preset": "default",   # "small" | "default" | "large" | "custom"
    "tap_image_scale": 1.0,         # multiplies the photo's measured rendered height
    "tap_text_scale": 1.0,          # multiplies the preferred card font sizes
    # Pagination / carousel.
    "paginate": False,              # when on, show `page_size` taps per page
    "page_size": 6,                 # taps per page (1..8) when paginating
    "rotation_seconds": 30,         # seconds each page is shown
    # Optional venue/company logo at the top of the display.
    "venue_logo": None,             # filename under /data (e.g. venue_logo.png) or null
    "venue_logo_height_vh": 0,      # 0..33 (% of viewport height; 0 hides the header)
    # Update checker - polls GitHub once per day for new releases. Only the
    # operator's intent lives here; what the check FOUND is Status and lives in
    # status.json, along with the sync timestamps that used to sit below it.
    "update_check_enabled": True,     # operator can disable for air-gapped deploys
    # The photo album (issue #47, ADR-0008). All customisable per Display profile
    # and OFF in the Default profile, so an upgrade changes no screen. When on, the
    # screen shows an Album visit after every `album_every_turns` tap-page turns
    # (0 = photos only): `album_photos_per_visit` photos, each for
    # `album_seconds_per_photo`, cross-faded over `album_fade_seconds`.
    "album_enabled": False,
    "album_every_turns": 4,
    "album_photos_per_visit": 6,
    "album_seconds_per_photo": 10,
    "album_order": ORDER_SHUFFLE,       # "shuffle" | "name" | "date" (see photo_library.ORDERS)
    # Which Albums: every one (the default), or a ticked list by album NAME. A
    # name that no longer exists is skipped, never an error (a folder renamed by
    # hand shows up as a new, unticked album).
    "album_all": True,
    "album_selection": [],
    "album_keep_ticker": False,         # keep the announcement ticker over the photos
    "album_fade_seconds": 1.5,
    # The framed print (issue #61): each photo on a mount, in a frame, drawn by
    # the page. "random" is chosen per photo, by the page, from the other
    # choices. "photo" is a muted colour sampled from the photo's edges when its
    # Display copy is made; `album_mount_custom` is used for "custom".
    "album_mount_style": "flat",        # see MOUNT_STYLES
    "album_mount_color": "photo",       # see MOUNT_COLORS
    "album_mount_custom": "#e9e4d8",
    "album_frame": "thin-black",        # see FRAMES
    # Portraits, captions and the slow zoom (issue #62). Two portraits from one
    # visit are shown side by side (each photo counts towards the visit's K);
    # `album_three_up` allows three when `album_pair_portraits` is on. Captions
    # sit on the mount's bottom edge (never a file name). Ken Burns moves the
    # photo inside its mount, never the mount.
    "album_pair_portraits": True,
    "album_three_up": False,
    "album_caption_date": False,
    "album_caption_album": False,
    "album_ken_burns": False,
    # Display profiles (ADR-0007): name -> {customised key: value}. A profile
    # holds ONLY its Customised settings; everything else follows the Default
    # profile (the rest of this dict). Names are validated and values clamped by
    # `_coerce_profiles`. Empty by default, so an upgrade changes no screen.
    "profiles": {},
}

# ---- Display profiles: what can be customised (ADR-0007) ---------------------
#
# The ONE declaration of which Settings a Display profile may customise. The
# guard tests read it now; the profile resolution, the admin form and the save
# path will read it (and nothing else may list these keys).
#
# NEVER_CUSTOMISABLE are venue-wide: they change what the sync fetches or
# stores, or are one venue's one fact (credentials, tap count, the logo image,
# the colour unit). Every other key in DEFAULT_CONFIG is customisable, either
# alone or as part of a one-unit group in CUSTOMISABLE_UNITS.
NEVER_CUSTOMISABLE: frozenset[str] = frozenset({
    "brewfather_user_id",
    "brewfather_api_key",
    "include_conditioning",
    "include_fermenting",
    "num_taps",
    "max_archive_age_days",
    "max_archive_storage_mb",
    "photo_removed_retention_days",
    "photo_format",
    "photo_size",
    "venue_logo",
    "color_unit",
    "update_check_enabled",
    "profiles",   # the profiles themselves are not something a profile customises
})

# Each customisable unit: unit name mapped to the keys customised (or followed) together,
# so a profile can never hold half a Theme. A setting with no partner is a unit
# of one.
CUSTOMISABLE_UNITS: dict[str, tuple[str, ...]] = {
    "theme": ("theme", "theme_custom"),
    "pagination": ("paginate", "page_size"),
    "tap_photo_size": ("tap_photo_preset", "tap_image_scale"),
    "tap_text_size": ("tap_text_preset", "tap_text_scale"),
    "stat_abv": ("show_abv", "hide_abv_when_empty"),
    "stat_ibu": ("show_ibu", "hide_ibu_when_empty"),
    "stat_color": ("show_color", "hide_color_when_empty"),
    "stat_og": ("show_og", "hide_og_when_empty"),
    "stat_fg": ("show_fg", "hide_fg_when_empty"),
    "rotation_seconds": ("rotation_seconds",),
    "hide_vacant_taps": ("hide_vacant_taps",),
    "announcement_text": ("announcement_text",),
    "glass_type": ("glass_type",),
    "venue_logo_height_vh": ("venue_logo_height_vh",),
    "show_source_badge": ("show_source_badge",),
    "show_conditioning_status": ("show_conditioning_status",),
    "show_upcoming_previews": ("show_upcoming_previews",),
    "max_upcoming_previews": ("max_upcoming_previews",),
    "upcoming_label": ("upcoming_label",),
    "show_upcoming_status": ("show_upcoming_status",),
    "show_upcoming_subtitle": ("show_upcoming_subtitle",),
    "show_upcoming_abv": ("show_upcoming_abv",),
    "upcoming_interval_seconds": ("upcoming_interval_seconds",),
    "upcoming_rotate_occupied": ("upcoming_rotate_occupied",),
    "show_upcoming_deck_page": ("show_upcoming_deck_page",),
    "show_upcoming_panel": ("show_upcoming_panel",),
    "upcoming_panel_multiple": ("upcoming_panel_multiple",),
    "upcoming_surface_scope": ("upcoming_surface_scope",),
    "album_enabled": ("album_enabled",),
    "album_every_turns": ("album_every_turns",),
    "album_photos_per_visit": ("album_photos_per_visit",),
    "album_seconds_per_photo": ("album_seconds_per_photo",),
    "album_order": ("album_order",),
    "album_selection": ("album_all", "album_selection"),
    "album_keep_ticker": ("album_keep_ticker",),
    "album_fade_seconds": ("album_fade_seconds",),
    "album_mount_style": ("album_mount_style",),
    "album_mount_color": ("album_mount_color", "album_mount_custom"),
    "album_frame": ("album_frame",),
    "album_pair_portraits": ("album_pair_portraits",),
    "album_three_up": ("album_three_up",),
    "album_captions": ("album_caption_date", "album_caption_album"),
    "album_ken_burns": ("album_ken_burns",),
}

CUSTOMISABLE_KEYS: frozenset[str] = frozenset(
    key for keys in CUSTOMISABLE_UNITS.values() for key in keys)


# Display profile names: lowercase letters, digits and hyphens, up to 32 long,
# matched regardless of case (so a name works safely in a URL).
MAX_PROFILE_NAME_LEN = 32
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9-]{1,%d}$" % MAX_PROFILE_NAME_LEN)
# "default" names the Default profile, which is not stored and cannot be a
# stored profile (it cannot be created, renamed or deleted).
DEFAULT_PROFILE = "default"
_MAX_RECORDED_NAME_LEN = 64   # longest name logged or remembered

# How a screen's profile was chosen (the last field of `Effective`).
CHOSEN_DEFAULT = "default"    # no profile named
CHOSEN_URL = "url"            # `?display=<name>` named an existing profile
CHOSEN_UNKNOWN = "unknown"    # a name was asked for but no such profile exists
CHOSEN_USER_AGENT = "user-agent"  # no name; the browser's User-Agent matched a profile

# A profile's optional "also use for screens whose User-Agent contains" text.
# It sits in the profile's dict beside the Customised settings but is not one:
# it is never merged into the effective settings (see `resolve_effective`).
PROFILE_UA_KEY = "match_user_agent"
MAX_PROFILE_UA_LEN = 200


def normalize_profile_name(name: Any) -> str | None:
    """The canonical form of a profile name, or None if it is not a valid one."""
    if not isinstance(name, str):
        return None
    clean = name.strip().lower()
    if clean == DEFAULT_PROFILE or not _PROFILE_NAME_RE.match(clean):
        return None
    return clean


class Effective(NamedTuple):
    """The result of resolving a screen's profile.

    `settings` are the **effective settings** (GLOSSARY.md): the profile's
    Customised settings over the Default profile. `profile` is the name that was
    applied ("default" for the Default profile). `chosen` says how: one of
    CHOSEN_DEFAULT, CHOSEN_URL, CHOSEN_USER_AGENT or CHOSEN_UNKNOWN.
    """
    settings: dict[str, Any]
    profile: str
    chosen: str


def normalize_user_agent_match(text: Any) -> str:
    """The stored form of a profile's User-Agent text: trimmed, one line, capped.

    Blank (or not a string) means the profile has no User-Agent match.
    """
    if not isinstance(text, str):
        return ""
    clean = " ".join(text.split())
    return clean[:MAX_PROFILE_UA_LEN].strip()


def profiles_matching_user_agent(settings: dict[str, Any],
                                 user_agent: str | None) -> list[str]:
    """Names of the profiles whose User-Agent text the browser's contains.

    Case-insensitive substring, in list order: the first is the one that wins,
    and more than one is a clash the Admin warns about.
    """
    if not user_agent:
        return []
    haystack = str(user_agent).lower()
    out = []
    for name, values in (settings.get("profiles") or {}).items():
        needle = normalize_user_agent_match(values.get(PROFILE_UA_KEY)).lower()
        if needle and needle in haystack:
            out.append(name)
    return out


def _effective_for(settings: dict[str, Any], name: str, chosen: str) -> Effective:
    base = {k: v for k, v in settings.items() if k != "profiles"}
    mine = {k: v for k, v in settings["profiles"][name].items() if k != PROFILE_UA_KEY}
    return Effective({**base, **mine}, name, chosen)


def resolve_effective(settings: dict[str, Any], profile_name: str | None,
                      user_agent: str | None = None) -> Effective:
    """Pure: resolve the effective settings for one screen.

    No I/O. `settings` is the coerced Settings (the Default profile plus the
    stored `profiles`); `profile_name` is what the screen asked for, or None;
    `user_agent` is the browser's own, used only when no name was asked for.
    A setting the profile does not customise is read from `settings` here, at
    resolution time, so a change to the Default profile reaches every profile
    that follows it.

    Precedence: `?display=` always wins. A name that is asked for but does not
    exist gets the Default profile (and is listed as unknown) without consulting
    the User-Agent, so a typo is never papered over. With no name, the first
    profile (in list order) whose User-Agent text the browser's contains wins;
    otherwise the Default profile.
    """
    profiles = settings.get("profiles") or {}
    base = {k: v for k, v in settings.items() if k != "profiles"}
    asked = profile_name is not None and str(profile_name).strip() != ""
    if asked and str(profile_name).strip().lower() == DEFAULT_PROFILE:
        # Naming the Default profile explicitly is not a typo, and is a choice.
        return Effective(base, DEFAULT_PROFILE, CHOSEN_DEFAULT)
    if asked:
        name = normalize_profile_name(profile_name)
        if name is None or name not in profiles:
            return Effective(base, DEFAULT_PROFILE, CHOSEN_UNKNOWN)
        return _effective_for(settings, name, CHOSEN_URL)
    matches = profiles_matching_user_agent(settings, user_agent)
    if matches:
        return _effective_for(settings, matches[0], CHOSEN_USER_AGENT)
    return Effective(base, DEFAULT_PROFILE, CHOSEN_DEFAULT)


def upcoming_wanted(settings: dict[str, Any]) -> bool:
    """True while the Default profile or any profile has "show upcoming beers" on.

    The sync works out and caches Upcoming Beers on this answer, and
    `/data/upcoming/` is cleared only when it is False (ADR-0006, ADR-0007).
    Each profile's effective value is resolved the way a screen's is, so a
    profile that follows the Default counts as the Default does. Whether a
    given screen *shows* them is a separate, per-profile answer made at board
    time.
    """
    profiles = settings.get("profiles") or {}
    return any(
        bool(resolve_effective(settings, name).settings.get(
            "show_upcoming_previews", False))
        for name in (None, *profiles))


def effective_settings() -> dict[str, Any]:
    """The Default profile's effective settings, read from config.json.

    What a caller with no screen in hand (a bare `build_board()`, the Admin's
    summary) resolves from. A screen's own profile goes through
    `resolve_effective`.
    """
    return resolve_effective(load_config(), None).settings


# Unknown profile names screens have asked for since the last restart (ADR-0007:
# runtime observations live in memory, not in Settings). Bounded, most recent
# last, so a scanner cannot grow it.
MAX_UNKNOWN_PROFILE_NAMES = 50
_unknown_names: dict[str, None] = {}
_unknown_lock = threading.Lock()


def record_unknown_profile(name: str) -> None:
    """Remember that a screen asked for a profile that does not exist."""
    clean = str(name).strip().lower()[:_MAX_RECORDED_NAME_LEN]
    if not clean:
        return
    with _unknown_lock:
        _unknown_names.pop(clean, None)      # re-asked: move to most recent
        _unknown_names[clean] = None
        while len(_unknown_names) > MAX_UNKNOWN_PROFILE_NAMES:
            _unknown_names.pop(next(iter(_unknown_names)))


def unknown_profile_names() -> list[str]:
    """Unknown profile names asked for since the last restart, oldest first."""
    with _unknown_lock:
        return list(_unknown_names)


def clear_unknown_profile_names() -> None:
    """Forget every recorded unknown name (tests; also a future Admin action)."""
    with _unknown_lock:
        _unknown_names.clear()


# Upper bound on the tap count. Well above any real venue; guards /api/board and
# the admin's per-tap rows from an accidental or pasted absurd value that would
# balloon every board build (each tap does per-slot filesystem probing).
MAX_NUM_TAPS = 200
# Cap the venue logo at a third of the screen height (per the design).
MAX_VENUE_LOGO_VH = 33
# Pagination / rotation bounds (the per-count grid layouts are tuned up to 8).
MAX_PAGE_SIZE = 8
MIN_ROTATION_SECONDS = 3
MAX_ROTATION_SECONDS = 600
# Upper bound on the Upcoming preview cap (issue #37). Not a system limit the
# way MAX_NUM_TAPS is - it exists so a mistyped value can't ask the board to
# resolve and sort an unbounded queue - so it is generous rather than tight.
MAX_UPCOMING_PREVIEWS = 20

# Cap on the operator's teaser-ribbon label (issue #39), a STRING length, not a
# numeric bound - it deliberately does not live in SETTINGS_BOUNDS, which
# clamps numbers and feeds the admin's numeric min/max rendering. The ribbon
# is a single unwrapped line (CLAUDE.md) and does not wrap, so a label past
# this length is truncated rather than rejected, at the same point _coerce
# clamps every numeric bound; the admin form's `maxlength` renders from this
# same constant. Checked against an eight-cards-across layout before shipping
# - see the ticket's browser verification note.
MAX_UPCOMING_LABEL_LEN = 32

# The admin's ribbon-label presets, plus the "Custom..." entry point the form
# adds itself. Whatever text is actually submitted is what gets stored and
# truncated - this tuple only seeds the dropdown, so a stale browser can never
# disagree with what the server saves.
UPCOMING_LABEL_PRESETS: tuple[str, ...] = (
    "Coming up", "Up next", "Coming soon", "Just around the bend",
)

# Card sizing bounds. The text range is deliberately wide: we cannot guess the
# operator's screen (a Fire Stick on a small TV needs different numbers from a 4K
# panel), so the clamp only rules out values that would render the board unusable,
# and the CSS keeps a px floor on every font size so even the extremes stay
# legible - see display.css.
#
# The photo range stops at 1.0, which is a structural fact rather than taste: the
# card gives its name and description their natural height first and the photo
# only gets what is left, so the photo can never be made bigger than the space the
# text leaves it. A scale above 1 would be a control that does nothing.
MIN_TAP_IMAGE_SCALE = 0.25
MAX_TAP_IMAGE_SCALE = 1.0
MIN_TAP_TEXT_SCALE = 0.5
MAX_TAP_TEXT_SCALE = 2.0

# Every numeric Settings bound, in one table: (minimum, maximum), with `None`
# for a maximum that is deliberately open (the cleanup limits have no ceiling -
# a venue may keep its archive forever).
#
# There is one table because there is one enforcement point. `_coerce` clamps
# from these entries, and the Admin form's inputs take their `min`/`max`
# attributes from the same entries, so the browser refuses at the point of
# typing exactly the value the store would otherwise clamp silently after a
# save. Nothing else in the app may restate a bound: a route checking one of
# these itself is how the two layers came to disagree about the tap count - the
# route rejected a negative, the store clamped it, and the ceiling was enforced
# in only one of the two. See GLOSSARY.md, Known hazards.
SETTINGS_BOUNDS: dict[str, tuple[float, float | None]] = {
    "num_taps": (0, MAX_NUM_TAPS),
    "max_upcoming_previews": (0, MAX_UPCOMING_PREVIEWS),
    # 1x to 6x the shared cadence (issue #42). The panel dispatches ahead of
    # the cross-fade on its claimed ticks, so the floor of 1 is the operator
    # choosing "the panel every tick" with eyes open - the ceiling of 6 is
    # generous enough that "practically never" is already reachable well
    # before it.
    "upcoming_panel_multiple": (1, 6),
    # 300, not rotation_seconds' 600 (CLAUDE.md): five minutes is already the
    # point where a customer there for one drink may never see a teaser, and
    # an operator reaching past it wants the feature off rather than slowed.
    # The 5-second floor sits well below the derived hold's 1.5s floor, so no
    # value in this range can produce a teaser that merely flashes.
    "upcoming_interval_seconds": (5, 300),
    "max_archive_age_days": (0, None),
    "max_archive_storage_mb": (0, None),
    # 0 purges the bin at every nightly cleanup; ten years is the practical top.
    "photo_removed_retention_days": (0, 3650),
    "page_size": (1, MAX_PAGE_SIZE),
    "rotation_seconds": (MIN_ROTATION_SECONDS, MAX_ROTATION_SECONDS),
    "venue_logo_height_vh": (0, MAX_VENUE_LOGO_VH),
    "tap_image_scale": (MIN_TAP_IMAGE_SCALE, MAX_TAP_IMAGE_SCALE),
    "tap_text_scale": (MIN_TAP_TEXT_SCALE, MAX_TAP_TEXT_SCALE),
    # The photo album (ADR-0008). 0 tap-page turns between visits means photos
    # only; the 2-second floor on a photo keeps a cross-fade readable; the fade
    # may be 0 (a hard cut) up to a long, slow dissolve.
    "album_every_turns": (0, 50),
    "album_photos_per_visit": (1, 50),
    "album_seconds_per_photo": (2, 600),
    "album_fade_seconds": (0, 10),
}

# The fixed scale behind each named preset, one map per axis. "custom" is absent
# on purpose: it means "leave the operator's own number alone", so it has nothing
# to resolve. These maps are the single definition - the admin form posts a preset
# key and the server resolves it, so the browser can never disagree with the
# stored config.
TAP_PHOTO_PRESETS: dict[str, float] = {
    # "default" is 1.0 and renders exactly as the board did before this control
    # existed, so an upgrade changes nothing until an operator shrinks a photo.
    "tiny": 0.4,
    "small": 0.6,
    "medium": 0.75,
    "default": 1.0,
}
TAP_TEXT_PRESETS: dict[str, float] = {
    "small": 0.75,
    "default": 1.0,
    "large": 1.4,
}
TAP_PHOTO_PRESET_KEYS = (*TAP_PHOTO_PRESETS.keys(), "custom")
TAP_TEXT_PRESET_KEYS = (*TAP_TEXT_PRESETS.keys(), "custom")


def _clamp(value: float, lo: float, hi: float | None) -> float:
    """Clamp to a bounds-table entry, where `None` means no maximum."""
    return max(lo, value if hi is None else min(hi, value))


def _coerce_int(value: Any, lo: int, hi: int | None, default: int) -> int:
    try:
        return int(_clamp(int(value), lo, hi))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, lo: float, hi: float | None, default: float) -> float:
    """Clamp a float setting, falling back to `default` for junk or NaN.

    NaN is checked explicitly because it survives float() and then loses every
    min/max comparison, so it would sail through a plain clamp and end up in a
    CSS custom property as the literal "nan".
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return _clamp(f, lo, hi)


def _resolve_preset(presets: dict[str, float], preset: str, scale: Any) -> tuple[str, Any]:
    """Resolve one card-sizing axis to the preset key and scale that get stored.

    A named preset owns its number: whatever the slider posted is discarded, so
    the config can never hold "small" next to Default's scale. "custom" (and any
    unrecognised key, which normalises to Custom rather than silently rewriting
    the operator's number) keeps the submitted value.
    """
    key = str(preset or "").strip().lower()
    fixed = presets.get(key)
    if fixed is not None:
        return key, fixed
    return "custom", scale


def resolve_tap_photo_preset(preset: str, image_scale: Any) -> tuple[str, Any]:
    """Resolve the photo axis. See `_resolve_preset`."""
    return _resolve_preset(TAP_PHOTO_PRESETS, preset, image_scale)


def resolve_tap_text_preset(preset: str, text_scale: Any) -> tuple[str, Any]:
    """Resolve the text axis. See `_resolve_preset`."""
    return _resolve_preset(TAP_TEXT_PRESETS, preset, text_scale)


def _preset_for_scale(presets: dict[str, float], scale: float) -> str:
    """Name the preset a bare scale corresponds to, or "custom" if none does.

    Only used when migrating a config written before the axis had its own preset
    key: the number is the truth on disk, so the picker is derived from it rather
    than left showing a preset whose scale is not the one being rendered.
    """
    for key, value in presets.items():
        if abs(value - scale) < 1e-9:
            return key
    return "custom"


# The framed print's choices (issue #61), in the order the Admin lists them.
# "random" is last in each: the page picks one of the others for each photo.
# display.js draws each of these by name, and a drift guard ties its lists to
# these tuples.
MOUNT_STYLES = ("none", "flat", "double", "shadow", "float", "random")
MOUNT_COLORS = ("photo", "white", "cream", "black", "custom", "random")
# The "real-" frames are generated photographs of real frames (static/frames/,
# provenance in its README), drawn with CSS border-image; the rest are drawn in CSS.
DRAWN_FRAMES = ("none", "thin-black", "thin-white", "oak", "walnut", "gilded", "polaroid")
REALISTIC_FRAMES = ("real-oak-limed", "real-oak-honey", "real-walnut", "real-gilded-baroque",
                    "real-gilded-classic", "real-black-ash", "real-white-stepped",
                    "real-barnwood", "real-farmhouse", "real-dark-timber")
FRAMES = DRAWN_FRAMES + REALISTIC_FRAMES + ("random",)


def _coerce_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower() if isinstance(value, str) else ""
    return text if text in allowed else default


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted config over defaults and coerce types defensively."""
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})

    # Every numeric Settings bound is applied here, from SETTINGS_BOUNDS, and
    # nowhere else. Out of range is clamped rather than refused: a hand-edited
    # config.json (ADR-0001 makes it editable) has nobody to report an error to
    # and must never stop the box booting, so clamping is the only safe
    # disposition. The Admin form carries the same numbers as input attributes
    # so an operator is stopped while typing instead. See GLOSSARY.md.
    for key in ("num_taps", "max_upcoming_previews", "upcoming_interval_seconds",
                "max_archive_age_days", "max_archive_storage_mb",
                "photo_removed_retention_days", "page_size",
                "rotation_seconds", "venue_logo_height_vh",
                "upcoming_panel_multiple", "album_every_turns",
                "album_photos_per_visit", "album_seconds_per_photo"):
        lo, hi = SETTINGS_BOUNDS[key]
        merged[key] = _coerce_int(merged[key], lo, hi, DEFAULT_CONFIG[key])

    merged["photo_format"] = _coerce_choice(merged["photo_format"], PHOTO_FORMATS, FORMAT_WEBP)
    merged["photo_size"] = _coerce_choice(merged["photo_size"], PHOTO_SIZES, SIZE_4K)
    merged["hide_vacant_taps"] = bool(merged["hide_vacant_taps"])
    merged["announcement_text"] = str(merged["announcement_text"] or "")
    # Credentials are stripped here rather than at the Admin route: a key pasted
    # with a trailing newline is just as likely to reach a hand-edited file, and
    # the whitespace would go out on every Brewfather request.
    merged["brewfather_user_id"] = str(merged["brewfather_user_id"] or "").strip()
    merged["brewfather_api_key"] = str(merged["brewfather_api_key"] or "").strip()
    merged["include_conditioning"] = bool(merged["include_conditioning"])
    merged["include_fermenting"] = bool(merged["include_fermenting"])
    merged["show_upcoming_previews"] = bool(merged["show_upcoming_previews"])
    merged["update_check_enabled"] = bool(merged["update_check_enabled"])
    merged["show_upcoming_status"] = bool(merged["show_upcoming_status"])
    merged["show_upcoming_subtitle"] = bool(merged["show_upcoming_subtitle"])
    merged["show_upcoming_abv"] = bool(merged["show_upcoming_abv"])
    merged["upcoming_rotate_occupied"] = bool(merged["upcoming_rotate_occupied"])
    merged["show_upcoming_deck_page"] = bool(merged["show_upcoming_deck_page"])
    merged["show_upcoming_panel"] = bool(merged["show_upcoming_panel"])
    # Unrecognised coerces to "overflow" rather than being rejected (CLAUDE.md):
    # a hand-edited config.json has no one to report an error to. "overflow" is
    # also the safer default to fall back to - it never shows a beer twice.
    scope = str(merged["upcoming_surface_scope"] or "").strip().lower()
    merged["upcoming_surface_scope"] = scope if scope in ("overflow", "all") else "overflow"
    # Truncate, never reject (CLAUDE.md): a hand-edited config.json has no one
    # to report an error to, the same reasoning the numeric bounds use above,
    # just for a string length instead of a number. An empty (or all-blank)
    # label falls back to the default rather than shipping a blank ribbon.
    label = str(merged["upcoming_label"] or "").strip()
    merged["upcoming_label"] = (label or DEFAULT_CONFIG["upcoming_label"])[:MAX_UPCOMING_LABEL_LEN]

    # Display options.
    merged["color_unit"] = "srm" if str(merged["color_unit"]).lower() == "srm" else "ebc"
    for flag in ("show_abv", "show_ibu", "show_color", "show_og", "show_fg",
                 "hide_abv_when_empty", "hide_ibu_when_empty", "hide_color_when_empty",
                 "hide_og_when_empty", "hide_fg_when_empty", "show_source_badge",
                 "show_conditioning_status"):
        merged[flag] = bool(merged[flag])

    # Theme + glassware.
    merged["theme"] = normalize_theme_name(merged["theme"])
    merged["theme_custom"] = coerce_custom_theme(merged["theme_custom"])
    merged["glass_type"] = normalize_glass(merged["glass_type"])

    # Card sizing. Each scale is coerced independently of its preset: the scales
    # are what the board actually sends, and a config hand-edited to an unknown
    # preset name should still render at whatever scale it asks for.
    for key in ("tap_image_scale", "tap_text_scale", "album_fade_seconds"):
        lo, hi = SETTINGS_BOUNDS[key]
        merged[key] = _coerce_float(merged[key], lo, hi, DEFAULT_CONFIG[key])
    # A config written before the axes were split has no preset key of its own
    # (the superseded `tap_size_preset` drove both, and is dropped by the merge
    # above because it is no longer in DEFAULT_CONFIG). Derive each picker from
    # the scale that survived clamping rather than translating the old three-way
    # preset: the axes did not mean the same thing, so the number is the only
    # honest source. A stored key is always trusted, including "custom".
    for key, presets, valid, scale_key in (
        ("tap_photo_preset", TAP_PHOTO_PRESETS, TAP_PHOTO_PRESET_KEYS, "tap_image_scale"),
        ("tap_text_preset", TAP_TEXT_PRESETS, TAP_TEXT_PRESET_KEYS, "tap_text_scale"),
    ):
        if key not in cfg:
            merged[key] = _preset_for_scale(presets, merged[scale_key])
            continue
        preset = str(merged[key] or "").strip().lower()
        merged[key] = preset if preset in valid else DEFAULT_CONFIG[key]

    # Pagination / carousel. The two numbers are clamped with the rest above.
    merged["paginate"] = bool(merged["paginate"])

    # The photo album. Unrecognised order coerces to Shuffle, never rejected.
    for flag in ("album_enabled", "album_all", "album_keep_ticker", "album_pair_portraits",
                 "album_three_up", "album_caption_date", "album_caption_album", "album_ken_burns"):
        merged[flag] = bool(merged[flag])
    order = str(merged["album_order"] or "").strip().lower()
    merged["album_order"] = order if order in ALBUM_ORDERS else ORDER_SHUFFLE
    merged["album_selection"] = _coerce_album_selection(merged["album_selection"])
    for key, allowed in (("album_mount_style", MOUNT_STYLES),
                         ("album_mount_color", MOUNT_COLORS), ("album_frame", FRAMES)):
        merged[key] = _coerce_choice(merged[key], allowed, DEFAULT_CONFIG[key])
    merged["album_mount_custom"] = (parse_hex_color(merged["album_mount_custom"])
                                    or DEFAULT_CONFIG["album_mount_custom"])

    merged["venue_logo"] = (str(merged["venue_logo"]) if merged["venue_logo"] else None)
    merged["profiles"] = _coerce_profiles(merged["profiles"])
    return merged


MAX_ALBUM_SELECTION = 200       # ticked albums kept (a venue has a handful)
MAX_ALBUM_NAME_LEN = 255        # a folder name's own limit


def _coerce_album_selection(value: Any) -> list[str]:
    """A ticked-albums list: strings only, trimmed, de-duplicated, order kept.

    Anything else coerces to no albums rather than being rejected. Whether a name
    still names a folder is not decided here: a stale name is skipped when the
    board is built, so a renamed folder never breaks a hand-edited config.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()[:MAX_ALBUM_NAME_LEN]
            if name and name not in out:
                out.append(name)
    return out[:MAX_ALBUM_SELECTION]


def _coerce_profile(values: Any) -> dict[str, Any]:
    """One profile's Customised settings, coerced exactly like the Default profile.

    Non-customisable keys are dropped. A one-unit group that is not wholly
    present is dropped whole (it coerces to followed). The surviving values go
    through `_coerce` itself, so every bound and normaliser is the Default
    profile's, with nothing restated here.
    """
    if not isinstance(values, dict):
        return {}
    kept: dict[str, Any] = {}
    for unit in CUSTOMISABLE_UNITS.values():
        if all(key in values for key in unit):
            kept.update({key: values[key] for key in unit})
    coerced = _coerce(kept)
    return {key: coerced[key] for key in kept}


def _coerce_profiles(profiles: Any) -> dict[str, dict[str, Any]]:
    """Valid names (lowercased) with coerced Customised settings; the rest dropped.

    Dropped rather than refused, like every other bound: a hand-edited
    config.json has nobody to report an error to and must still boot. Two keys
    that differ only in case collapse to one (the later wins).
    """
    if not isinstance(profiles, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_name, values in profiles.items():
        name = normalize_profile_name(raw_name)
        if name is None:
            log.warning("dropping Display profile with invalid name %r",
                        str(raw_name)[:_MAX_RECORDED_NAME_LEN])
            continue
        out[name] = _coerce_profile(values)
        ua = normalize_user_agent_match(values.get(PROFILE_UA_KEY)
                                        if isinstance(values, dict) else None)
        if ua:
            out[name][PROFILE_UA_KEY] = ua
    return out


# Each Brewfather credential and the environment variable that can manage it.
# Used twice below: to resolve the effective credential, and to make sure a
# credential the environment owns is never written to disk.
_CREDENTIAL_ENV_VARS: dict[str, str] = {
    "brewfather_user_id": "BREWFATHER_USER_ID",
    "brewfather_api_key": "BREWFATHER_API_KEY",
}


def _drop_env_managed_credentials(changes: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials the environment owns out of a pending write.

    Keeping the API key off disk is the whole point of the env vars, so the rule
    lives at the write seam rather than in the Admin route that happens to be
    the only caller submitting credentials today. Any future writer inherits it
    instead of having to remember it. Dropping the key leaves whatever is
    already on disk alone, which is what the Admin form's read-only "managed via
    environment" field means when it posts an empty value back.
    """
    kept = dict(changes)
    for key, env_var in _CREDENTIAL_ENV_VARS.items():
        if key in kept and os.environ.get(env_var, "").strip():
            kept.pop(key)
    return kept


def brewfather_credentials() -> dict[str, Any]:
    """Resolve effective Brewfather credentials, env taking precedence.

    BREWFATHER_USER_ID / BREWFATHER_API_KEY env vars override the values in
    config.json so the API key need not be persisted to disk. Each field is
    resolved independently, and the *_from_env flags let the admin UI show which
    are locked to the environment.
    """
    cfg = load_config()
    env_user = os.environ.get(_CREDENTIAL_ENV_VARS["brewfather_user_id"], "").strip()
    env_key = os.environ.get(_CREDENTIAL_ENV_VARS["brewfather_api_key"], "").strip()
    return {
        "user_id": env_user or cfg.get("brewfather_user_id", "").strip(),
        "api_key": env_key or cfg.get("brewfather_api_key", "").strip(),
        "user_from_env": bool(env_user),
        "key_from_env": bool(env_key),
    }


class ConfigUnreadable(RuntimeError):
    """config.json is present but could not be read/parsed (after retries).

    Raised so a read-modify-write (update_config) can REFUSE to overwrite an
    existing-but-momentarily-unreadable config with defaults - which would
    silently wipe the operator's settings. This matters in practice: a bind
    mount on Docker Desktop / Windows can fail or briefly mis-report a read,
    and the sync job writes config (sync status) every cycle.
    """


_READ_RETRIES = 5


def read_raw_config() -> dict[str, Any] | None:
    """Return config.json exactly as it sits on disk, uncoerced.

    `_coerce` drops every key that is not in DEFAULT_CONFIG, so a caller that
    needs to see a key the schema no longer knows about - the one-time Status
    migration in `status_store` is the only one - has to read past it. Absence
    and unreadability are reported on exactly the same terms as the coerced
    read: None for a genuine first run, ConfigUnreadable otherwise.
    """
    ensure_dirs()
    last_exc: Exception | None = None
    for attempt in range(_READ_RETRIES):
        try:
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            cfg = json.loads(raw)
            if not isinstance(cfg, dict):
                raise ValueError("config.json is not a JSON object")
            return cfg
        except FileNotFoundError as exc:
            last_exc = exc
            if attempt == _READ_RETRIES - 1:
                return None  # consistently missing -> genuine first run
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt == _READ_RETRIES - 1:
                raise ConfigUnreadable(f"{CONFIG_PATH}: {exc}") from exc
        time.sleep(0.05 * (attempt + 1))
    raise ConfigUnreadable(f"{CONFIG_PATH}: {last_exc}")


def _read_existing_config() -> dict[str, Any] | None:
    """Return the coerced config, None if it is genuinely absent (first run).

    Retries to ride out a transient FS error (or a cold bind mount that briefly
    reports the file missing). Only concludes "absent" when the file is *still*
    not found after every attempt; any other persistent error raises
    ConfigUnreadable rather than masquerading as a first run.
    """
    raw = read_raw_config()
    return None if raw is None else _coerce(raw)


def prune_unknown_keys() -> bool:
    """Rewrite config.json without keys the schema no longer knows. Returns True if it changed.

    Every write already drops unknown keys, because `save_config` coerces. This
    exists so the Status migration can *finish* deliberately rather than waiting
    for the operator's next Save to tidy up as a side effect. It rewrites from
    the raw file it just read, so no default can reach disk in place of a real
    value.
    """
    raw = read_raw_config()            # raises ConfigUnreadable on a bad read
    if raw is None:
        return False
    unknown = [k for k in raw if k not in DEFAULT_CONFIG]
    if not unknown:
        return False
    save_config(raw)                   # _coerce drops them on the way out
    log.info("dropped %d key(s) no longer in the config schema: %s", len(unknown), unknown)
    return True


def load_config() -> dict[str, Any]:
    """Load config for read/display paths. Never raises; bootstraps on first run.

    On a genuine first run the defaults are written once. If the file exists but
    is transiently unreadable, return in-memory defaults for THIS read only and
    do NOT persist them, so a glitch can never wipe the saved file.
    """
    try:
        cfg = _read_existing_config()
    except ConfigUnreadable as exc:
        log.error("%s; using in-memory defaults for this read (not persisting)", exc)
        return dict(DEFAULT_CONFIG)
    if cfg is None:
        log.info("config.json missing; writing first-run default")
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config atomically. Unknown keys are dropped via _coerce."""
    ensure_dirs()
    clean = _coerce(cfg)
    atomic_write_text(CONFIG_PATH, json.dumps(clean, indent=2, ensure_ascii=False))
    _invalidate_photo_pipeline()


def update_config(**changes: Any) -> dict[str, Any]:
    """Read-modify-write helper for admin saves and sync-status updates.

    Refuses to write when the existing config can't be read (raises
    ConfigUnreadable), so a transient read failure can never clobber the
    operator's saved settings with defaults.

    Every bound is applied here on the way out, by `_coerce`, and out-of-range
    values are clamped rather than refused - so callers must read the returned
    dict rather than assume what they passed is what was saved.
    """
    cfg = _read_existing_config()      # raises ConfigUnreadable on a bad read
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)     # genuine first run
    cfg.update(_drop_env_managed_credentials(changes))
    clean = _coerce(cfg)               # normalise/clamp before persisting...
    save_config(clean)
    return clean                       # ...and return exactly what was saved


def apply_settings(**fields: Any) -> dict[str, Any]:
    """Persist one operator Settings submission; return the Settings as saved.

    The domain operation behind the Admin's Save button. The route's whole job
    is to parse the form and hand the values here, so what a save *means* is not
    spread across an HTTP handler: a named card-sizing preset owns its number
    (whatever the sliders posted is discarded, so the stored Settings can never
    say "small" beside Default's scale), and everything else goes to
    `update_config`, which clamps every bound, drops env-managed credentials and
    writes atomically.

    Deliberately validates nothing itself. An out-of-range value is clamped; the
    Admin form's inputs are what stop an operator entering one. See GLOSSARY.md.

    One exception to "validates nothing": `/data/upcoming/` is cleared
    immediately, right here at the write seam, when a Save leaves no profile
    (the Default included) with "show upcoming beers" on, rather than waiting
    for the next sync's own convergence check (`brewfather.run_sync` clears it
    too, for a hand-edited config.json - see docs/adr/0006). It is the only
    Setting that deletes files, which is why the admin's checkbox carries its
    own warning rather than this being a surprise.
    """
    was_on = upcoming_wanted(load_config())
    fields = dict(fields)
    # The two axes resolve independently - picking a photo preset must not move
    # the text scale - and each is resolved only when the caller submitted it.
    if "tap_photo_preset" in fields:
        fields["tap_photo_preset"], fields["tap_image_scale"] = resolve_tap_photo_preset(
            fields["tap_photo_preset"], fields.get("tap_image_scale"))
    if "tap_text_preset" in fields:
        fields["tap_text_preset"], fields["tap_text_scale"] = resolve_tap_text_preset(
            fields["tap_text_preset"], fields.get("tap_text_scale"))
    saved = update_config(**fields)
    if was_on and not upcoming_wanted(saved):
        with JOB_LOCK:
            upcoming_store.clear()
    return saved
