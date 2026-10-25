"""Display profiles (issue #51, ADR-0007): stored differences, resolved server-side."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import config_store, paths, snapshot
from app.config_store import (CHOSEN_DEFAULT, CHOSEN_UNKNOWN, CHOSEN_URL,
                              resolve_effective)
from app.main import app

client = TestClient(app)


def _write_profiles(profiles, **settings):
    """Put profiles in config.json the way a hand edit would (uncoerced)."""
    raw = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
    raw.update(settings)
    raw["profiles"] = profiles
    paths.CONFIG_PATH.write_text(json.dumps(raw), encoding="utf-8")


def _rotation(path="/api/board"):
    return client.get(path).json()["rotation_seconds"]


# ---- a profile stores only its differences -------------------------------

def test_a_profile_overrides_only_what_it_customises():
    _write_profiles({"tv": {"rotation_seconds": 10}}, rotation_seconds=30, hide_vacant_taps=True)
    assert _rotation("/api/board?display=tv") == 10
    board = client.get("/api/board?display=tv").json()
    assert board["hide_vacant_taps"] is True       # followed
    assert _rotation() == 30                        # the Default profile is untouched


def test_a_followed_setting_tracks_the_default_after_it_changes():
    _write_profiles({"tv": {"hide_vacant_taps": False}}, rotation_seconds=30)
    assert _rotation("/api/board?display=tv") == 30
    config_store.update_config(rotation_seconds=45)
    assert _rotation("/api/board?display=tv") == 45
    assert _rotation() == 45


def test_saved_settings_keep_the_profiles():
    _write_profiles({"tv": {"rotation_seconds": 10}})
    config_store.update_config(announcement_text="Hello")
    assert config_store.load_config()["profiles"] == {"tv": {"rotation_seconds": 10}}


def test_the_board_never_carries_the_profiles():
    _write_profiles({"tv": {"rotation_seconds": 10}})
    assert "profiles" not in client.get("/api/board?display=tv").json()


# ---- one-unit groups ------------------------------------------------------

def test_a_half_written_unit_coerces_to_followed():
    _write_profiles({"tv": {"paginate": True}}, page_size=4, paginate=False)
    assert config_store.load_config()["profiles"]["tv"] == {}
    board = client.get("/api/board?display=tv").json()
    assert board["paginate"] is False and board["page_size"] == 4


def test_a_whole_unit_is_customised_together():
    _write_profiles({"tv": {"paginate": True, "page_size": 3}}, page_size=4, paginate=False)
    board = client.get("/api/board?display=tv").json()
    assert board["paginate"] is True and board["page_size"] == 3


def test_a_half_theme_follows_the_default_theme():
    _write_profiles({"tv": {"theme": "oled"}}, theme="default")
    assert config_store.load_config()["profiles"]["tv"] == {}


def test_a_whole_theme_unit_survives():
    unit = {"theme": "custom", "theme_custom": dict(config_store.DEFAULT_CONFIG["theme_custom"])}
    _write_profiles({"tv": unit})
    assert config_store.load_config()["profiles"]["tv"]["theme"] == "custom"


# ---- clamping and dropping ------------------------------------------------

def test_profile_values_are_clamped_to_the_settings_bounds():
    lo, hi = config_store.SETTINGS_BOUNDS["rotation_seconds"]
    _write_profiles({"low": {"rotation_seconds": 0}, "high": {"rotation_seconds": 10**6}})
    assert _rotation("/api/board?display=low") == lo
    assert _rotation("/api/board?display=high") == hi
    _, phi = config_store.SETTINGS_BOUNDS["page_size"]
    _write_profiles({"p": {"paginate": True, "page_size": 999}})
    assert client.get("/api/board?display=p").json()["page_size"] == phi


def test_non_customisable_keys_are_dropped():
    _write_profiles({"tv": {"num_taps": 9, "brewfather_api_key": "leak", "color_unit": "srm",
                            "profiles": {"x": {}}, "nonsense": 1, "rotation_seconds": 12}},
                    num_taps=2)
    assert config_store.load_config()["profiles"]["tv"] == {"rotation_seconds": 12}
    assert client.get("/api/board?display=tv").json()["rotation_seconds"] == 12


def test_invalid_names_are_dropped_and_the_box_boots():
    long_name = "a" * 33
    _write_profiles({"Bad Name": {"rotation_seconds": 5}, "under_score": {}, "": {},
                     long_name: {}, "default": {"rotation_seconds": 5},
                     "ok-1": {"rotation_seconds": 7}, "junk": "not a dict"})
    assert set(config_store.load_config()["profiles"]) == {"ok-1", "junk"}
    assert client.get("/api/board").status_code == 200


def test_a_profiles_value_that_is_not_an_object_is_ignored():
    _write_profiles(["tv"])
    assert config_store.load_config()["profiles"] == {}
    _write_profiles("tv")
    assert config_store.load_config()["profiles"] == {}


# ---- names ------------------------------------------------------------------

def test_a_name_is_matched_regardless_of_case():
    _write_profiles({"lounge-tv": {"rotation_seconds": 11}})
    assert _rotation("/api/board?display=Lounge-TV") == 11
    assert config_store.unknown_profile_names() == []


def test_a_stored_name_is_lowercased():
    _write_profiles({"Lounge-TV": {"rotation_seconds": 11}})
    assert list(config_store.load_config()["profiles"]) == ["lounge-tv"]


def test_a_32_character_name_is_valid():
    ok = "a" * 32
    _write_profiles({ok: {"rotation_seconds": 11}})
    assert list(config_store.load_config()["profiles"]) == [ok]


# ---- unknown names ----------------------------------------------------------

def test_no_parameter_gives_the_default_and_records_nothing():
    _write_profiles({"tv": {"rotation_seconds": 10}}, rotation_seconds=30)
    assert _rotation() == 30
    assert config_store.unknown_profile_names() == []


def test_an_unknown_name_gives_the_default_and_is_recorded():
    _write_profiles({"tv": {"rotation_seconds": 10}}, rotation_seconds=30)
    assert _rotation("/api/board?display=tablet") == 30
    assert _rotation("/api/board?display=Tablet") == 30
    assert config_store.unknown_profile_names() == ["tablet"]


def test_an_invalid_name_gives_the_default_and_is_recorded():
    assert _rotation("/api/board?display=bad%20name!") == _rotation()
    assert config_store.unknown_profile_names() == ["bad name!"]


def test_an_empty_display_parameter_is_the_default_and_not_unknown():
    assert client.get("/api/board?display=").status_code == 200
    assert config_store.unknown_profile_names() == []


def test_the_unknown_list_is_bounded_and_keeps_the_most_recent():
    for i in range(config_store.MAX_UNKNOWN_PROFILE_NAMES + 10):
        config_store.record_unknown_profile(f"n{i}")
    names = config_store.unknown_profile_names()
    assert len(names) == config_store.MAX_UNKNOWN_PROFILE_NAMES
    assert names[-1] == f"n{config_store.MAX_UNKNOWN_PROFILE_NAMES + 9}"
    assert "n0" not in names


def test_a_huge_unknown_name_is_truncated_when_recorded():
    config_store.record_unknown_profile("x" * 10_000)
    assert len(config_store.unknown_profile_names()[0]) <= 64


def test_a_deleted_profile_sends_its_screen_to_the_default():
    _write_profiles({"tv": {"rotation_seconds": 10}}, rotation_seconds=30)
    assert _rotation("/api/board?display=tv") == 10
    config_store.update_config(profiles={})
    assert _rotation("/api/board?display=tv") == 30
    assert config_store.unknown_profile_names() == ["tv"]


# ---- the pure resolution function ------------------------------------------

def _settings(**profiles):
    return {**config_store.DEFAULT_CONFIG, "profiles": profiles}


def test_resolve_reports_how_the_profile_was_chosen():
    s = _settings(tv={"rotation_seconds": 10})
    r = resolve_effective(s, "TV")
    assert (r.profile, r.chosen, r.settings["rotation_seconds"]) == ("tv", CHOSEN_URL, 10)
    r = resolve_effective(s, None)
    assert (r.profile, r.chosen) == ("default", CHOSEN_DEFAULT)
    r = resolve_effective(s, "nope")
    assert (r.profile, r.chosen) == ("default", CHOSEN_UNKNOWN)
    assert r.settings["rotation_seconds"] == config_store.DEFAULT_CONFIG["rotation_seconds"]


def test_resolve_does_not_mutate_its_input():
    s = _settings(tv={"rotation_seconds": 10})
    before = json.dumps(s, sort_keys=True)
    resolve_effective(s, "tv")
    assert json.dumps(s, sort_keys=True) == before


def test_naming_the_default_profile_is_the_default_and_not_unknown():
    r = resolve_effective(_settings(), "Default")
    assert (r.profile, r.chosen) == ("default", CHOSEN_DEFAULT)
    client.get("/api/board?display=default")
    assert config_store.unknown_profile_names() == []


# ---- snapshots --------------------------------------------------------------

def test_profiles_survive_a_snapshot_round_trip():
    config_store.update_config(profiles={
        "tv": {"rotation_seconds": 10, "paginate": True, "page_size": 3}, "tablet": {}})
    data = b"".join(snapshot.stream_snapshot(snapshot.settings_bytes(),
                                              snapshot.enumerate_entries()))
    config_store.update_config(profiles={})
    assert config_store.load_config()["profiles"] == {}

    snapshot.STAGED_UPLOAD_PATH.write_bytes(data)
    snapshot.import_snapshot(snapshot.STAGED_UPLOAD_PATH)
    assert config_store.load_config()["profiles"] == {
        "tv": {"rotation_seconds": 10, "paginate": True, "page_size": 3}, "tablet": {}}
    assert _rotation("/api/board?display=tv") == 10


# ---- the display page -------------------------------------------------------

_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "display.js"

def test_display_js_forwards_display_on_every_board_poll():
    js = _JS.read_text(encoding="utf-8")
    assert js.count("/api/board") == 2, "one fetch URL, built once"
    assert 'get("display")' in js
    assert "fetch(BOARD_URL" in js


def test_display_js_holds_no_profile_logic():
    """The page forwards the name untouched: no profile table, no merging."""
    js = _JS.read_text(encoding="utf-8")
    assert "profiles" not in js
    assert "toLowerCase" not in js.split("BOARD_URL")[1].split("DEFAULT_SETTINGS")[0]
