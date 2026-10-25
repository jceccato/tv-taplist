"""Organising photos (issue #58): albums, moving, and the Removed bin.

Driven through the library's own operations like `test_photo_library.py`, with
the Admin routes and the cleanup job covered at their seams below.
"""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import admin_ops, cleanup, config_store, paths, photo_library as lib
from app.main import app

from photo_fixtures import halves, write_image


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    monkeypatch.setattr(lib, "kick_processing", lambda: None)


def build(*rels):
    """Write distinct photos, then scan and process them (each gets its own colour)."""
    for n, rel in enumerate(rels):
        write_image(paths.PHOTOS_DIR / rel, halves(left=(10 + n * 30, 40, 90)))
    lib.scan()
    lib.process_pending()


def ids(album, *names):
    rows = {r.name: r.photo_id for r in lib.photos_in(album)}
    return [rows[n] for n in names]


def names(album):
    return sorted(r.name for r in lib.photos_in(album))


def removed_files():
    return sorted(p.name for p in paths.PHOTOS_REMOVED_DIR.iterdir())


# ---- the on-disk contract ---------------------------------------------------------

def test_removed_lives_in_photos_removed_and_is_created_at_start():
    assert paths.PHOTOS_REMOVED_DIR == paths.DATA_DIR / "photos_removed"
    paths.PHOTOS_REMOVED_DIR.rmdir()
    paths.ensure_dirs()
    assert paths.PHOTOS_REMOVED_DIR.is_dir()


# ---- albums -----------------------------------------------------------------------

def test_create_makes_an_empty_album_and_names_clash_regardless_of_case():
    assert lib.create_album("  Brew day ") == "Brew day"
    assert (paths.PHOTOS_DIR / "Brew day").is_dir() and "Brew day" in lib.list_albums()
    with pytest.raises(lib.AlbumExists):
        lib.create_album("brew DAY")


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "..", ".hidden", "x.", "Unsorted", "unsorted", "q?", "a" * 101])
def test_create_refuses_unsafe_or_reserved_names(bad):
    with pytest.raises(lib.AlbumRejected):
        lib.create_album(bad)
    assert [p.name for p in paths.PHOTOS_DIR.iterdir()] == []


def test_rename_moves_the_folder_and_keeps_the_ready_copies():
    build("Events/a.jpg", "Events/sub/b.jpg")
    before = {p.name: p.copy_id for p in lib.ready_photos()}
    assert lib.rename_album("Events", "Parties") == "Parties"
    assert lib.list_albums() == ["Parties"]
    assert (paths.PHOTOS_DIR / "Parties" / "sub" / "b.jpg").is_file()
    after = {p.name: (p.album, p.copy_id) for p in lib.ready_photos()}
    assert after == {n: ("Parties", c) for n, c in before.items()}
    assert lib.scan().added == 0                          # nothing re-hashed or re-processed
    assert lib.status().pending == 0


def test_rename_refuses_a_taken_name_and_unsorted():
    lib.create_album("A")
    lib.create_album("B")
    with pytest.raises(lib.AlbumExists):
        lib.rename_album("A", "b")
    with pytest.raises(lib.AlbumRejected):
        lib.rename_album("Unsorted", "X")
    with pytest.raises(lib.AlbumRejected):
        lib.rename_album("A", "Unsorted")
    with pytest.raises(lib.UnknownAlbum):
        lib.rename_album("Nope", "X")
    assert lib.list_albums() == ["A", "B"]


def test_rename_follows_a_removed_photos_remembered_album():
    build("Old/a.jpg")
    lib.remove_photos(ids("Old", "a.jpg"))
    lib.rename_album("Old", "New")
    assert [r.album for r in lib.list_removed()] == ["New"]


def test_rename_in_admin_updates_the_default_and_every_profile_that_ticked_it():
    lib.create_album("Events")
    lib.create_album("Other")
    config_store.update_config(album_all=False, album_selection=["Events", "Other"])
    for name, ticked in (("lounge", ["Other", "Events"]), ("bar", ["Other"])):
        admin_ops.create_profile(name)
        admin_ops.save_profile(name, customise={"album_selection"},
                               values={**config_store.DEFAULT_CONFIG, "album_all": False,
                                       "album_selection": ticked})
    admin_ops.create_profile("follows")        # follows the Default: holds no selection

    admin_ops.rename_album("Events", "Parties")

    cfg = config_store.load_config()
    assert cfg["album_selection"] == ["Parties", "Other"]
    assert cfg["profiles"]["lounge"]["album_selection"] == ["Other", "Parties"]
    assert cfg["profiles"]["bar"]["album_selection"] == ["Other"]
    assert "album_selection" not in cfg["profiles"]["follows"]


def test_a_refused_admin_rename_changes_no_profile():
    lib.create_album("A")
    lib.create_album("B")
    config_store.update_config(album_all=False, album_selection=["A"])
    with pytest.raises(lib.AlbumExists):
        admin_ops.rename_album("A", "B")
    assert config_store.load_config()["album_selection"] == ["A"]


def test_a_folder_renamed_by_hand_is_a_new_unticked_album_and_the_old_name_is_skipped():
    build("Events/a.jpg")
    config_store.update_config(album_all=False, album_selection=["Events"])
    os.rename(paths.PHOTOS_DIR / "Events", paths.PHOTOS_DIR / "Parties")
    lib.scan()
    cfg = config_store.load_config()
    assert "Parties" in lib.list_albums() and "Parties" not in cfg["album_selection"]
    assert lib.ready_photos(cfg["album_selection"]) == []        # the old name matches nothing


def test_delete_moves_every_photo_to_removed_and_drops_the_folder():
    build("Events/a.jpg", "Events/sub/b.jpg", "keep.jpg")
    assert lib.delete_album("Events") == 2
    assert "Events" not in lib.list_albums() and not (paths.PHOTOS_DIR / "Events").exists()
    assert [p.name for p in lib.ready_photos()] == ["keep.jpg"]
    assert sorted((r.name, r.album) for r in lib.list_removed()) == [
        ("a.jpg", "Events"), ("b.jpg", "Events")]
    with pytest.raises(lib.AlbumRejected):
        lib.delete_album("Unsorted")


def test_delete_leaves_a_folder_that_still_holds_something_that_is_not_a_photo():
    build("Events/a.jpg")
    (paths.PHOTOS_DIR / "Events" / "notes.txt").write_text("keep me")
    lib.delete_album("Events")
    assert (paths.PHOTOS_DIR / "Events" / "notes.txt").is_file()


# ---- moving -----------------------------------------------------------------------

def test_move_takes_a_multi_selection_to_another_album_and_keeps_it_ready():
    build("a.jpg", "b.jpg", "c.jpg")
    lib.create_album("Events")
    copies = {p.name: p.copy_id for p in lib.ready_photos()}
    assert lib.move_photos(ids("Unsorted", "a.jpg", "b.jpg"), "Events") == 2
    assert names("Unsorted") == ["c.jpg"] and names("Events") == ["a.jpg", "b.jpg"]
    assert (paths.PHOTOS_DIR / "Events" / "a.jpg").is_file()
    assert {p.name: (p.album, p.copy_id) for p in lib.ready_photos()} == {
        "a.jpg": ("Events", copies["a.jpg"]), "b.jpg": ("Events", copies["b.jpg"]),
        "c.jpg": ("Unsorted", copies["c.jpg"])}
    assert lib.status().pending == 0


def test_move_suffixes_a_name_that_is_taken_and_refuses_an_unknown_album():
    build("a.jpg", "Events/a.jpg")
    assert lib.move_photos(ids("Unsorted", "a.jpg"), "Events") == 1
    assert names("Events") == ["a (2).jpg", "a.jpg"]
    with pytest.raises(lib.UnknownAlbum):
        lib.move_photos(ids("Events", "a.jpg"), "Nope")
    assert names("Events") == ["a (2).jpg", "a.jpg"]


def test_move_to_unsorted_puts_the_file_at_the_top_level():
    build("Events/a.jpg")
    lib.move_photos(ids("Events", "a.jpg"), "Unsorted")
    assert (paths.PHOTOS_DIR / "a.jpg").is_file() and names("Unsorted") == ["a.jpg"]


# ---- removing, restoring, deleting ---------------------------------------------------

def test_remove_takes_a_selection_out_of_every_listing_and_remembers_the_album():
    build("Events/a.jpg", "Events/b.jpg", "c.jpg")
    assert lib.remove_photos(ids("Events", "a.jpg", "b.jpg")) == 2
    assert [p.name for p in lib.ready_photos()] == ["c.jpg"]
    assert lib.ready_photos(["Events"]) == []
    assert names("Events") == []
    assert not (paths.PHOTOS_DIR / "Events" / "a.jpg").exists()
    assert sorted((r.name, r.album) for r in lib.list_removed()) == [
        ("a.jpg", "Events"), ("b.jpg", "Events")]
    assert len([f for f in removed_files() if f.endswith(".jpg")]) == 2


def test_a_removed_photos_display_copy_is_gone_but_its_thumbnail_waits_in_the_bin():
    build("a.jpg")
    photo = lib.ready_photos()[0]
    lib.remove_photos(ids("Unsorted", "a.jpg"))
    assert lib.copy_file(photo.filename) is None              # nothing public serves it
    (removed,) = lib.list_removed()
    assert lib.thumb_file(f"{removed.copy_id}.webp") is not None
    lib.scan()                                                # the sweep keeps it too
    assert lib.thumb_file(f"{removed.copy_id}.webp") is not None


def test_a_copy_shared_by_an_identical_photo_survives_a_removal():
    build("Events/a.jpg")
    write_image(paths.PHOTOS_DIR / "twin.jpg", halves(left=(10, 40, 90)))   # same pixels as a.jpg
    (paths.PHOTOS_DIR / "twin.jpg").write_bytes((paths.PHOTOS_DIR / "Events" / "a.jpg").read_bytes())
    os.utime(paths.PHOTOS_DIR / "twin.jpg", (time.time() - 60,) * 2)
    lib.scan()
    lib.process_pending()
    lib.remove_photos(ids("Events", "a.jpg"))
    assert [p.name for p in lib.ready_photos()] == ["twin.jpg"]        # still has its copy


def test_restore_returns_a_photo_to_its_remembered_album_even_after_the_folder_went():
    build("Events/a.jpg", "Events/b.jpg")
    lib.delete_album("Events")
    assert not (paths.PHOTOS_DIR / "Events").exists()
    (a,) = [r for r in lib.list_removed() if r.name == "a.jpg"]
    assert lib.restore_removed([a.removed_id]) == 1
    assert (paths.PHOTOS_DIR / "Events" / "a.jpg").is_file()
    assert [r.name for r in lib.list_removed()] == ["b.jpg"]
    lib.process_pending()
    assert [(p.album, p.name) for p in lib.ready_photos()] == [("Events", "a.jpg")]


def test_restore_suffixes_a_name_that_was_taken_meanwhile():
    build("a.jpg")
    lib.remove_photos(ids("Unsorted", "a.jpg"))
    build_name = paths.PHOTOS_DIR / "a.jpg"
    write_image(build_name, halves(left=(1, 2, 3)))
    lib.scan()
    lib.restore_removed([lib.list_removed()[0].removed_id])
    assert sorted(p.name for p in paths.PHOTOS_DIR.iterdir()) == ["a (2).jpg", "a.jpg"]


def test_a_hand_edited_record_cannot_restore_outside_the_library():
    build("a.jpg")
    lib.remove_photos(ids("Unsorted", "a.jpg"))
    (record,) = paths.PHOTOS_REMOVED_DIR.glob("*.json")
    meta = json.loads(record.read_text())
    meta["album"] = "../../escape"
    record.write_text(json.dumps(meta))
    lib.restore_removed([record.stem])
    assert (paths.PHOTOS_DIR / "a.jpg").is_file()                    # fell back to Unsorted
    assert not (paths.DATA_DIR.parent / "escape").exists()


def test_delete_forever_and_empty_removed_delete_permanently_with_their_thumbnails():
    build("a.jpg", "b.jpg", "c.jpg")
    thumbs = {p.copy_id for p in lib.ready_photos()}
    lib.remove_photos(ids("Unsorted", "a.jpg", "b.jpg", "c.jpg"))
    first = lib.list_removed()[0]
    assert lib.delete_removed([first.removed_id, "not-an-id", "0" * 32]) == 1
    assert len(lib.list_removed()) == 2 and len(removed_files()) == 4
    assert lib.empty_removed() == 2
    assert removed_files() == []
    assert not any(lib.thumb_file(f"{c}.webp") for c in thumbs)
    assert lib.ready_photos() == []


def test_a_photo_deleted_from_disk_by_hand_is_simply_gone():
    build("a.jpg", "b.jpg")
    (paths.PHOTOS_DIR / "a.jpg").unlink()
    lib.scan()
    assert [p.name for p in lib.ready_photos()] == ["b.jpg"] and lib.list_removed() == []


def test_unknown_ids_change_nothing():
    build("a.jpg")
    assert lib.remove_photos(["deadbeefdeadbeef"]) == 0
    assert lib.restore_removed(["x" * 32]) == 0
    assert names("Unsorted") == ["a.jpg"]


# ---- the daily purge --------------------------------------------------------------------

def _age_removal(removed_id, days):
    record = paths.PHOTOS_REMOVED_DIR / f"{removed_id}.json"
    meta = json.loads(record.read_text())
    meta["removed_at"] = time.time() - days * 86400
    record.write_text(json.dumps(meta))


def test_purge_deletes_only_removed_photos_older_than_the_retention():
    build("old.jpg", "new.jpg", "keep.jpg")
    lib.remove_photos(ids("Unsorted", "old.jpg", "new.jpg"))
    by_name = {r.name: r.removed_id for r in lib.list_removed()}
    _age_removal(by_name["old.jpg"], 45)
    _age_removal(by_name["new.jpg"], 10)
    assert lib.purge_removed(30) == 1
    assert [r.name for r in lib.list_removed()] == ["new.jpg"]
    assert names("Unsorted") == ["keep.jpg"]


def test_the_daily_cleanup_purges_removed_photos_past_the_retention_days():
    build("old.jpg", "new.jpg")
    lib.remove_photos(ids("Unsorted", "old.jpg", "new.jpg"))
    by_name = {r.name: r.removed_id for r in lib.list_removed()}
    _age_removal(by_name["old.jpg"], 20)
    _age_removal(by_name["new.jpg"], 3)
    config_store.update_config(photo_removed_retention_days=7)

    result = cleanup.run_cleanup()

    assert result["purged_photos"] == 1
    assert [r.name for r in lib.list_removed()] == ["new.jpg"]
    assert not list(paths.PHOTOS_REMOVED_DIR.glob(f"{by_name['old.jpg']}.*"))


def test_the_retention_defaults_to_30_days_and_is_clamped():
    assert config_store.load_config()["photo_removed_retention_days"] == 30
    assert config_store.update_config(photo_removed_retention_days=-4)["photo_removed_retention_days"] == 0
    assert config_store.update_config(photo_removed_retention_days=99999)["photo_removed_retention_days"] == 3650
    assert "photo_removed_retention_days" in config_store.NEVER_CUSTOMISABLE


def test_a_settings_save_leaves_the_retention_alone():
    config_store.update_config(photo_removed_retention_days=12)
    c = _admin()
    r = c.post("/admin/settings", data={"num_taps": "1", "max_archive_age_days": "1",
                                        "max_archive_storage_mb": "1"})
    assert r.status_code == 200
    assert config_store.load_config()["photo_removed_retention_days"] == 12


# ---- the Admin routes ---------------------------------------------------------------------

def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


def test_every_organising_route_and_the_originals_need_the_admin_login():
    anon = TestClient(app)
    build("a.jpg")
    for method, url, data in (
        ("post", "/admin/photos/albums", {"name": "X"}),
        ("post", "/admin/photos/albums/rename", {"old": "X", "new": "Y"}),
        ("post", "/admin/photos/albums/delete", {"name": "X"}),
        ("post", "/admin/photos/move", {"ids": ["x"], "album": "X"}),
        ("post", "/admin/photos/remove", {"ids": ["x"]}),
        ("get", "/admin/photos/removed", None),
        ("post", "/admin/photos/removed/restore", {"ids": ["x"]}),
        ("post", "/admin/photos/removed/delete", {"ids": ["x"]}),
        ("post", "/admin/photos/removed/empty", {}),
        ("post", "/admin/photos/retention", {"photo_removed_retention_days": "5"}),
    ):
        r = getattr(anon, method)(url, **({"data": data} if data is not None else {}))
        assert r.status_code == 401, url
    # No route serves an original, and a thumbnail needs the login.
    thumb = lib.ready_photos()[0].copy_id
    assert anon.get(f"/admin/photos/thumb/{thumb}.webp").status_code == 401
    assert anon.get("/photos/a.jpg").status_code == 404
    original = (paths.PHOTOS_DIR / "a.jpg").read_bytes()
    assert anon.get("/img/a.jpg").content != original            # the tap-image route never reaches photos/
    assert anon.get("/img/photo/a.jpg").status_code == 404
    assert names("Unsorted") == ["a.jpg"] and lib.list_removed() == []
    assert config_store.load_config()["photo_removed_retention_days"] == 30


def test_the_admin_can_run_the_whole_album_and_removed_flow_over_http():
    c = _admin()
    build("a.jpg", "b.jpg")
    assert c.post("/admin/photos/albums", data={"name": "Events"}).json()["name"] == "Events"
    assert c.post("/admin/photos/albums", data={"name": "events"}).status_code == 409
    assert c.post("/admin/photos/albums", data={"name": "a/b"}).status_code == 422

    a, b = ids("Unsorted", "a.jpg", "b.jpg")
    assert c.post("/admin/photos/move", data={"ids": [a, b], "album": "Events"}).json()["moved"] == 2
    assert c.post("/admin/photos/move", data={"ids": [a], "album": "Nope"}).status_code == 404
    assert c.post("/admin/photos/albums/rename", data={"old": "Events", "new": "Parties"}).json()["name"] == "Parties"

    st = c.get("/admin/photos/status", params={"album": "Parties"}).json()
    assert sorted(p["name"] for p in st["photos"]) == ["a.jpg", "b.jpg"] and st["removed"] == 0

    ids_now = [p["id"] for p in st["photos"]]
    assert c.post("/admin/photos/remove", data={"ids": ids_now}).json()["removed"] == 2
    rem = c.get("/admin/photos/removed").json()
    assert rem["retention_days"] == 30
    assert sorted((p["name"], p["album"]) for p in rem["photos"]) == [("a.jpg", "Parties"), ("b.jpg", "Parties")]
    assert all(p["thumb"] and c.get(p["thumb"]).status_code == 200 for p in rem["photos"])
    assert all(p["days_left"] in (29, 30) for p in rem["photos"])

    first, second = (p["id"] for p in rem["photos"])
    assert c.post("/admin/photos/removed/restore", data={"ids": [first]}).json()["restored"] == 1
    assert c.post("/admin/photos/removed/delete", data={"ids": [second]}).json()["deleted"] == 1
    assert c.get("/admin/photos/removed").json()["photos"] == []
    assert c.post("/admin/photos/removed/empty").json()["deleted"] == 0

    assert c.post("/admin/photos/retention", data={"photo_removed_retention_days": "14"}).json()["days"] == 14
    assert c.post("/admin/photos/retention", data={"photo_removed_retention_days": "-3"}).json()["days"] == 0
    assert c.post("/admin/photos/albums/delete", data={"name": "Unsorted"}).status_code == 422
    assert c.post("/admin/photos/albums/delete", data={"name": "Parties"}).json()["removed"] == 1
    assert len(c.get("/admin/photos/removed").json()["photos"]) == 1


def test_removed_photos_never_reach_the_board():
    config_store.update_config(album_enabled=True)
    build("a.jpg", "b.jpg")
    c = TestClient(app)
    def board_filenames():
        board = c.get("/api/board").json()
        return json.dumps(board)
    assert "/img/photo/" in board_filenames()
    gone = lib.ready_photos()[0]
    lib.remove_photos([gone.photo_id])
    assert gone.filename not in board_filenames()
    assert c.get(f"/img/photo/{gone.filename}").status_code == 404
    lib.remove_photos([p.photo_id for p in lib.ready_photos()])
    assert "/img/photo/" not in board_filenames()


def test_a_removed_photo_deleted_from_disk_by_hand_leaves_the_bin_view():
    build("a.jpg")
    lib.remove_photos(ids("Unsorted", "a.jpg"))
    next(paths.PHOTOS_REMOVED_DIR.glob("*.jpg")).unlink()
    assert lib.list_removed() == []


def test_restore_joins_an_album_whose_folder_differs_only_in_case():
    build("a.jpg")
    lib.remove_photos(ids("Unsorted", "a.jpg"))
    record = next(paths.PHOTOS_REMOVED_DIR.glob("*.json"))
    meta = json.loads(record.read_text())
    meta["album"] = "BEACH"
    record.write_text(json.dumps(meta))
    lib.create_album("beach")
    lib.restore_removed([record.stem])
    assert (paths.PHOTOS_DIR / "beach" / "a.jpg").is_file()
    assert lib.list_albums() == ["beach"]
