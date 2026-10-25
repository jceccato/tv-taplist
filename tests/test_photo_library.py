"""The photo library: albums as folders, and the Display copy pipeline (ADR-0008).

Tested without HTTP, the way `test_tap_store.py` tests the Tap store: real small
images are generated in the tests, written into the photo folder the way an
operator would, and the library is driven through its own operations
(scan, process, list the ready photos). What is asserted is outcomes - what a
Display copy looks like, what a screen would be offered - not helpers.
"""
import io
import os
import shutil
import time

import pytest
from PIL import Image

from app import config_store, paths, photo_library as lib

from photo_fixtures import (
    HEIC_FIXTURE, backdate, dng_bytes, exif_bytes, halves, p3_profile, riff_chunks,
    write_bytes, write_image,
)

# HEIC needs pillow-heif built against a decoder-only libheif (ADR-0009), which a
# plain `pip install` box does not have, so its tests skip there. The image has
# it, and CI runs this suite inside the image with REQUIRE_HEIC=1, which makes a
# missing decoder a failure rather than a quiet skip.
needs_heic = pytest.mark.skipif(
    lib.pillow_heif is None and os.environ.get("REQUIRE_HEIC") != "1",
    reason="pillow-heif is not installed (built from source in the image, ADR-0009)")


def build(*relpaths_and_images):
    """Write photos into the photo folder, scan and process them all."""
    for rel, im, *kw in relpaths_and_images:
        write_image(paths.PHOTOS_DIR / rel, im, **(kw[0] if kw else {}))
    lib.scan()
    lib.process_pending()


def only_copy(album=None):
    ready = lib.ready_photos(None if album is None else [album])
    assert len(ready) == 1
    return ready[0], Image.open(lib.copy_file(ready[0].filename))


# ---- the on-disk contract ---------------------------------------------------------

def test_the_photo_folder_is_called_photos():
    # The operator drops files into <data>/photos by hand (ADR-0008), so the
    # folder name is a user-facing contract, restated here independently.
    assert paths.PHOTOS_DIR == paths.DATA_DIR / "photos"
    assert paths.PHOTO_CACHE_DIR.parent == paths.DATA_DIR


def test_loose_files_are_unsorted_and_folders_are_albums():
    build(("loose.jpg", halves()), ("Brew day/a.jpg", halves()), ("Events/b.png", halves()))
    assert lib.list_albums() == ["Brew day", "Events", "Unsorted"]
    assert {p.name: p.album for p in lib.ready_photos()} == {
        "loose.jpg": "Unsorted", "a.jpg": "Brew day", "b.png": "Events"}


def test_folders_inside_an_album_count_as_part_of_it():
    build(("Brew day/2026/oct/deep.jpg", halves()), ("Brew day/top.jpg", halves((300, 300))))
    assert lib.list_albums() == ["Brew day"]
    assert {p.album for p in lib.ready_photos()} == {"Brew day"}
    assert len(lib.ready_photos(["Brew day"])) == 2


def test_unsorted_is_listed_only_while_loose_photos_exist():
    build(("Events/b.jpg", halves()))
    assert lib.list_albums() == ["Events"]


def test_only_photo_extensions_are_photos_and_hidden_things_are_skipped():
    build(("a.jpg", halves()), ("b.jpeg", halves()), ("c.PNG", halves()), ("d.webp", halves()))
    write_bytes(paths.PHOTOS_DIR / "notes.txt", b"hello")
    write_bytes(paths.PHOTOS_DIR / "layers.psd", b"8BPS")
    write_bytes(paths.PHOTOS_DIR / "clip.mov", b"\0\0\0\x14ftypqt  ")
    (paths.PHOTOS_DIR / ".hidden.jpg").write_bytes(b"x")
    write_image(paths.PHOTOS_DIR / ".secret" / "e.jpg", halves())
    outside = paths.DATA_DIR / "outside.jpg"
    write_image(outside, halves())
    os.symlink(outside, paths.PHOTOS_DIR / "link.jpg")
    lib.scan()
    lib.process_pending()
    assert sorted(p.name for p in lib.ready_photos()) == ["a.jpg", "b.jpeg", "c.PNG", "d.webp"]
    assert lib.status().failed == 0       # ignored files are not "unreadable"


# ---- hand-made changes are picked up by the scan -----------------------------------

def test_a_photo_dropped_in_after_the_first_scan_is_picked_up():
    build(("one.jpg", halves()))
    assert len(lib.ready_photos()) == 1
    write_image(paths.PHOTOS_DIR / "Events" / "two.jpg", halves())
    lib.scan()
    lib.process_pending()
    assert len(lib.ready_photos()) == 2


def test_a_photo_deleted_from_disk_is_simply_gone():
    build(("one.jpg", halves()), ("two.jpg", halves((300, 300))))
    (paths.PHOTOS_DIR / "one.jpg").unlink()
    result = lib.scan()
    assert result.removed == 1
    assert [p.name for p in lib.ready_photos()] == ["two.jpg"]


def test_a_file_still_being_copied_in_waits_for_the_next_scan():
    write_image(paths.PHOTOS_DIR / "new.jpg", halves(), age_seconds=0)
    assert lib.scan().added == 0                 # modified a moment ago: not settled
    assert lib.scan(settle_seconds=0).added == 1


def test_an_unreadable_photo_folder_does_not_empty_the_index():
    build(("one.jpg", halves()))
    shutil.rmtree(paths.PHOTOS_DIR)              # e.g. a bind mount that went away
    lib.scan()
    assert len(lib.ready_photos()) == 1


def test_an_unchanged_file_is_not_hashed_again(monkeypatch):
    build(("one.jpg", halves()))
    calls = []
    real = lib._hash_file
    monkeypatch.setattr(lib, "_hash_file", lambda p: calls.append(p) or real(p))
    lib.scan()
    assert calls == []


# ---- the Display copy -----------------------------------------------------------------

def test_a_photo_shows_only_once_its_copy_is_ready():
    write_image(paths.PHOTOS_DIR / "one.jpg", halves())
    lib.scan()
    assert lib.ready_photos() == []              # known, not yet processed
    assert lib.status().pending == 1
    lib.process_next()
    assert len(lib.ready_photos()) == 1
    assert lib.status().pending == 0


def test_processing_is_one_photo_per_call():
    for i in range(3):
        write_image(paths.PHOTOS_DIR / f"p{i}.jpg", halves((400 + i, 200)))
    lib.scan()
    assert lib.process_next() is True
    assert len(lib.ready_photos()) == 1
    assert lib.process_next() and lib.process_next()
    assert lib.process_next() is False


def test_deleting_the_copy_cache_only_costs_reprocessing():
    build(("one.jpg", halves()), ("two.jpg", halves((300, 300))))
    shutil.rmtree(paths.PHOTO_CACHE_DIR)
    assert lib.ready_photos() == []              # nothing shows until it is rebuilt
    lib.reset_state()                            # as after a restart: the index is gone too
    lib.scan()
    assert lib.ready_photos() == []
    lib.process_pending()
    assert len(lib.ready_photos()) == 2
    # The originals were never touched.
    assert (paths.PHOTOS_DIR / "one.jpg").is_file()


def test_deleting_only_the_copy_files_also_recovers():
    build(("one.jpg", halves()))
    shutil.rmtree(paths.PHOTO_CACHE_DIR / "display")
    assert lib.ready_photos() == []
    lib.process_pending()
    assert len(lib.ready_photos()) == 1


def test_a_copy_is_upright_from_exif_orientation():
    # Orientation 6: the stored pixels must be turned 90 degrees clockwise, so the
    # left half (red) ends up on top of the right half (blue).
    build(("one.jpg", halves((400, 200)), {"exif": exif_bytes(orientation=6)}))
    photo, copy = only_copy()
    assert copy.size[0] < copy.size[1]            # was landscape, now portrait
    assert photo.aspect < 1
    top, bottom = copy.convert("RGB").getpixel((copy.size[0] // 2, 20)), \
        copy.convert("RGB").getpixel((copy.size[0] // 2, copy.size[1] - 20))
    assert top[0] > 150 > top[2] and bottom[2] > 150 > bottom[0]


@pytest.mark.parametrize("size, expected", [
    ((5000, 2500), (3840, 1920)),     # fitted down (wide)
    ((3000, 6000), (1080, 2160)),     # fitted down (tall)
    ((800, 600), (2880, 2160)),       # fitted UP: small photos are scaled, not left to the TV
    ((600, 900), (1440, 2160)),
])
def test_a_copy_is_fitted_to_4k_down_and_up(size, expected):
    build(("one.jpg", Image.new("RGB", size, (90, 120, 150))))
    _, copy = only_copy()
    assert copy.size == expected


def test_a_copy_is_webp_at_quality_99():
    noisy = Image.frombytes("RGB", (400, 300), os.urandom(400 * 300 * 3))
    build(("one.png", noisy))
    photo, _ = only_copy()
    data = lib.copy_file(photo.filename).read_bytes()
    assert riff_chunks(data)[0] == "VP8 "         # lossy WebP, not the lossless 'VP8L'
    assert lib.WEBP_QUALITY == 99
    assert lib.TARGET_SIZE == (3840, 2160)
    lossy_80 = io.BytesIO()
    Image.open(lib.copy_file(photo.filename)).save(lossy_80, "WEBP", quality=80)
    assert len(data) > len(lossy_80.getvalue())   # q99 really is the higher quality


def test_a_copy_carries_no_metadata_and_above_all_no_gps():
    original = halves((800, 600))
    build(("one.jpg", original, {"exif": exif_bytes(orientation=1, taken="2026:10:11 14:03:22", gps=True),
                                 "icc_profile": p3_profile()}))
    # The fixture really does carry a fix, so the assertion below means something.
    assert Image.open(paths.PHOTOS_DIR / "one.jpg").getexif().get_ifd(0x8825)
    photo, copy = only_copy()
    for blob in (lib.copy_file(photo.filename).read_bytes(),
                 (paths.PHOTO_CACHE_DIR / "thumbs" / f"{photo.copy_id}.webp").read_bytes()):
        assert set(riff_chunks(blob)) <= {"VP8 ", "VP8X", "ALPH"}   # no EXIF, XMP or ICCP chunk
        assert b"Exif" not in blob and b"GPS" not in blob and b"XMP" not in blob
        img = Image.open(io.BytesIO(blob))
        assert not img.getexif()
        assert not ({"exif", "icc_profile", "xmp"} & set(img.info))


def test_a_wide_gamut_photo_is_converted_to_srgb():
    flat = Image.new("RGB", (400, 300), (200, 100, 50))        # P3 values
    build(("p3.jpg", flat, {"icc_profile": p3_profile(), "quality": 100, "subsampling": 0}))
    _, copy = only_copy()
    r, g, b = copy.convert("RGB").getpixel((200, 150))
    # Displayed as sRGB the same colour is more saturated (about 216, 93, 24);
    # left as-is it would still read 200, 100, 50.
    assert (r, g, b) != (200, 100, 50)
    assert abs(r - 216) < 8 and abs(g - 93) < 8 and abs(b - 24) < 8


def test_an_srgb_tagged_photo_is_left_alone():
    from PIL import ImageCms
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    build(("s.jpg", Image.new("RGB", (400, 300), (200, 100, 50)), {"icc_profile": srgb, "quality": 100, "subsampling": 0}))
    _, copy = only_copy()
    r, g, b = copy.convert("RGB").getpixel((200, 150))
    assert abs(r - 200) < 6 and abs(g - 100) < 6 and abs(b - 50) < 6


def test_png_transparency_is_kept():
    im = Image.new("RGBA", (400, 300), (10, 200, 10, 255))
    im.paste((0, 0, 0, 0), (0, 0, 200, 150))
    build(("logo.png", im))
    _, copy = only_copy()
    assert copy.mode == "RGBA"
    assert copy.getpixel((10, 10))[3] == 0
    assert copy.getpixel((copy.size[0] - 10, copy.size[1] - 10))[3] == 255


def test_palette_cmyk_and_greyscale_sources_are_all_shown():
    build(("p.png", Image.new("P", (200, 100), 3)),
          ("c.jpg", Image.new("CMYK", (200, 100), (0, 100, 100, 0))),
          ("g.png", Image.new("L", (200, 100), 128)))
    assert sorted(p.name for p in lib.ready_photos()) == ["c.jpg", "g.png", "p.png"]


def test_a_thumbnail_is_made_alongside_the_copy():
    build(("one.jpg", halves((1600, 1200))))
    photo, _ = only_copy()
    thumb = Image.open(paths.PHOTO_CACHE_DIR / "thumbs" / f"{photo.copy_id}.webp")
    assert max(thumb.size) == lib.THUMB_SIZE


# ---- date taken, ordering ----------------------------------------------------------------

def test_date_taken_is_read_before_the_metadata_is_stripped():
    build(("one.jpg", halves(), {"exif": exif_bytes(taken="2026:10:11 14:03:22")}))
    photo, _ = only_copy()
    assert photo.taken == "2026-10-11T14:03:22"


def test_a_photo_with_no_date_taken_has_none_and_sorts_by_its_file_date():
    write_image(paths.PHOTOS_DIR / "old.jpg", halves(), age_seconds=30 * 86400)
    write_image(paths.PHOTOS_DIR / "new.jpg", halves((300, 300)), age_seconds=3600)
    lib.scan()
    lib.process_pending()
    assert {p.name: p.taken for p in lib.ready_photos()} == {"old.jpg": None, "new.jpg": None}
    assert [p.name for p in lib.ready_photos(order="date")] == ["new.jpg", "old.jpg"]


def test_date_order_is_by_date_taken_falling_back_to_file_date():
    # The file dates say b is newest, but b was TAKEN before a; c has no date taken.
    write_image(paths.PHOTOS_DIR / "a.jpg", halves(), exif=exif_bytes(taken="2026:10:11 10:00:00"),
                age_seconds=7 * 86400)
    write_image(paths.PHOTOS_DIR / "b.jpg", halves((300, 300)),
                exif=exif_bytes(taken="2024:01:01 09:00:00"), age_seconds=60)
    write_image(paths.PHOTOS_DIR / "c.jpg", halves((200, 300)), age_seconds=3600)
    lib.scan()
    lib.process_pending()
    order = [p.name for p in lib.ready_photos(order="date")]
    assert order[0] == "c.jpg"     # file date: an hour ago, newer than either date taken
    assert order[1:] == ["a.jpg", "b.jpg"]


def test_name_order_is_natural_and_case_blind():
    build(*[(n, halves((300 + i, 200))) for i, n in enumerate(["IMG_10.jpg", "img_2.jpg", "IMG_1.jpg"])])
    assert [p.name for p in lib.ready_photos(order="name")] == ["IMG_1.jpg", "img_2.jpg", "IMG_10.jpg"]


def test_the_album_filter_is_by_album_name():
    build(("a/1.jpg", halves()), ("b/2.jpg", halves((300, 300))), ("3.jpg", halves((200, 300))))
    assert [p.name for p in lib.ready_photos(["b"])] == ["2.jpg"]
    assert sorted(p.name for p in lib.ready_photos(["a", "Unsorted"])) == ["1.jpg", "3.jpg"]
    assert lib.ready_photos(["no-such-album"]) == []
    assert len(lib.ready_photos(None)) == 3


# ---- stale copies, failures, serving ------------------------------------------------------------

def test_a_stale_copy_keeps_being_served_until_its_replacement_is_ready():
    build(("one.jpg", halves((800, 600))))
    old, copy = only_copy()
    assert copy.size == (2880, 2160)
    config_store.update_config(photo_size="1080p")             # a settings change
    assert lib.status().pending == 1                          # every copy is now stale
    still = lib.ready_photos()
    assert [p.copy_id for p in still] == [old.copy_id]        # the old copy still shows
    assert lib.copy_file(still[0].filename).is_file()
    lib.process_next()
    new = lib.ready_photos()
    assert len(new) == 1 and new[0].copy_id != old.copy_id
    assert Image.open(lib.copy_file(new[0].filename)).size == (1440, 1080)
    assert lib.copy_file(old.filename) is not None            # screens on the old list still fetch it (see below)


class Clock:
    """A fake clock: the real time plus an offset the test advances, never slept."""

    def __init__(self, monkeypatch):
        real = time.time
        self.offset = 0.0
        monkeypatch.setattr(time, "time", lambda: real() + self.offset)

    def advance(self, seconds):
        self.offset += seconds


def test_a_replaced_copy_keeps_serving_for_the_grace_period_then_is_swept(monkeypatch):
    clock = Clock(monkeypatch)
    build(("one.jpg", halves((800, 600))))
    old, _ = only_copy()
    old_path = lib.copy_file(old.filename)
    old_path_age = 3 * 86400
    backdate(old_path, old_path_age)                          # written long ago: its own age must not count
    config_store.update_config(photo_size="1080p")
    lib.process_next()
    new, _ = only_copy()
    assert new.copy_id != old.copy_id
    assert lib.copy_file(old.filename) is not None            # a screen on the old list gets it
    lib.scan()
    assert lib.copy_file(old.filename) is not None            # a scan straight after still spares it
    assert lib.ORPHAN_GRACE_SECONDS >= 2 * 30                 # at least two poll intervals (POLL_MS in display.js)
    clock.advance(lib.ORPHAN_GRACE_SECONDS - 5)
    lib.scan()
    assert lib.copy_file(old.filename) is not None
    clock.advance(10)
    lib.scan()
    assert lib.copy_file(old.filename) is None                # swept
    assert lib.copy_file(new.filename) is not None


def test_a_removed_photo_stops_serving_its_copy_at_once_even_just_after_a_rebuild(monkeypatch):
    Clock(monkeypatch)
    build(("one.jpg", halves((800, 600))))
    old, _ = only_copy()
    config_store.update_config(photo_size="1080p")
    lib.process_next()
    new, _ = only_copy()
    assert lib.copy_file(old.filename) is not None            # in its grace period
    (row,) = lib.photos_in("Unsorted")
    lib.remove_photos([row.photo_id])
    assert lib.copy_file(new.filename) is None                # the current copy goes at once
    assert lib.copy_file(old.filename) is None                # and so does the one in grace
    assert lib.ready_photos() == []


def test_a_replaced_copy_shared_with_another_photo_is_not_retired(monkeypatch):
    same = halves((640, 480))
    build(("a.jpg", same), ("b.jpg", same))
    (shared,) = {p.copy_id for p in lib.ready_photos()}
    write_image(paths.PHOTOS_DIR / "a.jpg", Image.new("RGB", (900, 600), (1, 2, 3)))
    lib.scan()
    lib.process_pending()
    # b still lists the old copy, so it is simply still in use, not retired.
    assert shared in {p.copy_id for p in lib.ready_photos()}
    assert len(lib.ready_photos()) == 2


def test_a_kick_that_arrives_while_a_run_is_finishing_leads_to_another_pass(monkeypatch):
    import threading
    passes = []
    done = threading.Event()

    def fake_process_pending(*a, **k):
        passes.append(1)
        if len(passes) == 1:
            lib.kick_processing()          # arrives while the first run has not yet marked itself finished
        else:
            done.set()
        return 0

    monkeypatch.setattr(lib, "scan", lambda *a, **k: None)
    monkeypatch.setattr(lib, "process_pending", fake_process_pending)
    lib.kick_processing()
    assert done.wait(5)
    assert len(passes) == 2
    for _ in range(100):                   # the run then stops: a later kick starts a fresh one
        with lib._kick_lock:
            if not lib._kick_running:
                break
        time.sleep(0.02)
    assert lib._kick_running is False


def test_replacing_an_original_in_place_rebuilds_its_copy():
    build(("one.jpg", halves((800, 600))))
    first, _ = only_copy()
    write_image(paths.PHOTOS_DIR / "one.jpg", Image.new("RGB", (900, 600), (1, 2, 3)))
    lib.scan()
    assert [p.copy_id for p in lib.ready_photos()] == [first.copy_id]   # old one meanwhile
    lib.process_pending()
    second, _ = only_copy()
    assert second.copy_id != first.copy_id


def test_an_unreadable_file_is_reported_by_name_and_never_raises():
    (paths.PHOTOS_DIR / "Events").mkdir()
    (paths.PHOTOS_DIR / "Events" / "broken.jpg").write_bytes(b"this is not a jpeg")
    backdate(paths.PHOTOS_DIR / "Events" / "broken.jpg", 60)
    write_image(paths.PHOTOS_DIR / "fine.jpg", halves())
    lib.scan()
    lib.process_pending()
    status = lib.status()
    assert (status.total, status.ready, status.failed, status.pending) == (2, 1, 1, 0)
    album, name, reason = status.failures[0]
    assert (album, name) == ("Events", "broken.jpg") and reason
    assert [p.name for p in lib.ready_photos()] == ["fine.jpg"]


def test_a_failed_file_is_not_retried_until_it_changes(monkeypatch):
    (paths.PHOTOS_DIR / "broken.jpg").write_bytes(b"nope")
    backdate(paths.PHOTOS_DIR / "broken.jpg", 60)
    lib.scan()
    assert lib.process_next() is True and lib.process_next() is False
    write_image(paths.PHOTOS_DIR / "broken.jpg", halves())     # the operator re-exports it
    lib.scan()
    lib.process_pending()
    assert [p.name for p in lib.ready_photos()] == ["broken.jpg"]
    assert lib.status().failed == 0


def test_a_truncated_jpeg_is_reported_not_shown():
    full = io.BytesIO()
    halves((800, 600)).save(full, "JPEG")
    (paths.PHOTOS_DIR / "cut.jpg").write_bytes(full.getvalue()[: len(full.getvalue()) // 2])
    backdate(paths.PHOTOS_DIR / "cut.jpg", 60)
    lib.scan()
    lib.process_pending()
    assert lib.ready_photos() == [] and lib.status().failed == 1


def test_a_format_pillow_reads_but_we_do_not_accept_is_refused_by_content():
    # The extension decides nothing: a PPM renamed .jpg is not a photo format here.
    ppm = io.BytesIO()
    Image.new("RGB", (10, 10)).save(ppm, "PPM")
    write_bytes(paths.PHOTOS_DIR / "x.jpg", ppm.getvalue())
    lib.scan()
    lib.process_pending()
    assert lib.ready_photos() == []
    assert "unsupported format" in lib.status().failures[0][2]


@pytest.mark.parametrize("size", [(40, 40), (100, 100)])      # warns, then raises
def test_a_decompression_bomb_is_an_error_not_a_warning(monkeypatch, size):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    build(("bomb.png", Image.new("RGB", size, (1, 1, 1))))
    assert lib.ready_photos() == []
    assert lib.status().failed == 1
    assert lib.MAX_IMAGE_PIXELS == 200_000_000


def test_the_pixel_limit_is_set_explicitly():
    assert Image.MAX_IMAGE_PIXELS == lib.MAX_IMAGE_PIXELS


def test_copy_file_serves_only_well_formed_opaque_names():
    build(("secret-name.jpg", halves()))
    photo, _ = only_copy()
    assert photo.filename.endswith(".webp")
    assert "secret" not in photo.filename and "secret" not in photo.copy_id
    assert lib.copy_file(photo.filename) is not None
    for bad in ("../index.json", "../../config.json", photo.copy_id, "x.webp",
                photo.copy_id + ".jpg", "/etc/passwd", photo.filename + "/..", ""):
        assert lib.copy_file(bad) is None


def test_the_same_content_in_two_albums_shares_one_copy_and_both_show():
    same = halves((640, 480))
    build(("a/1.jpg", same), ("b/1.jpg", same))
    ready = lib.ready_photos()
    assert len(ready) == 2 and len({p.copy_id for p in ready}) == 1


def test_orphaned_cache_files_are_swept_after_the_grace_period():
    build(("one.jpg", halves()))
    stray = paths.PHOTO_CACHE_DIR / "display" / ("0" * 32 + ".webp")
    stray.write_bytes(b"x")
    lib.scan()
    assert stray.exists()                                     # too fresh to be sure it is an orphan
    backdate(stray, lib.ORPHAN_GRACE_SECONDS + 60)
    lib.scan()
    assert not stray.exists()
    assert len(lib.ready_photos()) == 1


def test_the_index_survives_a_restart_without_rehashing(monkeypatch):
    build(("one.jpg", halves()))
    lib.reset_state()
    calls = []
    real = lib._hash_file
    monkeypatch.setattr(lib, "_hash_file", lambda p: calls.append(p) or real(p))
    lib.scan()
    assert calls == [] and len(lib.ready_photos()) == 1
    assert lib.status().pending == 0


def test_a_corrupt_index_is_just_rebuilt():
    build(("one.jpg", halves()))
    (paths.PHOTO_CACHE_DIR / "index.json").write_text("{not json")
    lib.reset_state()
    lib.scan()
    lib.process_pending()
    assert len(lib.ready_photos()) == 1


# ---- every phone and camera format (issue #59) ----------------------------------------------

def _is_red(px): return px[0] > 150 > px[2] and px[1] < 100
def _is_blue(px): return px[2] > 150 > px[0] and px[1] < 100
def _is_green(px): return px[1] > 150 > px[0] and px[2] < 120


@pytest.mark.parametrize("name, kwargs", [
    ("still.gif", {}),
    ("scan.tif", {}),
    ("scan.tiff", {"compression": "tiff_lzw"}),
    ("old.bmp", {}),
    ("phone.avif", {"quality": 90}),
])
def test_gif_tiff_bmp_and_avif_each_become_a_correct_display_copy(name, kwargs):
    build((name, halves((400, 200)), kwargs))
    photo, copy = only_copy()
    assert copy.format == "WEBP" and copy.size == (3840, 1920)
    rgb = copy.convert("RGB")
    assert _is_red(rgb.getpixel((200, 960))) and _is_blue(rgb.getpixel((3640, 960)))
    assert set(riff_chunks(lib.copy_file(photo.filename).read_bytes())) <= {"VP8 ", "VP8X", "ALPH"}


def test_an_animated_gif_shows_its_first_frame():
    first, second = halves((300, 200)), Image.new("RGB", (300, 200), (20, 200, 20))
    path = paths.PHOTOS_DIR / "anim.gif"
    first.save(path, "GIF", save_all=True, append_images=[second], duration=100, loop=0)
    backdate(path, 60)
    lib.scan()
    lib.process_pending()
    _, copy = only_copy()
    rgb = copy.convert("RGB")
    assert _is_red(rgb.getpixel((100, copy.height // 2)))
    assert _is_blue(rgb.getpixel((copy.width - 100, copy.height // 2)))


def test_a_tiff_is_turned_upright_from_its_orientation():
    exif = Image.Exif()
    exif[0x0112] = 6
    build(("scan.tif", halves((400, 200)), {"exif": exif}))
    _, copy = only_copy()
    rgb = copy.convert("RGB")
    assert copy.width < copy.height
    assert _is_red(rgb.getpixel((copy.width // 2, 20)))
    assert _is_blue(rgb.getpixel((copy.width // 2, copy.height - 20)))


@needs_heic
def test_a_heic_becomes_an_upright_display_copy_with_its_date_and_no_metadata():
    shutil.copy(HEIC_FIXTURE, paths.PHOTOS_DIR / "IMG_0001.HEIC")
    backdate(paths.PHOTOS_DIR / "IMG_0001.HEIC", 60)
    lib.scan()
    lib.process_pending()
    assert lib.status().failed == 0
    photo, copy = only_copy()
    # Stored landscape with a 90 degree turn: upright it is a portrait, red over
    # blue, turned once (libheif applies the turn; nothing turns it again).
    assert copy.size == (1440, 2160) and photo.aspect < 1
    rgb = copy.convert("RGB")
    assert _is_red(rgb.getpixel((720, 100))) and _is_blue(rgb.getpixel((720, 2060)))
    assert photo.taken == "2025-03-04T05:06:07"
    blob = lib.copy_file(photo.filename).read_bytes()
    assert set(riff_chunks(blob)) <= {"VP8 ", "VP8X", "ALPH"}
    assert b"Exif" not in blob and b"GPS" not in blob


def test_a_heic_without_the_decoder_is_reported_with_what_to_do(monkeypatch):
    monkeypatch.setattr(lib, "pillow_heif", None)
    shutil.copy(HEIC_FIXTURE, paths.PHOTOS_DIR / "IMG_0001.heic")
    backdate(paths.PHOTOS_DIR / "IMG_0001.heic", 60)
    lib.scan()
    lib.process_pending()
    [(_, name, reason)] = lib.status().failures
    assert name == "IMG_0001.heic" and "HEIC" in reason


def test_a_camera_raw_with_no_big_preview_is_developed_at_half_size():
    # A small linear DNG with no embedded preview: LibRaw develops it, and turns
    # it upright from the RAW's own orientation (6: 90 degrees clockwise).
    raw = Image.new("RGB", (120, 80), (40, 200, 40))
    write_bytes(paths.PHOTOS_DIR / "Camera" / "DSC_0001.dng",
                dng_bytes(raw, orientation=6, taken="2023:01:02 03:04:05"))
    lib.scan()
    lib.process_pending()
    assert lib.status().failed == 0
    photo, copy = only_copy()
    assert copy.size == (1440, 2160)
    assert photo.taken == "2023-01-02T03:04:05"
    assert _is_green(copy.convert("RGB").getpixel((720, 1080)))
    out = lib._render(paths.PHOTOS_DIR / "Camera" / "DSC_0001.dng", lib.pipeline())
    assert out.source == lib.SOURCE_RAW_DEVELOP


def test_a_camera_raw_uses_its_embedded_preview_when_it_is_big_enough():
    # The preview (red/blue halves, 2400 px) differs from the RAW (green), so the
    # copy shows which one was used. The preview has no orientation of its own,
    # so the RAW's (6) turns it. The date taken is the RAW's.
    raw = Image.new("RGB", (120, 80), (40, 200, 40))
    preview = halves((2400, 1600))
    data = dng_bytes(raw, preview, orientation=6, taken="2024:05:06 07:08:09")
    write_bytes(paths.PHOTOS_DIR / "IMG_0002.dng", data)
    lib.scan()
    lib.process_pending()
    photo, copy = only_copy()
    assert copy.size == (1440, 2160)
    rgb = copy.convert("RGB")
    assert _is_red(rgb.getpixel((720, 100))) and _is_blue(rgb.getpixel((720, 2060)))
    assert photo.taken == "2024-05-06T07:08:09"
    assert lib._render(paths.PHOTOS_DIR / "IMG_0002.dng", lib.pipeline()).source == lib.SOURCE_RAW_PREVIEW


def test_an_embedded_preview_with_its_own_orientation_is_turned_by_that_alone():
    # Turned once: by the preview's EXIF, not again by the RAW's flag.
    data = dng_bytes(Image.new("RGB", (120, 80), (40, 200, 40)), halves((2400, 1600)),
                     orientation=6, preview_exif=exif_bytes(orientation=6))
    write_bytes(paths.PHOTOS_DIR / "IMG_0003.dng", data)
    lib.scan()
    lib.process_pending()
    _, copy = only_copy()
    assert copy.width < copy.height
    assert _is_red(copy.convert("RGB").getpixel((copy.width // 2, 50)))


def test_a_camera_raw_with_no_date_falls_back_to_the_file_date():
    # LibRaw reports an unknown date as the epoch, which must not become "1970".
    write_bytes(paths.PHOTOS_DIR / "nodate.dng", dng_bytes(Image.new("RGB", (60, 40), (40, 200, 40))))
    lib.scan()
    lib.process_pending()
    photo, _ = only_copy()
    assert photo.taken is None and photo.date > 1e9


def test_a_camera_raw_with_a_small_preview_is_developed_instead():
    raw = Image.new("RGB", (120, 80), (40, 200, 40))
    small = halves((lib.RAW_PREVIEW_MIN_LONG_SIDE - 400, 1000))
    write_bytes(paths.PHOTOS_DIR / "IMG_0004.dng", dng_bytes(raw, small))
    lib.scan()
    lib.process_pending()
    _, copy = only_copy()
    assert _is_green(copy.convert("RGB").getpixel((copy.width // 2, copy.height // 2)))
    assert lib.RAW_PREVIEW_MIN_LONG_SIDE == 2000


@pytest.mark.parametrize("name", ["IMG_0001.CR2", "IMG_0001.CR3", "DSC_0001.NEF", "DSC01.ARW",
                                  "DSCF0001.RAF", "P0001.ORF", "broken.dng"])
def test_every_raw_extension_is_read_and_a_corrupt_one_is_reported_not_raised(name):
    write_bytes(paths.PHOTOS_DIR / name, b"II*\0" + os.urandom(2048))
    write_image(paths.PHOTOS_DIR / "fine.jpg", halves())
    lib.scan()
    lib.process_pending()
    status = lib.status()
    assert (status.total, status.ready, status.failed, status.pending) == (2, 1, 1, 0)
    [(_, failed, reason)] = status.failures
    assert failed == name and "RAW" in reason


def test_an_oversized_raw_is_reported_before_it_is_developed(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 5000)
    write_bytes(paths.PHOTOS_DIR / "huge.dng", dng_bytes(Image.new("RGB", (120, 80), (9, 9, 9))))
    lib.scan()
    lib.process_pending()
    [(_, _, reason)] = lib.status().failures
    assert "too large" in reason


def test_an_unexpected_error_in_one_photo_never_stops_the_queue(monkeypatch):
    real = lib._decode

    def flaky(path, pipe):
        if path.name == "a.jpg":
            raise KeyError("something nobody expected")
        return real(path, pipe)

    monkeypatch.setattr(lib, "_decode", flaky)
    build(("a.jpg", halves()), ("b.jpg", halves((300, 300))))
    assert [p.name for p in lib.ready_photos()] == ["b.jpg"]
    [(_, name, reason)] = lib.status().failures
    assert name == "a.jpg" and "could not be processed" in reason


@pytest.mark.parametrize("long_side, soft", [(800, True), (999, True), (1000, False), (3000, False)])
def test_a_small_photo_is_marked_low_resolution_and_still_shown(long_side, soft):
    build(("p.jpg", halves((long_side, long_side * 2 // 3))))
    [row] = lib.photos_in(lib.UNSORTED)
    assert row.state == "ready" and row.low_res is soft
    assert len(lib.ready_photos()) == 1


def test_a_portrait_is_judged_by_its_long_side_and_a_raw_by_its_full_size():
    build(("tall.jpg", halves((700, 1200))))
    # The RAW is developed at half size, but it is the camera's full size that counts.
    write_bytes(paths.PHOTOS_DIR / "cam.dng", dng_bytes(Image.new("RGB", (1200, 800), (40, 200, 40))))
    lib.scan()
    lib.process_pending()
    assert {r.name: r.low_res for r in lib.photos_in(lib.UNSORTED)} == {
        "tall.jpg": False, "cam.dng": False}


def test_heic_and_raw_uploads_are_taken():
    for name, data in (("IMG_0001.HEIC", HEIC_FIXTURE.read_bytes()),
                       ("DSC_0001.DNG", dng_bytes(Image.new("RGB", (60, 40), (40, 200, 40)))),
                       *((f"x{ext}", ext.encode()) for ext in (
                           ".cr3", ".nef", ".arw", ".raf", ".orf", ".cr2", ".gif", ".tif",
                           ".tiff", ".bmp", ".avif", ".heif"))):
        assert _upload(data, name).outcome == "stored", name


def test_heic_is_never_installed_from_a_pypi_wheel():
    # ADR-0009: pillow-heif's wheels bundle x265 (GPLv2-only). It must be built from
    # source in the image, and kept out of the requirements a plain `pip install` uses.
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if not (root / "Dockerfile").is_file():
        pytest.skip("run inside the image, which holds no build files")
    for req in ("requirements.txt", "requirements-dev.txt"):
        lines = [ln.split("#")[0].strip().lower() for ln in (root / req).read_text().splitlines()]
        assert not any(ln.startswith(("pillow-heif", "pillow_heif")) for ln in lines), req
    dockerfile = (root / "Dockerfile").read_text()
    assert "--no-binary pillow-heif" in dockerfile
    assert "--no-install-recommends" in dockerfile and "x265" in dockerfile


# ---- guards on who may touch the photo folders ---------------------------------------------

def test_nothing_outside_the_photo_library_builds_a_photo_path():
    # ADR-0008, like ADR-0003 for Tap files: one module owns the photo folders.
    import ast
    from pathlib import Path
    offenders = []
    for py in sorted((Path(__file__).resolve().parent.parent / "app").glob("*.py")):
        if py.name in ("photo_library.py", "paths.py"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.ImportFrom):
                names += [a.name for a in node.names]
            if {"PHOTOS_DIR", "PHOTO_CACHE_DIR", "PHOTOS_REMOVED_DIR"} & set(names):
                offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == []


def test_the_scheduler_scans_for_photos_every_minute_and_once_at_start(monkeypatch):
    from app import scheduler

    jobs = {}

    class FakeScheduler:
        def __init__(self, *a, **k): pass
        def add_job(self, func, trigger, **kw): jobs[kw["id"]] = (func, trigger, kw)
        def start(self): pass

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.start_scheduler()
    func, trigger, kw = jobs["photo_scan"]
    assert trigger == "interval" and kw["minutes"] == 1
    assert kw["max_instances"] == 1 and kw["coalesce"] is True
    assert kw["next_run_time"] is not None            # runs at boot, not a minute later
    assert func is scheduler._safe_photo_scan


def test_a_copy_that_cannot_be_written_is_recorded_not_retried_forever(monkeypatch):
    write_image(paths.PHOTOS_DIR / "one.jpg", halves())
    lib.scan()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(lib, "atomic_write_bytes", boom)
    assert lib.process_pending() == 1          # one attempt, then it is marked failed
    assert lib.status().failed == 1
    assert lib.process_next() is False


# ---- uploads (issue #57) ------------------------------------------------------------

def _jpeg(color=(200, 30, 30), size=(300, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _upload(data: bytes, name: str, album: str = lib.UNSORTED, chunk: int = 4096):
    with lib.begin_upload(len(data)) as up:
        for i in range(0, len(data), chunk):
            up.write(data[i:i + chunk])
        return up.commit(name, album)


def _staged():
    d = paths.PHOTO_UPLOAD_STAGING_DIR
    return list(d.iterdir()) if d.exists() else []


def test_an_upload_is_filed_in_its_album_and_processed_like_any_photo():
    (paths.PHOTOS_DIR / "Events").mkdir()
    r = _upload(_jpeg(), "IMG_1.jpg", "Events")
    assert (r.outcome, r.album, r.name, r.renamed) == ("stored", "Events", "IMG_1.jpg", False)
    assert (paths.PHOTOS_DIR / "Events" / "IMG_1.jpg").read_bytes() == _jpeg()
    assert _staged() == []
    lib.process_pending()
    assert [(p.album, p.name) for p in lib.ready_photos()] == [("Events", "IMG_1.jpg")]


def test_uploads_default_to_unsorted_the_loose_files():
    _upload(_jpeg(), "loose.jpg")
    assert (paths.PHOTOS_DIR / "loose.jpg").is_file()
    assert lib.list_albums() == ["Unsorted"]


def test_an_exact_duplicate_by_content_is_skipped_within_an_album_even_under_another_name():
    (paths.PHOTOS_DIR / "Events").mkdir()
    _upload(_jpeg(), "first.jpg", "Events")
    r = _upload(_jpeg(), "renamed.jpg", "Events")
    assert (r.outcome, r.album, r.name) == ("duplicate", "Events", "first.jpg")
    assert not (paths.PHOTOS_DIR / "Events" / "renamed.jpg").exists()
    assert _staged() == []
    # Another album may hold the same photo.
    assert _upload(_jpeg(), "renamed.jpg").outcome == "stored"


def test_a_duplicate_of_a_photo_dropped_in_by_hand_is_caught_too():
    write_image(paths.PHOTOS_DIR / "hand.jpg", Image.new("RGB", (300, 200), (1, 2, 3)))
    # Dropped in a moment ago: too fresh for the scan to have hashed it yet.
    same = (paths.PHOTOS_DIR / "hand.jpg").read_bytes()
    assert _upload(same, "again.jpg").outcome == "duplicate"


def test_a_name_clash_gets_a_suffix_before_the_extension():
    a = _upload(_jpeg((1, 1, 1)), "IMG.jpg")
    b = _upload(_jpeg((2, 2, 2)), "IMG.jpg")
    c = _upload(_jpeg((3, 3, 3)), "img.JPG")      # case-blind, like a Windows bind mount
    assert (a.name, b.name, c.name) == ("IMG.jpg", "IMG (2).jpg", "img (3).jpg")
    assert (b.renamed, c.renamed) == (True, True)
    assert sorted(p.name for p in paths.PHOTOS_DIR.iterdir()) == ["IMG (2).jpg", "IMG.jpg", "img (3).jpg"]


@pytest.mark.parametrize("sent, kept", [
    ("../../etc/passwd.jpg", "passwd.jpg"),
    ("C:\\Users\\me\\a.png", "a.png"),
    (".hidden.jpg", "hidden.jpg"),
    ("we<ird>:name?.webp", "weirdname.webp"),
    ("", None),
])
def test_a_client_supplied_name_can_never_leave_the_album(sent, kept):
    if kept is None:
        with pytest.raises(lib.UploadRejected):
            _upload(_jpeg(), sent)
        return
    r = _upload(_jpeg(), sent)
    assert r.name == kept
    assert [p.name for p in paths.PHOTOS_DIR.iterdir()] == [kept]


def test_only_photo_formats_are_taken_and_nothing_is_left_staged():
    with pytest.raises(lib.UploadRejected, match="not a photo format"):
        _upload(b"%PDF-1.4", "doc.pdf")
    with pytest.raises(lib.UploadRejected, match="empty"):
        _upload(b"", "empty.jpg")
    assert _staged() == [] and list(paths.PHOTOS_DIR.iterdir()) == []


def test_an_unknown_album_is_refused_and_never_created():
    with pytest.raises(lib.UnknownAlbum):
        lib.begin_upload(10, "Nope")                     # refused before any byte is sent
    with pytest.raises(lib.UnknownAlbum):
        _upload(_jpeg(), "a.jpg", "../elsewhere")
    with pytest.raises(lib.UnknownAlbum):
        _upload(_jpeg(), "a.jpg", "Nope")
    assert list(paths.PHOTOS_DIR.iterdir()) == [] and _staged() == []


def test_the_250_mb_cap_is_enforced_as_declared_and_as_received(monkeypatch):
    assert lib.MAX_UPLOAD_BYTES == 250 * 1024 * 1024
    with pytest.raises(lib.UploadTooLarge, match="250 MB"):
        lib.begin_upload(lib.MAX_UPLOAD_BYTES + 1)         # announced: refused before staging
    assert _staged() == []
    monkeypatch.setattr(lib, "MAX_UPLOAD_BYTES", 1000)       # received: the stream is cut off
    with pytest.raises(lib.UploadTooLarge):
        with lib.begin_upload() as up:
            up.write(b"x" * 600)
            up.write(b"x" * 600)
    assert _staged() == []
    with lib.begin_upload(1000) as up:                        # exactly at the cap is fine
        up.write(b"x" * 1000)


def test_uploads_are_refused_below_one_gb_free(monkeypatch):
    assert lib.MIN_FREE_BYTES == 1024 ** 3
    monkeypatch.setattr(lib, "_free_bytes", lambda: lib.MIN_FREE_BYTES - 1)
    with pytest.raises(lib.LowDiskSpace, match="free"):
        lib.begin_upload(10)
    monkeypatch.setattr(lib, "_free_bytes", lambda: lib.MIN_FREE_BYTES)
    lib.begin_upload(10).close()
    assert _staged() == []


def test_a_staged_upload_is_removed_on_every_exit_path(monkeypatch):
    with pytest.raises(RuntimeError):                       # the request blew up mid-stream
        with lib.begin_upload() as up:
            up.write(b"abc")
            assert len(_staged()) == 1
            raise RuntimeError("client went away")
    assert _staged() == []
    with lib.begin_upload() as up:                          # never committed
        up.write(b"abc")
    assert _staged() == []
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):                            # the final move failed
        _upload(_jpeg(), "a.jpg")
    assert _staged() == []


def test_a_stale_staged_file_left_by_a_crash_is_swept(monkeypatch):
    paths.PHOTO_UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    old = paths.PHOTO_UPLOAD_STAGING_DIR / "crash.part"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 2 * lib.STALE_STAGING_SECONDS,) * 2)
    fresh = paths.PHOTO_UPLOAD_STAGING_DIR / "live.part"
    fresh.write_bytes(b"x")
    lib.scan()
    assert not old.exists() and fresh.exists()


def test_the_staging_folder_is_invisible_to_albums_and_scans():
    with lib.begin_upload() as up:
        up.write(_jpeg())
        lib.scan()
        assert lib.list_albums() == []
        assert lib.status().total == 0


def test_photos_in_reports_each_state_with_a_reason_for_failures():
    write_image(paths.PHOTOS_DIR / "ok.jpg", halves())
    (paths.PHOTOS_DIR / "bad.jpg").write_bytes(b"not an image at all")
    backdate(paths.PHOTOS_DIR / "bad.jpg", 60)
    lib.scan()
    waiting = {r.name: r.state for r in lib.photos_in(lib.UNSORTED)}
    assert waiting == {"bad.jpg": "waiting", "ok.jpg": "waiting"}
    lib.process_pending()
    rows = {r.name: r for r in lib.photos_in(lib.UNSORTED)}
    assert rows["ok.jpg"].state == "ready" and rows["ok.jpg"].copy_id
    assert lib.thumb_file(rows["ok.jpg"].copy_id + ".webp").is_file()
    assert rows["bad.jpg"].state == "failed" and "cannot be read" in rows["bad.jpg"].reason
    assert rows["bad.jpg"].copy_id is None
    assert lib.thumb_file("../../config.json") is None


# ---- the "From photo" mount colour (issue #61) ----------------------------------------

def _framed(edge, centre, size=(1200, 800)):
    """A photo whose outer edge is one colour and whose middle is another."""
    im = Image.new("RGB", size, edge)
    w, h = size
    im.paste(Image.new("RGB", (w * 6 // 10, h * 6 // 10), centre), (w // 5, h // 5))
    return im


def _hls(hex_colour):
    import colorsys
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def test_the_mount_colour_is_sampled_from_the_edges_and_muted():
    build(("edge.jpg", _framed((200, 40, 30), (20, 200, 40))))
    ready, _ = only_copy()
    assert ready.mount is not None and len(ready.mount) == 7 and ready.mount.startswith("#")
    h, l, s = _hls(ready.mount)
    red_h, _, red_s = _hls("#c8281e")
    # The edge's hue (red), not the centre's (green), and muted: far less saturated.
    assert min(abs(h - red_h), 1 - abs(h - red_h)) < 0.05
    assert s < red_s / 2
    # Never a near-black or a blinding white, whatever the photo.
    assert 0.1 < l < 0.9


@pytest.mark.parametrize("edge", [(0, 0, 0), (255, 255, 255)])
def test_a_black_or_white_edged_photo_still_gets_a_usable_mount(edge):
    build(("edge.png", _framed(edge, (90, 90, 200))))
    ready, _ = only_copy()
    _, l, _ = _hls(ready.mount)
    assert 0.1 < l < 0.9


def test_the_mount_colour_is_sampled_when_the_copy_is_made_and_kept_in_the_index():
    build(("edge.jpg", _framed((30, 60, 160), (240, 240, 0))))
    first = only_copy()[0].mount
    lib.reset_state()                     # read back from the index file, not memory
    assert only_copy()[0].mount == first


def test_a_copy_made_before_the_mount_colour_existed_is_rebuilt_with_one():
    build(("edge.jpg", _framed((30, 60, 160), (240, 240, 0))))
    entry = next(iter(lib._load_index().values()))
    # A copy as an earlier version wrote it: its own pipeline key, and no colour.
    old_key = lib.settings_key().rsplit("-p", 1)[0] + "-p1"
    entry["copy"]["settings"] = old_key
    entry["copy"].pop("mount", None)
    lib._save_index()
    stale = only_copy()[0]
    assert stale.mount is None            # still served meanwhile, with no colour
    assert lib.process_pending() == 1     # the sampling change marked it stale
    assert only_copy()[0].mount is not None


def test_retuning_the_mount_sampling_means_bumping_the_pipeline_version():
    """Copies keep the colour they were made with until their key changes, so a
    change to the sampling that leaves _PIPELINE_VERSION alone refreshes nothing.
    Update this pin only together with a bump."""
    assert (lib._PIPELINE_VERSION, lib.MOUNT_EDGE_FRACTION, lib.MOUNT_MAX_SATURATION,
            lib.MOUNT_SATURATION_SCALE, lib.MOUNT_LIGHTNESS) == (2, 0.08, 0.22, 0.45, (0.18, 0.84))


def test_a_transparent_edge_is_sampled_as_the_screen_shows_it_on_black():
    im = Image.new("RGBA", (800, 600), (255, 255, 255, 0))        # clear white
    im.paste(Image.new("RGBA", (400, 300), (200, 40, 30, 255)), (200, 150))
    build(("clear.png", im))
    _, light, _ = _hls(only_copy()[0].mount)
    assert light == pytest.approx(lib.MOUNT_LIGHTNESS[0], abs=0.01)   # black, held at the floor
