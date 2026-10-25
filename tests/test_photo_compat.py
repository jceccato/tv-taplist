"""Compatibility mode (JPEG) and 1080p Display copies (issue #60, ADR-0008).

Format and size are global settings. Changing either makes every Display copy
stale; the old copies keep being served until each new one is written, and
removal must still find a copy written under the old format.
"""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config_store, paths, photo_library as lib
from app.main import app

from photo_fixtures import exif_bytes, halves, p3_profile, write_image


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    monkeypatch.setattr(lib, "kick_processing", lambda: None)


def build(*rels, size=(800, 600)):
    for n, rel in enumerate(rels):
        write_image(paths.PHOTOS_DIR / rel, halves(size, left=(10 + n * 30, 40, 90)))
    lib.scan()
    lib.process_pending()


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


def age_out(monkeypatch):
    """Let the grace period of every replaced copy pass (a faked clock), then sweep."""
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + lib.ORPHAN_GRACE_SECONDS + 60)
    lib.scan()


def display_files():
    return sorted(p.name for p in (paths.PHOTO_CACHE_DIR / "display").iterdir())


def test_the_defaults_are_webp_at_4k_and_both_are_global():
    cfg = config_store.load_config()
    assert (cfg["photo_format"], cfg["photo_size"]) == ("webp", "4k")
    assert {"photo_format", "photo_size"} <= config_store.NEVER_CUSTOMISABLE
    assert lib.settings_key() == "webp-q99-3840x2160-p1"      # existing copies stay valid


def test_a_bad_stored_value_falls_back_to_the_default():
    saved = config_store.update_config(photo_format="avif", photo_size="8k")
    assert (saved["photo_format"], saved["photo_size"]) == ("webp", "4k")


def test_a_jpeg_copy_is_a_baseline_jpeg_with_no_metadata_and_above_all_no_gps():
    config_store.update_config(photo_format="jpeg")
    write_image(paths.PHOTOS_DIR / "one.jpg", halves((800, 600)),
                exif=exif_bytes(orientation=1, taken="2026:10:11 14:03:22", gps=True),
                icc_profile=p3_profile())
    assert Image.open(paths.PHOTOS_DIR / "one.jpg").getexif().get_ifd(0x8825)   # the fixture has a fix
    lib.scan()
    lib.process_pending()
    (photo,) = lib.ready_photos()
    assert photo.filename.endswith(".jpg")
    path = lib.copy_file(photo.filename)
    data = path.read_bytes()
    assert data[:2] == b"\xff\xd8"
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and img.mode == "RGB"
    assert not img.getexif() and not ({"exif", "icc_profile", "xmp", "adobe"} & set(img.info))
    assert not img.info.get("progressive") and not img.info.get("progression")
    assert b"Exif" not in data and b"GPS" not in data and b"XMP" not in data and b"ICC_PROFILE" not in data
    assert img.size == (2880, 2160)                      # fitted up to 4K, aspect kept
    assert photo.taken == "2026-10-11T14:03:22"          # the date is still read before stripping


def test_a_jpeg_copy_is_upright_and_flattens_transparency():
    config_store.update_config(photo_format="jpeg")
    tall = halves((400, 200))                            # red left, blue right
    write_image(paths.PHOTOS_DIR / "turned.jpg", tall, exif=exif_bytes(orientation=6))
    clear = Image.new("RGBA", (200, 100), (255, 255, 255, 0))
    write_image(paths.PHOTOS_DIR / "clear.png", clear)
    lib.scan()
    lib.process_pending()
    rows = {p.name: Image.open(lib.copy_file(p.filename)).convert("RGB") for p in lib.ready_photos()}
    turned = rows["turned.jpg"]
    assert turned.size[0] < turned.size[1]               # orientation 6 turned it upright
    assert sum(turned.getpixel((turned.size[0] // 2, 5))) > 0
    assert rows["clear.png"].getpixel((10, 10)) == (0, 0, 0)   # transparent became the plain background


def test_a_1080p_copy_is_fitted_to_1920_by_1080_in_both_directions():
    config_store.update_config(photo_size="1080p")
    build("big.jpg", size=(4000, 3000))
    write_image(paths.PHOTOS_DIR / "small.jpg", halves((400, 200)))
    lib.scan()
    lib.process_pending()
    sizes = {p.name: Image.open(lib.copy_file(p.filename)).size for p in lib.ready_photos()}
    assert sizes == {"big.jpg": (1440, 1080), "small.jpg": (1920, 960)}


def test_a_1080p_jpeg_copy_has_both_changes():
    config_store.update_config(photo_format="jpeg", photo_size="1080p")
    build("a.jpg", size=(3000, 2000))
    (photo,) = lib.ready_photos()
    img = Image.open(lib.copy_file(photo.filename))
    assert img.format == "JPEG" and img.size == (1620, 1080)


def test_changing_the_format_keeps_serving_the_old_copy_until_each_new_one_is_written(monkeypatch):
    build("a.jpg", "b.jpg")
    old = {p.name: p for p in lib.ready_photos()}
    config_store.update_config(photo_format="jpeg")
    assert lib.status().pending == 2
    # Nothing blanks: both old WebP copies still list and serve.
    still = {p.name: p for p in lib.ready_photos()}
    assert {n: p.copy_id for n, p in still.items()} == {n: p.copy_id for n, p in old.items()}
    assert all(p.filename.endswith(".webp") and lib.copy_file(p.filename) for p in still.values())

    assert lib.process_next() is True                    # one at a time
    mixed = {p.name: p for p in lib.ready_photos()}
    assert mixed["a.jpg"].filename.endswith(".jpg") and mixed["b.jpg"].filename.endswith(".webp")
    assert lib.copy_file(old["a.jpg"].filename) is not None        # the replaced copy lingers for the grace period
    assert lib.copy_file(old["b.jpg"].filename) is not None        # the other is still served
    assert len(lib.ready_photos()) == 2 and lib.status().ready == 2

    lib.process_pending()
    assert [p.filename[-4:] for p in lib.ready_photos()] == [".jpg", ".jpg"]
    age_out(monkeypatch)
    assert not any(n.endswith(".webp") for n in display_files())
    assert lib.status().pending == 0


def test_changing_the_size_keeps_serving_the_old_copy_until_replaced():
    build("a.jpg")
    (old,) = lib.ready_photos()
    config_store.update_config(photo_size="1080p")
    assert [p.copy_id for p in lib.ready_photos()] == [old.copy_id]
    lib.process_pending()
    (new,) = lib.ready_photos()
    assert new.copy_id != old.copy_id and Image.open(lib.copy_file(new.filename)).size == (1440, 1080)


def test_changing_back_rebuilds_again(monkeypatch):
    build("a.jpg")
    config_store.update_config(photo_format="jpeg")
    lib.process_pending()
    config_store.update_config(photo_format="webp")
    assert lib.status().pending == 1
    lib.process_pending()
    (photo,) = lib.ready_photos()
    age_out(monkeypatch)
    assert photo.filename.endswith(".webp") and display_files() == [photo.filename]


def test_removing_a_photo_during_a_format_change_deletes_its_old_format_copy(monkeypatch):
    build("a.jpg", "b.jpg")
    config_store.update_config(photo_format="jpeg")       # both copies are stale WebP
    rows = {r.name: r for r in lib.photos_in("Unsorted")}
    lib.remove_photos([rows["a.jpg"].photo_id])
    assert len(display_files()) == 1                      # the removed photo's .webp is gone
    assert [p.name for p in lib.ready_photos()] == ["b.jpg"]
    lib.process_pending()
    assert [p.filename[-4:] for p in lib.ready_photos()] == [".jpg"]
    age_out(monkeypatch)
    assert len(display_files()) == 1


def test_removing_a_photo_after_a_format_change_deletes_its_new_format_copy(monkeypatch):
    build("a.jpg", "b.jpg")
    config_store.update_config(photo_format="jpeg")
    lib.process_pending()
    rows = {r.name: r for r in lib.photos_in("Unsorted")}
    lib.remove_photos([rows["a.jpg"].photo_id])
    age_out(monkeypatch)       # b's replaced WebP copy lingers until its grace is over; a's went at once
    assert display_files() == [lib.ready_photos()[0].filename]
    # The thumbnail waits in Removed (WebP, admin only), whatever the format.
    (removed,) = lib.list_removed()
    assert lib.thumb_file(removed.copy_id + ".webp") is not None


def test_removing_a_photo_whose_copy_is_shared_keeps_the_copy_in_either_format():
    config_store.update_config(photo_format="jpeg")
    write_image(paths.PHOTOS_DIR / "a.jpg", halves((800, 600)))
    write_image(paths.PHOTOS_DIR / "Copy" / "a.jpg", halves((800, 600)))     # identical content
    lib.scan()
    lib.process_pending()
    assert len(display_files()) == 1
    lib.remove_photos([lib.photos_in("Unsorted")[0].photo_id])
    assert len(display_files()) == 1 and len(lib.ready_photos()) == 1


def test_a_copy_written_before_the_format_setting_existed_is_a_webp():
    build("a.jpg")
    idx = lib._load_index()
    for entry in idx.values():
        entry["copy"].pop("ext", None)                     # as an older box wrote it
    lib._save_index()
    lib.reset_state()
    (photo,) = lib.ready_photos()
    assert photo.filename.endswith(".webp")


def test_the_board_serves_a_jpeg_copy_as_image_jpeg():
    config_store.update_config(photo_format="jpeg", album_enabled=True)
    build("a.jpg")
    (photo,) = lib.ready_photos()
    r = TestClient(app).get("/img/photo/" + photo.filename)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert TestClient(app).get("/img/photo/" + photo.copy_id + ".png").status_code == 404


def test_a_hand_edit_of_the_config_is_noticed_without_a_restart():
    build("a.jpg")
    cfg = dict(config_store.load_config(), photo_size="1080p")
    paths.CONFIG_PATH.write_text(json.dumps(cfg))
    assert lib.pipeline().box == (1920, 1080)


# ---- the Admin route -------------------------------------------------------------------

def test_the_pipeline_route_needs_the_admin_login():
    anon = TestClient(app)
    assert anon.post("/admin/photos/pipeline", data={"photo_format": "jpeg"}).status_code == 401
    assert config_store.load_config()["photo_format"] == "webp"


def test_the_pipeline_route_saves_either_setting_and_refuses_anything_else(monkeypatch):
    kicked = []
    monkeypatch.setattr(lib, "kick_processing", lambda: kicked.append(1))
    c = _admin()
    assert c.post("/admin/photos/pipeline", data={"photo_format": "jpeg"}).json() == {
        "ok": True, "format": "jpeg", "size": "4k"}
    assert c.post("/admin/photos/pipeline", data={"photo_size": "1080p"}).json() == {
        "ok": True, "format": "jpeg", "size": "1080p"}
    assert kicked == [1, 1]                                # a change starts the rebuild at once
    assert c.post("/admin/photos/pipeline", data={"photo_format": "gif"}).status_code == 422
    assert c.post("/admin/photos/pipeline", data={"photo_size": "720p"}).status_code == 422
    assert c.post("/admin/photos/pipeline", data={}).json()["format"] == "jpeg"
    assert kicked == [1, 1]
    st = c.get("/admin/photos/status").json()
    assert (st["format"], st["size"]) == ("jpeg", "1080p")


def test_a_settings_save_leaves_the_format_and_size_alone():
    config_store.update_config(photo_format="jpeg", photo_size="1080p")
    c = _admin()
    r = c.post("/admin/settings", data={"num_taps": "1", "max_archive_age_days": "1",
                                        "max_archive_storage_mb": "1"})
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert (cfg["photo_format"], cfg["photo_size"]) == ("jpeg", "1080p")
