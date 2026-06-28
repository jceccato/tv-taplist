"""Move a tap's md + image pair into old_beers/ with a datetime suffix.

Used by the sync job (when a Brewfather tap is no longer desired) and by admin
(when saving a manual override over an existing bf_tap). The datetime suffix
(e.g. bf_tap_3_20260624T1530.md) means a tap that turns over twice in one day
does not overwrite its own archive entry.

Moving is done as copy-to-temp + atomic replace + unlink so a concurrent reader
never sees a half-moved file, and an interrupted move leaves the source intact.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from . import markdown_store as md
from .atomic import atomic_write_bytes, safe_unlink
from .paths import OLD_BEERS_DIR, TAPS_DIR
from .timezone import now_local

log = logging.getLogger("taplist.archive")


def _move_file(src: Path, dest: Path) -> None:
    """Move src -> dest across an atomic write, then remove src."""
    data = src.read_bytes()
    atomic_write_bytes(dest, data)
    safe_unlink(src)


def archive_tap(stem: str) -> bool:
    """Archive the md (and paired image) for a tap stem like 'bf_tap_3'.

    Returns True if anything was archived. Missing files are tolerated.
    """
    OLD_BEERS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = now_local().strftime("%Y%m%dT%H%M%S")
    archived_any = False

    md_src = TAPS_DIR / f"{stem}.md"
    if md_src.exists():
        try:
            _move_file(md_src, OLD_BEERS_DIR / f"{stem}_{suffix}.md")
            archived_any = True
        except OSError as exc:
            log.error("failed archiving %s: %s", md_src, exc)

    img_src = md.find_image_for(stem)
    if img_src is not None:
        try:
            _move_file(img_src, OLD_BEERS_DIR / f"{stem}_{suffix}{img_src.suffix}")
            archived_any = True
        except OSError as exc:
            log.error("failed archiving image %s: %s", img_src, exc)

    if archived_any:
        log.info("archived %s -> old_beers/%s_%s.*", stem, stem, suffix)
    return archived_any
