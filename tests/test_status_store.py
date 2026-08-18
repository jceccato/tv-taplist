"""Runtime Status storage: /data/status.json, and the split from Settings.

Status is disposable - every field regenerates on the next job cycle - so its
read policy is deliberately the opposite of the config store's: a bad read
degrades to "unknown" and a write always goes through, rather than refusing so
as to protect data that is not worth protecting. These tests pin both halves of
that asymmetry, and the one-time migration out of a pre-split config.json.
"""
import json
from pathlib import Path

from app import config_store, status_store
from app.paths import STATUS_PATH


def _write_raw_status(text: str) -> None:
    STATUS_PATH.write_text(text, encoding="utf-8")


def _read_raw_status() -> dict:
    with open(STATUS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _read_raw_config() -> dict:
    with open(config_store.CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---- the store ---------------------------------------------------------

def test_status_keys_are_not_settings():
    """The six Status fields must have left the Settings schema entirely."""
    assert status_store.STATUS_KEYS == (
        "last_sync_success", "last_sync_error", "last_sync_attempt",
        "update_last_check", "update_latest_version", "update_latest_url",
    )
    for key in status_store.STATUS_KEYS:
        assert key not in config_store.DEFAULT_CONFIG
    # update_check_enabled is operator intent, not runtime state: it stays in
    # Settings and must never appear here.
    assert config_store.DEFAULT_CONFIG["update_check_enabled"] is True
    assert "update_check_enabled" not in status_store.DEFAULT_STATUS


def test_fresh_install_reports_unknown_and_writes_nothing():
    assert not STATUS_PATH.exists()
    status = status_store.load_status()
    assert status == status_store.DEFAULT_STATUS
    assert status["last_sync_success"] is None
    # Reading Status must never create the file (rendering /admin is a read).
    assert not STATUS_PATH.exists()


def test_update_status_round_trips_and_merges():
    status_store.update_status(last_sync_attempt="2026-01-01T00:00:00")
    saved = status_store.update_status(last_sync_success="2026-01-01T00:00:01")
    assert saved["last_sync_attempt"] == "2026-01-01T00:00:00"
    assert saved["last_sync_success"] == "2026-01-01T00:00:01"
    assert status_store.load_status()["last_sync_attempt"] == "2026-01-01T00:00:00"


def test_update_status_can_clear_a_field():
    status_store.update_status(last_sync_error="boom")
    assert status_store.load_status()["last_sync_error"] == "boom"
    status_store.update_status(last_sync_error=None)
    assert status_store.load_status()["last_sync_error"] is None


def test_unknown_keys_are_dropped_and_values_coerced():
    saved = status_store.update_status(last_sync_error=123, brewfather_api_key="secret")
    assert saved["last_sync_error"] == "123"
    assert "brewfather_api_key" not in saved
    assert "brewfather_api_key" not in _read_raw_status()


def test_empty_string_reads_back_as_unset():
    _write_raw_status(json.dumps({"last_sync_success": ""}))
    assert status_store.load_status()["last_sync_success"] is None


def test_unreadable_status_degrades_to_unknown_rather_than_raising():
    _write_raw_status("{not json at all")
    assert status_store.load_status() == status_store.DEFAULT_STATUS


def test_write_still_lands_when_the_existing_file_is_unreadable():
    """The opposite policy to config: a bad read must not block a Status write.

    Refusing here would leave a box that syncs perfectly well reporting
    "never synced" forever, which is the failure this split exists to avoid.
    """
    _write_raw_status("{not json at all")
    saved = status_store.update_status(last_sync_success="2026-02-02T00:00:00")
    assert saved["last_sync_success"] == "2026-02-02T00:00:00"
    assert _read_raw_status()["last_sync_success"] == "2026-02-02T00:00:00"


# ---- Settings and Status do not clobber each other ---------------------

def test_settings_save_does_not_clobber_status():
    status_store.update_status(last_sync_success="2026-03-03T00:00:00",
                               update_latest_version="v9.9.9")
    config_store.update_config(num_taps=7, announcement_text="Keep me")
    status = status_store.load_status()
    assert status["last_sync_success"] == "2026-03-03T00:00:00"
    assert status["update_latest_version"] == "v9.9.9"


def test_status_write_does_not_clobber_settings():
    config_store.update_config(num_taps=7, brewfather_api_key="key-stays-put",
                               announcement_text="Keep me")
    status_store.update_status(last_sync_error="upstream 500")
    cfg = _read_raw_config()
    assert cfg["num_taps"] == 7
    assert cfg["brewfather_api_key"] == "key-stays-put"
    assert cfg["announcement_text"] == "Keep me"
    # ...and Status never leaks back into the credentials file.
    for key in status_store.STATUS_KEYS:
        assert key not in cfg


# ---- migration ---------------------------------------------------------

_LEGACY = {
    "last_sync_success": "2026-01-01T01:00:00",
    "last_sync_error": "old boom",
    "last_sync_attempt": "2026-01-01T02:00:00",
    "update_last_check": "2026-01-01T03:00:00",
    "update_latest_version": "v1.2.3",
    "update_latest_url": "https://example.invalid/releases/v1.2.3",
}


def _seed_legacy_config(**status_fields) -> None:
    """Write a pre-split config.json: real Settings plus legacy Status keys."""
    cfg = {**config_store.DEFAULT_CONFIG, "num_taps": 5,
           "brewfather_api_key": "key-stays-put", **status_fields}
    config_store.CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def test_migration_carries_all_six_fields_and_strips_them_from_config():
    _seed_legacy_config(**_LEGACY)
    assert status_store.migrate_legacy_status() is True

    assert status_store.load_status() == _LEGACY
    cfg = _read_raw_config()
    for key in status_store.STATUS_KEYS:
        assert key not in cfg
    # Settings survive the rewrite untouched - including the credential.
    assert cfg["num_taps"] == 5
    assert cfg["brewfather_api_key"] == "key-stays-put"


def test_migration_of_a_partial_legacy_config():
    """A config carrying only some of the six is normal on an older deployment."""
    _seed_legacy_config(last_sync_success="2026-01-01T01:00:00",
                        last_sync_attempt="2026-01-01T02:00:00")
    assert status_store.migrate_legacy_status() is True

    status = status_store.load_status()
    assert status["last_sync_success"] == "2026-01-01T01:00:00"
    assert status["last_sync_attempt"] == "2026-01-01T02:00:00"
    assert status["update_latest_version"] is None   # never set on that box
    assert "last_sync_success" not in _read_raw_config()


def test_migration_is_a_no_op_on_a_fresh_install():
    assert status_store.migrate_legacy_status() is False
    assert not STATUS_PATH.exists()


def test_migration_is_a_no_op_when_config_is_absent():
    config_store.CONFIG_PATH.unlink()
    assert status_store.migrate_legacy_status() is False
    assert not STATUS_PATH.exists()


def test_migration_is_idempotent():
    _seed_legacy_config(**_LEGACY)
    assert status_store.migrate_legacy_status() is True
    first = status_store.load_status()
    assert status_store.migrate_legacy_status() is False
    assert status_store.load_status() == first


def test_interrupted_migration_finishes_on_the_next_start():
    """Crash between "status.json written" and "config rewritten" loses nothing.

    Both files hold the values for a moment. The config copy is inert - the
    config store drops unknown keys on every read - and the next start prunes
    it.
    """
    _seed_legacy_config(**_LEGACY)
    status_store.save_status(_LEGACY)          # step 3 landed...
    # ...step 4 never ran, so config still carries the legacy keys.
    assert "last_sync_success" in _read_raw_config()

    assert status_store.migrate_legacy_status() is False   # nothing new carried
    assert status_store.load_status() == _LEGACY           # values intact
    assert "last_sync_success" not in _read_raw_config()   # and now pruned


def test_migration_does_not_roll_back_live_status():
    """An existing status.json is the authority; stale config values never win."""
    _seed_legacy_config(**_LEGACY)
    status_store.update_status(last_sync_success="2026-06-06T00:00:00",
                               last_sync_error=None)
    assert status_store.migrate_legacy_status() is False
    status = status_store.load_status()
    assert status["last_sync_success"] == "2026-06-06T00:00:00"
    assert status["last_sync_error"] is None


def test_migration_aborts_and_writes_nothing_when_config_is_unreadable(monkeypatch):
    _seed_legacy_config(**_LEGACY)
    orig = Path.read_text

    def boom(self, *a, **k):
        if self == config_store.CONFIG_PATH:
            raise OSError("simulated flaky read")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)
    assert status_store.migrate_legacy_status() is False
    assert not STATUS_PATH.exists()          # nothing half-written
    monkeypatch.undo()
    assert "last_sync_success" in _read_raw_config()   # config untouched
