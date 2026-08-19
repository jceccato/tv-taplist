"""status.json load/save - machine-written runtime Status, kept apart from Settings.

**Status** (see `CONTEXT.md`) is everything the scheduled jobs write about
themselves: when the last sync ran, whether it failed, and what the daily update
check found. **Settings** is what the operator deliberately configures, and it
lives in `config.json` next to the Brewfather API key.

The two used to share one file, which meant the sync job rewrote a file holding
a credential every cycle. They are separated here because their properties are
opposites:

* Settings is irreplaceable - a lost `config.json` costs the operator their key
  and every display choice - and is written only when a human presses Save.
* Status is **disposable**. Every field regenerates on the next cycle: the sync
  job rewrites all three `last_sync_*` fields each run, and the daily update
  check rewrites the three `update_*` ones. Losing the file costs at most a
  stale-looking admin panel until the next job.

That difference drives the read policy below, which is deliberately the reverse
of the config store's. See `docs/adr/0002-config-status-separation.md`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import config_store
from .atomic import atomic_write_text
from .paths import STATUS_PATH, ensure_dirs

log = logging.getLogger("taplist.status")

# The schema, exactly as DEFAULT_CONFIG is the schema for Settings. Every field
# is "unknown yet" (None) until a job writes it, and every field is written by
# exactly one job.
DEFAULT_STATUS: dict[str, Any] = {
    # Written by the Brewfather sync job (app/brewfather.py).
    "last_sync_success": None,   # ISO8601 of the last *successful* sync
    "last_sync_error": None,     # human-readable last error, or null
    "last_sync_attempt": None,   # ISO8601 of the last attempt (success or fail)
    # Written by the daily update check (app/update_check.py).
    "update_last_check": None,      # ISO8601 of the last check
    "update_latest_version": None,  # e.g. "v1.2.3" or "unreleased"
    "update_latest_url": None,      # release HTML URL
}

# Ordered tuple of the field names, for the one-time migration out of config.json
# and for tests that want to assert the split is complete.
STATUS_KEYS: tuple[str, ...] = tuple(DEFAULT_STATUS)


def _coerce(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted Status over defaults, coercing defensively.

    Same shape of guard as the config store's `_coerce`: unknown keys are
    dropped and every value is forced to the type the readers expect. Status is
    machine-written, so junk here means a hand edit or a truncated file, and
    both should degrade to "unknown" rather than reaching a Jinja template.
    """
    merged = dict(DEFAULT_STATUS)
    merged.update({k: v for k, v in raw.items() if k in DEFAULT_STATUS})
    for key in STATUS_KEYS:
        value = merged[key]
        # Every field is an optional string. Empty string means "unset" too, so
        # a hand-edited '""' does not render as a blank timestamp.
        merged[key] = str(value) if value not in (None, "") else None
    return merged


def load_status() -> dict[str, Any]:
    """Return the current Status. Never raises, and never writes.

    A missing file is the normal state of a box that has not run a job yet, so
    it is not a first-run bootstrap: the file appears when the first job records
    something. Keeping this read pure also means rendering the admin page can
    never create a file.

    **An unreadable file yields defaults, and that is deliberate** - the exact
    opposite of `config_store.load_config`, which refuses to let a flaky read
    turn into a write. There is nothing here worth defending: no secret, no
    operator input, and every field is rewritten by the next job. Reporting
    "unknown" for one admin page load is the correct degradation.
    """
    try:
        raw = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("status.json is not a JSON object")
        return _coerce(raw)
    except FileNotFoundError:
        return dict(DEFAULT_STATUS)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("%s unreadable (%s); reporting unknown Status", STATUS_PATH, exc)
        return dict(DEFAULT_STATUS)


def save_status(status: dict[str, Any]) -> None:
    """Persist Status atomically. Unknown keys are dropped via _coerce."""
    ensure_dirs()
    clean = _coerce(status)
    atomic_write_text(STATUS_PATH, json.dumps(clean, indent=2, ensure_ascii=False))


def update_status(**changes: Any) -> dict[str, Any]:
    """Read-modify-write helper for the scheduled jobs. Returns what was saved.

    Callers hold `JOB_LOCK` (sync and the update check both do), so the
    read-modify-write is not racing another writer inside this process.

    Unlike `config_store.update_config` this does **not** refuse to write when
    the existing file will not read. A rebuilt-from-defaults `status.json` loses
    only the fields the other job owns, and each of those is rewritten on its
    own next cycle; refusing instead would leave a box that syncs perfectly well
    permanently reporting "never synced", which is the failure this whole ticket
    exists to avoid.
    """
    status = load_status()      # tolerant by design; see the docstring above
    status.update(changes)
    clean = _coerce(status)
    save_status(clean)
    return clean


def migrate_legacy_status() -> bool:
    """Carry Status out of a pre-split `config.json`. Idempotent; never raises.

    Returns True when something was carried over.

    An upgraded box has the six fields sitting inside `config.json`; without
    this it would report "never synced" until the next cycle. The ordering is
    chosen so that an interruption at any point loses nothing:

    1. Read `config.json` **raw**. Coercion drops keys that are no longer in
       `DEFAULT_CONFIG`, so the legacy values are only visible before it.
       An unreadable config aborts the migration entirely - the next start
       retries, and nothing has been written yet.
    2. Bail out if `status.json` already exists. It is then the authority: the
       migration either already ran, or a job has since written real Status that
       must not be rolled back to the values frozen in the old config.
    3. Write `status.json` first, atomically. Until it lands, `config.json`
       still holds the only copy.
    4. Only then rewrite `config.json` without the legacy keys.

    A crash between 3 and 4 leaves the values in both files, which is harmless:
    the config store drops unknown keys on every read, so nothing can read the
    stale copy, and the next start finishes step 4.
    """
    try:
        raw = config_store.read_raw_config()
    except config_store.ConfigUnreadable as exc:
        log.warning("Status migration skipped, config unreadable (%s); will retry", exc)
        return False
    if raw is None:
        return False  # genuine first run: no legacy Status to carry

    legacy = {k: raw[k] for k in STATUS_KEYS if k in raw}
    if not legacy:
        return False  # already migrated, or written by a post-split version

    if STATUS_PATH.exists():
        # Step 2. Do not resurrect the old values over live ones. Worst case
        # (config was unreadable at the start that should have migrated, and a
        # job wrote status.json first) the panel is thin for one cycle.
        log.info("status.json already present; dropping legacy Status keys from config")
        _prune_legacy_keys()
        return False

    save_status(legacy)                       # step 3
    log.info("migrated %d Status field(s) from config.json to %s", len(legacy), STATUS_PATH)
    _prune_legacy_keys()                      # step 4
    return True


def _prune_legacy_keys() -> None:
    """Rewrite config.json without the carried keys, tolerating a failure.

    A failure here is recoverable on the next start (the keys are inert once
    nothing reads them), so it must not take down startup.
    """
    try:
        config_store.prune_unknown_keys()
    except (OSError, config_store.ConfigUnreadable) as exc:
        log.warning("could not drop legacy Status keys from config (%s); will retry", exc)
