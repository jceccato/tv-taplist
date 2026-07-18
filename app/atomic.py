"""Atomic file writes and the cross-job lock.

Three writers touch /data/taps while the display reads it: the periodic
Brewfather sync, the daily cleanup, and admin edits. Every write lands in a
temp file in the *same directory* and is then os.replace()'d onto the target,
which is an atomic rename on the same filesystem. Readers therefore always see
either the old complete file or the new complete file, never a half-written one.

The module-level JOB_LOCK serialises the sync and cleanup jobs (and admin
mutations) so they never interleave file operations.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

# Re-entrant so a job that already holds the lock can call helpers that also
# take it without deadlocking. Used by sync, cleanup, and admin writes.
JOB_LOCK = threading.RLock()


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes to target atomically via a temp file + os.replace()."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Temp file must be on the same filesystem (same dir) for replace to be atomic.
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_", suffix=target.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # Best-effort cleanup of the temp file if the replace never happened.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(target: Path, text: str) -> None:
    """Write UTF-8 text to target atomically."""
    atomic_write_bytes(target, text.encode("utf-8"))


def safe_unlink(path: Path) -> bool:
    """Delete a file if present; tolerate it already being gone (race-safe)."""
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
