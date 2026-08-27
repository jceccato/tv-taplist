"""**Beer** as a type: coercion, the round trip, and the disposition rule.

The rule these tests exist to protect is in
docs/adr/0005-beer-crosses-the-store-seam-as-a-type.md: file-level readability
and value-level validity are different questions. A file that will not read
stops the Source precedence walk; a value that will not parse must not. The
third test here is the one that fails if somebody later "improves" coercion into
validation.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from app import admin_ops, board, demo, mapping, paths, tap_store as taps
from app.beer import BEER_KEYS, Beer, SourceRevision, TapPresentation


# ---- purity ------------------------------------------------------------

# Everything app/beer.py is allowed to import. Mapping must be able to build a
# Beer without a client or a data directory - its own import guard forbids
# anything that touches disk - so the type has to stay reachable from there.
# `colors` is on the list because it already owns hex and saturation parsing;
# re-implementing either here would be a second opinion about Colour.
_ALLOWED_BEER_IMPORTS = {"__future__", "dataclasses", "typing", ".colors"}


def test_beer_imports_nothing_that_performs_io():
    tree = ast.parse(Path("app/beer.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * node.level
            if node.module:
                imported.add(dots + node.module)
            else:
                imported.update(dots + alias.name for alias in node.names)
    assert imported <= _ALLOWED_BEER_IMPORTS, imported - _ALLOWED_BEER_IMPORTS


# ---- the coercion table ------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("6.2", 6.2),          # YAML can hand back a string for a quoted number
    (6.2, 6.2),
    ("", None),            # a blank field is an absent reading
    (None, None),
    ("banana", None),      # hand-edited junk coerces, it does not raise
    (0, 0),                # 0 is a value: a 0 IBU lager has no bittering
    (0.0, 0),
    ("5.0", 5),            # integral floats store as ints, for hand editing
])
def test_the_attribute_coercion_table(given, expected):
    assert Beer(abv=given).abv == expected
    assert type(Beer(abv=given).abv) is type(expected)


def test_none_is_the_only_absence_past_the_seam():
    """The empty string never survives, which is what lets `_is_missing` shrink.

    `board._is_missing` used to test for `""` as well, once per Attribute per
    Tap on every poll from every TV, because the value came out of an untyped
    dict.
    """
    beer = Beer(abv="", ibu="", ebc="", og="", fg="", name=None)
    assert (beer.abv, beer.ibu, beer.ebc, beer.og, beer.fg) == (None,) * 5
    assert beer.name == ""
    assert board._is_missing("") is False   # cannot arrive; not defended against
    assert board._is_missing(None) is True


def test_zero_is_a_reading_and_stays_visible(write_tap):
    # The end-to-end version of the row above: 0 IBU reaches the board as 0 and
    # is not suppressed as empty.
    from app.config_store import update_config

    update_config(num_taps=1, show_ibu=True, hide_ibu_when_empty=True)
    write_tap("custom", 1, name="Lager", ibu=0)
    resolved = board.resolve_tap(1)
    assert resolved["ibu"] == 0
    assert resolved["ibu_visible"] is True


# ---- the round trip ----------------------------------------------------

def test_beer_survives_a_round_trip_through_yaml():
    """Beer -> front matter -> YAML -> Beer is the identity.

    The on-disk format is unchanged and hand-editable (ADR-0001), so the type
    has to be able to read back exactly what it wrote - otherwise a Tap would
    drift a little on every rewrite.
    """
    beer = Beer(name="Saison", abv=6.2, ibu=28, ebc=14, og=1.055, fg=1.008,
                saturation=0.6, color_override="#780606", glass="teku")
    text = taps.serialise_markdown(beer.to_front_matter(), "Peppery.")
    front_matter, body = taps.parse_markdown(text)
    assert Beer.from_front_matter(front_matter) == beer
    assert body == "Peppery."


def test_an_empty_beer_round_trips_too():
    # The Vacant-ish case: every field at its default must come back the same,
    # not turn into empty strings on the way through YAML.
    text = taps.serialise_markdown(Beer().to_front_matter(), "")
    front_matter, _ = taps.parse_markdown(text)
    assert Beer.from_front_matter(front_matter) == Beer()


def test_unknown_front_matter_keys_are_dropped():
    """The type is closed - see ADR-0005 on why `extra` was rejected.

    A key nobody reads is not carried through a rewrite. In practice this is a
    no-op: Manual files are rewritten only when the operator saves that Slot,
    and Brewfather files are cache that sync overwrites wholesale.
    """
    beer = Beer.from_front_matter({"name": "Ale", "brewery": "Somebody Else"})
    assert beer == Beer(name="Ale")
    assert "brewery" not in beer.to_front_matter()


# ---- the disposition rule (the load-bearing one) -----------------------

def test_a_bad_value_does_not_disturb_source_precedence(write_tap):
    """A typo must not do what a disk fault does.

    `tap_store.resolve` treats a file that vanished (precedence moves on) and a
    file that will not read (the walk stops, so a disk hiccup cannot put another
    brewery's beer on the TV) as different things. A bad *value* is neither: it
    coerces to None and the Tap resolves normally, under its own Source.

    This is the test that fails if somebody later turns coercion into
    validation - raising, or falling through to Brewfather, would both put the
    wrong beer on the board because an operator mistyped an ABV.
    """
    write_tap("custom", 1, name="Hand Edited", abv="banana", ebc=12)
    write_tap("bf", 1, name="Brewery IPA", abv=5.5, ebc=40)

    tap = taps.resolve(1)
    assert tap.source is taps.Source.MANUAL     # precedence untouched
    assert tap.beer.name == "Hand Edited"       # the Manual Beer, not the other
    assert tap.beer.abv is None                 # only the bad value was lost
    assert tap.beer.ebc == 12                   # its neighbours survived

    from app.config_store import update_config

    update_config(num_taps=1)
    resolved = board.resolve_tap(1)
    assert resolved["source"] == "custom"
    assert resolved["name"] == "Hand Edited"
    assert resolved["abv"] is None


def test_a_coercion_is_logged_at_the_write_not_at_the_read(write_tap, caplog):
    """The board is rebuilt on every poll from every TV, so reads stay silent.

    Same firehose reasoning ADR-0003 gives for not warning on a `source:`
    mismatch. What was dropped rides on the Beer instead, and the store logs it
    once when a file is written.
    """
    write_tap("custom", 1, name="Hand Edited", abv="banana")

    with caplog.at_level("WARNING", logger="taplist.taps"):
        beer = taps.resolve(1).beer
        board.resolve_tap(1)
    assert caplog.records == []
    assert beer.coerced == ("abv",)

    with caplog.at_level("WARNING", logger="taplist.taps"):
        taps.write(1, taps.Source.MANUAL, beer, "")
    assert any("abv" in r.getMessage() for r in caplog.records)


# ---- every writer builds a Beer ----------------------------------------

def _front_matter_keys(name: str) -> set[str]:
    from app import paths

    text = (paths.TAPS_DIR / name).read_text(encoding="utf-8")
    return set(taps.parse_markdown(text)[0])


def test_all_three_writers_produce_a_beer(monkeypatch):
    """The structural guard - the one that would have caught the demo seeder.

    Brewfather Mapping, the Admin's Manual override and the demo seeder each
    used to build a front-matter dict by hand, and the demo one wrote 7 keys out
    of roughly 18 without anybody noticing, because every reader defended itself
    with `.get()`. Nothing checked that the three agreed. Now they cannot
    disagree: the Beer is what each of them produces, and the key set on disk
    follows from the type rather than from each writer's memory.

    The Brewfather writer is asserted twice over: here on the Beer it maps
    (which needs no client at all, issue #10), and in test_brewfather_sync.py on
    the file a real sync leaves behind.
    """
    # 1. Brewfather Mapping, called with a plain Batch and nothing else.
    assert isinstance(mapping.beer({"_id": "b1", "recipe": {"name": "Ale"}}), Beer)

    # 2. The Admin's Manual override.
    assert isinstance(admin_ops.save_override(1, name="Hand Pour", abv="4.5"), Beer)
    assert set(BEER_KEYS) <= _front_matter_keys("custom_tap_1.md")

    # 3. The demo seeder, whose Beers lean entirely on the type's defaults - it
    # sets four fields and used to write a seven-key file because of it.
    monkeypatch.setenv("DEMO_MODE", "true")
    for source in taps.SOURCE_PRECEDENCE:
        for slot in taps.occupied_slots(source):
            for path in taps.existing_paths(slot, source):
                path.unlink()
    demo.maybe_seed_demo()
    seeded = taps.occupied_slots(taps.Source.BREWFATHER)
    assert seeded, "the demo seeder wrote nothing"
    assert isinstance(taps.read(seeded[0], taps.Source.BREWFATHER).beer, Beer)
    assert set(BEER_KEYS) <= _front_matter_keys(f"bf_tap_{seeded[0]}.md")

    # 3b. The demo seeder also seeds two Upcoming Beers (issue #43) through
    # the same typed Beer, one bound to a Slot it keeps Vacant (so the pinned
    # teaser needs no sync) and one unbound (so the overflow queue is not
    # empty either). This is the one that would have caught a seeder that
    # built the Upcoming front matter by hand instead of via Beer.
    from app import upcoming_store

    demo_entries = upcoming_store.list_all()
    assert len(demo_entries) == 2, "expected exactly two seeded demo Upcoming Beers"
    for entry in demo_entries:
        assert isinstance(entry.beer, Beer)
    bound = [e for e in demo_entries if e.slot is not None]
    unbound = [e for e in demo_entries if e.slot is None]
    assert len(bound) == 1 and len(unbound) == 1
    # The bound demo entry's Slot must be Vacant (no Tap file) so the board
    # marks it pinned without waiting for a sync - the whole point of #43.
    assert bound[0].slot not in seeded
    assert bound[0].slot not in taps.occupied_slots(taps.Source.MANUAL)

    from app.config_store import load_config as _load_config

    assert _load_config()["show_upcoming_previews"] is True

    # 4. The Upcoming store (issue #36), whose writer is handed a Beer built
    # by Mapping (in provisional mode) rather than building one itself - but
    # it is still a distinct write path with its own front matter, so it gets
    # its own structural check rather than riding on Mapping's alone.
    from app import upcoming_store

    upcoming_beer = mapping.beer({"_id": "b2", "recipe": {"name": "Saison"}}, provisional=True)
    upcoming_store.write("b2", upcoming_beer, "", slot=None, status="fermenting", revision=1)
    entry = upcoming_store.read("b2")
    assert isinstance(entry.beer, Beer)
    text = (paths.UPCOMING_DIR / next(
        p.name for p in paths.UPCOMING_DIR.glob("*.md")
    )).read_text(encoding="utf-8")
    assert set(BEER_KEYS) <= set(taps.parse_markdown(text)[0])


# ---- the two records that travel beside the Beer -----------------------

def test_presentation_is_stored_beside_the_beer_not_on_it():
    # show_og / show_fg say how this Slot renders. The same beer poured on
    # another Slot would not bring them along, so they are not Beer fields.
    assert not hasattr(Beer(), "show_og")
    assert TapPresentation.from_front_matter({"show_og": "", "show_fg": True}) \
        == TapPresentation(show_og=None, show_fg=True)


def test_a_file_with_no_revision_keys_has_no_revision_record():
    # None, not a record of blanks: that is what stops sync ever calling a
    # Manual Tap current.
    assert SourceRevision.from_front_matter({"name": "Mine"}) is None
    assert SourceRevision.from_front_matter({"batch_id": "b1"}) is not None
