"""The Admin's Manual-override domain operations, exercised without HTTP.

Every behaviour here used to be reachable only by posting a form and reading a
Tap file back, which is why these assertions are the point of the module split:
saving, clearing, keeping an existing photo and refusing a bad number are
domain rules, and a test should be able to state one without standing up a
request.
"""
from __future__ import annotations

import pytest

from app import admin_ops, paths, tap_store as taps
from app.beer import Beer, TapPresentation


def test_save_writes_a_manual_tap_with_the_submitted_beer():
    beer = admin_ops.save_override(
        1, name="Hand Pour", abv="4.5", ibu="18", color="9",
        description="Cask ale.")

    assert taps.exists(1, taps.Source.MANUAL)
    stored = taps.read(1, taps.Source.MANUAL)
    assert stored.beer == Beer(name="Hand Pour", abv=4.5, ibu=18, ebc=9)
    assert stored.body.strip() == "Cask ale."
    # The returned Beer is what was written, so a caller (the route) need not
    # read the file back to answer.
    assert beer == stored.beer


def test_save_names_an_unnamed_beer_after_its_slot():
    admin_ops.save_override(4, name="   ")
    assert taps.read(4, taps.Source.MANUAL).beer.name == "Tap 4"


def test_clearing_releases_the_slot():
    admin_ops.save_override(2, name="Tonight Only")
    assert taps.exists(2, taps.Source.MANUAL)

    assert admin_ops.clear_override(2) is True
    assert not taps.exists(2, taps.Source.MANUAL)
    # Released, not deleted: the Manual Tap is archived as a dated pair.
    assert list(paths.OLD_BEERS_DIR.glob("custom_tap_2_*.md"))


def test_clearing_a_vacant_slot_is_a_no_op():
    assert admin_ops.clear_override(7) is False


def test_saving_keeps_the_brewfather_tap_warm_underneath(write_tap):
    """Overriding a Slot must not archive the Brewfather Tap under it.

    Deliberately inverted: the skip that looks like the obvious optimisation is
    what made clearing an override leave the Slot Vacant until the next sync
    cycle. Sync keeps this file current underneath, so releasing the Slot
    reveals a current Beer at once (ADR-0003).
    """
    write_tap("bf", 3, name="Brewery IPA", ebc=20)
    admin_ops.save_override(3, name="Guest Keg")

    assert taps.exists(3, taps.Source.MANUAL)
    assert taps.exists(3, taps.Source.BREWFATHER)
    # Manual wins while it stands...
    assert taps.resolve(3).beer.name == "Guest Keg"
    # ...and the Brewfather Beer is there the instant it is released.
    admin_ops.clear_override(3)
    assert taps.resolve(3).beer.name == "Brewery IPA"


def test_a_rejected_value_writes_nothing():
    with pytest.raises(admin_ops.OverrideRejected):
        admin_ops.save_override(1, name="Bad", abv="not-a-number",
                                image=(b"\x89PNG\r\n\x1a\n", ".png"))

    # Neither the md file nor the image: every field that can reject is parsed
    # before anything touches the filesystem, so a bad number can never leave an
    # orphaned photo behind.
    assert not taps.exists(1, taps.Source.MANUAL)
    assert taps.image_for(1, taps.Source.MANUAL) is None


def test_a_rejected_value_leaves_an_existing_beer_untouched():
    admin_ops.save_override(1, name="Good Beer", abv="5")
    with pytest.raises(admin_ops.OverrideRejected):
        admin_ops.save_override(1, name="Edited", abv="oops")
    assert taps.read(1, taps.Source.MANUAL).beer.name == "Good Beer"


def test_an_upload_free_save_keeps_the_existing_image():
    """Editing the description must not silently drop the beer's photo."""
    admin_ops.save_override(1, name="Photographed",
                            image=(b"\x89PNG\r\n\x1a\n", ".png"))
    first = taps.image_for(1, taps.Source.MANUAL)
    assert first is not None

    admin_ops.save_override(1, name="Photographed", description="New words.")
    assert taps.image_for(1, taps.Source.MANUAL).name == first.name


def test_a_new_upload_replaces_the_photo():
    admin_ops.save_override(1, name="X", image=(b"\x89PNG\r\n\x1a\n", ".png"))
    admin_ops.save_override(1, name="X", image=(b"JPEGBYTES", ".jpg"))
    assert taps.image_for(1, taps.Source.MANUAL).name.endswith(".jpg")
    # The store owns the sweep of the previous extension, so only one remains.
    assert not (paths.TAPS_DIR / "custom_tap_1.png").exists()


@pytest.mark.parametrize("unit,expected", [("ebc", 10), ("srm", 19.7)])
def test_the_colour_is_stored_as_ebc_in_either_display_unit(unit, expected):
    admin_ops.save_override(1, name="Dark", color="10", unit=unit)
    assert taps.read(1, taps.Source.MANUAL).beer.ebc == pytest.approx(expected)


def test_saturation_glass_and_tri_states_are_normalised():
    beer = admin_ops.save_override(
        1, name="Loaded", color="20", saturation="60", color_override="780606",
        glass="teku", show_og=True, show_fg=False)
    assert beer.saturation == 0.6              # a percentage -> a fraction
    assert beer.color_override == "#780606"    # normalised with a leading #
    assert beer.glass == "teku"
    # The tri-states are Presentation of the Slot, not properties of the
    # beverage, so they are stored beside the Beer rather than on it.
    stored = taps.read(1, taps.Source.MANUAL)
    assert stored.presentation == TapPresentation(show_og=True, show_fg=False)


def test_an_unknown_glass_inherits_the_global_default():
    beer = admin_ops.save_override(1, name="X", glass="notaglass")
    assert beer.glass is None


def test_a_colour_override_that_will_not_parse_is_dropped_and_noted():
    """An unusable submitted value coerces rather than rejecting.

    Numbers reject because the Admin form has somebody to tell; a colour is a
    picker value, so an unusable one falls back to no override. The Beer records
    what it dropped so the store can log it once, at the write, rather than on
    every board build (docs/adr/0005).
    """
    beer = admin_ops.save_override(1, name="X", color_override="not-a-colour")
    assert beer.color_override is None
    assert beer.coerced == ("color_override",)


def test_the_operations_only_ever_touch_the_manual_source():
    """Named as a constant rather than remembered as a rule at each call site."""
    assert admin_ops.ADMIN_SOURCE is taps.Source.MANUAL


# ---- Display profiles (issue #52, ADR-0007) ---------------------------------
#
# The profile operations are domain rules, stated without HTTP: a profile holds
# only its differences, Follow drops a value, and the Default profile is never
# something these operations can create, rename or delete.

from app import config_store, upcoming_store  # noqa: E402


def _profiles():
    return config_store.load_config()["profiles"]


def test_create_adds_an_empty_profile_with_a_normalised_name():
    assert admin_ops.create_profile("  Lounge-TV ") == "lounge-tv"
    assert _profiles() == {"lounge-tv": {}}


@pytest.mark.parametrize("bad", ["", "   ", "default", "DEFAULT", "has space",
                                 "under_score", "x" * 33, "café"])
def test_create_refuses_a_name_that_cannot_be_a_profile(bad):
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.create_profile(bad)
    assert _profiles() == {}


def test_create_refuses_a_name_that_exists_in_any_case():
    admin_ops.create_profile("tv")
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.create_profile("TV")


def test_save_stores_only_the_customised_units():
    admin_ops.create_profile("tv")
    saved = admin_ops.save_profile(
        "tv", customise={"rotation_seconds"},
        values={"rotation_seconds": 12, "hide_vacant_taps": True,
                "announcement_text": "typed but followed"})
    assert saved == {"rotation_seconds": 12}
    assert _profiles()["tv"] == {"rotation_seconds": 12}


def test_save_writes_only_the_profile_being_edited():
    admin_ops.create_profile("tv")
    admin_ops.create_profile("tablet")
    admin_ops.save_profile("tablet", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9})
    default_before = {k: v for k, v in config_store.load_config().items()
                      if k != "profiles"}

    admin_ops.save_profile("tv", customise={"announcement_text"},
                           values={"announcement_text": "Hello"})

    assert _profiles()["tablet"] == {"rotation_seconds": 9}
    assert {k: v for k, v in config_store.load_config().items()
            if k != "profiles"} == default_before


def test_follow_drops_the_value_so_the_default_is_tracked_again():
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise={"rotation_seconds", "hide_vacant_taps"},
                           values={"rotation_seconds": 9, "hide_vacant_taps": True})
    # The next Save leaves rotation on Customise and flips hide_vacant_taps back.
    admin_ops.save_profile("tv", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9, "hide_vacant_taps": True})
    assert _profiles()["tv"] == {"rotation_seconds": 9}

    config_store.update_config(rotation_seconds=45)
    resolved = config_store.resolve_effective(config_store.load_config(), "tv")
    assert resolved.settings["rotation_seconds"] == 9
    assert resolved.settings["hide_vacant_taps"] is False


def test_a_unit_is_saved_whole_or_not_at_all():
    admin_ops.create_profile("tv")
    admin_ops.save_profile(
        "tv", customise={"pagination", "stat_og"},
        values={"paginate": True, "page_size": 4, "show_og": True,
                "hide_og_when_empty": False})
    assert _profiles()["tv"] == {"paginate": True, "page_size": 4,
                                 "show_og": True, "hide_og_when_empty": False}


def test_a_false_checkbox_is_stored_as_false_not_dropped():
    admin_ops.create_profile("tv")
    config_store.update_config(show_abv=True)
    admin_ops.save_profile("tv", customise={"stat_abv"},
                           values={"show_abv": False, "hide_abv_when_empty": False})
    assert _profiles()["tv"] == {"show_abv": False, "hide_abv_when_empty": False}


def test_a_named_card_size_preset_owns_its_scale_in_a_profile_too():
    admin_ops.create_profile("tv")
    admin_ops.save_profile(
        "tv", customise={"tap_photo_size"},
        values={"tap_photo_preset": "small", "tap_image_scale": 0.99})
    assert _profiles()["tv"] == {"tap_photo_preset": "small", "tap_image_scale": 0.6}


def test_profile_values_are_clamped_like_the_default_profiles():
    admin_ops.create_profile("tv")
    saved = admin_ops.save_profile("tv", customise={"rotation_seconds"},
                                   values={"rotation_seconds": 1})
    assert saved == {"rotation_seconds": config_store.MIN_ROTATION_SECONDS}


def test_a_key_that_is_not_customisable_is_never_stored():
    admin_ops.create_profile("tv")
    saved = admin_ops.save_profile(
        "tv", customise={"num_taps", "color_unit", "rotation_seconds"},
        values={"num_taps": 9, "color_unit": "srm", "rotation_seconds": 20})
    assert saved == {"rotation_seconds": 20}


def test_saving_an_unknown_profile_is_refused():
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.save_profile("ghost", customise={"rotation_seconds"},
                               values={"rotation_seconds": 9})
    assert _profiles() == {}


def test_the_default_profile_cannot_be_saved_renamed_or_deleted_as_a_profile():
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.save_profile("default", customise={"rotation_seconds"},
                               values={"rotation_seconds": 9})
    admin_ops.create_profile("tv")
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.rename_profile("default", "main")
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.rename_profile("tv", "default")
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.delete_profile("default")
    assert list(_profiles()) == ["tv"]


def test_rename_keeps_the_values_and_the_position():
    admin_ops.create_profile("aaa")
    admin_ops.create_profile("tv")
    admin_ops.create_profile("zzz")
    admin_ops.save_profile("tv", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9})
    assert admin_ops.rename_profile("tv", "Lounge") == "lounge"
    assert list(_profiles()) == ["aaa", "lounge", "zzz"]
    assert _profiles()["lounge"] == {"rotation_seconds": 9}


def test_rename_leaves_no_redirect_the_old_name_is_unknown():
    """A start URL using the old name falls back to the Default profile."""
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9})
    admin_ops.rename_profile("tv", "lounge")
    settings = config_store.load_config()
    old = config_store.resolve_effective(settings, "tv")
    assert (old.profile, old.chosen) == ("default", config_store.CHOSEN_UNKNOWN)
    assert old.settings["rotation_seconds"] == settings["rotation_seconds"]


def test_rename_refuses_a_missing_source_a_bad_name_and_a_clash():
    admin_ops.create_profile("tv")
    admin_ops.create_profile("tablet")
    for old, new in (("ghost", "x"), ("tv", "bad name"), ("tv", "TABLET")):
        with pytest.raises(admin_ops.ProfileRejected):
            admin_ops.rename_profile(old, new)
    assert list(_profiles()) == ["tv", "tablet"]


def test_renaming_to_the_same_name_changes_nothing():
    admin_ops.create_profile("tv")
    assert admin_ops.rename_profile("tv", "TV") == "tv"
    assert list(_profiles()) == ["tv"]


def test_delete_removes_the_profile_and_sends_its_screens_to_the_default():
    admin_ops.create_profile("tv")
    admin_ops.delete_profile("tv")
    assert _profiles() == {}
    resolved = config_store.resolve_effective(config_store.load_config(), "tv")
    assert resolved.chosen == config_store.CHOSEN_UNKNOWN


def test_delete_refuses_a_profile_that_does_not_exist():
    with pytest.raises(admin_ops.ProfileRejected):
        admin_ops.delete_profile("ghost")


def test_customised_units_names_what_a_profile_differs_in():
    values = {"rotation_seconds": 9, "paginate": True, "page_size": 3,
              "show_og": True}      # show_og without its partner: half a unit
    assert admin_ops.customised_units(values) == ["pagination", "rotation_seconds"]


def test_a_profile_save_that_ends_upcoming_beers_everywhere_clears_the_cache():
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise={"show_upcoming_previews"},
                           values={"show_upcoming_previews": True})
    marker = paths.UPCOMING_DIR / f"{upcoming_store._PREFIX}1.md"
    marker.write_text("x", encoding="utf-8")

    # Still wanted by the profile after the Default is not: the cache stays.
    admin_ops.save_profile("tv", customise={"show_upcoming_previews"},
                           values={"show_upcoming_previews": True})
    assert marker.exists()

    # The profile goes back to following a Default that is off: nobody wants it.
    admin_ops.save_profile("tv", customise=set(), values={})
    assert not marker.exists()
