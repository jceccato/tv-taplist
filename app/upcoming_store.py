"""The Upcoming store: /data/upcoming/, one markdown-plus-image pair per Batch.

An **Upcoming Beer** (CONTEXT.md) is a Beer destined for a Tap but not on one -
a teaser, never a Tap. It is derived entirely from a Brewfather Batch on every
sync; no operator ever authors one. See
docs/adr/0006-the-upcoming-store-is-disposable-and-separate.md, which is the
design record this module implements.

**Read policy: Status policy, not Settings policy.** There are now three
stores and two read policies in this project, and this is the second store on
the Status side:

* `config_store` (Settings, `config.json`) refuses to overwrite an
  existing-but-unreadable file with defaults, because a hand-edited Settings
  file and the operator's Brewfather key are irreplaceable.
* `status_store` (Status, `status.json`) and **this module** do the opposite:
  read tolerantly, always write. An unreadable entry here yields nothing
  rather than raising, and the next sync's rebuild overwrites it - there is
  nothing to lose, because an Upcoming Beer is a projection of a Brewfather
  Batch recomputed from scratch every cycle. Carrying `config_store`'s
  never-overwrite guard across would convert a transient read fault into a
  permanent gap with no operator-visible way out.

A reader who "unifies" the three stores' read policies for consistency breaks
one of them - see CONTEXT.md, Known hazards, and ADR-0002.

**Filenames are private to this module**, exactly as ADR-0003 requires of the
Tap file store (`app/tap_store.py`): nothing outside `app/upcoming_store.py`
constructs or parses one - `tests/test_snapshot.py`'s AST guard, which already
forbids `custom_tap_` / `bf_tap_` spellings outside their store, extends to
this module's `upcoming_` prefix.

Unlike the Tap file store, a filename here does not decode back to the Batch
id it names: a Batch id comes from an external API and may contain characters
that are not safe on every filesystem, so an id that does not pass a narrow
safe-character check is replaced by a short digest instead of being partially
sanitised (see `_stem`). That means identity has to survive in the file's own
content, not in its name - every entry's front matter carries its own
`batch_id`, and callers address one by id (`read`/`write`) or walk all of them
(`list_all`) rather than ever constructing a path themselves.

**Rebuilt, not merged.** Each sync writes the current qualifying set and calls
`rebuild()` to remove every entry whose Batch no longer qualifies. Nothing
here is ever Archived and nothing here is pruned by the daily cleanup
(`app/cleanup.py` does not know this directory exists) - see ADR-0006's
reasoning: an Upcoming Beer was never on the board as a Tap, so there is
nothing to retire, and the whole directory is disposable.

Writes go through the existing atomic-write helpers (`app/atomic.py`), and
callers are expected to hold `JOB_LOCK` around a write the same way every
other writer in this app does - this module does not take the lock itself,
mirroring `app/tap_store.py`.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .atomic import atomic_write_bytes, atomic_write_text, safe_unlink
from .beer import Beer
from .paths import UPCOMING_DIR
from .tap_store import IMAGE_EXTS, parse_markdown, serialise_markdown

log = logging.getLogger("taplist.upcoming")

# The private filename prefix. Every file this module writes starts with it,
# which is what lets `list_all`/`clear` glob for "everything this store
# owns" and what the AST guard checks for outside this module.
_PREFIX = "upcoming_"

# The two second-level tags, one per branch of `_stem`. Keeping them distinct
# is the whole collision proof: a passthrough name and a digested name can
# never coincide (they carry different tags), two passthrough names can only
# coincide if their raw ids were equal (impossible - ids are unique), and two
# digested names would need an actual SHA-256 collision to coincide.
_SAFE_TAG = "s_"
_HASH_TAG = "h_"

# Batch-id characters that pass straight through into a filename: no dot, no
# slash, no backslash, no leading dash-dash - deliberately narrow so a
# hostile id cannot spell a traversal or a hidden/extension-bearing name.
# Anything outside this set is digested instead of being partially
# sanitised, which is what keeps the mapping simple enough to prove
# collision-free rather than merely "probably fine".
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

def _stem(batch_id: Any) -> str:
    """The private filename stem for one Batch id. See the module docstring."""
    raw = str(batch_id)
    if _SAFE_ID_RE.match(raw):
        return f"{_PREFIX}{_SAFE_TAG}{raw}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{_PREFIX}{_HASH_TAG}{digest}"


def _md_path(batch_id: Any) -> Path:
    return UPCOMING_DIR / f"{_stem(batch_id)}.md"


def _image_for_stem(stem: str) -> Path | None:
    for ext in IMAGE_EXTS:
        p = UPCOMING_DIR / f"{stem}{ext}"
        if p.exists():
            return p
    return None


@dataclass(frozen=True)
class UpcomingEntry:
    """One Upcoming Beer as the store holds it - see CONTEXT.md, Upcoming Beer.

    `slot` is the bound Slot, or None for an unbound entry (an `upcoming:`
    token with no `tap:X`). `status` is the normalised Batch status
    (`mapping.status_label`). `revision` is the Batch's recency value
    (`mapping.revision`) at write time, carried so a future ordering pass
    (issue #37) needs no second read of Brewfather's payload.

    Ordering and the display cap are display-time per ADR-0006 and are
    deliberately not computed by this store - `list_all` returns entries in
    no particular order.
    """

    batch_id: Any
    beer: Beer = field(default_factory=Beer)
    slot: int | None = None
    status: str = "unknown"
    revision: int = 0
    body: str = ""
    image: Path | None = None


def _coerce_slot(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_revision(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_status(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _load(path: Path) -> UpcomingEntry | None:
    """Read one entry file. Tolerant: any problem returns None, never raises.

    This is the store's whole read policy in one function - see the module
    docstring. A file that cannot be read, cannot be parsed, or carries no
    `batch_id` at all (so there is nothing to key it by) is treated exactly
    like a Batch that no longer qualifies: absent, and replaced by the next
    write if it still does.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.info("upcoming: could not read %s, skipping (%s)", path.name, exc)
        return None
    front_matter, body = parse_markdown(text)
    batch_id = front_matter.get("batch_id")
    if batch_id is None:
        return None
    return UpcomingEntry(
        batch_id=batch_id,
        beer=Beer.from_front_matter(front_matter),
        slot=_coerce_slot(front_matter.get("slot")),
        status=_coerce_status(front_matter.get("status")),
        revision=_coerce_revision(front_matter.get("revision")),
        body=body,
        image=_image_for_stem(path.stem),
    )


def read(batch_id: Any) -> UpcomingEntry | None:
    """Read one Batch's cached entry, or None if there is none (or it is bad)."""
    path = _md_path(batch_id)
    if not path.exists():
        return None
    return _load(path)


def list_all() -> list[UpcomingEntry]:
    """Every readable entry currently cached, in no particular order.

    An unreadable file is silently skipped (see `_load`) rather than raising,
    so one bad entry never hides the rest of the queue.
    """
    if not UPCOMING_DIR.is_dir():
        return []
    entries: list[UpcomingEntry] = []
    for path in sorted(UPCOMING_DIR.glob(f"{_PREFIX}*.md")):
        entry = _load(path)
        if entry is not None:
            entries.append(entry)
    return entries


def write(
    batch_id: Any,
    beer: Beer,
    body: str,
    *,
    slot: int | None,
    status: str,
    revision: int,
    image_bytes: bytes | None = None,
    image_ext: str | None = None,
) -> None:
    """Atomically write one Batch's entry, and its image if new bytes are given.

    `image_bytes`/`image_ext` are optional: a caller that could not fetch a
    fresh photo this cycle (a failed download, or a Batch with no image at
    all) passes neither, which leaves any previously cached image exactly as
    it was - the same "never erase a good cache on a bad fetch" rule
    `tap_store`/`brewfather.py` already follow for Tap images.
    """
    if beer.coerced:
        # Logged at the write, not the read, for the same reason
        # `tap_store.write` does: this path runs once per sync, the read path
        # runs on every board poll from every TV.
        log.warning("upcoming batch %s: dropped unusable value(s) for %s",
                    batch_id, ", ".join(beer.coerced))
    front_matter: dict[str, Any] = beer.to_front_matter()
    front_matter["batch_id"] = batch_id
    front_matter["slot"] = slot
    front_matter["status"] = status
    front_matter["revision"] = revision
    atomic_write_text(_md_path(batch_id), serialise_markdown(front_matter, body))
    if image_bytes is not None and image_ext is not None:
        save_image(batch_id, image_bytes, image_ext)


def save_image(batch_id: Any, data: bytes, ext: str) -> str:
    """Store image bytes for a Batch and return the stored filename.

    Sweeps any previously stored image for this Batch with a *different*
    extension first, mirroring `tap_store.save_image`, so one entry can never
    end up with two image files disagreeing about which is current.
    """
    ext = (ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in IMAGE_EXTS:
        raise ValueError(f"unsupported image extension: {ext!r}")
    stem = _stem(batch_id)
    for old in IMAGE_EXTS:
        old_path = UPCOMING_DIR / f"{stem}{old}"
        if old != ext and old_path.exists():
            safe_unlink(old_path)
    dest = UPCOMING_DIR / f"{stem}{ext}"
    atomic_write_bytes(dest, data)
    return dest.name


def _remove(batch_id: Any) -> bool:
    """Delete one entry's markdown and image, if present. Returns whether anything was removed."""
    removed = safe_unlink(_md_path(batch_id))
    stem = _stem(batch_id)
    for ext in IMAGE_EXTS:
        if safe_unlink(UPCOMING_DIR / f"{stem}{ext}"):
            removed = True
    return removed


def rebuild(keep_ids: Iterable[Any]) -> int:
    """Remove every cached entry whose Batch id is not in `keep_ids`.

    Sync calls this once per cycle, after writing the current qualifying set,
    so a Batch that stopped qualifying (its `tap:`/`upcoming:` token was
    removed, or it moved onto a Tap) leaves no file behind - "rebuilt, not
    merged" (ADR-0006). Nothing removed here is Archived and nothing counts
    toward `old_beers/`. Returns the number of entries removed.
    """
    keep = {str(bid) for bid in keep_ids}
    removed = 0
    for entry in list_all():
        if str(entry.batch_id) not in keep:
            if _remove(entry.batch_id):
                removed += 1
    return removed


def clear() -> int:
    """Delete every cached entry. Returns the number removed.

    Called from two places, deliberately (ADR-0006), and neither is a
    fallback for the other: `config_store.apply_settings`, the instant a Save
    flips `show_upcoming_previews` from on to off, so the operator sees
    `/data` become honest immediately; and `brewfather.run_sync`, at the
    start of a cycle that finds the Setting off with the directory still
    present, so a hand-edited `config.json` converges the same way.
    """
    if not UPCOMING_DIR.is_dir():
        return 0
    removed = 0
    for path in UPCOMING_DIR.glob(f"{_PREFIX}*"):
        if safe_unlink(path):
            removed += 1
    return removed
