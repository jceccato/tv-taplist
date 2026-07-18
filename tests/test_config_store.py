"""Config load/save safety - especially the "don't clobber on a flaky read" guard.

A transient read failure (e.g. a Docker Desktop bind mount on Windows briefly
failing a read) must never cause update_config to overwrite the operator's saved
settings with defaults.
"""
import json
from pathlib import Path

import pytest

from app import config_store


def _read_raw() -> dict:
    """Read config.json bypassing Path.read_text (which tests may monkeypatch)."""
    with open(config_store.CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _patch_unreadable(monkeypatch):
    """Make Path.read_text raise for config.json only (simulate a flaky mount)."""
    orig = Path.read_text

    def boom(self, *a, **k):
        if self == config_store.CONFIG_PATH:
            raise OSError("simulated flaky read")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)


def test_update_config_refuses_to_clobber_on_unreadable_file(monkeypatch):
    config_store.save_config(
        {**config_store.DEFAULT_CONFIG, "num_taps": 8, "announcement_text": "Keep me"}
    )
    _patch_unreadable(monkeypatch)

    with pytest.raises(config_store.ConfigUnreadable):
        config_store.update_config(last_sync_attempt="2026-01-01T00:00:00")

    # The saved settings must survive untouched.
    on_disk = _read_raw()
    assert on_disk["num_taps"] == 8
    assert on_disk["announcement_text"] == "Keep me"


def test_load_config_returns_defaults_without_persisting_on_unreadable(monkeypatch):
    config_store.save_config({**config_store.DEFAULT_CONFIG, "num_taps": 8})
    _patch_unreadable(monkeypatch)

    cfg = config_store.load_config()
    assert cfg["num_taps"] == 0  # in-memory default for this read only

    # File on disk is NOT overwritten with the defaults.
    assert _read_raw()["num_taps"] == 8


def test_update_config_bootstraps_when_genuinely_missing():
    config_store.CONFIG_PATH.unlink()
    cfg = config_store.update_config(num_taps=3)
    assert cfg["num_taps"] == 3
    assert config_store.CONFIG_PATH.exists()
    assert _read_raw()["num_taps"] == 3


def test_update_config_preserves_unrelated_fields():
    config_store.save_config(
        {**config_store.DEFAULT_CONFIG, "num_taps": 6, "venue_logo": "venue_logo.png"}
    )
    config_store.update_config(last_sync_success="2026-01-01T00:00:00")
    on_disk = _read_raw()
    assert on_disk["num_taps"] == 6
    assert on_disk["venue_logo"] == "venue_logo.png"
    assert on_disk["last_sync_success"] == "2026-01-01T00:00:00"


def test_coerce_clamps_pagination_and_normalises_theme_glass():
    cfg = config_store.update_config(
        page_size=99, rotation_seconds=1, theme="bogus", glass_type="notaglass")
    assert cfg["page_size"] == config_store.MAX_PAGE_SIZE        # clamped to the grid max
    assert cfg["rotation_seconds"] == config_store.MIN_ROTATION_SECONDS
    assert cfg["theme"] == "default"                            # unknown -> default
    assert cfg["glass_type"] == "default"


def test_coerce_custom_theme_fills_invalid_colours():
    cfg = config_store.update_config(theme="custom", theme_custom={"bg": "not-a-hex", "text": "#abcdef"})
    # Invalid colours fall back to the default palette; valid ones are kept.
    assert cfg["theme_custom"]["bg"] == config_store.DEFAULT_THEME["bg"]
    assert cfg["theme_custom"]["text"] == "#abcdef"


def test_include_conditioning_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["include_conditioning"] is False
    cfg = config_store.update_config(include_conditioning="yes")  # truthy -> bool True
    assert cfg["include_conditioning"] is True
