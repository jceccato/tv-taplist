"""User-Agent fallback and recently seen screens (issue #54, ADR-0007)."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import admin_ops, config_store, paths, seen_screens
from app.config_store import (CHOSEN_DEFAULT, CHOSEN_UNKNOWN, CHOSEN_URL,
                              CHOSEN_USER_AGENT, PROFILE_UA_KEY, resolve_effective)
from app.main import app

client = TestClient(app)

LG = "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 Chrome/94 Safari/537.36 WebAppManager"
TIZEN = "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.5) AppleWebKit/537.36"


def _profiles(profiles):
    """Put profiles in config.json the way a hand edit would."""
    raw = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["profiles"] = profiles
    paths.CONFIG_PATH.write_text(json.dumps(raw), encoding="utf-8")


def _rotation(display=None, ua=None):
    headers = {"User-Agent": ua} if ua is not None else {}
    url = "/api/board" + (f"?display={display}" if display else "")
    return client.get(url, headers=headers).json()["rotation_seconds"]


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


# ---- matching, through /api/board ----------------------------------------------

def test_a_matching_user_agent_picks_the_profile():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "web0s"}})
    assert _rotation(ua=LG) == 11                      # case-insensitive substring
    assert _rotation(ua=TIZEN) == config_store.DEFAULT_CONFIG["rotation_seconds"]


def test_no_match_gives_the_default_profile():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "Web0S"}})
    default = config_store.DEFAULT_CONFIG["rotation_seconds"]
    assert _rotation(ua="Mozilla/5.0 (Linux; Android 13) Chrome/120") == default
    assert _rotation(ua="") == default


def test_the_url_beats_a_user_agent_match():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "Web0S"},
               "bar": {"rotation_seconds": 22}})
    assert _rotation(display="bar", ua=LG) == 22
    assert _rotation(ua=LG) == 11


def test_naming_the_default_profile_beats_a_user_agent_match():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "Web0S"}})
    assert _rotation(display="default", ua=LG) == config_store.DEFAULT_CONFIG["rotation_seconds"]


def test_an_unknown_name_gets_the_default_not_the_user_agent_match():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "Web0S"}})
    assert _rotation(display="lounje", ua=LG) == config_store.DEFAULT_CONFIG["rotation_seconds"]
    assert config_store.unknown_profile_names() == ["lounje"]


def test_the_first_matching_profile_wins():
    _profiles({"first": {"rotation_seconds": 11, PROFILE_UA_KEY: "SmartTV"},
               "second": {"rotation_seconds": 22, PROFILE_UA_KEY: "Web0S"}})
    assert _rotation(ua=LG) == 11
    _profiles({"second": {"rotation_seconds": 22, PROFILE_UA_KEY: "Web0S"},
               "first": {"rotation_seconds": 11, PROFILE_UA_KEY: "SmartTV"}})
    assert _rotation(ua=LG) == 22


def test_resolve_effective_says_how_the_user_agent_chose():
    settings = config_store.load_config()
    settings["profiles"] = {"lounge": {PROFILE_UA_KEY: "Tizen"}}
    got = resolve_effective(settings, None, TIZEN)
    assert (got.profile, got.chosen) == ("lounge", CHOSEN_USER_AGENT)
    assert resolve_effective(settings, "lounge", TIZEN).chosen == CHOSEN_URL
    assert resolve_effective(settings, "nope", TIZEN).chosen == CHOSEN_UNKNOWN
    assert resolve_effective(settings, None, LG).chosen == CHOSEN_DEFAULT
    assert resolve_effective(settings, None).chosen == CHOSEN_DEFAULT


def test_the_match_text_never_reaches_the_effective_settings_or_the_board():
    _profiles({"lounge": {"rotation_seconds": 11, PROFILE_UA_KEY: "Web0S"}})
    settings = config_store.load_config()
    assert PROFILE_UA_KEY not in resolve_effective(settings, "lounge").settings
    assert PROFILE_UA_KEY not in client.get("/api/board", headers={"User-Agent": LG}).json()


# ---- storing it -----------------------------------------------------------------

def test_the_text_is_trimmed_capped_and_blank_means_none():
    _profiles({"a": {PROFILE_UA_KEY: "  Web0S \n SmartTV "}, "b": {PROFILE_UA_KEY: "   "},
               "c": {PROFILE_UA_KEY: "x" * 500}, "d": {PROFILE_UA_KEY: 7}})
    stored = config_store.load_config()["profiles"]
    assert stored["a"][PROFILE_UA_KEY] == "Web0S SmartTV"
    assert PROFILE_UA_KEY not in stored["b"] and PROFILE_UA_KEY not in stored["d"]
    assert len(stored["c"][PROFILE_UA_KEY]) == config_store.MAX_PROFILE_UA_LEN


def test_save_profile_stores_changes_keeps_and_clears_the_text():
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise=set(), values={}, user_agent=" Web0S ")
    assert config_store.load_config()["profiles"]["tv"] == {PROFILE_UA_KEY: "Web0S"}
    # Not given: left alone, whatever else is saved.
    admin_ops.save_profile("tv", customise={"rotation_seconds"}, values={"rotation_seconds": 9})
    assert config_store.load_config()["profiles"]["tv"] == {
        "rotation_seconds": 9, PROFILE_UA_KEY: "Web0S"}
    admin_ops.save_profile("tv", customise=set(), values={}, user_agent="")
    assert config_store.load_config()["profiles"]["tv"] == {}


def test_a_rename_keeps_the_text_and_a_default_save_keeps_it():
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise=set(), values={}, user_agent="Web0S")
    admin_ops.rename_profile("tv", "lounge")
    config_store.update_config(announcement_text="Hi")
    assert config_store.load_config()["profiles"]["lounge"][PROFILE_UA_KEY] == "Web0S"


def test_the_editor_saves_the_text_over_http():
    admin_ops.create_profile("tv")
    c = _admin()
    html = c.get("/admin?profile=tv").text
    assert re.search(r'name="match_user_agent" form="settings-form"', html)
    form = {"profile": "tv", "num_taps": "1", "max_archive_age_days": "1",
            "max_archive_storage_mb": "1", "match_user_agent": "Tizen"}
    assert c.post("/admin/settings", data=form).status_code == 200
    assert config_store.load_config()["profiles"]["tv"][PROFILE_UA_KEY] == "Tizen"
    assert 'value="Tizen"' in c.get("/admin?profile=tv").text
    # Another profile's card shows it as text, not as a second field.
    admin_ops.create_profile("other")
    page = c.get("/admin?profile=other").text
    assert page.count('name="match_user_agent"') == 1
    assert "screens whose User-Agent contains <code>Tizen</code>" in page


# ---- recently seen screens ---------------------------------------------------------

def test_a_board_request_is_listed_with_its_profile_and_how():
    _profiles({"lounge": {PROFILE_UA_KEY: "Web0S"}, "bar": {}})
    _rotation(ua=LG)
    _rotation(display="bar", ua="Kiosk/1.0")
    _rotation(display="oops", ua="Kiosk/1.0")
    _rotation(ua="Plain/1.0")
    seen = {(s.user_agent, s.profile, s.chosen) for s in seen_screens.seen_screens()}
    assert seen == {(LG, "lounge", CHOSEN_USER_AGENT), ("Kiosk/1.0", "bar", CHOSEN_URL),
                    ("Kiosk/1.0", "default", CHOSEN_UNKNOWN),
                    ("Plain/1.0", "default", CHOSEN_DEFAULT)}


def test_the_list_is_bounded_most_recent_first_and_deduplicated():
    for i in range(seen_screens.MAX_SEEN_SCREENS + 10):
        seen_screens.record_seen_screen(f"ua-{i}", "default", CHOSEN_DEFAULT, now=1000 + i)
    seen_screens.record_seen_screen("ua-30", "default", CHOSEN_DEFAULT, now=5000)
    rows = seen_screens.seen_screens()
    assert len(rows) == seen_screens.MAX_SEEN_SCREENS
    assert rows[0].user_agent == "ua-30" and rows[0].last_seen == 5000
    assert [r.user_agent for r in rows].count("ua-30") == 1
    assert "ua-0" not in [r.user_agent for r in rows]


def test_a_missing_or_huge_user_agent_is_recorded_safely():
    seen_screens.record_seen_screen(None, "default", CHOSEN_DEFAULT)
    seen_screens.record_seen_screen("z" * 5000, "default", CHOSEN_DEFAULT)
    lengths = sorted(len(s.user_agent) for s in seen_screens.seen_screens())
    assert lengths == [len("(none sent)"), 300]


def test_the_admin_lists_seen_screens_and_copies_the_user_agent():
    _rotation(ua=LG)
    html = _admin().get("/admin").text
    assert "Recently seen screens" in html
    assert "Web0S; Linux/SmartTV" in html
    assert f'data-copy="{LG}"' in html
    assert "Got <strong>default</strong>" in html


def test_the_admin_says_when_nothing_was_seen():
    assert "No screen has loaded the board since the last restart." in _admin().get("/admin").text


def test_the_admin_warns_when_two_profiles_match_a_seen_screen():
    _profiles({"first": {PROFILE_UA_KEY: "SmartTV"}, "second": {PROFILE_UA_KEY: "web0s"},
               "third": {PROFILE_UA_KEY: "Tizen"}})
    _rotation(ua=LG)
    html = _admin().get("/admin").text
    assert "all match the User-Agent" in html
    assert "The first in the list, <strong>first</strong>, wins" in html


def test_no_warning_when_only_one_profile_matches():
    _profiles({"first": {PROFILE_UA_KEY: "SmartTV"}, "second": {PROFILE_UA_KEY: "Tizen"}})
    _rotation(ua=LG)
    assert "all match the User-Agent" not in _admin().get("/admin").text


def test_seen_screens_are_never_written_to_disk():
    _rotation(ua="Distinct-Marker-UA/9")
    for path in (paths.CONFIG_PATH, paths.STATUS_PATH):
        if path.exists():
            assert "Distinct-Marker-UA" not in path.read_text(encoding="utf-8")
    for file in Path(paths.DATA_DIR).rglob("*"):
        if file.is_file():
            assert b"Distinct-Marker-UA" not in file.read_bytes(), file


# ---- the kiosk script and the server share one name rule -----------------------------

def test_the_kiosk_script_and_the_server_agree_on_the_profile_name_rule():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "pi-kiosk.sh").read_text()
    found = re.findall(r'=~\s*(\^\[a-z0-9-\]\{1,\d+\}\$)', script)
    assert found, "pi-kiosk.sh no longer has the profile-name pattern this test reads"
    assert set(found) == {config_store._PROFILE_NAME_RE.pattern}
