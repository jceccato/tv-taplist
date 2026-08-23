"""Snapshot export and import: the Board as it stood, as a zip of the data directory.

A **Snapshot** mirrors the mapped data directory's layout, so restoring one is
unpacking it - `unzip` into the data directory and restart is a supported
restore path, and the reason there is no manifest or version marker inside the
zip (ADR-0001: the files *are* the interface, and a marker would land in the
data directory as a stray file when a Snapshot is unpacked by hand).

What a Snapshot carries:

  config.json          Settings, with the Brewfather credentials governed below
  taps/                the Tap files and images of BOTH Sources
  old_beers/           the Archived beers
  venue_logo.<ext>     the operator's logo, referenced by Settings by filename
  placeholder.<ext>    the placeholder image sitting in the data directory

What it never carries, and why:

* **Status** (`status.json`). Every field regenerates on the next cycle
  (ADR-0002), so restoring one would only make a fresh box describe a sync that
  never happened on it.
* **The Data Directory Identity** (`.data_dir_id`). The DDI names *which data
  directory this box is using*, not which data set sits in it. Carrying it would
  let two boxes claim one identity, which is the exact confusion it exists to
  detect. Importing data into a directory does not change which directory it is.

The Brewfather credential is opt-in, and one rule covers every combination
without a case matrix: **the export never reads environment values. It
optionally carries whatever `config.json` holds, and the choice is offered only
when the API key is one of them.** The two credential fields resolve
independently (a box can have the user ID in the environment and the key in
Settings), so anything that tried to reason per-field would need a matrix; this
does not.

Import asks one question up front, because a box that syncs overwrites any
Brewfather Tap it is given: **will this box have a working Brewfather key when
the import finishes?** If yes, the Snapshot's Brewfather Taps are skipped and
the operator is told why - importing them onto a box that will sync shows the
old beers for a few minutes and then silently replaces them, which looks like
the import worked and then quietly undid itself. Importing the Brewfather Taps
and keeping a working key are therefore mutually exclusive. This reads as a bug
and is not; do not "fix" it by keeping both.

Two smaller rules that are easy to get backwards:

* **The box's own key beats the Snapshot's.** A credential is restored only into
  a field the box has left empty. An import replacing a working credential with
  an older one is the single way this feature could break a box that was syncing
  fine, and the operator can always paste a key in afterwards.
* **Imported Settings are clamped, never rejected.** A Snapshot's `config.json`
  is, from the store's point of view, a hand-edited config file, so it goes
  through `config_store.update_config` like any other Settings write and an
  out-of-range value is clamped and saved. See CONTEXT.md, "Settings bounds are
  enforced by clamping".
"""
from __future__ import annotations

import json
import logging
import stat as stat_module
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import tap_store as taps
from .atomic import JOB_LOCK, atomic_write_bytes, safe_unlink
from .config_store import (
    brewfather_credentials,
    load_config,
    update_config,
)
from .paths import DATA_DIR, OLD_BEERS_DIR, TAPS_DIR, VENUE_LOGO_EXTS
from .timezone import now_local

log = logging.getLogger("taplist.snapshot")

# The Settings member. Named for the file it restores to, because that is the
# whole contract: unpacking a Snapshot must land config.json where config.json
# lives.
SETTINGS_NAME = "config.json"

# The two subdirectories, named from the real layout rather than restated, so a
# Snapshot cannot drift from the directory it mirrors.
TAPS_DIRNAME = TAPS_DIR.name
OLD_BEERS_DIRNAME = OLD_BEERS_DIR.name

# Images that live at the data directory root. Spelled out here rather than
# taken from `paths.placeholder_path()` on purpose: that helper falls back to
# the copy bundled inside the image, and a Snapshot must carry only what
# actually sits in the mapped data directory. The seeded placeholder counts -
# once it is on disk it is what the box serves, and the operator may have
# replaced it - but the bundled one is part of the image, not of the data.
PLACEHOLDER_NAMES = ("placeholder.svg", "placeholder.png", "placeholder.jpg")
VENUE_LOGO_NAMES = tuple(f"venue_logo{ext}" for ext in VENUE_LOGO_EXTS)
ROOT_IMAGE_NAMES = PLACEHOLDER_NAMES + VENUE_LOGO_NAMES

# The two Settings fields the import governs by rule instead of restoring
# verbatim. Named once so neither half of the feature can forget one.
CREDENTIAL_KEYS = ("brewfather_user_id", "brewfather_api_key")

# Read granularity when streaming a file into the zip. Big enough that a large
# photo is a couple of reads, small enough that the response never holds more
# than this much of one member in memory.
_STREAM_CHUNK = 1024 * 1024

# Suffixes whose bytes are already compressed. Deflating a JPEG costs CPU on an
# appliance and saves nothing; SVG, markdown and JSON are text and compress
# well, so they are not on this list.
_ALREADY_COMPRESSED = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# Ceiling on a single member's decompressed size. Every image in a Snapshot came
# through the admin's 10 MB upload cap, so this is far above anything real; it
# is here so a malicious zip cannot make the import allocate an arbitrary amount
# of memory when it reads a member in one go.
MAX_MEMBER_BYTES = 64 * 1024 * 1024

# The three answers to "will this box have a working Brewfather key afterwards?".
# The operator is prompted only for CHOOSE, which is the only one where they
# actually control the answer.
DECISION_ENVIRONMENT = "environment"   # key is an env var: cannot be cleared here
DECISION_CHOOSE = "choose"             # a key exists on either side: ask
DECISION_NONE = "none"                 # neither side has one: nothing to ask


# An uploaded Snapshot is staged here while it is validated and the operator
# answers the Brewfather question, and is deleted on every path out.
#
# It sits in the mapped data directory, not the system temp directory, for two
# reasons. The mapped directory is the one location on the box already sized for
# data of this magnitude - a Snapshot is as large as the data it came from,
# which the container's writable layer has no reason to have room for. And it is
# the same filesystem as every destination, so the restore never copies across
# a device boundary.
#
# The name is fixed rather than random so there is only ever ONE staged
# Snapshot: uploading a second replaces the first, and an operator who abandons
# an import mid-question leaves at most one file behind rather than one per
# attempt. The `.tmp_` prefix matches `atomic.py`'s temp files, and the Tap file
# store's filename predicates reject it, so it can never be mistaken for data.
STAGED_UPLOAD_PATH = DATA_DIR / ".tmp_snapshot_upload"


def discard_staged() -> None:
    """Delete the staged upload if there is one. Safe to call when there is not."""
    safe_unlink(STAGED_UPLOAD_PATH)


class SnapshotRejected(ValueError):
    """The uploaded file is not a Snapshot this box will restore.

    Carries text written for the operator, naming what was wrong, because the
    admin shows it verbatim. Raised before anything on disk changes, so a
    rejected import leaves every existing file exactly as it was.
    """


class DecisionRequired(RuntimeError):
    """The import needs the operator's Brewfather answer and did not get one.

    Only reachable for DECISION_CHOOSE. Raised before any write, like a
    rejection, so an import that stops here has changed nothing.
    """


# ---- export ---------------------------------------------------------------

def credential_choice_available() -> bool:
    """Whether the export may offer to carry the Brewfather credentials.

    True exactly when the API key sits in `config.json`. An environment-supplied
    key is never carried, and there is nothing to carry when no key is set, so
    in both of those cases the admin hides the option rather than disabling it:
    a checkbox that silently does nothing is worse than no checkbox.
    """
    creds = brewfather_credentials()
    return bool(creds["api_key"]) and not creds["key_from_env"]


def snapshot_settings(include_credentials: bool = False) -> dict[str, Any]:
    """The Settings a Snapshot carries.

    `load_config()` reads `config.json` and nothing else - the environment
    overlay lives in `brewfather_credentials()`, which is not consulted here.
    That is the whole of the "never export an environment value" rule: this
    function cannot reach one.

    The gate is re-checked rather than trusted, so a stale browser posting the
    option at a box whose key has since moved to the environment still gets a
    Snapshot with both credential fields blank.
    """
    settings = dict(load_config())
    if not (include_credentials and credential_choice_available()):
        for key in CREDENTIAL_KEYS:
            settings[key] = ""
    return settings


def settings_bytes(include_credentials: bool = False) -> bytes:
    """The Snapshot's config.json member, formatted like the real one."""
    settings = snapshot_settings(include_credentials)
    return json.dumps(settings, indent=2, ensure_ascii=False).encode("utf-8")


def enumerate_entries() -> list[tuple[str, Path]]:
    """The (name in the zip, file on disk) pairs a Snapshot carries.

    **This is the only part of an export that holds the job lock.** Holding it
    for the whole download would block sync, cleanup and every admin write for
    as long as the transfer takes - minutes over a venue LAN with the Archived
    beers included. Releasing it after the list is taken costs nothing the
    appliance does not already guarantee: every write goes through
    `atomic.atomic_write_*`, so a file read afterwards is the old version or the
    new one and never a torn one, and a file that has gone by the time the
    stream reaches it is simply skipped. The guarantee worth having was never
    "the data cannot change during a download"; it was "a Snapshot never
    contains a half-written Tap file", and snapshotting the list keeps it.

    Membership is decided by the Tap file store's filename predicates, not by a
    glob, which is also what keeps a `.tmp_` file from an atomic write in flight
    out of the Snapshot.
    """
    entries: list[tuple[str, Path]] = []
    with JOB_LOCK:
        for name in ROOT_IMAGE_NAMES:
            path = DATA_DIR / name
            if path.is_file():
                entries.append((name, path))
        for directory, identify in (
            (TAPS_DIR, taps.identify),
            (OLD_BEERS_DIR, taps.identify_archived),
        ):
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and identify(path.name) is not None:
                    entries.append((f"{directory.name}/{path.name}", path))
    return entries


class _StreamSink:
    """A write-only sink for `zipfile`, drained by the generator around it.

    Deliberately has no `seek`, `tell` or `seekable`: that is what makes
    `zipfile` treat it as a stream and emit each member's sizes and CRC in a
    trailing data descriptor instead of seeking back to patch the local header.
    Adding any of those three methods would silently turn this back into a
    buffered write of the whole zip.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:  # zipfile calls this on close
        pass

    def drain(self) -> list[bytes]:
        """Hand back everything buffered since the last drain."""
        out, self._chunks = self._chunks, []
        return out


def _zip_date_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    """A file's mtime as a ZipInfo date_time, floored at the zip epoch (1980)."""
    parts = time.localtime(mtime)[:6]
    if parts[0] < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return parts  # type: ignore[return-value]


def _compression_for(name: str) -> int:
    return (zipfile.ZIP_STORED if Path(name).suffix.lower() in _ALREADY_COMPRESSED
            else zipfile.ZIP_DEFLATED)


def stream_snapshot(settings: bytes, entries: list[tuple[str, Path]]) -> Iterator[bytes]:
    """Yield the Snapshot's bytes, one member at a time, building nothing on disk.

    Nothing larger than `_STREAM_CHUNK` of one file is ever held: each read is
    handed to `zipfile`, whatever `zipfile` emits is yielded, and the buffer is
    empty again. There is no temp copy of the zip anywhere - `zipfile` supports
    an unseekable output stream and writes data descriptors for it, which is
    what `_StreamSink` is for.

    Each member's size is declared up front from `stat()` so `zipfile` can
    decide whether the entry needs ZIP64 fields before it writes the local
    header. It is only a hint: if the file changed size since the list was taken
    the trailing data descriptor carries the real numbers, which is why
    releasing the job lock after enumeration is safe.

    A file that has vanished since enumeration - archived or purged mid-stream -
    is skipped without a member being started, so the zip stays well-formed.
    """
    sink = _StreamSink()
    with zipfile.ZipFile(sink, mode="w", allowZip64=True) as zf:
        info = zipfile.ZipInfo(SETTINGS_NAME, date_time=_zip_date_time(time.time()))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.file_size = len(settings)
        with zf.open(info, "w") as member:
            member.write(settings)
        yield from sink.drain()

        for arcname, path in entries:
            try:
                file_stat = path.stat()
                handle = path.open("rb")
            except OSError as exc:
                log.info("snapshot: %s is no longer readable, skipped (%s)", arcname, exc)
                continue
            info = zipfile.ZipInfo(arcname, date_time=_zip_date_time(file_stat.st_mtime))
            info.compress_type = _compression_for(arcname)
            info.file_size = file_stat.st_size
            with handle, zf.open(info, "w") as member:
                while True:
                    chunk = handle.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    member.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    # The central directory is written by ZipFile.close() on the way out.
    yield from sink.drain()


def snapshot_filename() -> str:
    """The download name, stamped in the box's configured local timezone."""
    return f"taplist-snapshot-{now_local().strftime('%Y%m%dT%H%M%S')}.zip"


# ---- import: validation ---------------------------------------------------

def _member_destination(name: str) -> Path | None:
    """Where a validated member lands, or None if the layout does not allow it.

    This is the single statement of what a Snapshot may contain. Validation and
    restore both go through it, so the set of names that pass the check and the
    set that get written cannot drift apart.
    """
    if name in ROOT_IMAGE_NAMES:
        return DATA_DIR / name
    if name == SETTINGS_NAME:
        return DATA_DIR / name
    head, sep, tail = name.partition("/")
    if not sep or "/" in tail:
        return None
    if head == TAPS_DIRNAME and taps.identify(tail) is not None:
        return TAPS_DIR / tail
    if head == OLD_BEERS_DIRNAME and taps.identify_archived(tail) is not None:
        return OLD_BEERS_DIR / tail
    return None


def _unsafe_path_reason(name: str) -> str | None:
    """Why this member name cannot be trusted as a path, or None if it can.

    Member names are attacker-controlled, and `ZipFile.extract` sanitising them
    is no help because this code writes the files itself. Anything that could
    escape the data directory is refused by name before any of it is read.
    """
    if not name:
        return "an entry with an empty name"
    if "\\" in name:
        return f"'{name}' (a backslash in the path)"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return f"'{name}' (an absolute path)"
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"'{name}' (a relative path segment)"
    return None


def _validate_layout(zf: zipfile.ZipFile) -> None:
    """Refuse anything that is not a Snapshot, naming what was wrong.

    Checked here and not later: the whole archive is judged before a single byte
    is written, so a refusal leaves the existing data byte-for-byte unchanged.
    """
    problems: list[str] = []
    seen: set[str] = set()
    root_images: list[str] = []
    has_settings = False

    for info in zf.infolist():
        name = info.filename
        unsafe = _unsafe_path_reason(name.rstrip("/") if info.is_dir() else name)
        if unsafe is not None:
            problems.append(unsafe)
            continue
        if stat_module.S_ISLNK(info.external_attr >> 16):
            problems.append(f"'{name}' (a symbolic link)")
            continue
        if info.is_dir():
            # Only the two directories the layout has. A directory entry is
            # optional - a Snapshot made elsewhere may carry none.
            if name.rstrip("/") not in (TAPS_DIRNAME, OLD_BEERS_DIRNAME):
                problems.append(f"'{name}' (an unexpected directory)")
            continue
        if name in seen:
            # Two members with one name: an extractor takes the last, so a
            # first-entry check could be walked straight past.
            problems.append(f"'{name}' (listed twice)")
            continue
        seen.add(name)
        if _member_destination(name) is None:
            problems.append(f"'{name}' (not part of a Snapshot)")
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            problems.append(f"'{name}' (larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB)")
            continue
        if name == SETTINGS_NAME:
            has_settings = True
        elif name in ROOT_IMAGE_NAMES:
            root_images.append(name)

    if not has_settings:
        problems.append(f"no {SETTINGS_NAME}")
    # Two spellings of one root image would leave the box unable to say which is
    # current, because both `placeholder_path()` and `venue_logo_path()` pick by
    # a fixed extension order rather than by which file is newer.
    for label, group in (("placeholder", PLACEHOLDER_NAMES), ("venue logo", VENUE_LOGO_NAMES)):
        found = [n for n in root_images if n in group]
        if len(found) > 1:
            problems.append(f"more than one {label} image ({', '.join(sorted(found))})")

    if problems:
        raise SnapshotRejected(
            "This file is not a Snapshot: " + "; ".join(problems[:10])
            + ("; and more" if len(problems) > 10 else "")
        )


def _validate_contents(zf: zipfile.ZipFile) -> None:
    """Decompress every member and discard it, so a corrupt one is caught early.

    This costs a second read of the whole Snapshot off local disk, and buys the
    thing the layout check alone cannot: a Snapshot whose bytes do not survive
    their own CRC is refused *whole* rather than half-applied. Without it a
    member that lies about its size, or a truncated download, would be
    discovered partway through the restore with files already replaced.
    """
    for info in zf.infolist():
        if info.is_dir():
            continue
        try:
            with zf.open(info) as member:
                total = 0
                while True:
                    chunk = member.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_MEMBER_BYTES:
                        raise SnapshotRejected(
                            f"'{info.filename}' unpacks to more than "
                            f"{MAX_MEMBER_BYTES // (1024 * 1024)} MB"
                        )
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise SnapshotRejected(f"'{info.filename}' is damaged: {exc}") from exc


def read_snapshot_settings(zf: zipfile.ZipFile) -> dict[str, Any]:
    """The Settings held in a validated Snapshot."""
    try:
        settings = json.loads(zf.read(SETTINGS_NAME).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise SnapshotRejected(f"its {SETTINGS_NAME} could not be read: {exc}") from exc
    if not isinstance(settings, dict):
        raise SnapshotRejected(f"its {SETTINGS_NAME} is not a settings object")
    return settings


def open_snapshot(path: Path) -> zipfile.ZipFile:
    """Open and fully validate a Snapshot, or refuse it. Nothing is written."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SnapshotRejected(f"This file is not a readable zip: {exc}") from exc
    try:
        _validate_layout(zf)
        _validate_contents(zf)
    except BaseException:
        zf.close()
        raise
    return zf


# ---- import: the Brewfather question --------------------------------------

@dataclass(frozen=True)
class ImportPlan:
    """Which Brewfather case this import falls into, and the facts behind it.

    `kind` is what the admin acts on. The two booleans are carried so the
    prompt can state both consequences accurately without re-deriving the rule
    in the browser - the client is told which case applies, never how to work
    it out.
    """

    kind: str
    box_has_key: bool
    key_from_env: bool
    snapshot_has_key: bool


def plan_import(snapshot_settings_dict: dict[str, Any]) -> ImportPlan:
    """Decide whether the operator has to be asked anything, and about what.

    The question is "will this box have a working key **afterwards**", not "does
    it have one now". They differ the moment a Snapshot can carry a credential:
    a keyless box importing a key-carrying Snapshot ends up with a key, so its
    imported Brewfather Taps would be overwritten on the next sync even though
    nothing about the box looked keyed beforehand.
    """
    creds = brewfather_credentials()
    box_has_key = bool(creds["api_key"])
    key_from_env = bool(creds["key_from_env"])
    snapshot_has_key = bool(str(snapshot_settings_dict.get("brewfather_api_key") or "").strip())

    if key_from_env:
        # An import cannot clear an environment variable, so the box will sync
        # whatever the operator would have said. Asking would be a question with
        # one answer.
        kind = DECISION_ENVIRONMENT
    elif box_has_key or snapshot_has_key:
        kind = DECISION_CHOOSE
    else:
        kind = DECISION_NONE
    return ImportPlan(
        kind=kind,
        box_has_key=box_has_key,
        key_from_env=key_from_env,
        snapshot_has_key=snapshot_has_key,
    )


def _credential_fields(
    snapshot_settings_dict: dict[str, Any],
    *,
    clear: bool,
) -> dict[str, str]:
    """The credential Settings this import writes, if any.

    Two rules, and the order matters:

    * `clear` blanks both fields. It is set only when the operator answered
      "stop syncing", so it is not the silent swap the next rule guards against
      - it is the answer to the question the import asked, and it is what makes
      the Snapshot's Brewfather beers stay put.
    * Otherwise a field is filled in only when the box has left it empty. The
      box's own credential always wins, per field, because the two resolve
      independently. Anything the environment owns is dropped again at the write
      seam by `update_config`, so an env-managed field cannot be written here
      even by accident.
    """
    if clear:
        return {key: "" for key in CREDENTIAL_KEYS}

    cfg = load_config()
    fields: dict[str, str] = {}
    for key in CREDENTIAL_KEYS:
        if str(cfg.get(key) or "").strip():
            continue
        value = str(snapshot_settings_dict.get(key) or "").strip()
        if value:
            fields[key] = value
    return fields


# ---- import: restoring ----------------------------------------------------

def _sweep_other_spellings(name: str) -> None:
    """Remove the box's other extensions of a root image being restored.

    `placeholder_path()` and `venue_logo_path()` pick by a fixed extension
    order, so restoring `venue_logo.png` onto a box that still has
    `venue_logo.svg` would leave the old one winning and the Snapshot's logo
    invisible. Only the image actually being restored is swept, and only its
    other spellings - an import never removes a root image the Snapshot does not
    carry.
    """
    group = PLACEHOLDER_NAMES if name in PLACEHOLDER_NAMES else VENUE_LOGO_NAMES
    for other in group:
        if other != name:
            safe_unlink(DATA_DIR / other)


def _restore_files(zf: zipfile.ZipFile, *, skip_brewfather: bool) -> dict[str, int]:
    """Write every member the plan allows. Returns what was written and skipped.

    Same-named files are replaced and everything else is left alone, which is
    exactly what unpacking the zip by hand does. Nothing is deleted to make room
    for the Snapshot: an import is a restore, not a wipe.
    """
    counts = {"taps": 0, "old_beers": 0, "images": 0, "brewfather_skipped": 0}
    for info in zf.infolist():
        if info.is_dir() or info.filename == SETTINGS_NAME:
            continue
        name = info.filename
        destination = _member_destination(name)
        if destination is None:      # unreachable after validation; belt and braces
            continue
        head, _, tail = name.partition("/")
        if head == TAPS_DIRNAME:
            identified = taps.identify(tail)
            if skip_brewfather and identified is not None and identified.source is taps.Source.BREWFATHER:
                counts["brewfather_skipped"] += 1
                continue
            counts["taps"] += 1
        elif head == OLD_BEERS_DIRNAME:
            counts["old_beers"] += 1
        else:
            _sweep_other_spellings(name)
            counts["images"] += 1
        atomic_write_bytes(destination, zf.read(name))
    return counts


def import_snapshot(path: Path, *, keep_syncing: bool | None = None) -> dict[str, Any]:
    """Restore a Snapshot from a file on disk. Returns a summary for the operator.

    Validation happens first and in full, so a refusal changes nothing. The job
    lock is then held for the whole restore - unlike the export, an import *is*
    a write, and sync or cleanup interleaving with it is exactly what the lock
    is for.

    Settings are written before the files. The one step that can refuse outright
    is `update_config`, which will not overwrite an existing-but-unreadable
    `config.json`; doing it first means that refusal happens with nothing else
    touched. The moment between the Settings landing and the venue logo
    following costs at most one poll of a board with no logo.
    """
    with open_snapshot(path) as zf:
        settings = read_snapshot_settings(zf)
        plan = plan_import(settings)
        if plan.kind == DECISION_CHOOSE and keep_syncing is None:
            raise DecisionRequired(
                "This Snapshot needs an answer about Brewfather syncing before it "
                "can be imported."
            )
        # The one question, answered: will this box have a working key when the
        # import finishes? Skipping the Snapshot's Brewfather Taps is not a
        # second decision - it is the same one, because a box that syncs rewrites
        # every Brewfather Tap within minutes.
        will_sync = (plan.kind == DECISION_ENVIRONMENT
                     or (plan.kind == DECISION_CHOOSE and bool(keep_syncing)))
        # Note this is NOT the complement of `will_sync`: a box where neither
        # side has a key is not *stopping* anything, so its credential fields are
        # restored-if-empty like any other case rather than blanked. Blanking
        # would throw away a Snapshot's user ID for no reason.
        stop_syncing = plan.kind == DECISION_CHOOSE and not keep_syncing

        restored = {key: value for key, value in settings.items()
                    if key not in CREDENTIAL_KEYS}
        restored.update(_credential_fields(settings, clear=stop_syncing))

        with JOB_LOCK:
            saved = update_config(**restored)
            counts = _restore_files(zf, skip_brewfather=will_sync)

    log.info(
        "snapshot imported (%s, syncing=%s): %d tap file(s), %d archived file(s), "
        "%d root image(s), %d brewfather file(s) skipped",
        plan.kind, will_sync, counts["taps"], counts["old_beers"],
        counts["images"], counts["brewfather_skipped"],
    )
    return {
        "decision": plan.kind,
        "keeps_syncing": will_sync,
        "counts": counts,
        "num_taps": saved["num_taps"],
    }
