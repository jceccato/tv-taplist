"""The Tap file store: Source precedence, enumeration, the md-plus-image pair.

These tests address the store the way callers will - by Slot and Source - with
one deliberate exception: `test_on_disk_filenames_are_the_documented_contract`
spells the four filenames out literally. ADR-0001 makes the mapped data
directory something operators read and edit by hand, so those names are a
user-facing contract rather than an implementation detail, and the suite has to
state them independently of the module under test.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app import paths, tap_store as store
from app.beer import BEER_KEYS, Beer, SourceRevision, TapPresentation


# ---- the Source enum and precedence order --------------------------------

def test_source_values_keep_the_legacy_disk_spellings():
    # The members read in glossary vocabulary; the values are what is on disk
    # and in the board payload, and must not drift.
    assert store.Source.MANUAL.value == "custom"
    assert store.Source.BREWFATHER.value == "brewfather"
    assert str(store.Source.MANUAL) == "custom"


def test_source_precedence_is_manual_then_brewfather():
    assert store.SOURCE_PRECEDENCE == (store.Source.MANUAL, store.Source.BREWFATHER)


# ---- resolve: the precedence walk ----------------------------------------

def test_resolve_prefers_manual_when_both_sources_hold_the_slot(write_tap):
    write_tap("custom", 1, name="Mine", body="Hand entered.")
    write_tap("bf", 1, name="Theirs", body="From Brewfather.")
    tap = store.resolve(1)
    assert tap.source is store.Source.MANUAL
    assert tap.beer.name == "Mine"
    assert tap.body == "Hand entered."


def test_resolve_falls_back_to_brewfather_when_only_it_holds_the_slot(write_tap):
    write_tap("bf", 2, name="Theirs")
    tap = store.resolve(2)
    assert tap.source is store.Source.BREWFATHER
    assert tap.slot == 2
    assert tap.beer.name == "Theirs"


def test_resolve_returns_none_for_a_vacant_slot():
    assert store.resolve(7) is None


def test_unreadable_manual_file_yields_an_empty_tap_not_the_brewfather_beer(
    write_tap, monkeypatch
):
    """Existence decides precedence, not readability.

    A transient read error on a bind mount must never demote a Manual Tap and
    put another brewery's beer on the TV, so the Slot renders empty instead.
    """
    write_tap("custom", 3, name="Mine")
    write_tap("bf", 3, name="Theirs")

    real_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self.name == "custom_tap_3.md":
            raise OSError("simulated bind-mount read error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    tap = store.resolve(3)
    assert tap is not None
    assert tap.source is store.Source.MANUAL   # not promoted to Brewfather
    assert tap.beer == Beer()
    assert tap.body == ""


def test_resolve_body_is_a_field_not_a_beer_attribute(write_tap):
    write_tap("bf", 4, name="Theirs", body="Bright citrus and pine.",
              description="not read from here")
    tap = store.resolve(4)
    assert tap.body == "Bright citrus and pine."
    # The description is the markdown body. A `description:` key in the front
    # matter is not a second way to set it - the Beer has no such field.
    assert not hasattr(tap.beer, "description")


# ---- read / write / exists ------------------------------------------------

def test_read_addresses_one_source_even_when_the_other_wins(write_tap):
    write_tap("custom", 5, name="Mine")
    write_tap("bf", 5, name="Theirs")
    assert store.read(5, store.Source.BREWFATHER).beer.name == "Theirs"
    assert store.read(5, store.Source.MANUAL).beer.name == "Mine"


def test_read_returns_none_when_the_source_holds_nothing():
    assert store.read(5, store.Source.MANUAL) is None


def test_write_then_read_round_trips_the_beer_and_body():
    store.write(6, store.Source.MANUAL, Beer(name="Saison", abv=6.2), "Peppery.")
    tap = store.read(6, store.Source.MANUAL)
    assert tap.slot == 6
    assert tap.source is store.Source.MANUAL
    assert tap.beer == Beer(name="Saison", abv=6.2)
    assert tap.body == "Peppery."
    # Written for the other Source's eyes only if asked for; not shared.
    assert store.read(6, store.Source.BREWFATHER) is None


def test_the_store_writes_the_serialisation_garnish_itself():
    """`source`, `image` and `updated` are the store's, not the caller's.

    None of the three is on the Beer, and none is read back as truth, but all
    three are written so a human opening the file sees a complete record. The
    store owns them because it is the one that knows which file this is.
    """
    store.write(6, store.Source.BREWFATHER, Beer(name="Saison"), "Peppery.")
    text = (paths.TAPS_DIR / "bf_tap_6.md").read_text(encoding="utf-8")
    front_matter, _ = store.parse_markdown(text)
    assert front_matter["source"] == "brewfather"
    assert front_matter["image"] is None       # no photo beside this file
    assert front_matter["updated"]             # stamped by the store
    # The Beer's own keys are all present, even the ones left unset, so the
    # file reads as a form a human can fill in.
    assert set(BEER_KEYS) <= set(front_matter)


def test_the_image_key_names_the_photo_that_is_actually_on_disk():
    """The key cannot disagree with the store: it is taken from `image_for`.

    It used to be passed in by each writer, which is a chance for a caller to
    write a filename a human then reads and believes.
    """
    store.save_image(6, store.Source.MANUAL, b"bytes", ".png")
    store.write(6, store.Source.MANUAL, Beer(name="Saison"), "")
    front_matter, _ = store.parse_markdown(
        (paths.TAPS_DIR / "custom_tap_6.md").read_text(encoding="utf-8"))
    assert front_matter["image"] == "custom_tap_6.png"


def test_a_source_that_writes_no_presentation_leaves_the_keys_out():
    """Sync writes no per-Slot Visibility overrides, so it writes no keys.

    Emitting two nulls into every Brewfather Tap file would be noise in a file
    operators read, and would suggest sync has an opinion about a Setting the
    Admin owns.
    """
    store.write(6, store.Source.BREWFATHER, Beer(name="Saison"), "")
    front_matter, _ = store.parse_markdown(
        (paths.TAPS_DIR / "bf_tap_6.md").read_text(encoding="utf-8"))
    assert "show_og" not in front_matter and "show_fg" not in front_matter
    assert store.read(6, store.Source.BREWFATHER).presentation == TapPresentation()


def test_a_manual_tap_has_no_revision_record():
    """`revision is None` is the type saying "Brewfather-only".

    A Manual Tap is not a cache of anything, so there is nothing for it to be
    current with - which is what stops sync ever treating one as up to date.
    """
    store.write(6, store.Source.MANUAL, Beer(name="Saison"), "")
    assert store.read(6, store.Source.MANUAL).revision is None

    store.write(6, store.Source.BREWFATHER, Beer(name="Theirs"), "",
                revision=SourceRevision(batch_id="b1", source_rev=3, map_rev=6))
    assert store.read(6, store.Source.BREWFATHER).revision == SourceRevision(
        batch_id="b1", source_rev=3, map_rev=6)


def test_an_unquoted_timestamp_does_not_take_the_board_down(write_tap):
    """A hand-edited `updated:` without quotes is a datetime to YAML.

    `/api/board` serialises with plain json.dumps, so that used to raise
    TypeError and blank every TV. ADR-0001 makes these files editable; a
    mistyped value must be coerced, never allowed to stop the box.
    """
    from datetime import datetime, timezone

    write_tap("custom", 6, name="Mine",
              updated=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc))
    tap = store.read(6, store.Source.MANUAL)
    assert isinstance(tap.updated, str)
    assert tap.updated.startswith("2026-08-08T09:00:00")


def test_exists_answers_is_this_slot_manual(write_tap):
    assert store.exists(8, store.Source.MANUAL) is False
    write_tap("custom", 8, name="Mine")
    assert store.exists(8, store.Source.MANUAL) is True
    assert store.exists(8, store.Source.BREWFATHER) is False


# ---- enumeration ----------------------------------------------------------

def test_occupied_slots_is_unbounded_by_the_configured_tap_count(write_tap):
    """Orphan retirement has to see Slots above the tap count to retire them."""
    from app import config_store

    config_store.update_config(num_taps=4)
    write_tap("bf", 2, name="Low")
    write_tap("bf", 9, name="Above the tap count")
    assert store.occupied_slots(store.Source.BREWFATHER) == [2, 9]


def test_occupied_slots_is_per_source_and_ignores_images(write_tap):
    write_tap("custom", 3, name="Mine", image_ext=".png")
    write_tap("bf", 1, name="Theirs", image_ext=".jpg")
    write_tap("bf", 12, name="Theirs too")
    assert store.occupied_slots(store.Source.MANUAL) == [3]
    assert store.occupied_slots(store.Source.BREWFATHER) == [1, 12]


def test_occupied_slots_ignores_unrelated_files():
    (paths.TAPS_DIR / "notes.md").write_text("hand written notes")
    (paths.TAPS_DIR / "bf_tap_notanumber.md").write_text("---\nname: x\n---\n")
    assert store.occupied_slots(store.Source.BREWFATHER) == []


# ---- the paired image -----------------------------------------------------

def test_image_for_finds_the_pair_and_never_crosses_sources(write_tap):
    write_tap("custom", 2, name="Mine")                      # no photo
    write_tap("bf", 2, name="Theirs", image_ext=".jpg")
    assert store.image_for(2, store.Source.MANUAL) is None
    assert store.image_for(2, store.Source.BREWFATHER).name == "bf_tap_2.jpg"
    # The winning Tap carries only its own Source's photo.
    assert store.resolve(2).image is None


def test_save_image_sweeps_a_previously_stored_other_extension():
    store.save_image(4, store.Source.BREWFATHER, b"first", ".webp")
    assert (paths.TAPS_DIR / "bf_tap_4.webp").exists()

    name = store.save_image(4, store.Source.BREWFATHER, b"second", ".jpg")

    assert name == "bf_tap_4.jpg"
    assert (paths.TAPS_DIR / "bf_tap_4.jpg").read_bytes() == b"second"
    assert not (paths.TAPS_DIR / "bf_tap_4.webp").exists()
    assert store.image_for(4, store.Source.BREWFATHER).name == "bf_tap_4.jpg"


def test_save_image_normalises_jpeg_and_rejects_unknown_extensions():
    assert store.save_image(5, store.Source.MANUAL, b"x", ".JPEG") == "custom_tap_5.jpg"
    with pytest.raises(ValueError):
        store.save_image(5, store.Source.MANUAL, b"x", ".exe")


# ---- the archiving crack --------------------------------------------------

def test_existing_paths_returns_only_the_files_that_are_there(write_tap):
    assert store.existing_paths(6, store.Source.BREWFATHER) == []
    write_tap("bf", 6, name="Theirs")
    assert [p.name for p in store.existing_paths(6, store.Source.BREWFATHER)] == [
        "bf_tap_6.md",
    ]
    write_tap("bf", 6, name="Theirs", image_ext=".png")
    assert [p.name for p in store.existing_paths(6, store.Source.BREWFATHER)] == [
        "bf_tap_6.md", "bf_tap_6.png",
    ]


def test_archived_stem_carries_the_datetime_suffix():
    when = datetime(2026, 6, 24, 15, 30, 0)
    assert store.archived_stem(3, store.Source.BREWFATHER, when) == "bf_tap_3_20260624T153000"
    assert store.archived_stem(3, store.Source.MANUAL, when) == "custom_tap_3_20260624T153000"


# ---- the on-disk contract -------------------------------------------------

def test_on_disk_filenames_are_the_documented_contract():
    """Pin the four filenames the store may create for one Slot.

    Stated literally rather than derived from the store, so a naming change
    fails loudly here instead of agreeing with itself. Operators' notes,
    scripts, and habits depend on these names (ADR-0001).
    """
    store.write(11, store.Source.MANUAL, Beer(name="Mine"), "")
    store.write(11, store.Source.BREWFATHER, Beer(name="Theirs"), "")
    store.save_image(11, store.Source.MANUAL, b"a", ".png")
    store.save_image(11, store.Source.BREWFATHER, b"b", ".jpg")

    assert sorted(p.name for p in paths.TAPS_DIR.iterdir()) == [
        "bf_tap_11.jpg",
        "bf_tap_11.md",
        "custom_tap_11.md",
        "custom_tap_11.png",
    ]
