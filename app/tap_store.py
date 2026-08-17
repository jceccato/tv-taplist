"""The Tap file store: address a Tap file by Slot and Source, never by filename.

Callers name a Slot (an int) and a Source (the enum below); the store answers
with the Tap file, its paired image, and - for `resolve` - which Source won.
Filenames are private to this module, which is the whole point: the naming
convention used to be a shared secret rebuilt by hand in six other modules, and
Source precedence emerged from a couple of `if` branches rather than existing
anywhere. See docs/adr/0001-file-storage-as-source-seam.md, which forecast this
shape, and CONTEXT.md for the vocabulary (Slot, Tap, Source, Vacant).

On-disk layout (unchanged, and a user-facing contract - operators read and edit
these files by hand):

  taps/custom_tap_<X>.md / custom_tap_<X>.<ext>   -> Manual, wins
  taps/bf_tap_<X>.md     / bf_tap_<X>.<ext>       -> Brewfather

Two rules worth stating up front, because both are easy to "fix" wrongly:

* **The filename is authoritative.** The front-matter `source:` key is still
  written by every writer and never read back as truth. It exists so a human
  opening a single file can see what it is; the filename decides. A mismatch is
  not warned about - the board payload is rebuilt on every poll from every TV,
  so the log would be a firehose.

* **Existence decides precedence, not readability.** `resolve` picks the first
  Source in SOURCE_PRECEDENCE whose markdown file *exists*, then reads it. A
  file that exists but fails to read yields an empty Tap rather than falling
  through to the next Source. Without that, a transient read error - the exact
  failure mode the config store's never-overwrite guard already exists for, on
  the same bind mounts - would silently demote a Manual Tap and put the
  Brewfather beer on the TV.

The format layer (front-matter parse/serialise, the image extension tuple) is
public here: those functions are pure, independently tested, and used by callers
that hold markdown text rather than a Slot. Path construction is private.

All writes go through atomic.atomic_write_*. Reads tolerate a file being renamed
or deleted mid-cycle (they return None rather than raising).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from .atomic import atomic_write_bytes, atomic_write_text, safe_unlink
from .paths import TAPS_DIR

log = logging.getLogger("taplist.taps")


class Source(str, Enum):
    """Where a Tap's data came from.

    The members read in the glossary's vocabulary (Manual, Brewfather); the
    *values* keep the legacy spellings, which are what appears in filenames on
    disk and in the `source` field of the board payload. Renaming the prefix
    would mean a data migration in an appliance meant to run untouched for
    months, for a word - so it stays `custom`.

    Mixes in `str` (rather than 3.11's StrEnum) because the app targets 3.12 but
    the development interpreter here is 3.10; pinning __str__ to str's gives the
    StrEnum behaviour on both, so f"{Source.MANUAL}" is "custom" everywhere.
    """

    MANUAL = "custom"
    BREWFATHER = "brewfather"

    def __str__(self) -> str:  # noqa: D105 - see class docstring
        return self.value


# Source precedence: the first Source here with a file for a Slot wins; a Slot
# with a file from neither is Vacant. A third Source would be one edit to this
# tuple plus one entry in _PREFIX, which is the point of the module.
SOURCE_PRECEDENCE: tuple[Source, ...] = (Source.MANUAL, Source.BREWFATHER)

# The filename prefix per Source. Private on purpose - callers pass a Source.
_PREFIX: dict[Source, str] = {
    Source.MANUAL: "custom_tap_",
    Source.BREWFATHER: "bf_tap_",
}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class TapFile:
    """One Tap file on disk: which Slot, which Source, its content, its image.

    `front_matter` is deliberately an untyped dict - turning it into a typed
    Beer is the Mapping/fetch split's job (issue #10), and doing both at once
    would make either change unreviewable.

    `body` is the markdown after the front matter (the description / tasting
    notes). It is a named field rather than being smuggled back into the
    front-matter dict under `description`, which used to make that key
    simultaneously a real key and a synthesised one.

    `image` is a Path or None - never a URL. The store knows nothing about web
    routes; the board builds the image URL from this.
    """

    slot: int
    source: Source
    front_matter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    image: Path | None = None


# ---- private path construction -------------------------------------------

def _stem(slot: int, source: Source) -> str:
    """The shared filename stem for a Slot/Source pair, e.g. 'bf_tap_3'."""
    return f"{_PREFIX[source]}{int(slot)}"


def _md_path(slot: int, source: Source) -> Path:
    return TAPS_DIR / f"{_stem(slot, source)}.md"


# ---- front matter parse / serialise --------------------------------------

def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown string into (front_matter_dict, body)."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        # No front matter: treat whole thing as body.
        return {}, text.strip()
    fm_raw, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(fm_raw) or {}
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError as exc:
        log.warning("bad YAML front matter: %s", exc)
        data = {}
    return data, body.strip()


def serialise_markdown(front_matter: dict[str, Any], body: str) -> str:
    """Build a markdown string from front matter + body."""
    fm = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    body = (body or "").strip()
    return f"---\n{fm}\n---\n{body}\n"


# ---- reading --------------------------------------------------------------

# Three-way result markers for the private loader. They exist only so `resolve`
# can tell "no file here" (try the next Source) from "a file that would not
# read" (stop, and render the Slot empty). Public `read` collapses both to None.
_MISSING = object()
_UNREADABLE = object()


def _load(slot: int, source: Source) -> Any:
    """Return a TapFile, or _MISSING, or _UNREADABLE. Private - see the markers.

    A file that vanishes between an existence check and this read counts as
    _MISSING: it does not exist, so precedence moves on. Anything else that
    goes wrong is _UNREADABLE and must not promote the next Source.
    """
    path = _md_path(slot, source)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _MISSING
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)
        return _UNREADABLE
    front_matter, body = parse_markdown(text)
    return TapFile(
        slot=int(slot),
        source=source,
        front_matter=front_matter,
        body=body,
        image=image_for(slot, source),
    )


def read(slot: int, source: Source) -> TapFile | None:
    """Read one Slot's Tap file for one Source, or None if missing/unreadable.

    Tolerates the file being renamed or removed mid-read (atomic rename races).
    Callers that need to distinguish "no Tap" from "bad file" want `resolve`,
    which is where that distinction changes the answer.
    """
    tap = _load(slot, source)
    return tap if isinstance(tap, TapFile) else None


def resolve(slot: int) -> TapFile | None:
    """The winning Tap for a Slot, or None if the Slot is Vacant.

    Walks SOURCE_PRECEDENCE and takes the first Source whose markdown file
    *exists*. Existence - not readability - decides: a file that exists but
    will not read yields an empty TapFile for that Source rather than letting
    the next Source through, so a disk hiccup can never swap one brewery's beer
    for another's on the board. See the module docstring.
    """
    for source in SOURCE_PRECEDENCE:
        if not exists(slot, source):
            continue
        tap = _load(slot, source)
        if isinstance(tap, TapFile):
            return tap
        if tap is _MISSING:
            # Deleted between the check and the read - genuinely not there.
            continue
        # _UNREADABLE: the Slot belongs to this Source, but we cannot say what
        # is in it. An empty Tap renders as a blank card, which is honest;
        # falling through would render a different beer, which is not.
        return TapFile(
            slot=int(slot),
            source=source,
            front_matter={},
            body="",
            image=image_for(slot, source),
        )
    return None


def exists(slot: int, source: Source) -> bool:
    """Whether this Source holds a Tap file for this Slot.

    This is the single answer to "is this Slot Manual?" - `exists(slot,
    Source.MANUAL)` - which used to be answered four different ways.
    """
    return _md_path(slot, source).exists()


def occupied_slots(source: Source) -> list[int]:
    """Every Slot this Source holds a Tap file for, ascending.

    Deliberately *not* bounded by the configured tap count. Orphan retirement
    has to be able to see Slots above it: an operator who lowers the tap count
    still has Brewfather files sitting at the old higher Slots, and those are
    exactly the ones a later sweep needs to find. Bounding this by a display
    setting is how a presentation choice turns into a data decision.
    """
    prefix = _PREFIX[source]
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.md$")
    slots: list[int] = []
    for path in TAPS_DIR.glob(f"{prefix}*.md"):
        m = pattern.match(path.name)
        if m:
            slots.append(int(m.group(1)))
    return sorted(slots)


# ---- writing --------------------------------------------------------------

def write(slot: int, source: Source, front_matter: dict[str, Any], body: str) -> None:
    """Atomically write one Slot's Tap file for one Source.

    The store does not police which Source a caller writes: Admin legitimately
    writes a Manual file over an occupied Brewfather Slot and demo seeding
    writes both. Sync's "never touch a Manual Tap" rule is a sync policy, and
    it is enforced structurally by sync only ever passing Source.BREWFATHER.
    """
    atomic_write_text(_md_path(slot, source), serialise_markdown(front_matter, body))


# ---- the paired image -----------------------------------------------------

def _normalise_ext(ext: str) -> str:
    """Coerce an extension to the stored spelling ('.jpg', lower-case, dotted)."""
    ext = (ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    if ext == ".jpeg":
        ext = ".jpg"
    return ext


def image_for(slot: int, source: Source) -> Path | None:
    """The image paired with this Slot/Source Tap file, whatever its extension.

    Never falls back to the other Source: a Tap comes entirely from one Source,
    so a Manual Tap with no photo shows the placeholder rather than borrowing
    the Brewfather one.
    """
    stem = _stem(slot, source)
    for ext in IMAGE_EXTS:
        p = TAPS_DIR / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def save_image(slot: int, source: Source, data: bytes, ext: str) -> str:
    """Store image bytes for a Slot/Source and return the stored filename.

    Sweeps any previously stored image for this Tap with a *different*
    extension, so a Slot can never end up with both bf_tap_5.jpg and
    bf_tap_5.webp and no way to say which one is current. The md-plus-image
    pair is one unit here precisely so that sweep cannot be present on one
    write path and absent from another.

    The caller vouches for the bytes: HTTP concerns (the extension allow-list,
    the upload size cap, and validating before anything on disk changes) stay
    in the route. `ext` is still coerced and checked here so an unknown
    extension fails loudly instead of landing on disk.
    """
    ext = _normalise_ext(ext)
    if ext not in IMAGE_EXTS:
        raise ValueError(f"unsupported image extension: {ext!r}")
    stem = _stem(slot, source)
    for old in IMAGE_EXTS:
        old_path = TAPS_DIR / f"{stem}{old}"
        if old != ext and old_path.exists():
            safe_unlink(old_path)
    dest = TAPS_DIR / f"{stem}{ext}"
    atomic_write_bytes(dest, data)
    return dest.name


# ---- for archiving only ---------------------------------------------------
#
# The two functions below hand out paths and a destination name, which is
# otherwise exactly what this module exists to stop doing. They are the one
# acknowledged crack in an otherwise private interface: archiving is a
# transition between two directories rather than storage of current Taps, and
# it owns genuine mechanics of its own (copy-to-temp, atomic replace, unlink,
# and tolerance of missing files). Absorbing that into the store would make the
# store the owner of a second directory for no gain. Nothing else should call
# these.

def existing_paths(slot: int, source: Source) -> list[Path]:
    """The files that exist for this Tap (md, and its image if any).

    **For archiving only** - see the note above. Callers that want the content
    want `read`/`resolve`; callers that want the photo want `image_for`.
    """
    paths: list[Path] = []
    md_path = _md_path(slot, source)
    if md_path.exists():
        paths.append(md_path)
    img = image_for(slot, source)
    if img is not None:
        paths.append(img)
    return paths


def archived_stem(slot: int, source: Source, when: datetime) -> str:
    """The datetime-suffixed stem an archived copy of this Tap is filed under.

    e.g. 'bf_tap_3_20260624T153000'. The suffix means a Slot that turns over
    twice in one day does not overwrite its own archive entry, and cleanup
    reads the archive back generically by stem.

    **For archiving only** - see the note above.
    """
    return f"{_stem(slot, source)}_{when.strftime('%Y%m%dT%H%M%S')}"
