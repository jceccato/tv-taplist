"""config.json load/save with first-run bootstrap and atomic writes.

config.json holds all operator settings plus sync status. It is the single
source of truth for credentials, tap count, display toggles, and cleanup limits.
Secrets (the Brewfather key) live here in plaintext; this is a documented,
conscious choice for the appliance scope (see README).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .atomic import atomic_write_text
from .beer_glass import normalize_glass
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
    "glass_type": "default",        # default glassware for the no-photo placeholder
    # Card sizing. The preset is remembered only so the admin can re-open the
    # picker where the operator left it; the two scales are what actually reach
    # the board, so a preset never needs re-resolving at render time.
    "tap_size_preset": "normal",    # "small" | "normal" | "large" | "custom"
    "tap_image_scale": 1.0,         # multiplies the beer photo's max height
    "tap_text_scale": 1.0,          # multiplies the preferred card font sizes
    # Pagination / carousel.
    "paginate": False,              # when on, show `page_size` taps per page
    "page_size": 6,                 # taps per page (1..8) when paginating
    "rotation_seconds": 30,         # seconds each page is shown
    # Optional venue/company logo at the top of the display.
    "venue_logo": None,             # filename under /data (e.g. venue_logo.png) or null
    "venue_logo_height_vh": 0,      # 0..33 (% of viewport height; 0 hides the header)
    # Update checker — polls GitHub once per day for new releases.
    "update_check_enabled": True,     # operator can disable for air-gapped deploys
    "update_last_check": None,        # ISO8601 of last check
    "update_latest_version": None,    # e.g. "v1.2.3" or "unreleased"
    "update_latest_url": None,        # release HTML URL
    # Status fields, updated by the sync job so an unattended box is debuggable.
    "last_sync_success": None,   # ISO8601 string of last *successful* sync
    "last_sync_error": None,     # human-readable last error, or null
    "last_sync_attempt": None,   # ISO8601 of last attempt (success or fail)
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

# Card sizing bounds. Deliberately wide: the point of the feature is that we
# cannot guess the operator's screen (a Fire Stick on a small TV needs different
# numbers from a 4K panel), so the clamp only rules out values that would render
# the board unusable. The CSS keeps its own px floor/ceiling on every font size,
# so even the extremes of these ranges stay legible - see display.css.
MIN_TAP_IMAGE_SCALE = 0.25
MAX_TAP_IMAGE_SCALE = 3.0
MIN_TAP_TEXT_SCALE = 0.5
MAX_TAP_TEXT_SCALE = 2.0

# The fixed scales behind each named preset. "custom" is absent on purpose: it
# means "leave the operator's own numbers alone", so it has nothing to resolve.
# This map is the single definition - the admin form posts a preset key and the
# server resolves it, so the browser can never disagree with the stored config.
TAP_SIZE_PRESETS: dict[str, tuple[float, float]] = {
    # preset -> (tap_image_scale, tap_text_scale)
    "small": (0.6, 0.75),
    "normal": (1.0, 1.0),
    "large": (1.5, 1.4),
}
TAP_SIZE_PRESET_KEYS = (*TAP_SIZE_PRESETS.keys(), "custom")


def _coerce_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, lo: float, hi: float, default: float) -> float:
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
    return max(lo, min(hi, f))


def resolve_tap_size_preset(preset: str, image_scale: Any, text_scale: Any) -> tuple[str, float, float]:
    """Resolve a card-sizing preset to the scales that get stored.

    A named preset owns its numbers: whatever the sliders posted is discarded,
    so the config can never hold "small" next to Large's scales. "custom" (and
    any unrecognised key, which normalises to Custom rather than silently
    rewriting the operator's numbers) keeps the submitted values.
    """
    key = str(preset or "").strip().lower()
    fixed = TAP_SIZE_PRESETS.get(key)
    if fixed is not None:
        return key, fixed[0], fixed[1]
    return "custom", image_scale, text_scale


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted config over defaults and coerce types defensively."""
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})

    # Type coercion guards against hand-edited config files.
    try:
        merged["num_taps"] = max(0, min(MAX_NUM_TAPS, int(merged["num_taps"])))
    except (TypeError, ValueError):
        merged["num_taps"] = 0
    try:
        merged["max_archive_age_days"] = max(0, int(merged["max_archive_age_days"]))
    except (TypeError, ValueError):
        merged["max_archive_age_days"] = DEFAULT_CONFIG["max_archive_age_days"]
    try:
        merged["max_archive_storage_mb"] = max(0, int(merged["max_archive_storage_mb"]))
    except (TypeError, ValueError):
        merged["max_archive_storage_mb"] = DEFAULT_CONFIG["max_archive_storage_mb"]

    merged["hide_vacant_taps"] = bool(merged["hide_vacant_taps"])
    merged["announcement_text"] = str(merged["announcement_text"] or "")
    merged["brewfather_user_id"] = str(merged["brewfather_user_id"] or "")
    merged["brewfather_api_key"] = str(merged["brewfather_api_key"] or "")
    merged["include_conditioning"] = bool(merged["include_conditioning"])
    merged["include_fermenting"] = bool(merged["include_fermenting"])

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

    # Card sizing. The two scales are coerced independently of the preset: they
    # are what the board actually sends, and a config hand-edited to an unknown
    # preset name should still render at whatever scales it asks for.
    preset = str(merged["tap_size_preset"] or "").strip().lower()
    merged["tap_size_preset"] = (
        preset if preset in TAP_SIZE_PRESET_KEYS else DEFAULT_CONFIG["tap_size_preset"])
    merged["tap_image_scale"] = _coerce_float(
        merged["tap_image_scale"], MIN_TAP_IMAGE_SCALE, MAX_TAP_IMAGE_SCALE,
        DEFAULT_CONFIG["tap_image_scale"])
    merged["tap_text_scale"] = _coerce_float(
        merged["tap_text_scale"], MIN_TAP_TEXT_SCALE, MAX_TAP_TEXT_SCALE,
        DEFAULT_CONFIG["tap_text_scale"])

    # Pagination / carousel.
    merged["paginate"] = bool(merged["paginate"])
    merged["page_size"] = _coerce_int(merged["page_size"], 1, MAX_PAGE_SIZE, DEFAULT_CONFIG["page_size"])
    merged["rotation_seconds"] = _coerce_int(
        merged["rotation_seconds"], MIN_ROTATION_SECONDS, MAX_ROTATION_SECONDS,
        DEFAULT_CONFIG["rotation_seconds"])

    merged["venue_logo"] = (str(merged["venue_logo"]) if merged["venue_logo"] else None)
    try:
        merged["venue_logo_height_vh"] = max(0, min(MAX_VENUE_LOGO_VH, int(merged["venue_logo_height_vh"])))
    except (TypeError, ValueError):
        merged["venue_logo_height_vh"] = 0
    return merged


def brewfather_credentials() -> dict[str, Any]:
    """Resolve effective Brewfather credentials, env taking precedence.

    BREWFATHER_USER_ID / BREWFATHER_API_KEY env vars override the values in
    config.json so the API key need not be persisted to disk. Each field is
    resolved independently, and the *_from_env flags let the admin UI show which
    are locked to the environment.
    """
    cfg = load_config()
    env_user = os.environ.get("BREWFATHER_USER_ID", "").strip()
    env_key = os.environ.get("BREWFATHER_API_KEY", "").strip()
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
    """
    cfg = _read_existing_config()      # raises ConfigUnreadable on a bad read
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)     # genuine first run
    cfg.update(changes)
    clean = _coerce(cfg)               # normalise/clamp before persisting...
    save_config(clean)
    return clean                       # ...and return exactly what was saved
