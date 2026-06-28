"""Daily archive cleanup of old_beers/.

Each archived beer is a *pair*: a markdown file and its image, sharing a stem
like 'bf_tap_3_20260624T1530'. Both are deleted together and both count toward
the folder total.

Two conditions, applied in order:
  1. Age: delete any pair whose markdown mtime is older than Max Archive Age
     (days), measured against the configured local timezone.
  2. Size: if the folder still exceeds Max Archive Storage Limit (MB), delete
     oldest-first (by mtime) until under the limit.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .atomic import JOB_LOCK, safe_unlink
from .config_store import load_config
from .markdown_store import IMAGE_EXTS
from .paths import OLD_BEERS_DIR, ensure_dirs
from .timezone import now_local

log = logging.getLogger("taplist.cleanup")


class _Pair:
    """An archived beer: its .md plus optional paired image."""

    __slots__ = ("stem", "md_path", "img_path", "mtime", "size")

    def __init__(self, md_path: Path) -> None:
        self.stem = md_path.stem
        self.md_path = md_path
        self.img_path: Path | None = None
        for ext in IMAGE_EXTS:
            cand = md_path.with_suffix(ext)
            if cand.exists():
                self.img_path = cand
                break
        # Use the markdown mtime as the canonical age of the pair.
        try:
            self.mtime = md_path.stat().st_mtime
        except OSError:
            self.mtime = 0.0
        self.size = _file_size(md_path) + (_file_size(self.img_path) if self.img_path else 0)


def _file_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _collect_pairs() -> list[_Pair]:
    """All archived pairs (each md plus its paired image)."""
    return [_Pair(md_path) for md_path in OLD_BEERS_DIR.glob("*.md")]


def _delete_pair(pair: _Pair) -> int:
    """Delete a pair's files; return bytes reclaimed."""
    reclaimed = 0
    if pair.img_path is not None and pair.img_path.exists():
        reclaimed += _file_size(pair.img_path)
        safe_unlink(pair.img_path)
    if pair.md_path.exists():
        reclaimed += _file_size(pair.md_path)
        safe_unlink(pair.md_path)
    log.info("cleanup deleted archive pair %s (%d bytes)", pair.stem, reclaimed)
    return reclaimed


def run_cleanup() -> dict[str, Any]:
    """Run the daily cleanup. Never raises."""
    ensure_dirs()
    cfg = load_config()
    max_age_days = int(cfg.get("max_archive_age_days", 0) or 0)
    max_size_mb = int(cfg.get("max_archive_storage_mb", 0) or 0)
    max_size_bytes = max_size_mb * 1024 * 1024

    with JOB_LOCK:
        log.info("cleanup starting (max_age=%dd max_size=%dMB)", max_age_days, max_size_mb)
        deleted_by_age = 0
        deleted_by_size = 0
        try:
            pairs = _collect_pairs()

            # Condition 1: age. mtime is epoch seconds; compare against local now.
            if max_age_days > 0:
                cutoff = now_local().timestamp() - max_age_days * 86400
                survivors: list[_Pair] = []
                for pair in pairs:
                    if pair.mtime < cutoff:
                        _delete_pair(pair)
                        deleted_by_age += 1
                    else:
                        survivors.append(pair)
                pairs = survivors

            # Condition 2: size. Oldest first until under the limit.
            if max_size_bytes > 0:
                total = sum(p.size for p in pairs)
                if total > max_size_bytes:
                    for pair in sorted(pairs, key=lambda p: p.mtime):
                        if total <= max_size_bytes:
                            break
                        total -= pair.size
                        _delete_pair(pair)
                        deleted_by_size += 1

        except OSError as exc:
            log.error("cleanup error: %s", exc)
            return {"ok": False, "message": str(exc)}

        log.info(
            "cleanup finished: %d deleted by age, %d deleted by size",
            deleted_by_age, deleted_by_size,
        )
        return {"ok": True, "deleted_by_age": deleted_by_age, "deleted_by_size": deleted_by_size}
