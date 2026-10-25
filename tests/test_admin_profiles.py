"""The Admin's Display profile editor over HTTP (issue #52, ADR-0007)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import admin_ops, config_store, main, paths
from app.main import app

REQUIRED = {"num_taps": "1", "max_archive_age_days": "1", "max_archive_storage_mb": "1"}


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


def _on_disk():
    return json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))


def _bool_flags():
    """Every boolean on the Settings form that a profile may customise."""
    return [n for n, f in main.SettingsForm.model_fields.items()
            if f.annotation is bool and n in config_store.CUSTOMISABLE_KEYS]


def _customise_all():
    return {f"customise_{u}": "true" for u in config_store.CUSTOMISABLE_UNITS}


# ---- saving one profile -------------------------------------------------

def test_a_profile_save_writes_only_that_profile():
    admin_ops.create_profile("tv")
    admin_ops.create_profile("tablet")
    default_before = {k: v for k, v in _on_disk().items() if k != "profiles"}
    c = _admin()
    r = c.post("/admin/settings", data={
        **REQUIRED, "profile": "tv", "customise_rotation_seconds": "true",
        "rotation_seconds": "11", "announcement_text": "typed but followed"})
    assert r.status_code == 200
    disk = _on_disk()
    assert disk["profiles"] == {"tv": {"rotation_seconds": 11}, "tablet": {}}
    assert {k: v for k, v in disk.items() if k != "profiles"} == default_before


def test_a_profile_save_never_touches_a_venue_wide_setting():
    admin_ops.create_profile("tv")
    config_store.update_config(num_taps=5, color_unit="ebc")
    _admin().post("/admin/settings", data={
        **REQUIRED, "num_taps": "9", "color_unit": "srm", "profile": "tv",
        **_customise_all()})
    disk = _on_disk()
    assert disk["num_taps"] == 5 and disk["color_unit"] == "ebc"
    assert not set(disk["profiles"]["tv"]) & config_store.NEVER_CUSTOMISABLE


def test_follow_drops_the_value_on_save():
    admin_ops.create_profile("tv")
    c = _admin()
    c.post("/admin/settings", data={**REQUIRED, "profile": "tv",
                                    "customise_rotation_seconds": "true",
                                    "rotation_seconds": "11"})
    assert _on_disk()["profiles"]["tv"] == {"rotation_seconds": 11}
    c.post("/admin/settings", data={**REQUIRED, "profile": "tv",
                                    "customise_rotation_seconds": "false",
                                    "rotation_seconds": "11"})
    assert _on_disk()["profiles"]["tv"] == {}


def test_a_default_save_keeps_every_profile():
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9})
    _admin().post("/admin/settings", data={**REQUIRED, "rotation_seconds": "40"})
    assert _on_disk()["rotation_seconds"] == 40
    assert _on_disk()["profiles"]["tv"] == {"rotation_seconds": 9}


def test_naming_the_default_profile_saves_the_default_profile():
    _admin().post("/admin/settings", data={**REQUIRED, "profile": "default",
                                           "rotation_seconds": "41"})
    assert _on_disk()["rotation_seconds"] == 41


def test_saving_a_profile_that_does_not_exist_is_refused():
    r = _admin().post("/admin/settings", data={**REQUIRED, "profile": "ghost",
                                               "customise_rotation_seconds": "true"})
    assert r.status_code == 422
    assert _on_disk()["profiles"] == {}


def test_every_unchecked_checkbox_saves_false_in_a_customised_profile():
    flags = _bool_flags()
    assert len(flags) >= 10
    admin_ops.create_profile("tv")
    data = {**REQUIRED, "profile": "tv", **_customise_all(),
            **{f: "false" for f in flags}}
    assert _admin().post("/admin/settings", data=data).status_code == 200
    stored = _on_disk()["profiles"]["tv"]
    for flag in flags:
        assert stored[flag] is False, f"{flag} saved as {stored.get(flag)!r}"


def test_every_checked_checkbox_saves_true_in_a_customised_profile():
    flags = _bool_flags()
    admin_ops.create_profile("tv")
    data = {**REQUIRED, "profile": "tv", **_customise_all(),
            **{f: "true" for f in flags}}
    assert _admin().post("/admin/settings", data=data).status_code == 200
    stored = _on_disk()["profiles"]["tv"]
    for flag in flags:
        assert stored[flag] is True, f"{flag} saved as {stored.get(flag)!r}"


def test_a_followed_checkbox_is_dropped_whatever_it_posts():
    flags = _bool_flags()
    admin_ops.create_profile("tv")
    data = {**REQUIRED, "profile": "tv", **{f: "true" for f in flags}}
    _admin().post("/admin/settings", data=data)
    assert _on_disk()["profiles"]["tv"] == {}


def test_a_profile_save_carries_the_theme_palette():
    admin_ops.create_profile("tv")
    data = {**REQUIRED, "profile": "tv", "customise_theme": "true", "theme": "custom",
            "theme_bg": "#112233"}
    _admin().post("/admin/settings", data=data)
    stored = _on_disk()["profiles"]["tv"]
    assert stored["theme"] == "custom"
    assert stored["theme_custom"]["bg"] == "#112233"


# ---- create, rename, delete -------------------------------------------------

def test_the_profile_routes_need_the_admin_login():
    c = TestClient(app)
    assert c.post("/admin/profiles", data={"name": "tv"}).status_code == 401
    assert c.post("/admin/profiles/tv/rename", data={"new_name": "x"}).status_code == 401
    assert c.post("/admin/profiles/tv/delete").status_code == 401
    assert _on_disk()["profiles"] == {}


def test_create_rename_and_delete_over_http():
    c = _admin()
    assert c.post("/admin/profiles", data={"name": "Tv"}).json() == {"ok": True, "name": "tv"}
    assert c.post("/admin/profiles", data={"name": "tv"}).status_code == 422
    assert c.post("/admin/profiles", data={"name": "default"}).status_code == 422
    assert c.post("/admin/profiles/tv/rename", data={"new_name": "lounge"}).json()["name"] == "lounge"
    assert list(_on_disk()["profiles"]) == ["lounge"]
    assert c.post("/admin/profiles/default/rename", data={"new_name": "x"}).status_code == 422
    assert c.post("/admin/profiles/default/delete").status_code == 422
    assert c.post("/admin/profiles/lounge/delete").status_code == 200
    assert _on_disk()["profiles"] == {}


# ---- the page ------------------------------------------------------------------

def test_the_default_page_lists_each_profile_with_its_start_url_and_customisations():
    admin_ops.create_profile("tv")
    admin_ops.create_profile("tablet")
    admin_ops.save_profile("tv", customise={"rotation_seconds", "theme"},
                           values={"rotation_seconds": 9, "theme": "default",
                                   "theme_custom": {}})
    html = _admin().get("/admin", headers={"host": "taps.lan:8080"}).text
    assert "http://taps.lan:8080/?display=tv" in html
    assert "http://taps.lan:8080/?display=tablet" in html
    assert "Customises: Theme and colours, Page rotation time." in html
    assert "Follows the Default profile in everything." in html
    # The Default profile editor has no switches and keeps the global tabs.
    assert "unit-customise" not in html
    assert 'data-tab="overrides"' in html and 'data-tab="snapshot"' in html


def test_the_start_url_follows_the_host_the_admin_was_reached_on():
    admin_ops.create_profile("tv")
    html = _admin().get("/admin", headers={"host": "192.168.1.50"}).text
    assert "http://192.168.1.50/?display=tv" in html


def test_the_page_lists_unknown_names_asked_for_since_restart():
    client = TestClient(app)
    client.get("/api/board?display=lounge-tvv")
    html = _admin().get("/admin").text
    assert "lounge-tvv" in html


def test_a_profile_page_shows_the_defaults_greyed_value_and_the_switches():
    config_store.update_config(rotation_seconds=45, announcement_text="Default words")
    admin_ops.create_profile("tv")
    admin_ops.save_profile("tv", customise={"rotation_seconds"},
                           values={"rotation_seconds": 9})
    html = _admin().get("/admin?profile=tv").text
    # Customised: its own value, switch on. Followed: the Default's value, switch off.
    assert re.search(r'name="rotation_seconds"[^>]*value="9"', html)
    assert re.search(r'name="customise_rotation_seconds" checked', html)
    assert "Default words" in html
    assert re.search(r'name="customise_announcement_text" >', html)
    assert 'name="profile" value="tv"' in html
    # Editing state is clear, and the defaults travel with the page.
    assert "Editing the tv profile" in html
    defaults = json.loads(re.search(
        r'id="profile-defaults">(.*?)</script>', html, re.S).group(1))
    assert defaults["rotation_seconds"] == 45


def test_every_customisable_unit_has_a_switch_on_the_profile_page():
    admin_ops.create_profile("tv")
    html = _admin().get("/admin?profile=tv").text
    for unit in config_store.CUSTOMISABLE_UNITS:
        assert f'data-unit="{unit}"' in html, f"{unit} has no control on the editor"
        assert f'name="customise_{unit}"' in html


def test_every_unit_has_a_label_for_the_customised_list():
    missing = set(config_store.CUSTOMISABLE_UNITS) - set(main.PROFILE_UNIT_LABELS)
    assert not missing, f"add labels in main.PROFILE_UNIT_LABELS: {sorted(missing)}"


def test_global_only_sections_are_hidden_while_editing_a_profile():
    admin_ops.create_profile("tv")
    c = _admin()
    page = c.get("/admin?profile=tv").text
    assert "apply to every screen" in page
    assert 'data-tab="overrides"' not in page and 'data-tab="snapshot"' not in page
    assert 'id="tab-overrides"' not in page and 'id="tab-snapshot"' not in page
    # The Brewfather connection stays in the markup (the Save still needs the
    # required fields) but hidden.
    for marker in ('<legend>Brewfather</legend>', '<legend>Archive cleanup</legend>'):
        before = page[:page.index(marker)]
        assert "hidden" in before[before.rindex("<fieldset"):]
    default = c.get("/admin").text
    assert "apply to every screen" not in default
    assert 'id="tab-overrides"' in default


def test_an_unknown_profile_page_shows_the_default():
    html = _admin().get("/admin?profile=ghost").text
    assert "unit-customise" not in html


def test_the_upcoming_warning_says_the_cache_is_cleared_only_when_no_profile_wants_it():
    html = _admin().get("/admin").text
    assert "in every profile deletes" in html
