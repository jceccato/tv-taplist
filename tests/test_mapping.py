"""Mapping: a Brewfather Batch to a Beer, with no network client in sight.

Every test in this file calls the Mapping module with plain dicts - no HTTP
client, no monkeypatching, no network fake. That is the point of the module
existing separately (issue #10): asking "what Beer does this Batch map to?"
needs nothing but the Batch.
"""
import ast
from pathlib import Path

import pytest

from app import config_store, mapping
from app.beer import Beer, SourceRevision


# ---- purity ------------------------------------------------------------

# Everything app/mapping.py is allowed to import, and why the list is closed:
# the module's whole value is that a Batch can be mapped to a Beer with no
# client and no data directory, so an import that opens a socket or touches
# disk would quietly take that back. `config_store` is on the list for one
# constant (MAX_NUM_TAPS, the system bound on tap numbers), which stays with
# the Settings schema deliberately - see issue #10's brief.
_ALLOWED_MAPPING_IMPORTS = {
    "__future__", "logging", "re", "typing",
    ".beer", ".beer_glass", ".colors", ".config_store",
}


def test_mapping_imports_nothing_that_performs_io():
    """Pin Mapping's import list, so its purity cannot erode by accident.

    Reading the import block by eye is not enough to keep this true: the whole
    point of the module is that it can be called with plain data, and the way
    that gets lost is one convenient `from . import tap_store` added later by
    somebody who needed a filename or a cached value.
    """
    tree = ast.parse(Path(mapping.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * node.level
            if node.module:
                imported.add(dots + node.module)
            else:  # `from . import x` names the submodule in the alias
                imported.update(dots + alias.name for alias in node.names)
    assert imported <= _ALLOWED_MAPPING_IMPORTS, imported - _ALLOWED_MAPPING_IMPORTS


# ---- field extraction --------------------------------------------------

def test_slot_claim_variants():
    assert mapping.slot_claim({"batchNotes": "pour on tap:3"}) == 3
    assert mapping.slot_claim({"batchNotes": "Tap: 12 please"}) == 12
    assert mapping.slot_claim({"batchNotes": "no token"}) is None
    assert mapping.slot_claim({"notes": [{"note": "tap:7"}]}) == 7


def test_out_of_range_slot_claim_is_rejected_and_logged(caplog):
    # A mistyped token must not claim a Slot nothing can ever display, and must
    # not pass in silence either - silence is how a mistyped token stays
    # mistyped. The bound is MAX_NUM_TAPS, a system limit, never the operator's
    # configured tap count.
    too_high = config_store.MAX_NUM_TAPS + 1
    batch = {"recipe": {"name": "Fat Fingered Ale"}, "batchNotes": f"tap:{too_high}"}
    with caplog.at_level("WARNING", logger="taplist.sync"):
        assert mapping.slot_claim(batch) is None
    assert "Fat Fingered Ale" in caplog.text
    assert str(too_high) in caplog.text


def test_abv_prefers_measured():
    assert mapping.abv({"measuredAbv": 6.5, "recipe": {"abv": 6.0}}) == 6.5
    assert mapping.abv({"recipe": {"abv": 6.0}}) == 6.0


def test_beer_name_prefers_recipe_over_generic_batch():
    # Brewfather's default batch name is generic; the recipe holds the beer name.
    assert mapping.beer_name({"name": "Batch", "recipe": {"name": "Hazy IPA"}}) == "Hazy IPA"
    assert mapping.beer_name({"name": "Batch #12", "recipe": {"name": "Stout"}}) == "Stout"
    # A user-customised batch name is respected over the recipe name.
    assert mapping.beer_name(
        {"name": "Festbier 2026", "recipe": {"name": "Festbier"}}) == "Festbier 2026"
    # No recipe name -> fall back to the batch number.
    assert mapping.beer_name({"name": "Batch", "batchNo": 7}) == "Batch 7"


def test_zero_stats_are_treated_as_missing():
    # Brewfather sends 0 (not null) for unset values; we store None so the
    # display hides the stat instead of showing a "0".
    assert mapping.abv({"measuredAbv": 0, "recipe": {"abv": 0}}) is None
    assert mapping.ibu({"measuredIbu": 0}) is None
    assert mapping.ebc({"measuredEbc": 0, "estimatedColor": 0}) is None
    # A real value still comes through even when a measured field is 0.
    assert mapping.abv({"measuredAbv": 0, "recipe": {"abv": 5.2}}) == 5.2


def test_description_uses_taste_notes_then_style():
    # A dedicated tasting-note field wins (and any tap token in it is stripped).
    assert mapping.description(
        {"tasteNotes": "Crisp and clean", "batchNotes": "tap:4"}) == "Crisp and clean"
    # No tasting notes -> fall back to the recipe style name.
    assert mapping.description(
        {"batchNotes": "tap:4", "recipe": {"style": {"name": "English Porter"}}}) == "English Porter"
    assert mapping.description(
        {"recipe": {"style": "Cider With Other Fruit"}}) == "Cider With Other Fruit"
    # Batch notes (control data) are NEVER used as the description body.
    assert mapping.description({"batchNotes": "tap:4 brew log text"}) == ""
    # Nothing available -> blank.
    assert mapping.description({"recipe": {}}) == ""


def test_ebc_and_srm():
    # A measured EBC reading is taken at face value.
    assert mapping.ebc({"measuredEbc": 40}) == 40.0
    # estimatedColor / color / recipe.color are SRM -> converted to EBC (*1.97).
    assert mapping.ebc({"estimatedColor": 37.5}) == pytest.approx(73.9, abs=0.05)
    assert mapping.ebc({"recipe": {"color": 25}}) == pytest.approx(49.25, abs=0.06)
    assert mapping.ebc({"srm": 10}) == pytest.approx(19.7, abs=0.05)
    # Measured EBC wins over an estimated SRM colour.
    assert mapping.ebc({"measuredEbc": 30, "estimatedColor": 99}) == 30.0


def test_image_url_handles_null():
    assert mapping.image_url({"recipe": {"img_url": None}}) is None
    assert mapping.image_url({"recipe": {"img_url": "http://x/y.webp"}}) == "http://x/y.webp"


def test_wants_image_is_the_pure_half_of_the_freshness_check():
    # "Does this Batch offer a photo" is a Mapping question; "does the store
    # already hold one" is not, and deliberately does not live here.
    assert mapping.wants_image({"recipe": {"img_url": "http://x/y.webp"}}) is True
    assert mapping.wants_image({"recipe": {}}) is False


def test_saturation_from_notes():
    assert mapping.saturation({"batchNotes": "tap:3 saturation:60"}) == 0.6
    assert mapping.saturation({"batchNotes": "saturation: 0.4"}) == 0.4
    assert mapping.saturation({"batchNotes": "tap:3 only"}) is None


def test_saturation_token_stripped_from_description():
    # A stray saturation token in tasting notes is not shown on the card.
    assert mapping.description(
        {"tasteNotes": "Roasty saturation:70 finish"}) == "Roasty finish"


def test_color_override_token():
    assert mapping.color_override({"batchNotes": "tap:3 colour:#780606"}) == "#780606"
    assert mapping.color_override({"batchNotes": "color: 780606"}) == "#780606"
    assert mapping.color_override({"batchNotes": "tap:3"}) is None


def test_glass_token():
    assert mapping.glass({"batchNotes": "tap:3 glass:nonicpint"}) == "nonicpint"
    assert mapping.glass({"batchNotes": "glass:Teku"}) == "teku"
    assert mapping.glass({"batchNotes": "glass:notaglass"}) is None
    assert mapping.glass({"batchNotes": "tap:3"}) is None


def test_color_and_glass_tokens_stripped_from_description():
    assert mapping.description(
        {"tasteNotes": "Smooth colour:#112233 and glass:tulip pour"}) == "Smooth and pour"


def test_upcoming_token_is_valueless_case_insensitive_and_independent_of_tap():
    # Presence alone is the whole signal - no payload, no ETA.
    assert mapping.is_upcoming({"batchNotes": "upcoming:"}) is True
    assert mapping.is_upcoming({"batchNotes": "UPCOMING: yes please"}) is True
    assert mapping.is_upcoming({"batchNotes": "Upcoming:"}) is True
    assert mapping.is_upcoming({"batchNotes": "no token here"}) is False
    assert mapping.is_upcoming({}) is False
    # A Batch carrying both tokens still reports its Slot claim - `upcoming:`
    # does not override it. The two functions simply answer different
    # questions; neither reads the other.
    both = {"batchNotes": "tap:3 upcoming:"}
    assert mapping.slot_claim(both) == 3
    assert mapping.is_upcoming(both) is True


def test_upcoming_token_stripped_from_description():
    assert mapping.description(
        {"tasteNotes": "Hoppy upcoming: finish"}) == "Hoppy finish"


def test_og_fg_specific_gravity_only():
    assert mapping.og({"measuredOg": 1.052, "recipe": {"og": 1.060}}) == 1.052
    assert mapping.og({"recipe": {"og": 1.060}}) == 1.060
    assert mapping.fg({"measuredFg": 1.010}) == 1.010
    # Unset (0 / 1.0) or out-of-range (Plato-like) values are treated as missing.
    assert mapping.og({"measuredOg": 0, "og": 1.0}) is None
    assert mapping.og({"og": 12.5}) is None
    assert mapping.fg({}) is None


# ---- the whole Beer ----------------------------------------------------

def test_beer_maps_a_batch_with_no_client():
    """One Batch in, one Beer out - no client, no disk, no fake.

    This is the assertion that used to be impossible to make: producing the Tap
    file's fields required an httpx client, because the same function also
    downloaded the photo.
    """
    batch = {
        "_id": "b1",
        "name": "Batch #4",
        "status": "Completed",
        "recipe": {"name": "Hazy IPA", "ibu": 30},
        "measuredAbv": 6.5,
        "measuredEbc": 24,
        "measuredOg": 1.055,
        "measuredFg": 1.012,
        "batchNotes": "tap:3 saturation:60 colour:#445566 glass:tulip",
        "tasteNotes": "Juicy and soft",
        "_timestamp_ms": 1234,
    }
    assert mapping.beer(batch) == Beer(
        name="Hazy IPA",
        abv=6.5,
        ibu=30.0,
        ebc=24.0,
        og=1.055,
        fg=1.012,
        saturation=0.6,
        color_override="#445566",
        glass="tulip",
    )
    # `source`, `image` and `updated` are the store's to write, so a Batch maps
    # to none of them - see docs/adr/0005.
    assert not hasattr(mapping.beer(batch), "source")
    assert not hasattr(mapping.beer(batch), "image")
    assert not hasattr(mapping.beer(batch), "updated")


def test_beer_is_deterministic_for_one_batch():
    # Nothing is read from the clock, the environment or the disk, so the same
    # Batch must map to an equal Beer every time. The clock used to be an
    # argument for exactly this reason; now it is not this function's business.
    batch = {"_id": "b1", "recipe": {"name": "Ale"}, "_timestamp_ms": 7}
    assert mapping.beer(batch) == mapping.beer(batch)


def test_a_batch_maps_to_a_beer_whose_values_are_already_coerced():
    # The Beer that reaches the store is typed, so nothing downstream re-parses
    # a saturation percentage or a hex string out of a dict.
    batch = {"_id": "b1", "recipe": {"name": "Ale"},
             "batchNotes": "tap:1 saturation:60 colour:445566"}
    beer = mapping.beer(batch)
    assert beer.saturation == 0.6
    assert beer.color_override == "#445566"
    assert beer.coerced == ()


# ---- the recipe rule for a provisional (Upcoming) Beer -----------------

def _fermenting_batch_with_measured_and_recipe_values() -> dict:
    """A mid-ferment Batch: measured readings that are only true today, plus a
    recipe that describes the finished beer. Shared by every recipe-rule test
    below so each one exercises the same disagreement between the two."""
    return {
        "_id": "f1",
        "status": "Fermenting",
        "recipe": {
            "name": "Future Stout",
            "abv": 7.0, "ibu": 45, "color": 60, "og": 1.070, "fg": 1.014,
        },
        # Mid-ferment measured values: a lower gravity than the recipe's FG,
        # which is what "still fermenting" means, plus stray estimated/measured
        # fields that must not leak into a provisional Beer.
        "measuredAbv": 2.1,
        "measuredIbu": 45,
        "measuredEbc": 10,
        "measuredOg": 1.070,
        "measuredFg": 1.040,
    }


def test_provisional_beer_from_an_unfinished_batch_uses_the_recipe_for_all_five_together():
    # The all-or-nothing property is the assertion: every one of the five
    # Attributes comes from the recipe, not a mix of measured and recipe
    # fields. This is one test, not five independent field checks.
    batch = _fermenting_batch_with_measured_and_recipe_values()
    beer = mapping.beer(batch, provisional=True)
    assert (beer.abv, beer.ibu, beer.ebc, beer.og, beer.fg) == (
        mapping._recipe_attributes(batch)["abv"],
        mapping._recipe_attributes(batch)["ibu"],
        mapping._recipe_attributes(batch)["ebc"],
        mapping._recipe_attributes(batch)["og"],
        mapping._recipe_attributes(batch)["fg"],
    )
    # None of the measured/mid-ferment values leaked through.
    assert beer.abv != 2.1
    assert beer.fg != 1.040
    assert beer.ebc != round(10, 1)


@pytest.mark.parametrize("status", ["Completed", "Conditioning"])
def test_recipe_rule_does_not_apply_to_a_completed_or_conditioning_batch(status):
    batch = _fermenting_batch_with_measured_and_recipe_values()
    batch["status"] = status
    provisional = mapping.beer(batch, provisional=True)
    measured = mapping.beer(batch, provisional=False)
    assert provisional == measured


def test_non_provisional_beer_keeps_measured_first_behaviour_for_every_status_including_fermenting():
    # This is the test that fails if someone makes the recipe rule
    # unconditional: asking for a non-provisional (pouring-Tap) Beer must give
    # today's measured-first answer for every status, Fermenting included -
    # otherwise a fermenting Batch with tap:X would render differently the
    # instant this ticket lands, even with the Upcoming feature off.
    for status in ("Fermenting", "Brewing", "Planning", "Conditioning", "Completed"):
        batch = _fermenting_batch_with_measured_and_recipe_values()
        batch["status"] = status
        assert mapping.beer(batch, provisional=False) == mapping.beer(batch)
        assert mapping.beer(batch).abv == mapping.abv(batch)
        assert mapping.beer(batch).fg == mapping.fg(batch)


def test_mapping_version_is_7():
    # The single bump for the whole of issue #4 (issue #35): the upcoming:
    # token, the recipe rule and batch_status all land under one rewrite.
    assert mapping.MAPPING_VERSION == 7


def test_is_current_compares_batch_revision_and_mapping_version():
    batch = {"_id": "b1", "_timestamp_ms": 500}
    cached = mapping.source_revision(batch, 500)
    assert mapping.is_current(cached, batch, 500) is True
    # A newer revision of the same Batch is not current.
    assert mapping.is_current(cached, batch, 900) is False
    # A different Batch on the same Slot is not current.
    assert mapping.is_current(cached, {"_id": "b2", "_timestamp_ms": 500}, 500) is False


def test_is_current_is_false_for_a_tap_with_no_revision_record():
    # What a Manual Tap looks like: no record at all. It is not a cache of
    # anything, so it can never be current with a Batch.
    assert mapping.is_current(None, {"_id": "b1", "_timestamp_ms": 500}, 500) is False


def test_is_current_is_false_for_a_tap_cached_at_an_older_mapping_version():
    # The whole reason MAPPING_VERSION exists: a cached Tap written by older
    # extraction logic must be rewritten even though its Batch never changed.
    batch = {"_id": "b1", "_timestamp_ms": 500}
    cached = SourceRevision(batch_id="b1", source_rev=500,
                            map_rev=mapping.MAPPING_VERSION - 1)
    assert mapping.is_current(cached, batch, 500) is False


def test_is_current_tolerates_yaml_reparsing_the_stored_values():
    # The cached side comes back from YAML, which may have parsed a numeric id
    # or revision into an int; both sides are compared as strings.
    batch = {"_id": 42, "_timestamp_ms": 500}
    cached = SourceRevision(batch_id="42", source_rev="500",
                            map_rev=str(mapping.MAPPING_VERSION))
    assert mapping.is_current(cached, batch, 500) is True


# ---- desired map / conflict resolution ---------------------------------

def test_conflict_newest_wins():
    batches = [
        {"_id": "a", "name": "Old", "status": "Completed", "batchNotes": "tap:3", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "Completed", "batchNotes": "tap:3", "updated": 200},
    ]
    assert mapping.desired_map(batches)[3]["batch"]["name"] == "New"


def test_conflict_completed_beats_newer_conditioning():
    # The beer that is pouring must not be pushed off its Slot by the next brew
    # that already carries the same token - and a conditioning Batch is edited
    # far more often than a finished one, so recency alone picks the wrong beer.
    batches = [
        {"_id": "a", "name": "Pouring", "status": "Completed",
         "batchNotes": "tap:3", "_timestamp_ms": 100},
        {"_id": "b", "name": "NextBrew", "status": "Conditioning",
         "batchNotes": "tap:3", "_timestamp_ms": 900},
    ]
    assert mapping.desired_map(batches)[3]["batch"]["name"] == "Pouring"
    # Order of arrival must not matter: the same pair reversed resolves the same.
    assert mapping.desired_map(
        list(reversed(batches)))[3]["batch"]["name"] == "Pouring"


def test_conflict_conditioning_beats_newer_fermenting():
    batches = [
        {"_id": "a", "name": "Conditioning", "status": "Conditioning",
         "batchNotes": "tap:6", "_timestamp_ms": 100},
        {"_id": "b", "name": "Fermenting", "status": "Fermenting",
         "batchNotes": "tap:6", "_timestamp_ms": 900},
    ]
    assert mapping.desired_map(batches)[6]["batch"]["name"] == "Conditioning"


def test_conflict_within_one_status_still_resolves_by_recency():
    # Status only orders DIFFERENT statuses; inside one, newest still wins.
    batches = [
        {"_id": "a", "name": "Old", "status": "Conditioning",
         "batchNotes": "tap:2", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "Conditioning",
         "batchNotes": "tap:2", "_timestamp_ms": 200},
    ]
    assert mapping.desired_map(batches)[2]["batch"]["name"] == "New"
    assert mapping.desired_map(
        list(reversed(batches)))[2]["batch"]["name"] == "New"


def test_conflict_unknown_status_loses_to_a_known_one():
    # An unlabelled Batch ranks below every status the API does name, however
    # recent it is - we cannot tell how far along it is, so it does not win.
    batches = [
        {"_id": "a", "name": "Fermenting", "status": "Fermenting",
         "batchNotes": "tap:4", "_timestamp_ms": 100},
        {"_id": "b", "name": "Unlabelled", "batchNotes": "tap:4",
         "_timestamp_ms": 900},
    ]
    assert mapping.desired_map(batches)[4]["batch"]["name"] == "Fermenting"
    assert mapping.desired_map(
        list(reversed(batches)))[4]["batch"]["name"] == "Fermenting"


def test_conflict_all_unknown_status_falls_back_to_recency():
    # If Brewfather ever stops sending `status`, everything ties on rank and
    # resolution degrades to the newest-wins behaviour that shipped before.
    batches = [
        {"_id": "a", "name": "Old", "batchNotes": "tap:5", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "", "batchNotes": "tap:5",
         "_timestamp_ms": 200},
    ]
    assert mapping.desired_map(batches)[5]["batch"]["name"] == "New"


def test_status_rank_orders_the_whole_lifecycle():
    ranks = [mapping.status_rank({"status": s})
             for s in ("Completed", "Conditioning", "Fermenting", "Brewing", "Planning")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    # Case and stray whitespace from the API must not demote a Batch.
    assert mapping.status_rank({"status": " completed "}) == \
        mapping.status_rank({"status": "Completed"})
    # Missing, empty, non-string and unrecognised statuses all rank last.
    for batch in ({}, {"status": ""}, {"status": None}, {"status": "Archived"}):
        assert mapping.status_rank(batch) == len(mapping.STATUS_PRECEDENCE)


def test_no_tap_token_is_ignored():
    assert mapping.desired_map([{"_id": "a", "status": "Completed", "batchNotes": "x"}]) == {}
