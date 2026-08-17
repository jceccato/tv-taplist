"""Move a Tap's md + image pair into old_beers/ with a datetime suffix.

Used by the sync job (when a Brewfather Tap is no longer claimed) and by admin
(when clearing or replacing an override). Callers name a Slot and a Source, the
same way they address anything else in taps/; the Tap file store answers with
the files to move and the stem to file them under. The datetime suffix (e.g.
bf_tap_3_20260624T153000.md) means a Slot that turns over twice in one day does
not overwrite its own archive entry.

Archiving keeps its own module because it is a transition between two
directories rather than storage of current Taps, and because the mechanics below
are genuinely its own: moving is copy-to-temp + atomic replace + unlink, so a
concurrent reader never sees a half-moved file and an interrupted move leaves
the source intact. Missing files are tolerated - archiving a Slot that has no
image, or no files at all, is a no-op rather than an error. Cleanup reads the
archive back generically by stem and needs to know nothing about any of this.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import tap_store as taps
from .atomic import atomic_write_bytes, safe_unlink
from .paths import OLD_BEERS_DIR
from .timezone import now_local

log = logging.getLogger("taplist.archive")


def _move_file(src: Path, dest: Path) -> None:
    """Move src -> dest across an atomic write, then remove src."""
    data = src.read_bytes()
    atomic_write_bytes(dest, data)
    safe_unlink(src)


def archive_tap(slot: int, source: taps.Source) -> bool:
    """Archive the md (and paired image) for one Slot from one Source.

    Returns True if anything was archived. Missing files are tolerated.

    The timestamp is taken once here and handed to `archived_stem` for every
    file, so a Tap's md and its image always land under the same suffix and can
    be recognised as a pair afterwards.
    """
    OLD_BEERS_DIR.mkdir(parents=True, exist_ok=True)
    stem = taps.archived_stem(slot, source, now_local())
    archived_any = False

    for src in taps.existing_paths(slot, source):
        # The archived name keeps the source file's extension, so the md lands
        # as <stem>.md and its photo as <stem>.jpg / .png / ...
        try:
            _move_file(src, OLD_BEERS_DIR / f"{stem}{src.suffix}")
            archived_any = True
        except OSError as exc:
            log.error("failed archiving %s: %s", src, exc)

    if archived_any:
        log.info("archived tap %s (%s) -> old_beers/%s.*", slot, source, stem)
    return archived_any
