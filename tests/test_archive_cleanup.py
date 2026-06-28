"""Archiving a tap and the daily age/size cleanup of old_beers/."""
import os
import time

from app import cleanup, config_store, markdown_store as md, paths
from app.archive import archive_tap


def test_archive_moves_md_and_image_pair(write_tap):
    write_tap("bf", 3, name="Retiring", abv=5, ebc=10, image_ext=".jpg")
    assert archive_tap("bf_tap_3") is True
    # Originals gone.
    assert not md.bf_md_path(3).exists()
    assert not (paths.TAPS_DIR / "bf_tap_3.jpg").exists()
    # Archived with datetime suffix, both md + image.
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_3_*.md"))
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_3_*.jpg"))


def test_archive_missing_is_noop():
    assert archive_tap("bf_tap_99") is False


def _make_archived(stem: str, *, age_days: float = 0.0, md_size: int = 10, img_size: int = 10):
    md_path = paths.OLD_BEERS_DIR / f"{stem}.md"
    img_path = paths.OLD_BEERS_DIR / f"{stem}.jpg"
    md_path.write_bytes(b"x" * md_size)
    img_path.write_bytes(b"y" * img_size)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(md_path, (old, old))
        os.utime(img_path, (old, old))
    return md_path, img_path


def test_cleanup_deletes_by_age():
    config_store.update_config(max_archive_age_days=30, max_archive_storage_mb=0)
    _make_archived("old_beer", age_days=60)   # older than 30 days -> delete
    _make_archived("new_beer", age_days=5)    # keep

    result = cleanup.run_cleanup()
    assert result["deleted_by_age"] == 1
    assert not (paths.OLD_BEERS_DIR / "old_beer.md").exists()
    assert not (paths.OLD_BEERS_DIR / "old_beer.jpg").exists()  # pair deleted together
    assert (paths.OLD_BEERS_DIR / "new_beer.md").exists()


def test_cleanup_deletes_by_size_oldest_first():
    # 1 MB limit; make three 0.5 MB pairs so the oldest must go.
    half_mb = 512 * 1024
    config_store.update_config(max_archive_age_days=0, max_archive_storage_mb=1)
    _make_archived("oldest", age_days=10, md_size=half_mb, img_size=1)
    _make_archived("middle", age_days=5, md_size=half_mb, img_size=1)
    _make_archived("newest", age_days=1, md_size=half_mb, img_size=1)

    result = cleanup.run_cleanup()
    assert result["deleted_by_size"] >= 1
    # Oldest deleted first; newest survives.
    assert not (paths.OLD_BEERS_DIR / "oldest.md").exists()
    assert (paths.OLD_BEERS_DIR / "newest.md").exists()


def test_cleanup_counts_both_files_toward_total():
    # Each pair is ~1MB (md) + ~1MB (img) = 2MB; limit 3MB -> one pair must go.
    one_mb = 1024 * 1024
    config_store.update_config(max_archive_age_days=0, max_archive_storage_mb=3)
    _make_archived("a", age_days=2, md_size=one_mb, img_size=one_mb)
    _make_archived("b", age_days=1, md_size=one_mb, img_size=one_mb)

    cleanup.run_cleanup()
    remaining = list(paths.OLD_BEERS_DIR.glob("*.md"))
    assert len(remaining) == 1  # only one 2MB pair fits under 3MB
