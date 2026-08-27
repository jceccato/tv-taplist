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
import time
from typing import Any

from . import upcoming_store
from .atomic import JOB_LOCK, atomic_write_text
from .beer_glass import DEFAULT_GLASS, normalize_glass
from .paths import CONFIG_PATH, ensure_dirs
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
    # CLAUDE.md's "resolved answers, not inputs" and CONTEXT.md's Visibility.
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
    # cross-fade cannot reach. Each carries its own on/off toggle and its own
    # multiplier of upcoming_interval_seconds - the multiplier is not
    # decoration, it is what stops a surface stealing so many ticks that a
    # beer late in the cross-fade's list never gets a turn (CLAUDE.md).
    "show_upcoming_deck_page": False,
    "upcoming_deck_multiple": 3,
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
}

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
# in only one of the two. See CONTEXT.md, Known hazards.
SETTINGS_BOUNDS: dict[str, tuple[float, float | None]] = {
    "num_taps": (0, MAX_NUM_TAPS),
    "max_upcoming_previews": (0, MAX_UPCOMING_PREVIEWS),
    # 1x to 6x the shared cadence (issue #41). The floor of 1 keeps a surface
    # from being configured to steal every single tick, which the cross-fade's
    # one-teaser-per-tick cycling could starve outright; 6 is generous enough
    # that "practically never" is already reachable well before the ceiling.
    "upcoming_deck_multiple": (1, 6),
    # 300, not rotation_seconds' 600 (CLAUDE.md): five minutes is already the
    # point where a customer there for one drink may never see a teaser, and
    # an operator reaching past it wants the feature off rather than slowed.
    # The 5-second floor sits well below the derived hold's 1.5s floor, so no
    # value in this range can produce a teaser that merely flashes.
    "upcoming_interval_seconds": (5, 300),
    "max_archive_age_days": (0, None),
    "max_archive_storage_mb": (0, None),
    "page_size": (1, MAX_PAGE_SIZE),
    "rotation_seconds": (MIN_ROTATION_SECONDS, MAX_ROTATION_SECONDS),
    "venue_logo_height_vh": (0, MAX_VENUE_LOGO_VH),
    "tap_image_scale": (MIN_TAP_IMAGE_SCALE, MAX_TAP_IMAGE_SCALE),
    "tap_text_scale": (MIN_TAP_TEXT_SCALE, MAX_TAP_TEXT_SCALE),
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


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted config over defaults and coerce types defensively."""
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})

    # Every numeric Settings bound is applied here, from SETTINGS_BOUNDS, and
    # nowhere else. Out of range is clamped rather than refused: a hand-edited
    # config.json (ADR-0001 makes it editable) has nobody to report an error to
    # and must never stop the box booting, so clamping is the only safe
    # disposition. The Admin form carries the same numbers as input attributes
    # so an operator is stopped while typing instead. See CONTEXT.md.
    for key in ("num_taps", "max_upcoming_previews", "upcoming_interval_seconds",
                "max_archive_age_days", "max_archive_storage_mb", "page_size",
                "rotation_seconds", "venue_logo_height_vh", "upcoming_deck_multiple"):
        lo, hi = SETTINGS_BOUNDS[key]
        merged[key] = _coerce_int(merged[key], lo, hi, DEFAULT_CONFIG[key])

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
                 "hide_og_when_empty", "hide_fg_when_empty", "show_source_badge"):
        merged[flag] = bool(merged[flag])

    # Theme + glassware.
    merged["theme"] = normalize_theme_name(merged["theme"])
    merged["theme_custom"] = coerce_custom_theme(merged["theme_custom"])
    merged["glass_type"] = normalize_glass(merged["glass_type"])

    # Card sizing. Each scale is coerced independently of its preset: the scales
    # are what the board actually sends, and a config hand-edited to an unknown
    # preset name should still render at whatever scale it asks for.
    for key in ("tap_image_scale", "tap_text_scale"):
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

    merged["venue_logo"] = (str(merged["venue_logo"]) if merged["venue_logo"] else None)
    return merged


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
    Admin form's inputs are what stop an operator entering one. See CONTEXT.md.

    One exception to "validates nothing": `show_upcoming_previews` going from
    on to off clears `/data/upcoming/` immediately, right here at the write
    seam, rather than waiting for the next sync's own convergence check
    (`brewfather.run_sync` clears it too, for a hand-edited config.json - see
    docs/adr/0006). It is the only Setting that deletes files, which is why
    the admin's checkbox carries its own warning rather than this being a
    surprise.
    """
    was_on = bool(load_config().get("show_upcoming_previews", False))
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
    if was_on and not saved.get("show_upcoming_previews", False):
        with JOB_LOCK:
            upcoming_store.clear()
    return saved
