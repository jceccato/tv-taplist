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
from typing import Any

from .atomic import atomic_write_text
from .paths import CONFIG_PATH, ensure_dirs

log = logging.getLogger("taplist.config")

DEFAULT_CONFIG: dict[str, Any] = {
    "brewfather_user_id": "",
    "brewfather_api_key": "",
    "num_taps": 0,
    "hide_vacant_taps": False,
    "announcement_text": "",
    "max_archive_age_days": 180,
    "max_archive_storage_mb": 2048,
    # Display options.
    "color_unit": "ebc",            # "ebc" or "srm" — colour stat display unit
    "show_abv": True,               # global show/hide for each stat
    "show_ibu": True,
    "show_color": True,
    "hide_abv_when_empty": True,    # when shown, hide per-beer if value missing
    "hide_ibu_when_empty": True,
    "hide_color_when_empty": True,
    # Optional venue/company logo at the top of the display.
    "venue_logo": None,             # filename under /data (e.g. venue_logo.png) or null
    "venue_logo_height_vh": 0,      # 0..33 (% of viewport height; 0 hides the header)
    # Status fields, updated by the sync job so an unattended box is debuggable.
    "last_sync_success": None,   # ISO8601 string of last *successful* sync
    "last_sync_error": None,     # human-readable last error, or null
    "last_sync_attempt": None,   # ISO8601 of last attempt (success or fail)
}

# Cap the venue logo at a third of the screen height (per the design).
MAX_VENUE_LOGO_VH = 33


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted config over defaults and coerce types defensively."""
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})

    # Type coercion guards against hand-edited config files.
    try:
        merged["num_taps"] = max(0, int(merged["num_taps"]))
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

    # Display options.
    merged["color_unit"] = "srm" if str(merged["color_unit"]).lower() == "srm" else "ebc"
    for flag in ("show_abv", "show_ibu", "show_color",
                 "hide_abv_when_empty", "hide_ibu_when_empty", "hide_color_when_empty"):
        merged[flag] = bool(merged[flag])
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


def load_config() -> dict[str, Any]:
    """Load config.json, creating a sensible default on first run."""
    ensure_dirs()
    if not CONFIG_PATH.exists():
        log.info("config.json missing; writing first-run default")
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            raise ValueError("config.json is not a JSON object")
        return _coerce(cfg)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Never crash the appliance on a corrupt config; fall back to defaults
        # but do NOT overwrite the file so the operator can recover it.
        log.error("config.json unreadable (%s); using in-memory defaults", exc)
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config atomically. Unknown keys are dropped via _coerce."""
    ensure_dirs()
    clean = _coerce(cfg)
    atomic_write_text(CONFIG_PATH, json.dumps(clean, indent=2, ensure_ascii=False))


def update_config(**changes: Any) -> dict[str, Any]:
    """Read-modify-write helper used by admin saves and the sync status update."""
    cfg = load_config()
    cfg.update(changes)
    save_config(cfg)
    return cfg
