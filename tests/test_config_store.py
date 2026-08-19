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
        config_store.update_config(rotation_seconds=45)

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
    config_store.update_config(rotation_seconds=45)
    on_disk = _read_raw()
    assert on_disk["num_taps"] == 6
    assert on_disk["venue_logo"] == "venue_logo.png"
    assert on_disk["rotation_seconds"] == 45


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


def test_include_fermenting_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["include_fermenting"] is False
    cfg = config_store.update_config(include_fermenting="yes")  # truthy -> bool True
    assert cfg["include_fermenting"] is True


def test_include_fermenting_absent_from_stored_config_reads_false():
    # A config written before the toggle existed has no such key; the merge over
    # DEFAULT_CONFIG must treat that as off rather than raising or going truthy.
    assert config_store._coerce({"num_taps": 4})["include_fermenting"] is False


# ---- card sizing ---------------------------------------------------------

def test_card_sizing_defaults_to_default_at_scale_one():
    assert config_store.DEFAULT_CONFIG["tap_photo_preset"] == "default"
    assert config_store.DEFAULT_CONFIG["tap_text_preset"] == "default"
    assert config_store.DEFAULT_CONFIG["tap_image_scale"] == 1.0
    assert config_store.DEFAULT_CONFIG["tap_text_scale"] == 1.0


def test_card_sizing_absent_from_an_existing_config_reads_as_default():
    # A config written before this feature existed has none of the keys; it must
    # keep rendering exactly as it did, at Default on both axes.
    config_store.save_config({"num_taps": 4})
    cfg = config_store.load_config()
    assert cfg["tap_photo_preset"] == "default"
    assert cfg["tap_text_preset"] == "default"
    assert cfg["tap_image_scale"] == 1.0
    assert cfg["tap_text_scale"] == 1.0


def test_legacy_size_preset_migrates_to_the_two_axes():
    # A config written while one preset drove both axes: the legacy key is
    # dropped, the photo scale is clamped into the new range, and each picker is
    # named from the scale that survived rather than from the old preset.
    config_store.save_config({
        "num_taps": 4, "tap_size_preset": "large",
        "tap_image_scale": 1.5, "tap_text_scale": 1.4,
    })
    cfg = config_store.load_config()
    assert "tap_size_preset" not in cfg
    assert cfg["tap_image_scale"] == 1.0          # 1.5 is structurally impossible
    assert cfg["tap_photo_preset"] == "default"   # ...which is exactly Default
    assert cfg["tap_text_scale"] == 1.4
    assert cfg["tap_text_preset"] == "large"


def test_legacy_scale_with_no_matching_preset_migrates_to_custom():
    config_store.save_config({"num_taps": 4, "tap_image_scale": 0.55})
    cfg = config_store.load_config()
    assert cfg["tap_image_scale"] == 0.55
    assert cfg["tap_photo_preset"] == "custom"


def test_coerce_clamps_card_scales_to_their_bounds():
    cfg = config_store.update_config(tap_image_scale=99, tap_text_scale=0.01)
    assert cfg["tap_image_scale"] == config_store.MAX_TAP_IMAGE_SCALE
    assert cfg["tap_text_scale"] == config_store.MIN_TAP_TEXT_SCALE


def test_photo_scale_cannot_exceed_one():
    # Above 1 the card has nothing left to give the photo, so the clamp is the
    # feature: a config asking for 1.5 lands at 1.0 rather than pretending.
    assert config_store.MAX_TAP_IMAGE_SCALE == 1.0
    assert config_store.update_config(tap_image_scale=1.5)["tap_image_scale"] == 1.0


def test_coerce_rejects_junk_and_nan_card_scales():
    # NaN survives float() and loses every comparison, so a plain clamp would
    # pass it straight through into a CSS custom property.
    cfg = config_store.update_config(tap_image_scale=float("nan"), tap_text_scale="wide")
    assert cfg["tap_image_scale"] == 1.0
    assert cfg["tap_text_scale"] == 1.0


def test_coerce_normalises_the_presets_but_leaves_the_scales_alone():
    cfg = config_store.update_config(
        tap_photo_preset="ENORMOUS", tap_text_preset="ENORMOUS",
        tap_image_scale=0.6, tap_text_scale=0.75)
    assert cfg["tap_photo_preset"] == "default"   # unknown preset -> the default
    assert cfg["tap_text_preset"] == "default"
    # The scales are what the board sends, so an unknown preset name must not
    # rewrite them out from under a hand-edited config.
    assert cfg["tap_image_scale"] == 0.6
    assert cfg["tap_text_scale"] == 0.75


def test_resolve_preset_overrides_the_submitted_scale():
    for preset, image in config_store.TAP_PHOTO_PRESETS.items():
        # Whatever the slider posted is discarded: the preset owns its number.
        assert config_store.resolve_tap_photo_preset(preset, 2.5) == (preset, image)
    for preset, text in config_store.TAP_TEXT_PRESETS.items():
        assert config_store.resolve_tap_text_preset(preset, 1.9) == (preset, text)


def test_resolve_custom_keeps_the_submitted_scale():
    assert config_store.resolve_tap_photo_preset("custom", 0.4) == ("custom", 0.4)
    assert config_store.resolve_tap_text_preset("custom", 1.8) == ("custom", 1.8)
    # An unrecognised key is treated as Custom rather than silently rewriting
    # the operator's number to some preset's.
    assert config_store.resolve_tap_photo_preset("bogus", 0.4) == ("custom", 0.4)
    assert config_store.resolve_tap_text_preset("bogus", 1.8) == ("custom", 1.8)
