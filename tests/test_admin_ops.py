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


def test_save_writes_a_manual_tap_with_the_submitted_beer():
    front_matter = admin_ops.save_override(
        1, name="Hand Pour", abv="4.5", ibu="18", color="9",
        description="Cask ale.")

    assert taps.exists(1, taps.Source.MANUAL)
    stored = taps.read(1, taps.Source.MANUAL)
    assert stored.front_matter["name"] == "Hand Pour"
    assert stored.front_matter["abv"] == 4.5
    assert stored.front_matter["ebc"] == 9
    assert stored.body.strip() == "Cask ale."
    # The returned front matter is what was written, so a caller (the route)
    # need not read the file back to answer.
    assert front_matter["name"] == stored.front_matter["name"]


def test_save_names_an_unnamed_beer_after_its_slot():
    admin_ops.save_override(4, name="   ")
    assert taps.read(4, taps.Source.MANUAL).front_matter["name"] == "Tap 4"


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
    assert taps.resolve(3).front_matter["name"] == "Guest Keg"
    # ...and the Brewfather Beer is there the instant it is released.
    admin_ops.clear_override(3)
    assert taps.resolve(3).front_matter["name"] == "Brewery IPA"


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
    assert taps.read(1, taps.Source.MANUAL).front_matter["name"] == "Good Beer"


def test_an_upload_free_save_keeps_the_existing_image():
    """Editing the description must not silently drop the beer's photo."""
    admin_ops.save_override(1, name="Photographed",
                            image=(b"\x89PNG\r\n\x1a\n", ".png"))
    first = taps.image_for(1, taps.Source.MANUAL)
    assert first is not None

    front_matter = admin_ops.save_override(1, name="Photographed",
                                           description="New words.")
    assert front_matter["image"] == first.name
    assert taps.image_for(1, taps.Source.MANUAL).name == first.name


def test_a_new_upload_replaces_the_photo():
    admin_ops.save_override(1, name="X", image=(b"\x89PNG\r\n\x1a\n", ".png"))
    front_matter = admin_ops.save_override(1, name="X", image=(b"JPEGBYTES", ".jpg"))
    assert front_matter["image"].endswith(".jpg")
    # The store owns the sweep of the previous extension, so only one remains.
    assert not (paths.TAPS_DIR / "custom_tap_1.png").exists()


@pytest.mark.parametrize("unit,expected", [("ebc", 10), ("srm", 19.7)])
def test_the_colour_is_stored_as_ebc_in_either_display_unit(unit, expected):
    admin_ops.save_override(1, name="Dark", color="10", unit=unit)
    assert taps.read(1, taps.Source.MANUAL).front_matter["ebc"] == pytest.approx(expected)


def test_saturation_glass_and_tri_states_are_normalised():
    front_matter = admin_ops.save_override(
        1, name="Loaded", color="20", saturation="60", color_override="780606",
        glass="teku", show_og=True, show_fg=False)
    assert front_matter["saturation"] == 0.6          # a percentage -> a fraction
    assert front_matter["color_override"] == "#780606"  # normalised with a leading #
    assert front_matter["glass"] == "teku"
    assert front_matter["show_og"] is True and front_matter["show_fg"] is False


def test_an_unknown_glass_inherits_the_global_default():
    front_matter = admin_ops.save_override(1, name="X", glass="notaglass")
    assert front_matter["glass"] is None


def test_the_operations_only_ever_touch_the_manual_source():
    """Named as a constant rather than remembered as a rule at each call site."""
    assert admin_ops.ADMIN_SOURCE is taps.Source.MANUAL
