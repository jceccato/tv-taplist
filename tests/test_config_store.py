"""Config load/save safety - especially the "don't clobber on a flaky read" guard.

A transient read failure (e.g. a Docker Desktop bind mount on Windows briefly
failing a read) must never cause update_config to overwrite the operator's saved
settings with defaults.
"""
import json
from pathlib import Path

import pytest

from app import beer_glass, config_store


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
    assert cfg["glass_type"] == beer_glass.DEFAULT_GLASS      # unknown -> default


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


# ---- show_upcoming_previews (issue #36) -----------------------------------

def test_show_upcoming_previews_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_previews"] is False
    cfg = config_store.update_config(show_upcoming_previews="yes")  # truthy -> bool True
    assert cfg["show_upcoming_previews"] is True


def test_show_upcoming_previews_absent_from_stored_config_reads_false():
    assert config_store._coerce({"num_taps": 4})["show_upcoming_previews"] is False


def test_apply_settings_flipping_the_toggle_off_clears_the_upcoming_store():
    # This is the one Setting that deletes files (ADR-0006), and the clearing
    # has to happen right at the write seam so the operator sees /data become
    # honest the instant they save - not on the next sync.
    from app import upcoming_store
    from app.beer import Beer

    config_store.apply_settings(show_upcoming_previews=True, num_taps=4,
                                 max_archive_age_days=1, max_archive_storage_mb=1)
    upcoming_store.write("batch-1", Beer(name="Saison"), "",
                          slot=None, status="fermenting", revision=1)
    assert upcoming_store.list_all() != []

    config_store.apply_settings(show_upcoming_previews=False, num_taps=4,
                                 max_archive_age_days=1, max_archive_storage_mb=1)
    assert upcoming_store.list_all() == []


def test_apply_settings_leaves_the_upcoming_store_alone_when_already_off():
    # A save that does not flip the toggle (off staying off, or on staying on)
    # must not touch the store at all - only the on-to-off transition clears.
    from app import upcoming_store
    from app.beer import Beer

    upcoming_store.write("batch-2", Beer(name="Lager"), "",
                          slot=None, status="fermenting", revision=1)
    config_store.apply_settings(show_upcoming_previews=False, num_taps=4,
                                 max_archive_age_days=1, max_archive_storage_mb=1)
    # The write above did not go through the gate (it bypassed sync), so this
    # only pins that an off->off save is not itself a clearing trigger.
    assert upcoming_store.read("batch-2") is not None


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


# ---- the single enforcement point -----------------------------------------

def test_every_numeric_bound_clamps_rather_than_raising():
    """Out of range in either direction is saved at the bound, silently.

    The store is the one place a Settings bound is applied. It clamps rather
    than refusing because config.json is hand-editable (ADR-0001) and a file has
    nobody to report an error to - a raise here would stop the box booting over
    a typo. The Admin form carries the same numbers as input attributes so an
    operator is stopped while typing instead. See CONTEXT.md, Known hazards.
    """
    for field, (lo, hi) in config_store.SETTINGS_BOUNDS.items():
        below = config_store.update_config(**{field: lo - 1000})
        assert below[field] == lo, f"{field} below its minimum"
        if hi is not None:
            above = config_store.update_config(**{field: hi + 1000})
            assert above[field] == hi, f"{field} above its maximum"


def test_the_cleanup_limits_have_no_ceiling():
    """A venue may keep its archive forever; only the floor is a bound."""
    for field in ("max_archive_age_days", "max_archive_storage_mb"):
        assert config_store.SETTINGS_BOUNDS[field][1] is None
        assert config_store.update_config(**{field: 10_000_000})[field] == 10_000_000


def test_update_config_returns_what_was_saved_not_what_was_passed():
    saved = config_store.update_config(num_taps=5000)
    assert saved["num_taps"] == config_store.MAX_NUM_TAPS
    assert saved == config_store.load_config()


# ---- env-managed credentials never reach disk ------------------------------

def test_update_config_never_persists_an_env_managed_credential(monkeypatch):
    """The write seam drops a credential the environment owns.

    Keeping the API key off disk is the whole point of the env vars, so the rule
    lives here rather than in the Admin route that happens to be its only caller
    today - any future writer inherits it. Asserted against config.json itself.
    """
    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key")
    monkeypatch.delenv("BREWFATHER_USER_ID", raising=False)

    config_store.update_config(brewfather_user_id="typed-user",
                               brewfather_api_key="typed-key")

    on_disk = _read_raw()
    assert on_disk["brewfather_api_key"] == ""            # never written
    assert on_disk["brewfather_user_id"] == "typed-user"  # not env-managed
    # The effective credential still resolves, from the environment.
    creds = config_store.brewfather_credentials()
    assert creds["api_key"] == "env-key" and creds["key_from_env"] is True


def test_dropping_an_env_credential_leaves_a_previously_saved_one_alone(monkeypatch):
    """Dropping the key is not the same as blanking it.

    The Admin form shows a read-only "managed via environment" field and posts
    an empty value back; that must not erase a key an operator saved before the
    env var existed, because unsetting the env var would then leave them with
    nothing.
    """
    config_store.update_config(brewfather_api_key="saved-on-disk")
    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key")
    config_store.update_config(brewfather_api_key="")
    assert _read_raw()["brewfather_api_key"] == "saved-on-disk"


def test_credentials_are_stripped_on_the_way_in():
    config_store.update_config(brewfather_api_key="  k  ", brewfather_user_id="\tu\n")
    on_disk = _read_raw()
    assert on_disk["brewfather_api_key"] == "k"
    assert on_disk["brewfather_user_id"] == "u"


# ---- the Settings domain operation -----------------------------------------

def test_apply_settings_resolves_the_presets_and_returns_the_saved_config():
    saved = config_store.apply_settings(
        num_taps=4, tap_photo_preset="small", tap_image_scale=2.5,
        tap_text_preset="custom", tap_text_scale=1.8)
    # A named preset owns its number; Custom keeps what was submitted.
    assert saved["tap_photo_preset"] == "small" and saved["tap_image_scale"] == 0.6
    assert saved["tap_text_preset"] == "custom" and saved["tap_text_scale"] == 1.8
    assert saved["num_taps"] == 4


def test_apply_settings_clamps_and_does_not_raise():
    assert config_store.apply_settings(num_taps=-5)["num_taps"] == 0


# ---- The teaser card's words (issue #39) -----------------------------------

def test_upcoming_label_defaults_and_truncates_rather_than_rejects():
    assert config_store.DEFAULT_CONFIG["upcoming_label"] == "Coming up"
    long_label = "x" * 200
    cfg = config_store.update_config(upcoming_label=long_label)
    assert cfg["upcoming_label"] == "x" * config_store.MAX_UPCOMING_LABEL_LEN
    assert len(cfg["upcoming_label"]) == config_store.MAX_UPCOMING_LABEL_LEN


def test_upcoming_label_blank_falls_back_to_the_default():
    cfg = config_store.update_config(upcoming_label="   ")
    assert cfg["upcoming_label"] == "Coming up"


def test_upcoming_label_within_the_cap_is_untouched():
    cfg = config_store.update_config(upcoming_label="Up next")
    assert cfg["upcoming_label"] == "Up next"


def test_show_upcoming_status_defaults_true_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_status"] is True
    cfg = config_store.update_config(show_upcoming_status="")  # falsy -> bool False
    assert cfg["show_upcoming_status"] is False


def test_show_upcoming_subtitle_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_subtitle"] is False
    cfg = config_store.update_config(show_upcoming_subtitle="yes")
    assert cfg["show_upcoming_subtitle"] is True


def test_show_upcoming_abv_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_abv"] is False
    cfg = config_store.update_config(show_upcoming_abv="yes")
    assert cfg["show_upcoming_abv"] is True


# ---- Scheduling (issue #40) -------------------------------------------------

def test_upcoming_rotate_occupied_defaults_true_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["upcoming_rotate_occupied"] is True
    cfg = config_store.update_config(upcoming_rotate_occupied="")  # falsy -> bool False
    assert cfg["upcoming_rotate_occupied"] is False


def test_upcoming_interval_seconds_defaults_and_is_in_the_bounds_table():
    assert config_store.DEFAULT_CONFIG["upcoming_interval_seconds"] == 20
    lo, hi = config_store.SETTINGS_BOUNDS["upcoming_interval_seconds"]
    assert (lo, hi) == (5, 300)


def test_upcoming_interval_seconds_clamps_below_the_floor_and_above_the_ceiling():
    """The 300s ceiling is deliberately below rotation_seconds' 600s (CLAUDE.md)."""
    below = config_store.update_config(upcoming_interval_seconds=1)
    assert below["upcoming_interval_seconds"] == 5
    above = config_store.update_config(upcoming_interval_seconds=1000)
    assert above["upcoming_interval_seconds"] == 300
    assert config_store.SETTINGS_BOUNDS["upcoming_interval_seconds"][1] < \
        config_store.MAX_ROTATION_SECONDS


def test_upcoming_words_settings_absent_from_stored_config_read_the_default():
    # A config written before issue #39 has none of these four keys; the merge
    # over DEFAULT_CONFIG must fall back to the schema defaults rather than
    # raising or reading as falsy/blank.
    merged = config_store._coerce({"num_taps": 4})
    assert merged["upcoming_label"] == "Coming up"
    assert merged["show_upcoming_status"] is True
    assert merged["show_upcoming_subtitle"] is False
    assert merged["show_upcoming_abv"] is False


# ---- The on-deck page surface (issue #41) ----------------------------------

def test_show_upcoming_deck_page_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_deck_page"] is False
    cfg = config_store.update_config(show_upcoming_deck_page="yes")
    assert cfg["show_upcoming_deck_page"] is True
    cfg = config_store.update_config(show_upcoming_deck_page="")
    assert cfg["show_upcoming_deck_page"] is False


def test_upcoming_deck_multiple_defaults_and_is_in_the_bounds_table():
    assert config_store.DEFAULT_CONFIG["upcoming_deck_multiple"] == 3
    assert config_store.SETTINGS_BOUNDS["upcoming_deck_multiple"] == (1, 6)


def test_upcoming_deck_multiple_clamps_below_the_floor_and_above_the_ceiling():
    below = config_store.update_config(upcoming_deck_multiple=0)
    assert below["upcoming_deck_multiple"] == 1
    above = config_store.update_config(upcoming_deck_multiple=99)
    assert above["upcoming_deck_multiple"] == 6


def test_upcoming_surface_scope_defaults_to_overflow():
    assert config_store.DEFAULT_CONFIG["upcoming_surface_scope"] == "overflow"
    cfg = config_store.update_config(num_taps=1)
    assert cfg["upcoming_surface_scope"] == "overflow"


def test_upcoming_surface_scope_accepts_all():
    cfg = config_store.update_config(upcoming_surface_scope="all")
    assert cfg["upcoming_surface_scope"] == "all"


def test_upcoming_surface_scope_is_case_insensitive_and_trims_whitespace():
    cfg = config_store.update_config(upcoming_surface_scope="  All  ")
    assert cfg["upcoming_surface_scope"] == "all"


def test_unrecognised_upcoming_surface_scope_coerces_to_overflow():
    """The stated regression guard: junk must never be rejected or raise.

    A hand-edited config.json (or a value from a future version this build
    does not know) has no one to report an error to (CLAUDE.md), and the
    fallback is deliberately the scope that never shows a beer twice.
    """
    for junk in ("", "bogus", "OVERFLOW-ish", None, 123, "everything"):
        cfg = config_store.update_config(upcoming_surface_scope=junk)
        assert cfg["upcoming_surface_scope"] == "overflow", junk


def test_upcoming_deck_settings_absent_from_stored_config_read_the_default():
    merged = config_store._coerce({"num_taps": 4})
    assert merged["show_upcoming_deck_page"] is False
    assert merged["upcoming_deck_multiple"] == 3
    assert merged["upcoming_surface_scope"] == "overflow"


# ---- The half-board panel surface (issue #42) -------------------------------

def test_show_upcoming_panel_defaults_false_and_coerces_bool():
    assert config_store.DEFAULT_CONFIG["show_upcoming_panel"] is False
    cfg = config_store.update_config(show_upcoming_panel="yes")
    assert cfg["show_upcoming_panel"] is True
    cfg = config_store.update_config(show_upcoming_panel="")
    assert cfg["show_upcoming_panel"] is False


def test_upcoming_panel_multiple_defaults_and_is_in_the_bounds_table():
    # The default is 2, not the on-deck page's 3 (CLAUDE.md/#42): the panel
    # is a cheaper interruption - the top half of the board stays readable
    # underneath it - so it can afford to take its turn more often.
    assert config_store.DEFAULT_CONFIG["upcoming_panel_multiple"] == 2
    assert config_store.SETTINGS_BOUNDS["upcoming_panel_multiple"] == (1, 6)


def test_upcoming_panel_multiple_clamps_below_the_floor_and_above_the_ceiling():
    below = config_store.update_config(upcoming_panel_multiple=0)
    assert below["upcoming_panel_multiple"] == 1
    above = config_store.update_config(upcoming_panel_multiple=99)
    assert above["upcoming_panel_multiple"] == 6


def test_upcoming_panel_multiple_is_independent_of_the_deck_multiple():
    """Two surfaces, two independent knobs (issue #42's acceptance criteria).

    Saving one must never move the other - the deck page and the panel are
    not the same control wearing two names.
    """
    cfg = config_store.update_config(upcoming_deck_multiple=5, upcoming_panel_multiple=1)
    assert cfg["upcoming_deck_multiple"] == 5
    assert cfg["upcoming_panel_multiple"] == 1
    cfg = config_store.update_config(upcoming_panel_multiple=6)
    assert cfg["upcoming_deck_multiple"] == 5   # untouched by the panel's own save
    assert cfg["upcoming_panel_multiple"] == 6


def test_upcoming_panel_settings_absent_from_stored_config_read_the_default():
    merged = config_store._coerce({"num_taps": 4})
    assert merged["show_upcoming_panel"] is False
    assert merged["upcoming_panel_multiple"] == 2


# ---- The conditioning-on-tap status marker (issue #45) ---------------------

def test_show_conditioning_status_defaults_off_and_coerces_bool():
    """Off by default: this one changes a board that is pouring right now.

    Unlike `show_upcoming_status`, which only renders inside a feature that is
    itself off by default, turning this on marks a live Tap card - so an
    operator upgrading must opt in rather than find their board changed.
    """
    assert config_store.DEFAULT_CONFIG["show_conditioning_status"] is False
    cfg = config_store.update_config(show_conditioning_status="yes")
    assert cfg["show_conditioning_status"] is True
    cfg = config_store.update_config(show_conditioning_status="")  # falsy -> False
    assert cfg["show_conditioning_status"] is False


def test_show_conditioning_status_absent_from_stored_config_reads_the_default():
    # A config written before issue #45 has no such key; the merge over
    # DEFAULT_CONFIG must fall back to the schema default rather than raising.
    merged = config_store._coerce({"num_taps": 4})
    assert merged["show_conditioning_status"] is False
