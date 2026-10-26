"""Organising photos in a real browser (issue #58).

Multi-select on the thumbnails, move and remove, the Removed view with its
restore, delete-forever and empty, and the album create / rename / delete
dialogs. Python tests cover the rules; this covers that the page drives them.
"""
import json

from PIL import Image

from app import admin_ops, config_store, paths, photo_library as lib

from photo_fixtures import write_image


def _sign_in(page, base):
    r = page.context.request.post(f"{base}/admin/login", form={"password": "testpw"},
                                  max_redirects=0)
    assert r.status == 303


def _photos():
    for n, rel in enumerate(["a.jpg", "b.jpg", "c.jpg", "Events/d.jpg"]):
        write_image(paths.PHOTOS_DIR / rel, Image.new("RGB", (640, 480), (30 + 50 * n, 90, 160)))
    lib.scan()
    lib.process_pending()


def _open(page, serve):
    base = serve()
    _sign_in(page, base)
    page.goto(f"{base}/admin")
    page.click('.tab[data-tab="photos"]')
    page.wait_for_selector("#photos-grid .photo-tile[data-state='ready']")
    return base


def _tick(page, grid, name):
    page.check(f"#{grid} .photo-tile[data-name='{name}'] .photo-pick")


def _tiles(page, grid):
    return sorted(page.eval_on_selector_all(
        f"#{grid} .photo-tile", "ts => ts.map(t => t.dataset.name)"))


def test_select_many_move_them_then_remove_restore_and_empty(serve, page):
    _photos()
    _open(page, serve)
    assert _tiles(page, "photos-grid") == ["a.jpg", "b.jpg", "c.jpg"]
    assert page.is_hidden("#photos-selbar")

    # Multi-select, then move to Events.
    _tick(page, "photos-grid", "a.jpg")
    _tick(page, "photos-grid", "b.jpg")
    assert page.inner_text("#photos-selcount") == "2 selected"
    page.select_option("#photos-moveto", "Events")
    page.click("#photos-move")
    page.wait_for_function("document.querySelectorAll('#photos-grid .photo-tile').length === 1")
    assert _tiles(page, "photos-grid") == ["c.jpg"]
    assert page.is_hidden("#photos-selbar")                    # the selection was spent
    page.select_option("#photos-album", "Events")
    page.wait_for_function("document.querySelectorAll('#photos-grid .photo-tile').length === 3")
    assert _tiles(page, "photos-grid") == ["a.jpg", "b.jpg", "d.jpg"]

    # Remove two (the confirm dialog is accepted).
    page.on("dialog", lambda d: d.accept())
    _tick(page, "photos-grid", "a.jpg")
    _tick(page, "photos-grid", "d.jpg")
    page.click("#photos-remove")
    page.wait_for_function("document.querySelectorAll('#removed-grid .photo-tile').length === 2")
    assert _tiles(page, "photos-grid") == ["b.jpg"]
    assert _tiles(page, "removed-grid") == ["a.jpg", "d.jpg"]
    assert "From Events" in page.inner_text("#removed-grid .photo-tile")
    assert page.eval_on_selector_all(
        "#removed-grid img", "is => is.every(i => i.complete && i.naturalWidth > 0)")
    assert page.is_hidden("#removed-empty")

    # Restore one: it returns to its remembered album.
    _tick(page, "removed-grid", "a.jpg")
    page.click("#removed-restore")
    page.wait_for_function("document.querySelectorAll('#removed-grid .photo-tile').length === 1")
    assert (paths.PHOTOS_DIR / "Events" / "a.jpg").is_file()
    page.wait_for_function("document.querySelectorAll('#photos-grid .photo-tile').length === 2")

    # Delete forever, then the bin is empty.
    _tick(page, "removed-grid", "d.jpg")
    page.click("#removed-delete")
    page.wait_for_selector("#removed-empty:not([hidden])")
    assert list(paths.PHOTOS_REMOVED_DIR.iterdir()) == []

    # Empty removed clears several at once.
    _tick(page, "photos-grid", "a.jpg")
    _tick(page, "photos-grid", "b.jpg")
    page.click("#photos-remove")
    page.wait_for_function("document.querySelectorAll('#removed-grid .photo-tile').length === 2")
    page.click("#removed-empty-all")
    page.wait_for_selector("#removed-empty:not([hidden])")
    assert list(paths.PHOTOS_REMOVED_DIR.iterdir()) == []


def test_new_rename_and_delete_album_and_a_rename_keeps_a_profile_ticked(serve, page):
    _photos()
    admin_ops.create_profile("lounge")
    admin_ops.save_profile("lounge", customise={"album_selection"},
                           values={**config_store.DEFAULT_CONFIG, "album_all": False,
                                   "album_selection": ["Events"]})
    _open(page, serve)

    answers = []
    def on_dialog(d):
        if d.type == "prompt":
            d.accept(answers.pop(0))
        else:
            d.accept()
    page.on("dialog", on_dialog)

    answers.append("Brew day")
    page.click("#album-new")
    page.wait_for_function("document.getElementById('photos-album').value === 'Brew day'")
    assert (paths.PHOTOS_DIR / "Brew day").is_dir()

    page.select_option("#photos-album", "Events")
    page.wait_for_selector("#photos-grid .photo-tile[data-name='d.jpg']")
    answers.append("Parties")
    page.click("#album-rename")
    page.wait_for_function("document.getElementById('photos-album').value === 'Parties'")
    assert (paths.PHOTOS_DIR / "Parties" / "d.jpg").is_file()
    assert config_store.load_config()["profiles"]["lounge"]["album_selection"] == ["Parties"]

    page.click("#album-delete")
    page.wait_for_function("document.getElementById('photos-album').value === 'Unsorted'")
    assert not (paths.PHOTOS_DIR / "Parties").exists()
    page.wait_for_selector("#removed-grid .photo-tile[data-name='d.jpg']")
    assert "From Parties" in page.inner_text("#removed-grid .photo-tile")

    # A refused name shows the reason and changes nothing.
    answers.append("a/b")
    page.click("#album-new")
    page.wait_for_selector("#toast.err")
    assert "cannot contain" in page.inner_text("#toast")


def test_the_retention_days_save_from_the_removed_panel(serve, page):
    _photos()
    _open(page, serve)
    page.fill("#retention-days", "9")
    page.click("#retention-form button")
    page.wait_for_function("document.getElementById('toast').textContent.includes('9 days')")
    assert config_store.load_config()["photo_removed_retention_days"] == 9
    page.reload()
    assert json.loads(page.eval_on_selector("#retention-days", "e => JSON.stringify(e.value)")) == "9"


def test_the_display_copies_panel_saves_format_and_size_and_survives_a_reload(serve, page):
    _photos()
    _open(page, serve)
    page.select_option("#photo-format", "jpeg")
    page.select_option("#photo-size", "1080p")
    page.click("#copies-form button")
    page.wait_for_function("document.getElementById('toast').textContent.includes('JPEG')")
    cfg = config_store.load_config()
    assert (cfg["photo_format"], cfg["photo_size"]) == ("jpeg", "1080p")
    page.reload()
    page.click('.tab[data-tab="photos"]')
    assert page.input_value("#photo-format") == "jpeg"
    assert page.input_value("#photo-size") == "1080p"


def test_a_deleted_album_kept_by_a_non_photo_file_stays_in_view_and_says_why(serve, page):
    _photos()
    (paths.PHOTOS_DIR / "Events" / "notes.txt").write_text("keep me")
    _open(page, serve)
    page.on("dialog", lambda d: d.accept())
    page.select_option("#photos-album", "Events")
    page.wait_for_selector("#photos-grid .photo-tile[data-name='d.jpg']")
    assert "notes.txt" in page.inner_text("#album-kept")      # said before it is deleted

    page.click("#album-delete")
    page.wait_for_selector("#removed-grid .photo-tile[data-name='d.jpg']")
    page.wait_for_selector("#photos-grid .photo-tile", state="detached")
    assert page.input_value("#photos-album") == "Events"
    assert page.is_visible("#album-kept") and "notes.txt" in page.inner_text("#album-kept")
    assert "not photos" in page.inner_text("#toast")
    assert (paths.PHOTOS_DIR / "Events" / "notes.txt").is_file()
