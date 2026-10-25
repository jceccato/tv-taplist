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

from app import paths, photo_library as lib

from photo_fixtures import (
    backdate, exif_bytes, halves, p3_profile, riff_chunks, write_image,
)


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


def test_only_jpeg_png_and_webp_are_photos_and_hidden_things_are_skipped():
    build(("a.jpg", halves()), ("b.jpeg", halves()), ("c.PNG", halves()), ("d.webp", halves()))
    (paths.PHOTOS_DIR / "notes.txt").write_text("hello")
    (paths.PHOTOS_DIR / "anim.gif").write_bytes(b"GIF89a")
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

def test_a_stale_copy_keeps_being_served_until_its_replacement_is_ready(monkeypatch):
    build(("one.jpg", halves((800, 600))))
    old, copy = only_copy()
    assert copy.size == (2880, 2160)
    monkeypatch.setattr(lib, "TARGET_SIZE", (1920, 1080))     # a settings change
    assert lib.status().pending == 1                          # every copy is now stale
    still = lib.ready_photos()
    assert [p.copy_id for p in still] == [old.copy_id]        # the old copy still shows
    assert lib.copy_file(still[0].filename).is_file()
    lib.process_next()
    new = lib.ready_photos()
    assert len(new) == 1 and new[0].copy_id != old.copy_id
    assert Image.open(lib.copy_file(new[0].filename)).size == (1440, 1080)
    assert lib.copy_file(old.filename) is None                # the replaced copy is removed


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


def test_a_png_pretending_to_be_a_gif_family_format_is_refused_by_content():
    # Anything Pillow decodes that is not JPEG/PNG/WebP is a later ticket's format.
    gif = io.BytesIO()
    Image.new("P", (10, 10)).save(gif, "GIF")
    (paths.PHOTOS_DIR / "x.jpg").write_bytes(gif.getvalue())
    backdate(paths.PHOTOS_DIR / "x.jpg", 60)
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
            if {"PHOTOS_DIR", "PHOTO_CACHE_DIR"} & set(names):
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
