"""Atomic writes, front-matter round-trips, config bootstrap/coercion.

The front-matter format layer stayed public when path construction moved into
the Tap file store, so these tests are pointed at `tap_store` rather than
folded into `test_tap_store.py`: the rest of this file is about atomic writes
and the config store, which the Tap file store has nothing to do with.
"""
import json

from app import config_store, paths, tap_store as taps
from app.atomic import atomic_write_bytes, atomic_write_text, safe_unlink


def test_atomic_write_text_and_no_temp_left_behind():
    target = paths.TAPS_DIR / "atomic.txt"
    atomic_write_text(target, "hello")
    assert target.read_text() == "hello"
    # No leftover temp files in the directory.
    leftovers = [p for p in paths.TAPS_DIR.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == []


def test_atomic_write_overwrites_existing():
    target = paths.TAPS_DIR / "atomic.bin"
    atomic_write_bytes(target, b"v1")
    atomic_write_bytes(target, b"v2")
    assert target.read_bytes() == b"v2"


def test_safe_unlink_is_race_safe():
    target = paths.TAPS_DIR / "gone.txt"
    atomic_write_text(target, "x")
    assert safe_unlink(target) is True
    assert safe_unlink(target) is False  # already gone, no exception


def test_front_matter_round_trip():
    fm = {"name": "West Coast IPA", "abv": 6.8, "ibu": 65, "ebc": 18, "source": "custom", "image": None}
    body = "Bright citrus and pine."
    text = taps.serialise_markdown(fm, body)
    parsed_fm, parsed_body = taps.parse_markdown(text)
    assert parsed_fm["name"] == "West Coast IPA"
    assert parsed_fm["abv"] == 6.8
    assert parsed_fm["image"] is None
    assert parsed_body == body


def test_parse_markdown_tolerates_no_front_matter():
    fm, body = taps.parse_markdown("just a body, no front matter")
    assert fm == {}
    assert body == "just a body, no front matter"


def test_config_bootstrap_creates_default():
    paths.CONFIG_PATH.unlink()
    cfg = config_store.load_config()
    assert cfg["num_taps"] == 0
    assert paths.CONFIG_PATH.exists()


def test_config_coerces_bad_types():
    paths.CONFIG_PATH.write_text(json.dumps({
        "num_taps": "8", "max_archive_age_days": "abc", "hide_vacant_taps": 1,
    }))
    cfg = config_store.load_config()
    assert cfg["num_taps"] == 8                       # coerced from string
    assert cfg["max_archive_age_days"] == 180         # bad -> default
    assert cfg["hide_vacant_taps"] is True


def test_config_unreadable_falls_back_without_overwriting():
    paths.CONFIG_PATH.write_text("{ this is not json")
    cfg = config_store.load_config()
    assert cfg["num_taps"] == 0
    # The corrupt file is preserved for operator recovery.
    assert paths.CONFIG_PATH.read_text().startswith("{ this is not json")
