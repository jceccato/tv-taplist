"""Album rename and delete when something goes wrong (issue #69).

The normal paths are in `test_photo_removed.py`; these are the three edges: a
config write that fails after the folder moved, a Settings Save that runs while
(or after a page opened before) a rename, and a deleted album whose folder stays.
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import admin_ops, config_store, paths, photo_library as lib
from app.main import app

from photo_fixtures import write_bytes

REQUIRED = {"num_taps": "1", "max_archive_age_days": "1", "max_archive_storage_mb": "1"}


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    monkeypatch.setattr(lib, "kick_processing", lambda: None)


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


def _tick(name, ticked):
    admin_ops.create_profile(name)
    admin_ops.save_profile(name, customise={"album_selection"},
                           values={**config_store.DEFAULT_CONFIG, "album_all": False,
                                   "album_selection": ticked})


# ---- a failed config write rolls the folder back ---------------------------------

def _fail_the_write(monkeypatch):
    def boom(**_fields):
        raise OSError("disk full")
    monkeypatch.setattr(config_store, "apply_settings", boom)


def test_a_failed_config_write_puts_the_folder_back_under_its_old_name(monkeypatch):
    lib.create_album("Events")
    (paths.PHOTOS_DIR / "Events" / "a.jpg").write_bytes(b"x")
    config_store.update_config(album_all=False, album_selection=["Events"])
    _fail_the_write(monkeypatch)
    with pytest.raises(admin_ops.AlbumRenameFailed, match="not renamed"):
        admin_ops.rename_album("Events", "Parties")
    assert lib.list_albums() == ["Events"]
    assert (paths.PHOTOS_DIR / "Events" / "a.jpg").is_file()
    assert config_store.load_config()["album_selection"] == ["Events"]


def _namesake_in_removed():
    """Parties/old.jpg sits in Removed from a deleted album; Events/b.jpg was removed from Events."""
    from photo_fixtures import halves, write_image
    for n, rel in enumerate(("Parties/old.jpg", "Events/a.jpg", "Events/b.jpg")):
        write_image(paths.PHOTOS_DIR / rel, halves(left=(10 + n * 60, 40, 90)))
    lib.scan()
    lib.process_pending()
    pid = {r.name: r.photo_id for r in lib.photos_in("Events")}
    lib.delete_album("Parties")
    lib.remove_photos([pid["b.jpg"]])


def _removed_albums():
    return {r.name: r.album for r in lib.list_removed()}


def test_a_failed_rename_leaves_every_removed_records_album_as_it_was(monkeypatch):
    """A deleted namesake's Removed photos must not be retagged by the rollback."""
    _namesake_in_removed()
    before = _removed_albums()
    assert before == {"old.jpg": "Parties", "b.jpg": "Events"}
    _fail_the_write(monkeypatch)
    with pytest.raises(admin_ops.AlbumRenameFailed, match="not renamed"):
        admin_ops.rename_album("Events", "Parties")
    assert _removed_albums() == before
    assert lib.list_albums() == ["Events"]


def test_a_successful_rename_retags_its_own_removed_photos_but_not_a_namesakes():
    _namesake_in_removed()
    admin_ops.rename_album("Events", "Parties")
    assert _removed_albums() == {"old.jpg": "Parties", "b.jpg": "Parties"}
    admin_ops.rename_album("Parties", "Gala")   # old.jpg is flagged, so it stays put
    assert _removed_albums() == {"old.jpg": "Parties", "b.jpg": "Gala"}


def test_a_failed_rename_is_not_remembered_for_later_saves(monkeypatch):
    lib.create_album("Events")
    _fail_the_write(monkeypatch)
    with pytest.raises(admin_ops.AlbumRenameFailed):
        admin_ops.rename_album("Events", "Parties")
    monkeypatch.undo()
    lib.rename_album("Events", "Gone")        # by hand, not through the Admin
    saved = admin_ops.save_settings(album_all=False, album_selection=["Events"])
    assert saved["album_selection"] == ["Events"]


def test_the_admin_shows_an_error_when_the_config_write_fails(monkeypatch):
    lib.create_album("Events")
    c = _admin()
    _fail_the_write(monkeypatch)
    r = c.post("/admin/photos/albums/rename", data={"old": "Events", "new": "Parties"})
    assert r.status_code == 500 and "not renamed" in r.json()["detail"]
    assert lib.list_albums() == ["Events"]


def test_when_the_folder_cannot_be_moved_back_the_error_says_where_it_is(monkeypatch):
    lib.create_album("Events")
    _fail_the_write(monkeypatch)
    real = lib.rename_album
    calls = []

    def flaky(old, new, **kw):
        calls.append((old, new))
        if len(calls) > 1:
            raise OSError("busy")
        return real(old, new, **kw)
    monkeypatch.setattr(lib, "rename_album", flaky)
    with pytest.raises(admin_ops.AlbumRenameFailed, match="could not be moved back") as exc:
        admin_ops.rename_album("Events", "Parties")
    assert "Parties" in str(exc.value) and "Events" in str(exc.value)


# ---- a Settings Save cannot restore the old name ----------------------------------

def test_a_save_that_arrives_during_a_rename_waits_and_keeps_the_new_name(monkeypatch):
    lib.create_album("Events")
    lib.create_album("Other")
    config_store.update_config(album_all=False, album_selection=["Events", "Other"])
    _tick("lounge", ["Events"])
    real = lib.rename_album
    renamed, go = threading.Event(), threading.Event()

    def slow(old, new, **kw):
        out = real(old, new, **kw)
        renamed.set()
        assert go.wait(5)
        return out
    monkeypatch.setattr(lib, "rename_album", slow)

    results = {}
    rename = threading.Thread(target=lambda: results.setdefault(
        "rename", admin_ops.rename_album("Events", "Parties")))
    rename.start()
    assert renamed.wait(5)
    save_done = threading.Event()

    def save():
        # The page was opened before the rename, so it still posts the old name.
        results["save"] = admin_ops.save_settings(
            album_all=False, album_selection=["Events", "Other"], num_taps=3)
        save_done.set()
    saver = threading.Thread(target=save)
    saver.start()
    assert not save_done.wait(0.3)            # held back by the rename
    go.set()
    rename.join(5)
    saver.join(5)

    cfg = config_store.load_config()
    assert cfg["num_taps"] == 3               # the Save still happened
    assert cfg["album_selection"] == ["Parties", "Other"]
    assert cfg["profiles"]["lounge"]["album_selection"] == ["Parties"]


def test_a_settings_page_opened_before_a_rename_cannot_write_the_old_name_back():
    lib.create_album("Events")
    config_store.update_config(album_all=False, album_selection=["Events"])
    admin_ops.rename_album("Events", "Parties")
    r = _admin().post("/admin/settings", data={
        **REQUIRED, "album_all": "false", "album_selection": json.dumps(["Events"])})
    assert r.status_code == 200
    assert config_store.load_config()["album_selection"] == ["Parties"]


def test_a_profile_save_from_an_old_page_follows_the_rename_too():
    lib.create_album("Events")
    _tick("lounge", ["Events"])
    admin_ops.rename_album("Events", "Parties")
    stored = admin_ops.save_profile(
        "lounge", customise={"album_selection"},
        values={**config_store.DEFAULT_CONFIG, "album_all": False, "album_selection": ["Events"]})
    assert stored["album_selection"] == ["Parties"]


def test_an_album_made_again_under_an_old_name_keeps_meaning_itself():
    lib.create_album("Events")
    admin_ops.rename_album("Events", "Parties")
    lib.create_album("Events")
    saved = admin_ops.save_settings(album_all=False, album_selection=["Events"])
    assert saved["album_selection"] == ["Events"]


def test_two_renames_in_a_row_are_followed_to_the_end():
    lib.create_album("A")
    admin_ops.rename_album("A", "B")
    admin_ops.rename_album("B", "C")
    saved = admin_ops.save_settings(album_all=False, album_selection=["A", "B"])
    assert saved["album_selection"] == ["C"]


# ---- a kept folder says why --------------------------------------------------------

def test_non_photo_files_are_listed_for_an_album_and_hidden_ones_too():
    lib.create_album("Events")
    write_bytes(paths.PHOTOS_DIR / "Events" / "notes.txt", b"n")
    write_bytes(paths.PHOTOS_DIR / "Events" / "sub" / "menu.pdf", b"r")
    write_bytes(paths.PHOTOS_DIR / "Events" / ".DS_Store", b"h")
    write_bytes(paths.PHOTOS_DIR / "Events" / "a.jpg", b"j")
    assert lib.non_photo_files("Events") == [".DS_Store", "notes.txt", "sub/menu.pdf"]
    assert lib.non_photo_files("Unsorted") == [] and lib.non_photo_files("../x") == []
    assert lib.non_photo_files("Nope") == []


def test_the_photos_tab_names_the_files_that_keep_a_deleted_album():
    c = _admin()
    lib.create_album("Events")
    write_bytes(paths.PHOTOS_DIR / "Events" / "notes.txt", b"n")
    r = c.post("/admin/photos/albums/delete", data={"name": "Events"})
    assert r.json()["kept_files"] == ["notes.txt"]
    st = c.get("/admin/photos/status", params={"album": "Events"}).json()
    assert "Events" in st["albums"] and st["non_photo_files"] == ["notes.txt"]


def test_an_album_that_deletes_cleanly_reports_no_kept_files():
    c = _admin()
    lib.create_album("Events")
    r = c.post("/admin/photos/albums/delete", data={"name": "Events"})
    assert r.json()["kept_files"] == []
