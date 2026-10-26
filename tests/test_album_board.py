"""The photo album as the board and the Admin see it (issue #56, ADR-0007/0008).

Seam 1 (the board with a profile) and seam 3 (admin writes), over HTTP: what the
payload says for a profile with the album on or off, which photos it carries and
in what order, that no original is ever referenced, and that the settings are
customisable per profile, clamped, and saved by the profile editor.
"""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import admin_ops, config_store, paths, photo_library as lib
from app.main import app

from photo_fixtures import exif_bytes, halves, write_image

client = TestClient(app)

REQUIRED = {"num_taps": "1", "max_archive_age_days": "1", "max_archive_storage_mb": "1"}
ALBUM_KEYS = ("album_enabled", "album_every_turns", "album_photos_per_visit",
              "album_seconds_per_photo", "album_order", "album_all",
              "album_selection", "album_keep_ticker", "album_fade_seconds")
PRINT_KEYS = ("album_mount_style", "album_mount_color", "album_mount_custom", "album_frame")


def _photos(*rels, process=True):
    for i, rel in enumerate(rels):
        write_image(paths.PHOTOS_DIR / rel, halves((300 + 7 * i, 200)))
    lib.scan()
    if process:
        lib.process_pending()


def _album_profile(**settings):
    """A profile `lounge` with the album on (customised), plus any other settings."""
    admin_ops.create_profile("lounge")
    values = {"album_enabled": True, **settings}
    admin_ops.save_profile("lounge", customise={"album_enabled"} | {
        u for u, keys in config_store.CUSTOMISABLE_UNITS.items()
        if any(k in settings for k in keys)}, values={**_full_defaults(), **values})


def _full_defaults():
    return {k: v for k, v in config_store.DEFAULT_CONFIG.items()}


def _board(display="lounge"):
    return client.get(f"/api/board?display={display}").json()


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


# ---- the settings -------------------------------------------------------------------

def test_the_album_is_off_in_the_default_profile():
    cfg = config_store.load_config()
    assert cfg["album_enabled"] is False
    assert (cfg["album_every_turns"], cfg["album_photos_per_visit"],
            cfg["album_seconds_per_photo"], cfg["album_order"], cfg["album_all"],
            cfg["album_selection"], cfg["album_keep_ticker"], cfg["album_fade_seconds"]) == (
        4, 6, 10, "shuffle", True, [], False, 1.5)


def test_every_album_setting_is_customisable_and_none_is_venue_wide():
    customisable = config_store.CUSTOMISABLE_KEYS
    assert set(ALBUM_KEYS) <= customisable
    assert not set(ALBUM_KEYS) & config_store.NEVER_CUSTOMISABLE
    # The two selection keys are one unit: a profile cannot hold half of it.
    assert config_store.CUSTOMISABLE_UNITS["album_selection"] == ("album_all", "album_selection")


@pytest.mark.parametrize("key, bad, expected", [
    ("album_every_turns", 999, 50), ("album_every_turns", -3, 0),
    ("album_photos_per_visit", 0, 1), ("album_photos_per_visit", 10_000, 50),
    ("album_seconds_per_photo", 0, 2), ("album_seconds_per_photo", 9999, 600),
    ("album_fade_seconds", -1, 0), ("album_fade_seconds", 99, 10),
    ("album_order", "random", "shuffle"), ("album_order", " NAME ", "name"),
    ("album_selection", "not a list", []), ("album_selection", [1, None, " a ", "a", ""], ["a"]),
])
def test_album_settings_are_clamped_in_the_default_profile_and_in_a_profile(key, bad, expected):
    default = config_store.update_config(**{key: bad})
    assert default[key] == expected
    # A profile's own values go through the same coercion (a hand-edited config).
    units = [u for u, keys in config_store.CUSTOMISABLE_UNITS.items() if key in keys][0]
    raw = json.loads(paths.CONFIG_PATH.read_text())
    raw["profiles"] = {"lounge": {k: config_store.DEFAULT_CONFIG[k]
                                  for k in config_store.CUSTOMISABLE_UNITS[units]} | {key: bad}}
    paths.CONFIG_PATH.write_text(json.dumps(raw))
    assert config_store.load_config()["profiles"]["lounge"][key] == expected


def test_a_profile_follows_the_default_for_album_settings_it_does_not_customise():
    _photos("a.jpg")
    config_store.update_config(album_seconds_per_photo=25, album_fade_seconds=3.0)
    _album_profile(album_every_turns=2)
    album = _board()["album"]
    assert album["every_turns"] == 2                    # customised
    assert album["seconds_per_photo"] == 25             # followed, live
    assert album["fade_seconds"] == 3.0
    config_store.update_config(album_seconds_per_photo=40)
    assert _board()["album"]["seconds_per_photo"] == 40


# ---- the board ------------------------------------------------------------------------------

def test_a_profile_with_the_album_off_carries_no_album_fields_at_all():
    _photos("a.jpg")
    admin_ops.create_profile("tablet")
    for display in ("tablet", "default", "no-such-profile"):
        board = _board(display)
        assert "album" not in board
        assert "photo" not in json.dumps(board).lower()


def test_only_the_profile_with_the_album_on_carries_it():
    _photos("a.jpg")
    _album_profile()
    admin_ops.create_profile("tablet")
    assert "album" in _board("lounge")
    assert "album" not in _board("tablet")
    assert "album" not in client.get("/api/board").json()


def test_the_album_block_carries_the_visit_plan_and_the_ordered_ready_photos():
    _photos("Brew day/b.jpg", "Brew day/a.jpg", "c.jpg")
    _album_profile(album_every_turns=3, album_photos_per_visit=2, album_seconds_per_photo=7,
                   album_fade_seconds=0.5, album_keep_ticker=True, album_order="name")
    album = _board()["album"]
    assert {k: album[k] for k in ("every_turns", "photos_per_visit", "seconds_per_photo",
                                  "fade_seconds", "keep_ticker", "order")} == {
        "every_turns": 3, "photos_per_visit": 2, "seconds_per_photo": 7,
        "fade_seconds": 0.5, "keep_ticker": True, "order": "name"}
    assert [p["album"] for p in album["photos"]] == ["Brew day", "Brew day", "Unsorted"]
    first = album["photos"][0]
    assert set(first) == {"id", "url", "aspect", "album", "caption_album", "taken", "mount"}
    assert first["mount"].startswith("#") and len(first["mount"]) == 7
    assert first["url"].startswith("/img/photo/") and first["url"].endswith(".webp")
    assert first["aspect"] == pytest.approx(1.5, abs=0.05)


def test_the_photo_order_follows_the_setting():
    write_image(paths.PHOTOS_DIR / "b.jpg", halves((300, 200)), age_seconds=86400)
    write_image(paths.PHOTOS_DIR / "a.jpg", halves((310, 200)), age_seconds=100 * 86400)
    write_image(paths.PHOTOS_DIR / "c.jpg", halves((320, 200)),
                exif=exif_bytes(taken="2020:01:01 00:00:00"), age_seconds=60)
    lib.scan()
    lib.process_pending()
    names = {p.photo_id: p.name for p in lib.ready_photos()}

    def order(setting):
        _album_profile_reset(album_order=setting)
        return [names[p["id"]] for p in _board()["album"]["photos"]]

    assert order("name") == ["a.jpg", "b.jpg", "c.jpg"]
    assert order("date") == ["b.jpg", "a.jpg", "c.jpg"]       # c was TAKEN in 2020
    assert sorted(order("shuffle")) == ["a.jpg", "b.jpg", "c.jpg"]


def _album_profile_reset(**settings):
    raw = json.loads(paths.CONFIG_PATH.read_text())
    raw["profiles"] = {}
    paths.CONFIG_PATH.write_text(json.dumps(raw))
    _album_profile(**settings)


def test_the_album_selection_filters_by_album_name():
    _photos("Events/e.jpg", "Brewery/b.jpg", "loose.jpg")
    _album_profile(album_all=False, album_selection=["Events", "Unsorted", "Gone"])
    got = {p["album"] for p in _board()["album"]["photos"]}
    assert got == {"Events", "Unsorted"}                  # "Gone" is skipped, not an error
    _album_profile_reset(album_all=True, album_selection=["Events"])
    assert {p["album"] for p in _board()["album"]["photos"]} == {"Events", "Brewery", "Unsorted"}


def test_a_photo_still_being_processed_is_not_on_the_board_yet():
    _photos("ready.jpg")
    _photos("later.jpg", process=False)
    _album_profile()
    assert len(_board()["album"]["photos"]) == 1
    lib.process_pending()
    assert len(_board()["album"]["photos"]) == 2


def test_no_ready_photos_is_an_empty_list_not_an_error():
    _album_profile()
    assert _board()["album"]["photos"] == []


def test_the_board_never_references_an_original():
    _photos("Secret Events/IMG_0001 private name.jpg")
    _album_profile()
    text = client.get("/api/board?display=lounge").text
    # The album's name is a caption field and may appear; the original's own file
    # name, its folder path and the data directory never do.
    assert "IMG_0001" not in text and "private name" not in text
    assert "photos/" not in text and str(paths.DATA_DIR) not in text
    assert ".jpg" not in text


# ---- serving Display copies ----------------------------------------------------------------------

def test_a_display_copy_is_served_publicly_with_the_img_security_headers():
    _photos("one.jpg")
    _album_profile()
    url = _board()["album"]["photos"][0]["url"]
    r = client.get(url)                       # no login: it is a public route
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["content-security-policy"] == "script-src 'none'; sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in r.headers["cache-control"]
    assert Image.open(__import__("io").BytesIO(r.content)).format == "WEBP"


def test_originals_cannot_be_fetched_on_a_public_route():
    _photos("Events/one.jpg")
    for url in ("/img/photo/one.jpg", "/img/photo/Events/one.jpg", "/img/Events/one.jpg",
                "/img/one.jpg", "/photos/Events/one.jpg", "/img/photo/..%2Fphotos%2Fone.jpg",
                "/img/photo/index.json", "/img/photo/..%2Findex.json", "/static/../photos/Events/one.jpg"):
        r = client.get(url)
        assert r.status_code in (200, 404)
        assert b"Exif" not in r.content
        assert r.content != (paths.PHOTOS_DIR / "Events" / "one.jpg").read_bytes()
    assert client.get("/img/photo/" + "0" * 32 + ".webp").status_code == 404


def test_a_photo_that_is_not_ready_is_a_404_not_the_placeholder():
    assert client.get("/img/photo/" + "a" * 32 + ".webp").status_code == 404


# ---- the Admin: the profile editor -----------------------------------------------------------------

def test_the_album_fieldset_is_on_the_settings_tab_for_the_default_and_a_profile():
    _photos("Events/e.jpg", "loose.jpg")
    admin_ops.create_profile("lounge")
    c = _admin()
    for url in ("/admin", "/admin?profile=lounge"):
        html = c.get(url).text
        assert 'id="album-settings"' in html
        assert "Events" in html and "Unsorted" in html     # the album picker
        assert 'name="album_enabled"' in html
    profile = c.get("/admin?profile=lounge").text
    assert 'name="customise_album_enabled"' in profile
    assert 'name="customise_album_selection"' in profile


def test_the_editor_switches_the_album_on_for_one_profile_only():
    admin_ops.create_profile("lounge")
    admin_ops.create_profile("tablet")
    r = _admin().post("/admin/settings", data={
        **REQUIRED, "profile": "lounge",
        "customise_album_enabled": "true", "album_enabled": "true",
        "customise_album_every_turns": "true", "album_every_turns": "2",
        "customise_album_selection": "true", "album_all": "false",
        "album_selection": json.dumps(["Events", 'Quote " & <b>']),
        "album_photos_per_visit": "9"})            # posted but followed: dropped
    assert r.status_code == 200
    stored = config_store.load_config()
    assert stored["profiles"]["lounge"] == {
        "album_enabled": True, "album_every_turns": 2,
        "album_all": False, "album_selection": ["Events", 'Quote " & <b>']}
    assert stored["profiles"]["tablet"] == {}
    assert stored["album_enabled"] is False and stored["album_photos_per_visit"] == 6


def test_following_drops_a_customised_album_setting():
    _album_profile(album_every_turns=2)
    _admin().post("/admin/settings", data={
        **REQUIRED, "profile": "lounge", "customise_album_enabled": "true",
        "album_enabled": "true", "album_every_turns": "2"})
    assert config_store.load_config()["profiles"]["lounge"] == {"album_enabled": True}


def test_a_default_save_posts_the_album_settings_and_the_selection_round_trips():
    c = _admin()
    r = c.post("/admin/settings", data={
        **REQUIRED, "album_enabled": "false", "album_every_turns": "5", "album_all": "false",
        "album_selection": json.dumps(["Events"]), "album_order": "date",
        "album_fade_seconds": "2.5", "album_keep_ticker": "true"})
    assert r.status_code == 200
    saved = config_store.load_config()
    assert (saved["album_every_turns"], saved["album_all"], saved["album_selection"],
            saved["album_order"], saved["album_fade_seconds"], saved["album_keep_ticker"]) == (
        5, False, ["Events"], "date", 2.5, True)
    html = c.get("/admin").text
    assert "&#34;Events&#34;" in html or "&quot;Events&quot;" in html    # attribute-escaped JSON


def test_junk_album_selection_posts_save_as_no_albums_and_a_missing_all_means_all():
    c = _admin()
    c.post("/admin/settings", data={**REQUIRED, "album_selection": "{not json"})
    saved = config_store.load_config()
    assert saved["album_selection"] == [] and saved["album_all"] is True


def test_a_ticked_album_with_no_folder_stays_visible_so_a_save_does_not_drop_it():
    config_store.update_config(album_all=False, album_selection=["Renamed away"])
    html = _admin().get("/admin").text
    assert "Renamed away" in html and "no such folder" in html


def test_the_settings_form_declares_every_album_setting():
    from app import main
    assert set(ALBUM_KEYS) <= set(main.SettingsForm.model_fields)
    assert set(config_store.CUSTOMISABLE_UNITS["album_selection"]) <= set(main.SettingsForm.model_fields)
    for unit in ("album_enabled", "album_every_turns", "album_selection"):
        assert unit in main.PROFILE_UNIT_LABELS


def test_the_snapshot_carries_the_album_settings_but_not_the_photos():
    _photos("one.jpg")
    config_store.update_config(album_enabled=True, album_every_turns=7)
    import io, zipfile
    r = _admin().get("/admin/snapshot")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "config.json" in names
    assert not any(n.startswith(("photos", "photo_cache")) for n in names)
    carried = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("config.json"))
    assert carried["album_enabled"] is True and carried["album_every_turns"] == 7


def test_two_files_with_the_same_content_have_distinct_board_ids():
    # They share one Display copy, but the page tracks its place by id, so the ids
    # must differ or Name/Date order would jump back and skip photos.
    same = halves((640, 480))
    write_image(paths.PHOTOS_DIR / "a.jpg", same)
    write_image(paths.PHOTOS_DIR / "b.jpg", same)
    lib.scan()
    lib.process_pending()
    _album_profile()
    photos = _board()["album"]["photos"]
    assert len(photos) == 2 and len({p["id"] for p in photos}) == 2
    assert len({p["url"] for p in photos}) == 1


# ---- the framed print (issue #61) -------------------------------------------------------

def test_the_print_defaults_are_a_flat_mount_from_the_photo_in_a_thin_black_frame():
    cfg = config_store.load_config()
    assert (cfg["album_mount_style"], cfg["album_mount_color"], cfg["album_frame"]) == (
        "flat", "photo", "thin-black")
    assert config_store.MOUNT_STYLES == ("none", "flat", "double", "shadow", "float", "random")
    assert config_store.MOUNT_COLORS == ("photo", "white", "cream", "black", "custom", "random")
    assert config_store.FRAMES == ("none", "thin-black", "thin-white", "oak", "walnut",
                                   "gilded", "polaroid", "real-oak-limed", "real-oak-honey",
                                   "real-walnut", "real-gilded-baroque", "real-gilded-classic",
                                   "real-black-ash", "real-white-stepped", "real-barnwood",
                                   "real-farmhouse", "real-dark-timber", "random")


def test_every_print_setting_is_customisable_and_the_custom_colour_goes_with_its_choice():
    assert set(PRINT_KEYS) <= config_store.CUSTOMISABLE_KEYS
    assert config_store.CUSTOMISABLE_UNITS["album_mount_color"] == (
        "album_mount_color", "album_mount_custom")
    assert config_store.CUSTOMISABLE_UNITS["album_mount_style"] == ("album_mount_style",)
    assert config_store.CUSTOMISABLE_UNITS["album_frame"] == ("album_frame",)


@pytest.mark.parametrize("key, bad, expected", [
    ("album_mount_style", "Shadow ", "shadow"), ("album_mount_style", "velvet", "flat"),
    ("album_mount_color", "RANDOM", "random"), ("album_mount_color", 7, "photo"),
    ("album_frame", "Gilded", "gilded"), ("album_frame", "chrome", "thin-black"),
    ("album_mount_custom", "ABC", "#aabbcc"), ("album_mount_custom", "nope",
                                               config_store.DEFAULT_CONFIG["album_mount_custom"]),
])
def test_print_settings_are_coerced_never_rejected(key, bad, expected):
    assert config_store.update_config(**{key: bad})[key] == expected


def test_the_album_block_carries_the_print_choices():
    _photos("a.jpg")
    _album_profile(album_mount_style="double", album_mount_color="custom",
                   album_mount_custom="#123456", album_frame="random")
    assert _board()["album"]["print"] == {
        "mount_style": "double", "mount_color": "custom", "mount_custom": "#123456",
        "frame": "random"}


def test_a_profile_follows_the_default_print_until_it_customises_it():
    _photos("a.jpg")
    config_store.update_config(album_frame="oak")
    _album_profile(album_mount_style="none")
    print_ = _board()["album"]["print"]
    assert (print_["mount_style"], print_["frame"]) == ("none", "oak")


def test_a_default_save_posts_the_print_settings():
    c = _admin()
    r = c.post("/admin/settings", data={
        **REQUIRED, "album_mount_style": "float", "album_mount_color": "custom",
        "album_mount_custom": "#a0b0c0", "album_frame": "polaroid"})
    assert r.status_code == 200
    saved = config_store.load_config()
    assert [saved[k] for k in PRINT_KEYS] == ["float", "custom", "#a0b0c0", "polaroid"]
    html = c.get("/admin").text
    for name in PRINT_KEYS:
        assert f'name="{name}"' in html
    from app import main
    assert set(PRINT_KEYS) <= set(main.SettingsForm.model_fields)
    for unit in ("album_mount_style", "album_mount_color", "album_frame"):
        assert unit in main.PROFILE_UNIT_LABELS


def test_the_admin_offers_every_print_choice():
    html = _admin().get("/admin").text
    for name, choices in (("album_mount_style", config_store.MOUNT_STYLES),
                          ("album_mount_color", config_store.MOUNT_COLORS),
                          ("album_frame", config_store.FRAMES)):
        select = html.split(f'name="{name}"', 1)[1].split("</select>", 1)[0]
        assert [c for c in choices if f'value="{c}"' in select] == list(choices)


# ---- portraits, captions and the slow zoom (issue #62) ------------------------------------

SPREAD_KEYS = ("album_pair_portraits", "album_three_up", "album_caption_date",
               "album_caption_album", "album_ken_burns")


def test_the_spread_defaults_pair_portraits_and_switch_the_rest_off():
    cfg = config_store.load_config()
    assert [cfg[k] for k in SPREAD_KEYS] == [True, False, False, False, False]


def test_every_spread_setting_is_customisable_and_the_two_captions_are_one_unit():
    assert set(SPREAD_KEYS) <= config_store.CUSTOMISABLE_KEYS
    assert not set(SPREAD_KEYS) & config_store.NEVER_CUSTOMISABLE
    assert config_store.CUSTOMISABLE_UNITS["album_captions"] == (
        "album_caption_date", "album_caption_album")


def test_the_album_block_carries_the_spread_choices():
    _photos("a.jpg")
    _album_profile(album_three_up=True, album_caption_album=True, album_ken_burns=True)
    album = _board()["album"]
    assert (album["pair_portraits"], album["three_up"], album["ken_burns"]) == (True, True, True)
    assert album["captions"] == {"date": False, "album": True}


def test_three_up_is_sent_as_off_when_pairing_is_off():
    _photos("a.jpg")
    _album_profile(album_pair_portraits=False, album_three_up=True)
    album = _board()["album"]
    assert (album["pair_portraits"], album["three_up"]) == (False, False)


def test_a_photo_without_an_exif_date_still_carries_a_caption_date():
    _photos("Events/a.jpg")
    taken = _album_profile_board_photo()["taken"]
    assert taken and len(taken) == 10 and taken[4] == "-"


def _album_profile_board_photo():
    _album_profile()
    return _board()["album"]["photos"][0]


def test_a_default_save_posts_the_spread_settings_and_the_admin_labels_them():
    c = _admin()
    r = c.post("/admin/settings", data={
        **REQUIRED, "album_pair_portraits": "false", "album_three_up": "true",
        "album_caption_date": "true", "album_caption_album": "true", "album_ken_burns": "true"})
    assert r.status_code == 200
    saved = config_store.load_config()
    assert [saved[k] for k in SPREAD_KEYS] == [False, True, True, True, True]
    html = c.get("/admin").text
    for name in SPREAD_KEYS:
        assert f'name="{name}"' in html
    from app import main
    assert set(SPREAD_KEYS) <= set(main.SettingsForm.model_fields)
    for unit in ("album_pair_portraits", "album_three_up", "album_captions", "album_ken_burns"):
        assert unit in main.PROFILE_UNIT_LABELS
