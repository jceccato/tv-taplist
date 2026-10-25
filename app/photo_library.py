"""The photo library: the one module that owns `/data/photos/` and the Display copy cache.

An **Album** is a folder under `/data/photos/` holding the operator's untouched
originals; loose files at the top level form the Album "Unsorted", and folders
inside an Album's folder count as part of that Album (ADR-0008). A screen is
never sent an original. It is sent a **Display copy**: upright, converted to
sRGB, fitted to the target box up or down with Lanczos, encoded as WebP at
quality 99 (or, in compatibility mode, as baseline JPEG for old TVs) and
stripped of every byte of metadata (GPS above all). Copies and
thumbnails live in `/data/photo_cache/` and are disposable: deleting that
directory costs only reprocessing time, which is the same lifecycle test as
ADR-0002 and ADR-0006.

**Nothing else in `app/` builds a path under `photos/` or `photo_cache/`**, just
as nothing outside `tap_store.py` spells a Tap filename (ADR-0003). Callers use
the operations below: list albums, scan, process the next pending photo, list
the ready photos for some albums in an order, serve a Display copy by its
public filename, and report status.

Two rules are load-bearing:

* **A photo shows only once its Display copy is ready.** `ready_photos` lists a
  photo only while its copy file exists on disk, so a big camera file being
  processed (or a cache someone deleted) simply is not in the list yet.
* **A stale copy keeps being served until its replacement is written.** A copy
  is keyed by the original's content hash together with the pipeline settings
  (format, size, quality). When either changes the new copy is built in the
  background and the old one stays in the list until then. Each copy records its
  own file extension, because the old copy of a format change has the old one.

The cache index (`index.json`) records, for each original, its size, mtime and
content hash (so a minute-by-minute scan only re-hashes what changed), its date
taken, and which copy it has. It is as disposable as the copies: an unreadable
or missing index just means everything is re-hashed and rebuilt.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import unicodedata
import uuid
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterable, NamedTuple

from PIL import Image, ImageCms, ImageOps

from . import paths
from .atomic import atomic_write_bytes, atomic_write_text, safe_unlink

log = logging.getLogger("taplist.photos")

# ---- the pipeline's fixed choices (ADR-0008) ------------------------------------

# Formats this ticket reads. Everything else (GIF, TIFF, BMP, AVIF, HEIC, RAW) is
# a later ticket's business, so such files are not even listed as unreadable.
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP")

UNSORTED = "Unsorted"

# The target box a Display copy is fitted into, up or down. 4K by default;
# the "size" setting (global, Photos tab) can choose 1080p instead.
TARGET_SIZE = (3840, 2160)
TARGET_SIZE_1080P = (1920, 1080)
FORMAT_WEBP = "webp"
FORMAT_JPEG = "jpeg"       # compatibility mode, for TVs whose browser cannot show WebP
FORMATS = (FORMAT_WEBP, FORMAT_JPEG)
SIZE_4K = "4k"
SIZE_1080P = "1080p"
SIZES = (SIZE_4K, SIZE_1080P)
# Baseline (not progressive) JPEG, no chroma subsampling: the safest thing for an
# old TV browser, and 95 is visually indistinguishable on photographs.
JPEG_QUALITY = 95
# What a transparent PNG is flattened onto for JPEG, which has no alpha.
JPEG_BACKGROUND = (0, 0, 0)
# WebP quality 99 was measured over 24 photos fitted to 4K: it was the quality
# nearest 40 % of the lossless size for most of them (ADR-0008).
WEBP_QUALITY = 99
THUMB_SIZE = 480
THUMB_QUALITY = 80
# Bump when the pipeline changes what a copy looks like, so every copy rebuilds.
_PIPELINE_VERSION = 1

# Pillow's decompression-bomb guard, set explicitly. A bomb warning (Pillow
# warns between this and twice this, then raises) is promoted to an error while
# decoding, so an absurd file is reported as unreadable instead of exhausting RAM.
MAX_IMAGE_PIXELS = 200_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# A file modified within this many seconds is probably still being copied in, so
# the scan leaves it for the next pass rather than hashing half a file.
SETTLE_SECONDS = 2.0
# How often a long processing run re-scans so hand-made changes are not starved.
RESCAN_SECONDS = 60.0
# An unreferenced cache file must be this old before it is deleted, so a copy
# just written and not yet committed to the index is never swept away.
ORPHAN_GRACE_SECONDS = 600.0

_COPY_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_INDEX_VERSION = 1
_ERROR_TEXT_LEN = 200


# ---- uploads (issue #57) ----------------------------------------------------------

# One uploaded file may be at most this big (separate from the 10 MB beer-image cap).
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
# Uploads are refused while the data disk has less than this free, so a flood of
# photos can never fill the disk the whole box (config, taps) lives on.
MIN_FREE_BYTES = 1024 * 1024 * 1024
# A staged upload older than this is a crash's leftover and is swept.
STALE_STAGING_SECONDS = 3600.0
_MAX_STEM_LEN = 120


class Pipeline(NamedTuple):
    """The global settings a Display copy is built with, read once per photo."""
    format: str
    box: tuple[int, int]

    @property
    def extension(self) -> str:
        return ".jpg" if self.format == FORMAT_JPEG else ".webp"

    @property
    def key(self) -> str:
        """What a copy is built with: a copy with another key is stale."""
        quality = JPEG_QUALITY if self.format == FORMAT_JPEG else WEBP_QUALITY
        return f"{self.format}-q{quality}-{self.box[0]}x{self.box[1]}-p{_PIPELINE_VERSION}"


# Every extension a Display copy can have. A copy keeps the one it was built
# with, so a format change leaves the old copies findable (and removable).
_COPY_EXTENSIONS = (".webp", ".jpg")

_pipeline_cache: tuple[Any, Pipeline] | None = None


def invalidate_pipeline() -> None:
    """Forget the cached format and size; the config store calls this on every save."""
    global _pipeline_cache
    _pipeline_cache = None


def pipeline() -> Pipeline:
    """The current format and size settings.

    Read from the config (lazily, since the config store imports this module),
    and cached against the config file's stamp so the per-photo loops do not
    re-read it. A save invalidates it explicitly as well.
    """
    global _pipeline_cache
    from . import config_store
    try:
        st = config_store.CONFIG_PATH.stat()
        stamp: Any = (str(config_store.CONFIG_PATH), st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        stamp = None
    cached = _pipeline_cache
    if cached is not None and stamp is not None and cached[0] == stamp:
        return cached[1]
    cfg = config_store.load_config()
    fmt = cfg.get("photo_format")
    size = cfg.get("photo_size")
    result = Pipeline(fmt if fmt in FORMATS else FORMAT_WEBP,
                      TARGET_SIZE_1080P if size == SIZE_1080P else TARGET_SIZE)
    _pipeline_cache = (stamp, result) if stamp is not None else None
    return result


def settings_key() -> str:
    """What a Display copy is built with: a copy with another key is stale."""
    return pipeline().key


def _copy_extension(copy: dict[str, Any] | None = None) -> str:
    """A copy's own file extension; with no copy, the one a new copy would get.

    Copies written before the format setting existed have no recorded
    extension and are WebP.
    """
    if copy is None:
        return pipeline().extension
    ext = copy.get("ext")
    return ext if ext in _COPY_EXTENSIONS else ".webp"


# ---- state ------------------------------------------------------------------------

# `_LOCK` guards the in-memory index and its file. `_PROCESS_LOCK` makes
# processing one photo at a time, however many threads ask (the scheduled job,
# a test, a future "process now" button).
_LOCK = threading.RLock()
_PROCESS_LOCK = threading.Lock()
_index: dict[str, dict[str, Any]] | None = None
_last_scan = 0.0


def _display_dir() -> Path:
    return paths.PHOTO_CACHE_DIR / "display"


def _thumb_dir() -> Path:
    return paths.PHOTO_CACHE_DIR / "thumbs"


def _index_path() -> Path:
    return paths.PHOTO_CACHE_DIR / "index.json"


def _load_index() -> dict[str, dict[str, Any]]:
    """The index, from memory or (once) from disk. Unreadable means empty."""
    global _index
    with _LOCK:
        if _index is not None:
            return _index
        loaded: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(_index_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == _INDEX_VERSION:
                files = raw.get("files")
                if isinstance(files, dict):
                    loaded = {k: v for k, v in files.items()
                              if isinstance(k, str) and isinstance(v, dict)}
        except (OSError, ValueError):
            pass   # disposable: rebuilt by the next scan
        _index = loaded
        return _index


def _save_index() -> None:
    with _LOCK:
        idx = _load_index()
        try:
            atomic_write_text(_index_path(), json.dumps(
                {"version": _INDEX_VERSION, "files": idx}, separators=(",", ":")))
        except OSError as exc:
            log.warning("could not write the photo index: %s", exc)


def reset_state() -> None:
    """Forget the in-memory index (the next call reloads it from disk).

    For tests, which point the module at a fresh data tree between cases.
    """
    global _index, _last_scan
    with _LOCK:
        _index = None
        _last_scan = 0.0


# ---- albums -------------------------------------------------------------------------

def _album_of(rel: str) -> str:
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else UNSORTED


def _name_of(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def list_albums() -> list[str]:
    """Album names: each folder under `photos/`, plus Unsorted when loose photos exist."""
    names: set[str] = set()
    try:
        with os.scandir(paths.PHOTOS_DIR) as it:
            for entry in it:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    names.add(entry.name)
    except OSError:
        pass
    with _LOCK:
        if any(_album_of(rel) == UNSORTED for rel in _load_index()):
            names.add(UNSORTED)
    return sorted(names, key=str.casefold)


# ---- scanning ------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanResult:
    added: int
    changed: int
    removed: int


def _walk_photos() -> dict[str, os.stat_result] | None:
    """Every candidate original under `photos/`, by relative path; None if unreadable.

    Hidden entries and symlinks are skipped: a link could point outside the
    photo folder, and the operator's own sub-sorting never needs one.
    """
    root = paths.PHOTOS_DIR
    if not root.is_dir():
        return None
    found: dict[str, os.stat_result] = {}

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                rel = prefix + entry.name
                if entry.is_dir(follow_symlinks=False):
                    visit(Path(entry.path), rel + "/")
                elif entry.is_file(follow_symlinks=False) \
                        and entry.name.lower().endswith(PHOTO_EXTENSIONS):
                    try:
                        found[rel] = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue

    try:
        visit(root, "")
    except OSError as exc:
        log.warning("could not read the photo folder: %s", exc)
        return None
    return found


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def scan(settle_seconds: float = SETTLE_SECONDS) -> ScanResult:
    """Reconcile the index with the disk, serialised with moves, renames and removals."""
    with _STORE_LOCK:
        return _scan(settle_seconds)


def _scan(settle_seconds: float) -> ScanResult:
    """Reconcile the index with what is on disk (hand-made changes included).

    Only a file whose size or mtime changed is re-hashed. A file that vanished
    leaves the index and its copies are swept. A changed file keeps its previous
    copy in the list until the new one is ready.
    """
    global _last_scan
    found = _walk_photos()
    if found is None:
        return ScanResult(0, 0, 0)   # an unreadable folder must never empty the index
    now = time.time()
    idx = _load_index()

    with _LOCK:
        known = {rel: (e.get("size"), e.get("mtime_ns")) for rel, e in idx.items()}
    to_hash = [rel for rel, st in found.items()
               if known.get(rel) != (st.st_size, st.st_mtime_ns)
               and now - st.st_mtime >= settle_seconds]

    hashed: dict[str, tuple[os.stat_result, str]] = {}
    for rel in to_hash:
        path = paths.PHOTOS_DIR / rel
        try:
            hashed[rel] = (path.stat(), _hash_file(path))
        except OSError as exc:
            log.warning("could not read photo %s: %s", rel, exc)

    added = changed = 0
    with _LOCK:
        for rel, (st, digest) in hashed.items():
            prev = idx.get(rel)
            entry = {
                "size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": digest,
                "taken": prev.get("taken") if prev else None,
                "date": prev.get("date") if prev else st.st_mtime,
                # Kept so the previous copy is served until the new one is ready.
                "copy": prev.get("copy") if prev else None,
                "retired": prev.get("retired") if prev else None,
                "error": None,
            }
            if prev is None:
                added += 1
            else:
                changed += 1
            idx[rel] = entry
        gone = [rel for rel in idx if rel not in found]
        for rel in gone:
            del idx[rel]
        if added or changed or gone:
            _save_index()
        _sweep_orphans()
        _sweep_stale_staging()
        _last_scan = time.time()
    if added or changed or gone:
        log.info("photo scan: %d new, %d changed, %d removed", added, changed, len(gone))
    return ScanResult(added, changed, len(gone))


def _referenced_ids(thumbs: bool = False) -> set[str]:
    """Copy ids some photo still needs: Display copies of the index, plus, for
    thumbnails, those of Removed photos (the Removed view shows them)."""
    with _LOCK:
        ids = {e["copy"]["id"] for e in _load_index().values()
               if isinstance(e.get("copy"), dict) and "id" in e["copy"]}
    if thumbs:
        ids |= {m["copy_id"] for m in _removed_metas().values() if m.get("copy_id")}
    return ids


def _sweep_orphans() -> None:
    """Delete cache files no index entry points at (once past a grace period)."""
    cutoff = time.time() - ORPHAN_GRACE_SECONDS
    for directory, keep in ((_display_dir(), _referenced_ids()),
                            (_thumb_dir(), _referenced_ids(thumbs=True))):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            stem = name.rsplit(".", 1)[0]
            if stem in keep:
                continue
            path = directory / name
            try:
                if path.stat().st_mtime < cutoff:
                    safe_unlink(path)
            except OSError:
                continue


# ---- processing ----------------------------------------------------------------------

class UnreadablePhoto(Exception):
    """A file that cannot be turned into a Display copy; the message is for the operator."""


def _parse_exif_date(value: Any) -> str | None:
    """EXIF's 'YYYY:MM:DD HH:MM:SS' as ISO 8601 text, or None if it is junk."""
    if not isinstance(value, str):
        return None
    text = value.strip().strip("\x00")
    try:
        return datetime.strptime(text, "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def _date_taken(im: Image.Image) -> str | None:
    """When the photo was taken, from its EXIF - read BEFORE metadata is stripped."""
    try:
        exif = im.getexif()
        for source, tag in ((lambda: exif.get_ifd(0x8769), 0x9003),   # DateTimeOriginal
                            (lambda: exif.get_ifd(0x8769), 0x9004),   # DateTimeDigitized
                            (lambda: exif, 0x0132)):                  # DateTime
            parsed = _parse_exif_date(source().get(tag))
            if parsed:
                return parsed
    except Exception:  # noqa: BLE001 - a malformed EXIF block must not fail the photo
        pass
    return None


def _epoch(iso: str) -> float:
    # EXIF times are the camera's local clock with no zone; read them as this
    # box's local time so they compare with a file's real mtime (the fallback).
    return time.mktime(datetime.fromisoformat(iso).timetuple())


def _to_srgb(im: Image.Image, icc: bytes | None) -> Image.Image:
    """Convert to an sRGB image (RGB, or RGBA when it has transparency)."""
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (
        im.mode == "P" and "transparency" in im.info)
    alpha = None
    if has_alpha:
        im = im.convert("RGBA")
        alpha = im.getchannel("A")
    if im.mode == "CMYK" and icc:
        rgb = im          # the profile describes CMYK, so convert through it directly
    else:
        rgb = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in (ImageCms.getProfileDescription(src) or "").lower():
                rgb = ImageCms.profileToProfile(
                    rgb, src, ImageCms.createProfile("sRGB"),
                    renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")
        except Exception as exc:  # noqa: BLE001 - a bad profile falls back to a plain convert
            log.debug("colour profile ignored: %s", exc)
    if rgb.mode != "RGB":
        rgb = rgb.convert("RGB")
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb


def _fit(width: int, height: int, box: tuple[int, int]) -> tuple[int, int]:
    scale = min(box[0] / width, box[1] / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _flatten(im: Image.Image) -> Image.Image:
    """An opaque RGB image: JPEG has no alpha, so transparency goes onto a plain background."""
    if im.mode == "RGB":
        return im
    flat = Image.new("RGB", im.size, JPEG_BACKGROUND)
    flat.paste(im, mask=im.getchannel("A") if "A" in im.getbands() else None)
    flat.info = {}
    return flat


def _render(path: Path, pipe: Pipeline) -> tuple[bytes, bytes, int, int, str | None]:
    """Decode one original and return (display copy, thumbnail, width, height, date taken).

    Raises UnreadablePhoto with an operator-readable reason for anything that
    cannot be decoded, is the wrong format, or is absurdly large.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                if im.format not in _ACCEPTED_FORMATS:
                    raise UnreadablePhoto(f"unsupported format {im.format or 'unknown'}")
                taken = _date_taken(im)
                orientation = im.getexif().get(0x0112, 1)
                icc = im.info.get("icc_profile")
                w, h = im.size
                if orientation in (5, 6, 7, 8):
                    w, h = h, w
                if im.format == "JPEG":
                    # Decode large JPEGs at reduced scale: draft() only ever picks a
                    # size at least as big as asked for, so quality is unaffected.
                    fw, fh = _fit(w, h, pipe.box)
                    if fw < w:
                        im.draft("RGB", (fh, fw) if orientation in (5, 6, 7, 8) else (fw, fh))
                im.load()
                im = ImageOps.exif_transpose(im)
                im = _to_srgb(im, icc)
    except UnreadablePhoto:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise UnreadablePhoto(f"cannot be read as an image ({str(exc)[:_ERROR_TEXT_LEN]})") from exc

    width, height = im.size
    target = _fit(width, height, pipe.box)
    if target != (width, height):
        im = im.resize(target, Image.Resampling.LANCZOS)
    im.info = {}          # nothing carried over: no EXIF, no GPS, no profile, no XMP
    buf = io.BytesIO()
    if pipe.format == FORMAT_JPEG:
        _flatten(im).save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0,
                          optimize=True, progressive=False)
    else:
        im.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    thumb = im.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.info = {}
    tbuf = io.BytesIO()
    thumb.save(tbuf, "WEBP", quality=THUMB_QUALITY, method=4)
    return buf.getvalue(), tbuf.getvalue(), target[0], target[1], taken


def _copy_id(content_hash: str, key: str) -> str:
    return hashlib.sha256(f"{content_hash}|{key}".encode()).hexdigest()[:32]


def _copy_file_exists(entry: dict[str, Any]) -> bool:
    copy = entry.get("copy")
    return isinstance(copy, dict) and (
        _display_dir() / f"{copy.get('id')}{_copy_extension(copy)}").is_file()


def _is_pending(entry: dict[str, Any], key: str | None = None) -> bool:
    key = key or settings_key()
    err = entry.get("error")
    if isinstance(err, dict) and err.get("settings") == key:
        return False        # failed with these settings; retried when the file or the settings change
    copy = entry.get("copy")
    if not isinstance(copy, dict) or copy.get("settings") != key \
            or copy.get("src") != entry.get("hash"):
        return True     # no copy, built with other settings, or of an older version of the file
    return not _copy_file_exists(entry)


def _next_pending(key: str) -> tuple[str, dict[str, Any]] | None:
    with _LOCK:
        for rel in sorted(_load_index(), key=str.casefold):
            entry = _load_index()[rel]
            if _is_pending(entry, key):
                return rel, dict(entry)
    return None


def process_next() -> bool:
    """Build the Display copy of the next pending photo. False when nothing is pending.

    One photo at a time: the lock means two callers never decode two photos at
    once, which is what keeps the screens smooth on a Raspberry Pi.
    """
    with _PROCESS_LOCK:
        pipe = pipeline()        # one reading for the whole photo, even if the setting changes meanwhile
        picked = _next_pending(pipe.key)
        if picked is None:
            return False
        rel, snapshot = picked
        path = paths.PHOTOS_DIR / rel
        try:
            display, thumb, width, height, taken = _render(path, pipe)
        except UnreadablePhoto as exc:
            _record_failure(rel, snapshot, str(exc), pipe.key)
            return True
        except OSError as exc:       # the file vanished or became unreadable mid-run
            _record_failure(rel, snapshot, f"cannot be read ({str(exc)[:_ERROR_TEXT_LEN]})", pipe.key)
            return True

        copy_id = _copy_id(snapshot["hash"], pipe.key)
        try:
            atomic_write_bytes(_display_dir() / f"{copy_id}{pipe.extension}", display)
            atomic_write_bytes(_thumb_dir() / f"{copy_id}.webp", thumb)
        except OSError as exc:
            # Recorded, or this photo stays first in line and is retried forever.
            _record_failure(rel, snapshot, f"copy could not be written ({str(exc)[:_ERROR_TEXT_LEN]})", pipe.key)
            return True
        with _LOCK:
            entry = _load_index().get(rel)
            # The file changed under us: this copy is of the old content. Drop it
            # (the sweep removes the files) and let the next pass do the new one.
            if entry is None or entry.get("hash") != snapshot["hash"]:
                # Unreferenced and fresh, so the sweep would spare it for the grace
                # period: delete it now (a removed photo's copy must not be served).
                _drop_copy_files_if_unused(copy_id)
                return True
            old = entry.get("copy")
            entry["copy"] = {"id": copy_id, "settings": pipe.key, "ext": pipe.extension,
                             "src": snapshot["hash"], "w": width, "h": height}
            entry["error"] = None
            entry["taken"] = taken
            entry["date"] = _epoch(taken) if taken else _mtime_seconds(entry)
            _save_index()
            if isinstance(old, dict) and old.get("id") and old["id"] != copy_id:
                _retire_copy(entry, old["id"])
                _save_index()
        return True


def _mtime_seconds(entry: dict[str, Any]) -> float:
    return float(entry.get("mtime_ns", 0)) / 1e9


def _record_failure(rel: str, snapshot: dict[str, Any], reason: str, key: str) -> None:
    with _LOCK:
        entry = _load_index().get(rel)
        if entry is not None and entry.get("hash") == snapshot["hash"]:
            entry["error"] = {"reason": reason, "settings": key}
            _save_index()
    log.warning("photo %s cannot be shown: %s", rel, reason)


def process_pending(max_seconds: float | None = None) -> int:
    """Process pending photos one at a time until none are left; returns how many.

    Re-scans every minute while it works, so photos dropped in during a long run
    are picked up without waiting for it to finish.
    """
    started = time.monotonic()
    done = 0
    while True:
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            break
        if time.time() - _last_scan > RESCAN_SECONDS:
            scan()
        if not process_next():
            break
        done += 1
    return done


def run_scan_and_process() -> None:
    """The scheduled job: reconcile the disk, then work through what is pending."""
    scan()
    process_pending()


# ---- reading ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadyPhoto:
    """A photo whose Display copy exists, as the board needs it."""
    photo_id: str            # unique per original (two files with equal content share a copy)
    copy_id: str
    filename: str            # the Display copy's public filename (opaque, versioned by content)
    album: str
    name: str                # the original's file name, for ordering and captions
    aspect: float            # width / height of the Display copy
    taken: str | None        # ISO date taken, when the EXIF had one
    date: float              # sort date: date taken, else the file's date


def _natural_key(text: str) -> list[Any]:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text.casefold())]


ORDER_SHUFFLE = "shuffle"
ORDER_NAME = "name"
ORDER_DATE = "date"
ORDERS = (ORDER_SHUFFLE, ORDER_NAME, ORDER_DATE)


def ready_photos(albums: Iterable[str] | None = None,
                 order: str = ORDER_SHUFFLE) -> list[ReadyPhoto]:
    """The photos with a Display copy on disk, for some albums (None = all), in an order.

    `name` is by file name, `date` is newest first (date taken, falling back to
    the file's date). `shuffle` returns the set in name order: shuffling is the
    screen's business, since it also has to remember its place across visits.
    """
    wanted = None if albums is None else set(albums)
    with _LOCK:
        rows = [(rel, dict(e)) for rel, e in _load_index().items()
                if (wanted is None or _album_of(rel) in wanted)
                and isinstance(e.get("copy"), dict)]
    out: list[ReadyPhoto] = []
    for rel, e in rows:
        copy = e["copy"]
        cid = copy.get("id")
        if not isinstance(cid, str) or not _COPY_ID_RE.match(cid):
            continue
        ext = _copy_extension(copy)
        if not (_display_dir() / f"{cid}{ext}").is_file():
            continue
        w, h = copy.get("w") or 1, copy.get("h") or 1
        out.append(ReadyPhoto(
            photo_id=hashlib.sha256(rel.encode()).hexdigest()[:16], copy_id=cid, filename=f"{cid}{ext}", album=_album_of(rel),
            name=_name_of(rel), aspect=round(w / h, 4), taken=e.get("taken"),
            date=float(e.get("date") or 0.0)))
    if order == ORDER_DATE:
        out.sort(key=lambda p: (-p.date, _natural_key(p.name), _natural_key(p.album)))
    else:
        out.sort(key=lambda p: (_natural_key(p.name), _natural_key(p.album), p.copy_id))
    return out


def copy_file(filename: str) -> Path | None:
    """The Display copy for a public filename, or None (unknown, malformed, or traversal)."""
    stem, dot, ext = str(filename).rpartition(".")
    if not dot or f".{ext}" not in _COPY_EXTENSIONS or not _COPY_ID_RE.match(stem):
        return None
    path = _display_dir() / f"{stem}.{ext}"
    return path if path.is_file() else None


@dataclass(frozen=True)
class Status:
    total: int
    ready: int
    pending: int
    failed: int
    failures: tuple[tuple[str, str, str], ...]   # (album, file name, reason)


def status() -> Status:
    """Counts for the Admin, and the unreadable files listed by name with their reason."""
    with _LOCK:
        rows = [(rel, dict(e)) for rel, e in _load_index().items()]
    ready = pending = 0
    failures = []
    for rel, e in rows:
        if _copy_file_exists(e):
            ready += 1
        if isinstance(e.get("error"), dict) and e["error"].get("settings") == settings_key():
            failures.append((_album_of(rel), _name_of(rel), str(e["error"].get("reason", ""))))
        elif _is_pending(e):
            pending += 1
    return Status(len(rows), ready, pending, len(failures), tuple(sorted(failures)))


# ---- uploads ---------------------------------------------------------------------------

class UploadRejected(Exception):
    """An upload the library will not take; the message is for the operator.

    `status_code` is the HTTP status the route answers with, so the route needs
    no rules of its own.
    """
    status_code = 422


class UploadTooLarge(UploadRejected):
    status_code = 413


class LowDiskSpace(UploadRejected):
    status_code = 507


class UnknownAlbum(UploadRejected):
    status_code = 404


def _free_bytes() -> int:
    """Free space on the data disk (a seam for tests)."""
    return shutil.disk_usage(paths.DATA_DIR).free


def _sweep_stale_staging() -> None:
    cutoff = time.time() - STALE_STAGING_SECONDS
    try:
        names = os.listdir(paths.PHOTO_UPLOAD_STAGING_DIR)
    except OSError:
        return
    for name in names:
        path = paths.PHOTO_UPLOAD_STAGING_DIR / name
        try:
            if path.stat().st_mtime < cutoff:
                safe_unlink(path)
        except OSError:
            continue


@dataclass(frozen=True)
class StoredUpload:
    """What happened to one uploaded file.

    `outcome` is `stored` (filed under `name` in `album`) or `duplicate` (its
    content is already in that album as `album`/`name`; nothing was added).
    """
    outcome: str
    album: str
    name: str
    renamed: bool = False        # the name clashed and got a suffix


def _clean_name(raw: str) -> tuple[str, str]:
    """A safe (stem, extension) from whatever name the client sent. Extension is lowercased."""
    name = unicodedata.normalize("NFC", str(raw)).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.strip(" .")[:_MAX_STEM_LEN].strip(" .")
    return stem or "photo", ("." + ext.lower()) if ext else ""


def _album_dir(album: str) -> Path:
    """The folder of an existing album (Unsorted is the top level); else UnknownAlbum."""
    if album == UNSORTED:
        return paths.PHOTOS_DIR
    if album in list_albums() and "/" not in album and album not in (".", ".."):
        folder = paths.PHOTOS_DIR / album
        if folder.is_dir() and not folder.is_symlink():
            return folder
    raise UnknownAlbum(f"There is no album called “{album}”.")


def _unique_name(folder: Path, stem: str, ext: str) -> tuple[str, int]:
    """`stem+ext`, or `stem (2)+ext`, `stem (3)+ext`... the first free in `folder`.

    Returns the name and the number tried (1 when the name was free).
    """
    taken = {n.casefold() for n in os.listdir(folder)}
    name, n = f"{stem}{ext}", 1
    while name.casefold() in taken:
        n += 1
        name = f"{stem} ({n}){ext}"
    return name, n


class Upload:
    """One file being received: streamed to a staging file, hashed as it goes.

    Use as a context manager. The staging file is removed on every way out
    (stored, duplicate, rejected, failed, client gone), so a staged upload never
    outlives the request.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: BinaryIO | None = open(path, "wb")
        self._hash = hashlib.sha256()
        self.size = 0

    def __enter__(self) -> "Upload":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
        safe_unlink(self._path)

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > MAX_UPLOAD_BYTES:
            raise UploadTooLarge(_too_large_message())
        assert self._file is not None
        self._hash.update(chunk)
        self._file.write(chunk)

    def commit(self, filename: str, album: str = UNSORTED) -> StoredUpload:
        """File the received photo into an album, or report it as a duplicate."""
        assert self._file is not None
        self._file.flush()
        self._file.close()
        self._file = None
        stem, ext = _clean_name(filename)
        if ext not in PHOTO_EXTENSIONS:
            raise UploadRejected(
                f"“{stem}{ext}” is not a JPEG, PNG or WebP photo.")
        if self.size == 0:
            raise UploadRejected("The file is empty.")
        folder = _album_dir(album)
        scan(settle_seconds=0)      # so a photo dropped in by hand a moment ago counts for the duplicate check
        digest = self._hash.hexdigest()
        with _STORE_LOCK, _LOCK:
            idx = _load_index()
            for rel, e in idx.items():
                if e.get("hash") == digest and _album_of(rel) == album:
                    return StoredUpload("duplicate", _album_of(rel), _name_of(rel))
            name, n = _unique_name(folder, stem, ext)
            dest = folder / name
            os.replace(self._path, dest)
            st = dest.stat()
            rel = name if folder == paths.PHOTOS_DIR else f"{folder.name}/{name}"
            # Indexed with its hash now, so a later upload of the same content is
            # a duplicate straight away, without waiting for a scan to settle.
            idx[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": digest,
                        "taken": None, "date": st.st_mtime, "copy": None, "error": None}
            _save_index()
        return StoredUpload("stored", album, name, renamed=(n > 1))


def _too_large_message() -> str:
    return f"That file is over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit for one photo."


_STORE_LOCK = threading.Lock()


def begin_upload(declared_size: int | None = None, album: str = UNSORTED) -> Upload:
    """Start receiving one photo, or raise UploadRejected before reading any of it.

    The album is checked here too, so a file for an album that does not exist is
    refused before it is sent.

    `declared_size` is the client's Content-Length, when it sent one: a file that
    announces itself as too big is refused without a byte being staged.
    """
    if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(_too_large_message())
    _album_dir(album)
    free = _free_bytes()
    if free < MIN_FREE_BYTES:
        raise LowDiskSpace(
            f"Uploads are paused: only {free / 1024 ** 3:.1f} GB is free on the data disk "
            f"and at least {MIN_FREE_BYTES // 1024 ** 3} GB is needed. Free some space and try again.")
    paths.PHOTO_UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_stale_staging()
    return Upload(paths.PHOTO_UPLOAD_STAGING_DIR / f"{uuid.uuid4().hex}.part")


# ---- processing after an upload ---------------------------------------------------------

_kick_lock = threading.Lock()
_kick_running = False
_kick_again = False


def kick_processing() -> None:
    """Start working through pending photos now rather than at the next minute's job.

    At most one such background run exists at a time; the one-at-a-time lock in
    `process_next` still governs the actual decoding. A kick that arrives while a
    run is going (even one just finishing) makes it take another pass, because
    what the kick is about may have been missed by the pass in progress.
    """
    global _kick_running, _kick_again
    with _kick_lock:
        if _kick_running:
            _kick_again = True
            return
        _kick_running = True
        _kick_again = False

    def run() -> None:
        global _kick_running, _kick_again
        while True:
            try:
                scan()
                process_pending()
            except Exception:  # noqa: BLE001 - the scheduled job will try again
                log.exception("photo processing after an upload failed")
            with _kick_lock:
                if _kick_again:
                    _kick_again = False
                    continue
                _kick_running = False
                return

    threading.Thread(target=run, name="photo-process", daemon=True).start()


# ---- the Photos tab's listing ------------------------------------------------------------

@dataclass(frozen=True)
class PhotoRow:
    photo_id: str
    album: str
    name: str
    state: str               # ready | waiting | failed
    reason: str | None       # why, when failed
    copy_id: str | None      # names the thumbnail, when there is one


def photos_in(album: str) -> list[PhotoRow]:
    """Every photo of one album with its state, in natural name order."""
    with _LOCK:
        rows = [(rel, dict(e)) for rel, e in _load_index().items() if _album_of(rel) == album]
    out = []
    for rel, e in rows:
        err = e.get("error")
        copy = e.get("copy") if _copy_file_exists(e) else None
        if isinstance(err, dict) and err.get("settings") == settings_key():
            state, reason = "failed", str(err.get("reason", ""))
        elif copy is not None:
            state, reason = "ready", None      # a stale copy is still served until replaced
        else:
            state, reason = "waiting", None
        out.append(PhotoRow(_photo_id(rel), album, _name_of(rel),
                            state, reason, copy["id"] if copy else None))
    out.sort(key=lambda r: _natural_key(r.name))
    return out


def thumb_file(filename: str) -> Path | None:
    """The thumbnail for `<copy id>.webp`, or None (unknown, malformed, or traversal)."""
    stem, dot, ext = str(filename).rpartition(".")
    if not dot or ext != "webp" or not _COPY_ID_RE.match(stem):
        return None
    path = _thumb_dir() / f"{stem}.webp"
    return path if path.is_file() else None


# ---- organising: albums, moving, Removed (issue #58) --------------------------------------
#
# Removing, restoring, moving and renaming go through here so the state changes
# at once, without waiting for the minute's scan. Every operation holds
# `_STORE_LOCK`, the same lock an upload takes to file a photo.

class AlbumRejected(Exception):
    """An album change the library will not make; the message is for the operator."""
    status_code = 422


class AlbumExists(AlbumRejected):
    status_code = 409


MAX_ALBUM_NAME_LEN = 100
_REMOVED_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _photo_id(rel: str) -> str:
    return hashlib.sha256(rel.encode()).hexdigest()[:16]


def _rel_in(album: str, name: str) -> str:
    return name if album == UNSORTED else f"{album}/{name}"


def _valid_album_name(raw: Any) -> str:
    """A folder name that is safe to create and to type, or AlbumRejected."""
    name = unicodedata.normalize("NFC", str(raw if raw is not None else "")).strip()
    if not name:
        raise AlbumRejected("Give the album a name.")
    if len(name) > MAX_ALBUM_NAME_LEN:
        raise AlbumRejected(f"An album name can be at most {MAX_ALBUM_NAME_LEN} characters.")
    if any(not c.isprintable() or c in '/\\<>:"|?*' for c in name):
        raise AlbumRejected("An album name cannot contain / \\ < > : \" | ? * or control characters.")
    if name.startswith(".") or name.endswith("."):
        raise AlbumRejected("An album name cannot start or end with a dot.")
    if name.casefold() == UNSORTED.casefold():
        raise AlbumRejected(f"“{UNSORTED}” is the album for loose photos and is already there.")
    return name


def _drop_copy_files_if_unused(copy_id: str) -> None:
    """Delete a copy's Display copy and thumbnail once no photo, removed or not, needs them."""
    if copy_id not in _referenced_ids():
        # Whichever format it was built in: the setting may have changed since.
        for ext in _COPY_EXTENSIONS:
            safe_unlink(_display_dir() / f"{copy_id}{ext}")
    if copy_id not in _referenced_ids(thumbs=True):
        safe_unlink(_thumb_dir() / f"{copy_id}.webp")


def _retire_copy(entry: dict[str, Any], copy_id: str) -> None:
    """A copy was replaced: keep its Display copy served for the grace period.

    Screens fetch a new board only every 30 s, so one still on the previous list
    asks for the old file. Its mtime is set to now, and the orphan sweep (which
    spares files younger than `ORPHAN_GRACE_SECONDS`) removes it later. The
    thumbnail is admin-only and goes at once. The id is remembered on the entry so
    removing the photo can still delete it at once. The caller holds `_LOCK`.
    """
    now = time.time()
    retired = [r for r in (entry.get("retired") or [])
               if isinstance(r, dict) and now - float(r.get("at", 0)) < ORPHAN_GRACE_SECONDS]
    if copy_id not in _referenced_ids():
        for ext in _COPY_EXTENSIONS:
            try:
                os.utime(_display_dir() / f"{copy_id}{ext}", (now, now))
            except OSError:
                pass
        retired.append({"id": copy_id, "at": now})
    if copy_id not in _referenced_ids(thumbs=True):
        safe_unlink(_thumb_dir() / f"{copy_id}.webp")
    entry["retired"] = retired


def create_album(name: str) -> str:
    """Create an empty album (a folder). Names clash regardless of case."""
    clean = _valid_album_name(name)
    with _STORE_LOCK:
        if any(a.casefold() == clean.casefold() for a in list_albums()):
            raise AlbumExists(f"There is already an album called “{clean}”.")
        (paths.PHOTOS_DIR / clean).mkdir(parents=True, exist_ok=False)
    log.info("album created")
    return clean


def rename_album(old: str, new: str) -> str:
    """Rename an album's folder, keeping every photo's state (copies, dates).

    Profiles that ticked the old name are the caller's to update (config lives a
    layer above this module): `admin_ops.rename_album` does both.
    """
    if old == UNSORTED:
        raise AlbumRejected(f"“{UNSORTED}” cannot be renamed.")
    clean = _valid_album_name(new)
    with _STORE_LOCK:
        src = _album_dir(old)
        if any(a.casefold() == clean.casefold() and a != old for a in list_albums()):
            raise AlbumExists(f"There is already an album called “{clean}”.")
        if clean == old:
            return clean
        os.rename(src, paths.PHOTOS_DIR / clean)
        with _LOCK:
            idx = _load_index()
            prefix = old + "/"
            for rel in [r for r in idx if r.startswith(prefix)]:
                idx[f"{clean}/{rel[len(prefix):]}"] = idx.pop(rel)
            _save_index()
        for token, meta in _removed_metas().items():
            if meta.get("album") == old:
                _write_removed_meta(token, {**meta, "album": clean})
    log.info("album renamed")
    return clean


def delete_album(album: str) -> int:
    """Move every photo of an album to Removed, then drop its (now empty) folder.

    Returns how many photos moved. Files the library does not treat as photos
    stay where they are, and keep the folder alive: they are the operator's.
    """
    if album == UNSORTED:
        raise AlbumRejected(
            f"“{UNSORTED}” holds the loose photos and cannot be deleted. Remove its photos instead.")
    folder = _album_dir(album)
    with _STORE_LOCK:
        rels = [rel for rel in sorted(_walk_photos() or {}) if _album_of(rel) == album]
        moved = len(_remove_rels(rels))
        for root, _dirs, _files in os.walk(folder, topdown=False):
            try:
                os.rmdir(root)       # only succeeds when empty
            except OSError:
                pass
    log.info("album deleted: %d photo(s) to Removed", moved)
    return moved


def _rels_for(photo_ids: Iterable[str]) -> list[str]:
    with _LOCK:
        by_id = {_photo_id(rel): rel for rel in _load_index()}
    return [by_id[i] for i in dict.fromkeys(photo_ids) if i in by_id]


def move_photos(photo_ids: Iterable[str], album: str) -> int:
    """Move photos (by the ids the Photos tab lists) to the top of another album."""
    dest = _album_dir(album)
    moved = 0
    with _STORE_LOCK:
        for rel in _rels_for(photo_ids):
            if _album_of(rel) == album:
                continue
            src = paths.PHOTOS_DIR / rel
            if not src.is_file():
                continue
            stem, ext = _clean_name(_name_of(rel))
            name, _ = _unique_name(dest, stem, ext)
            shutil.move(str(src), str(dest / name))
            with _LOCK:
                idx = _load_index()
                idx[_rel_in(album, name)] = idx.pop(rel)
                _save_index()
            moved += 1
    return moved


# -- Removed ---------------------------------------------------------------------------

def _removed_meta_path(token: str) -> Path:
    return paths.PHOTOS_REMOVED_DIR / f"{token}.json"


def _write_removed_meta(token: str, meta: dict[str, Any]) -> None:
    atomic_write_text(_removed_meta_path(token), json.dumps(meta, separators=(",", ":")))


def _removed_metas() -> dict[str, dict[str, Any]]:
    """Every removed photo's record, by token. Unreadable records are skipped."""
    out: dict[str, dict[str, Any]] = {}
    try:
        names = os.listdir(paths.PHOTOS_REMOVED_DIR)
    except OSError:
        return out
    for name in names:
        token, dot, ext = name.rpartition(".")
        if not dot or ext != "json" or not _REMOVED_ID_RE.match(token):
            continue
        try:
            meta = json.loads(_removed_meta_path(token).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("ext") in PHOTO_EXTENSIONS:
            out[token] = meta
    return out


def _remove_rels(rels: Iterable[str]) -> list[str]:
    """Move originals (by path under `photos/`) into Removed; returns their tokens.

    The caller holds `_STORE_LOCK`. The record is written before the file moves
    and withdrawn if the move fails, so a photo is never in the bin unrecorded.
    """
    paths.PHOTOS_REMOVED_DIR.mkdir(parents=True, exist_ok=True)
    tokens = []
    for rel in rels:
        src = paths.PHOTOS_DIR / rel
        if not src.is_file() or src.is_symlink():
            continue
        token = uuid.uuid4().hex
        ext = src.suffix.lower()
        with _LOCK:
            entry = _load_index().get(rel) or {}
        copy = entry.get("copy")
        retired = [r["id"] for r in (entry.get("retired") or [])
                   if isinstance(r, dict) and r.get("id")]
        meta = {"album": _album_of(rel), "name": _name_of(rel), "ext": ext,
                "removed_at": time.time(),
                "copy_id": copy.get("id") if isinstance(copy, dict) else None}
        _write_removed_meta(token, meta)
        try:
            shutil.move(str(src), str(paths.PHOTOS_REMOVED_DIR / f"{token}{ext}"))
        except OSError:
            safe_unlink(_removed_meta_path(token))
            raise
        with _LOCK:
            popped = _load_index().pop(rel, None) or {}
            _save_index()
        tokens.append(token)
        # A copy installed by the pipeline since the snapshot above is dropped too.
        latest = popped.get("copy")
        if isinstance(latest, dict) and latest.get("id") and latest["id"] != meta["copy_id"]:
            retired.append(latest["id"])
        for retired_id in retired:
            _drop_copy_files_if_unused(retired_id)    # a removed photo must stop showing at once
        if meta["copy_id"]:
            # The new Removed record keeps the thumbnail alive; the Display copy
            # (served on a public route) goes unless another photo shares it.
            _drop_copy_files_if_unused(meta["copy_id"])
    return tokens


def remove_photos(photo_ids: Iterable[str]) -> int:
    """Move photos to Removed. They stop showing at once and remember their album."""
    with _STORE_LOCK:
        return len(_remove_rels(_rels_for(photo_ids)))


@dataclass(frozen=True)
class RemovedPhoto:
    removed_id: str
    album: str
    name: str
    removed_at: float
    copy_id: str | None      # names the thumbnail, when it still exists


def list_removed() -> list[RemovedPhoto]:
    """The Removed view's photos, newest removal first."""
    out = []
    for token, m in _removed_metas().items():
        if not (paths.PHOTOS_REMOVED_DIR / f"{token}{m['ext']}").is_file():
            continue          # deleted by hand: simply gone
        cid = m.get("copy_id")
        out.append(RemovedPhoto(
            token, str(m.get("album") or UNSORTED), str(m.get("name") or token),
            float(m.get("removed_at") or 0.0),
            cid if isinstance(cid, str) and thumb_file(f"{cid}.webp") else None))
    out.sort(key=lambda r: (-r.removed_at, r.removed_id))
    return out


def _tokens(removed_ids: Iterable[str]) -> list[str]:
    metas = _removed_metas()
    return [t for t in dict.fromkeys(removed_ids) if t in metas]


def restore_removed(removed_ids: Iterable[str]) -> int:
    """Put removed photos back in the album they came from (recreating its folder).

    A name already taken there gets a suffix, as in an upload. The restored
    files are picked up and given Display copies like any new photo.
    """
    restored = 0
    with _STORE_LOCK:
        metas = _removed_metas()
        for token in _tokens(removed_ids):
            meta = metas[token]
            src = paths.PHOTOS_REMOVED_DIR / f"{token}{meta['ext']}"
            if not src.is_file():
                continue
            album = str(meta.get("album") or UNSORTED)
            if album != UNSORTED:
                try:
                    album = _valid_album_name(album)
                except AlbumRejected:
                    album = UNSORTED           # a hand-edited record: never leave the library
            album = next((a for a in list_albums() if a.casefold() == album.casefold()), album)
            folder = paths.PHOTOS_DIR if album == UNSORTED else paths.PHOTOS_DIR / album
            folder.mkdir(parents=True, exist_ok=True)
            stem, ext = _clean_name(str(meta.get("name") or token))
            name, _ = _unique_name(folder, stem, meta["ext"])
            shutil.move(str(src), str(folder / name))
            safe_unlink(_removed_meta_path(token))
            restored += 1
    if restored:
        scan(settle_seconds=0)
    return restored


def _delete_removed(token: str, meta: dict[str, Any]) -> None:
    safe_unlink(paths.PHOTOS_REMOVED_DIR / f"{token}{meta['ext']}")
    safe_unlink(_removed_meta_path(token))
    if meta.get("copy_id"):
        _drop_copy_files_if_unused(meta["copy_id"])


def delete_removed(removed_ids: Iterable[str]) -> int:
    """Delete removed photos for good."""
    with _STORE_LOCK:
        metas = _removed_metas()
        tokens = _tokens(removed_ids)
        for token in tokens:
            _delete_removed(token, metas[token])
    return len(tokens)


def empty_removed() -> int:
    """Delete everything in Removed for good."""
    with _STORE_LOCK:
        metas = _removed_metas()
        for token, meta in metas.items():
            _delete_removed(token, meta)
    return len(metas)


def purge_removed(retention_days: int, now: float | None = None) -> int:
    """Delete removed photos older than `retention_days`; the daily cleanup's job."""
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    with _STORE_LOCK:
        old = {t: m for t, m in _removed_metas().items()
               if float(m.get("removed_at") or 0.0) < cutoff}
        for token, meta in old.items():
            _delete_removed(token, meta)
    return len(old)
