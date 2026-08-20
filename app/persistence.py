"""Startup durability checks: is the data directory actually going to persist?

Everything the operator cannot get back - Settings, the Brewfather key, Manual
Taps - lives in the mapped data directory. Nothing in the app ever checked that
the directory is real storage, so a box whose mapping never took effect looks
perfectly healthy: every write succeeds, the board renders, and the loss only
shows up after a container recreate, as *half* a board. Brewfather Taps rebuild
themselves on the next sync, so the symptom reads as "my manual beers vanished",
which looks like an app bug and is really storage that was never persistent.

Two independent signals are computed **once at startup** and surfaced as at most
one admin banner:

**(a) Not mapped.** Inside a container, the data directory must appear as a
mount in ``/proc/self/mountinfo``. If it does not, nothing was mapped onto it
and it is a plain directory in the container's writable layer, which dies with
the container. This is a clean boolean only because the image no longer declares
``VOLUME ["/data"]``: with that directive Docker silently supplied an anonymous
volume, which is indistinguishable from a named one (same filesystem type, same
device, and the only difference is a 64-hex directory name - a guess about
Docker internals). The directive was removed rather than worked around. **A test
in tests/test_persistence.py fails if it comes back**, because restoring it in
the name of "Docker best practice" would silently disable this check.

**(b) Data Directory Identity (DDI).** A random identifier written to two
places: one in the data directory, one in a container-local path outside it. The
data directory's copy is always the authority; the container-local copy is only
a memory of what was last seen there. Comparing them at boot distinguishes a
container recreate over intact data (silent) from data that was wiped or
swapped underneath a surviving container (warn).

**DDI is not a general wipe detector - do not read it as one.** It cannot see
the failure this work descends from (#2, a Docker Desktop VM reset): both halves
of the identifier live inside that same VM disk, so a reset takes both and the
next boot reads as a first run. Signal (a) is what catches that case, because
such a box is unmapped. What (b) does cover is narrower: a data directory mapped
onto host tmpfs or a RAM disk, a mapped directory the operator deleted, a NAS or
external drive that failed to mount before Docker started, and remapping
mistakes. A future reader must not "improve" (b) into a wipe detector, and must
not assume silence from it means the data is safe.

Deliberate non-goals, all considered and rejected: no ``/healthz`` field, no
warning on the TV display (it makes the product look broken to customers over
something venue staff cannot fix from the room), and **never** a refusal to
start - a durability warning must not become a total outage on a box whose
selling point is serving through failures.
"""
from __future__ import annotations

import logging
import os
import posixpath
import uuid
from dataclasses import dataclass
from pathlib import Path

from .atomic import atomic_write_text
from .paths import DATA_DIR

log = logging.getLogger("taplist.persistence")

# --- verdicts ---------------------------------------------------------------
# One value, read by the admin route. Signal (a) outranks signal (b): (a) is a
# live misconfiguration the operator fixes in one line, (b) is a report about
# something already over, and the actionable warning must not have to compete
# with the unactionable one.
VERDICT_OK = "ok"
VERDICT_NOT_MAPPED = "not_mapped"
VERDICT_DATA_REPLACED = "data_replaced"

# --- DDI storage ------------------------------------------------------------
# The data directory's copy. A dotfile: operators read and hand-edit this
# directory (ADR-0001), and this is the one file in it they should not touch -
# deleting it is indistinguishable from a wipe and costs them a spurious
# warning.
DDI_FILENAME = ".data_dir_id"

# The container-local copy. NOT /tmp: some hardened setups mount that as tmpfs,
# which would disable signal (b) permanently and silently. This directory is
# created and chowned in the Dockerfile and re-chowned by entrypoint.sh, which
# is what keeps it writable after the gosu drop to an arbitrary PUID/PGID.
CONTAINER_STATE_DIR = Path("/var/lib/taplist")
CONTAINER_DDI_PATH = CONTAINER_STATE_DIR / "data_dir_id"

# --- DDI states -------------------------------------------------------------
# Finer-grained than the verdict, so tests can pin each row of the truth table
# and so the log says which situation was recognised.
DDI_FIRST_RUN = "first_run"        # neither copy exists: brand new box
DDI_ADOPTED = "adopted"            # container copy missing, data copy present
DDI_UNCHANGED = "unchanged"        # both present and equal: normal boot
DDI_WIPED = "wiped"                # container copy present, data copy gone
DDI_REPLACED = "replaced"          # both present and different
DDI_INCONCLUSIVE = "inconclusive"  # a copy exists but could not be read/written
DDI_SKIPPED = "skipped"            # not running in a container


@dataclass(frozen=True)
class DdiResult:
    """Outcome of one DDI comparison."""

    state: str
    identity: str | None  # what the data directory holds afterwards
    warn: bool


def _dockerenv_exists() -> bool:
    """True when this process looks like it is running inside a container.

    ``/.dockerenv`` is what Docker itself drops in; ``/run/.containerenv`` is
    the Podman equivalent. Outside a container both signals are skipped
    entirely - a developer running uvicorn against a local folder does not need
    telling that their folder is a folder.
    """
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _unescape_mount_field(field: str) -> str:
    """Decode the octal escapes the kernel uses in mountinfo path fields."""
    for escape, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        field = field.replace(escape, char)
    return field


def mount_points(mountinfo_path: str = "/proc/self/mountinfo") -> set[str]:
    """Every path currently mounted, per the kernel's own view.

    Deliberately *not* keyed on filesystem type or on the shape of a volume
    name: measured against a live daemon, an anonymous volume and a named one
    are both ext4 off the same device and differ only by a directory name.
    Presence or absence of the entry is the only reliable distinction, and
    dropping the VOLUME directive is what makes absence meaningful.
    """
    points: set[str] = set()
    try:
        with open(mountinfo_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                fields = line.split()
                # 0 mount-id, 1 parent-id, 2 major:minor, 3 root, 4 mount point.
                if len(fields) >= 5:
                    points.add(_unescape_mount_field(fields[4]))
    except OSError:
        # No /proc (or no permission). Report nothing rather than guessing;
        # is_data_dir_mapped() treats that as "cannot tell" and stays quiet.
        return set()
    return points


def is_data_dir_mapped(
    data_dir: Path | None = None, mountinfo_path: str = "/proc/self/mountinfo"
) -> bool | None:
    """Is the data directory backed by a mount? None when the question does not apply.

    None means "not asked": outside a container, or with no readable mountinfo.
    Only an exact match counts. A host directory mapped *below* the data
    directory would leave the data directory itself unmounted, and that is the
    honest answer - the files the app writes directly into it still would not
    survive.
    """
    if not _dockerenv_exists():
        return None
    points = mount_points(mountinfo_path)
    if not points:
        return None
    # posixpath, not os.path: mountinfo is a Linux kernel file and its paths are
    # POSIX whatever the interpreter is running on, so os.path.realpath() would
    # turn "/data" into "C:\data" when the suite runs on a maintainer's Windows
    # box. DATA_DIR is already symlink-resolved by paths.py, so normalising the
    # spelling is all that is left to do.
    target = posixpath.normpath(str(data_dir or DATA_DIR))
    return target in points


def _read_identity(path: Path) -> tuple[str | None, bool]:
    """Read an identifier file. Returns (identity, readable).

    The two are not the same question. A file that is *absent* is information -
    it is what a wipe looks like. A file that exists but will not read is not:
    Docker Desktop bind mounts on Windows do occasionally misreport a read, and
    the config store already carries a guard for exactly that. Treating an
    unreadable file as a wipe would warn the operator about data that is fine
    and then overwrite the identifier that proves it, so the caller stays silent
    and writes nothing instead.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    value = text.strip()
    return (value or None), True


def _write_identity(path: Path, identity: str) -> bool:
    """Persist an identifier, returning False if the write did not happen."""
    try:
        atomic_write_text(path, identity + "\n")
        return True
    except OSError as exc:
        log.warning("could not write the data directory identity to %s: %s", path, exc)
        return False


def evaluate_identity(container_path: Path, data_path: Path) -> DdiResult:
    """Compare the two identifier copies, reconcile them, and report.

    The data directory's copy is the authority whenever it is present; the
    container-local copy is adopted from it. Both warning states end by making
    the two agree, which is what makes signal (b) fire exactly once: the boot
    after a detected wipe is a normal boot.
    """
    container_id, container_readable = _read_identity(container_path)
    data_id, data_readable = _read_identity(data_path)

    if not container_readable or not data_readable:
        return DdiResult(DDI_INCONCLUSIVE, data_id, False)

    # Row 4: normal boot.
    if container_id and data_id and container_id == data_id:
        return DdiResult(DDI_UNCHANGED, data_id, False)

    # Row 2: container recreated over intact data (an image update, say). The
    # data directory is the authority, so its identifier is preserved untouched
    # and only the container-local memory is refreshed.
    if not container_id and data_id:
        _write_identity(container_path, data_id)
        return DdiResult(DDI_ADOPTED, data_id, False)

    # Row 1: brand new box. Mint one identifier and seed both copies.
    if not container_id and not data_id:
        identity = uuid.uuid4().hex
        wrote_data = _write_identity(data_path, identity)
        _write_identity(container_path, identity)
        return DdiResult(DDI_FIRST_RUN, identity if wrote_data else None, False)

    # Row 3: the data directory was wiped, or remapped to an empty one. The
    # container's remembered identifier is written back rather than a fresh one,
    # so a data directory that comes *back* (a NAS that mounted late, say) still
    # matches and does not produce a second, misleading warning.
    if container_id and not data_id:
        _write_identity(data_path, container_id)
        return DdiResult(DDI_WIPED, container_id, True)

    # Row 5: remapped onto a different populated directory. Adopt what is on
    # disk - that directory's identity is the real one from here on.
    _write_identity(container_path, data_id)
    return DdiResult(DDI_REPLACED, data_id, True)


def check_identity(
    data_dir: Path | None = None, container_path: Path | None = None
) -> DdiResult:
    """Run the DDI comparison for this boot, skipping it outside a container."""
    if not _dockerenv_exists():
        return DdiResult(DDI_SKIPPED, None, False)
    data_path = (Path(data_dir) if data_dir is not None else DATA_DIR) / DDI_FILENAME
    return evaluate_identity(container_path or CONTAINER_DDI_PATH, data_path)


def _demo_mode() -> bool:
    """True when DEMO_MODE is enabled (mirrors the parsing in app/demo.py)."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


# The boot verdict, computed once. Deliberately module-level rather than
# per-request: the mount check is a boot fact that cannot change while the
# process lives, and the DDI comparison must happen exactly once or it would
# re-adopt its own writes and never warn.
_verdict: str = VERDICT_OK


def run_startup_checks(
    data_dir: Path | None = None, container_path: Path | None = None
) -> str:
    """Compute both signals, log once, stash the verdict for the admin page.

    Never raises. A durability warning that took the box down would be worse
    than the problem it reports.
    """
    global _verdict
    try:
        _verdict = _compute(data_dir, container_path)
    except Exception:  # noqa: BLE001 - startup must survive anything in here
        log.exception("data directory persistence check failed; continuing")
        _verdict = VERDICT_OK
    return _verdict


def _compute(data_dir: Path | None, container_path: Path | None) -> str:
    # DEMO_MODE says "this box is disposable", the same way it already licenses
    # a passwordless admin, and the documented demo is an unmapped one-liner.
    # The findings are still logged, at INFO, so a demo box stays traceable.
    level = logging.INFO if _demo_mode() else logging.WARNING

    mapped = is_data_dir_mapped(data_dir)
    if mapped is False:
        log.log(
            level,
            "The data directory (%s) is not a mapped host directory. Everything the "
            "appliance stores - Settings, the Brewfather key, and Manual Taps - is "
            "written into the container and is lost when the container is recreated. "
            "Map a host directory onto it (see docs/INSTALLATION.md).",
            data_dir or DATA_DIR,
        )
        # Signal (b) still runs so its bookkeeping stays current, but its
        # verdict is discarded: (a) is the actionable one.
        check_identity(data_dir, container_path)
        return VERDICT_NOT_MAPPED

    result = check_identity(data_dir, container_path)
    if result.warn:
        log.log(
            level,
            "The data directory (%s) is not the one this container last saw (%s). "
            "Manual Taps and Settings written before now are gone; Brewfather Taps "
            "rebuild on the next sync. Check that the mapped host directory is on "
            "permanent storage and was mounted before the container started.",
            data_dir or DATA_DIR,
            result.state,
        )
        return VERDICT_DATA_REPLACED

    log.info("data directory persistence check: mapped=%s ddi=%s", mapped, result.state)
    return VERDICT_OK


def verdict() -> str:
    """The verdict computed at startup."""
    return _verdict


def admin_banner() -> str | None:
    """Which banner the admin page should render, or None for no banner.

    DEMO_MODE suppresses both, on the passwordless-admin precedent: demo mode
    already means "this box is disposable and says so".
    """
    if _demo_mode() or _verdict == VERDICT_OK:
        return None
    return _verdict
