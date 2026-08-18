"""Runtime Status storage: /data/status.json, and the split from Settings.

Status is disposable - every field regenerates on the next job cycle - so its
read policy is deliberately the opposite of the config store's: a bad read
degrades to "unknown" and a write always goes through, rather than refusing so
as to protect data that is not worth protecting. These tests pin both halves of
that asymmetry, and the one-time migration out of a pre-split config.json.
"""
import json

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

def test_status_schema_is_the_six_machine_written_fields():
    assert status_store.STATUS_KEYS == (
        "last_sync_success", "last_sync_error", "last_sync_attempt",
        "update_last_check", "update_latest_version", "update_latest_url",
    )
    # update_check_enabled is operator intent, not runtime state: it belongs to
    # Settings and must never appear here.
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
