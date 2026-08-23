"""**Beer** - the beverage itself, as a type, plus the two small records that
travel beside it across the Tap file store seam.

CONTEXT.md has called Beer a first-class term since the glossary was written
("the beverage itself: name, ABV, IBU, colour, description, image, independent
of where it is served"). In code it was a `dict[str, Any]` built independently
by three writers - Brewfather Mapping, the Admin's Manual override, and the demo
seeder - and read back with `.get()` plus a defensive coercion at every use.
Nothing made the three writers agree, and they did not: the demo seeder wrote 7
of the ~18 keys and nobody noticed, because every reader defended itself. This
module is the one description of what a Beer is, so a fourth writer - a second
Source connector, which ADR-0003 designs for - cannot drift the same way.

**Nothing here performs I/O**, and nothing here may. `mapping.py` constructs a
`Beer` and is pinned by an import-purity guard that forbids anything touching
disk, so the type has to be reachable without the Tap file store; `tap_store.py`
in turn imports this module to parse and serialise. The only import is
`colors.py`, which is pure arithmetic and regexes, and which already owns hex and
saturation parsing - re-implementing either here would be a second opinion about
Colour. `tests/test_beer.py` pins that import list.

Three types live here rather than one because they share the coercion helpers
below and are written into the same file by the same store:

* `Beer` - the beverage. What the store reads back and hands to the board.
* `TapPresentation` - the per-Slot Visibility overrides (`show_og`/`show_fg`).
  Not properties of the beverage: they say how *this Slot* renders, and the same
  Beer poured on another Slot would not carry them.
* `SourceRevision` - a Source's cache-coherence record. Meaningless for a Manual
  Tap, which is why `TapFile.revision` is `None` there: the type says
  "Brewfather-only" instead of a comment saying it.

## What is deliberately NOT on Beer

`source`, `image` and `updated` are written into the Tap file for a human
reading it in a text editor, and none of them is read back as truth: the
filename decides the Source (ADR-0003), the store finds the photo by globbing
the stem (`tap_store.image_for`), and `updated` is a fact about the file rather
than about the beverage - a Manual Tap edited today did not change the beer.
One rule covers all three: **Beer carries what the store reads back; anything
written only for a human is serialisation garnish**, and the store adds it.

## The rules the type enforces

* **`None` is the only absence.** `""` never survives the seam, and `0` is a
  value rather than a missing reading - a 0 IBU says the beverage has no
  bittering component. This is not new; it is what `board._is_missing` already
  implemented on every board build. Doing it once here is what lets that
  function collapse to `value is None`.
* **Coerce per field, never reject.** `abv: banana` in a hand-edited file
  becomes `abv=None` and the Tap resolves normally, under its own Source. See
  docs/adr/0005-beer-crosses-the-store-seam-as-a-type.md: file-level readability
  and value-level validity are different questions, and merging them would let a
  typo do what a disk fault does.
* **The type is closed.** Unknown front-matter keys are dropped when a file is
  rewritten. An `extra: dict` passthrough was rejected - it would make Beer an
  untyped dict wearing a hat (ADR-0005 again).

Coercion is silent here on purpose. This runs on the read path, and the board is
rebuilt on every poll from every TV, so a warning would be a firehose - the same
reasoning ADR-0003 gives for not warning on a `source:` mismatch. What was
dropped is recorded on `Beer.coerced` instead, and `tap_store.write` logs it
once, at the write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .colors import parse_hex_color, parse_saturation

# The front-matter keys a Beer round-trips, in the order they are written. The
# order is the one both real writers already used, so a Tap file opened in an
# editor reads the same after this change as before it.
BEER_KEYS: tuple[str, ...] = (
    "name", "abv", "ibu", "ebc", "og", "fg",
    "saturation", "color_override", "glass",
)

_PRESENTATION_KEYS: tuple[str, ...] = ("show_og", "show_fg")
_REVISION_KEYS: tuple[str, ...] = ("batch_id", "source_rev", "map_rev")


# ---- coercion -------------------------------------------------------------
#
# Each helper answers with the coerced value and whether a *usable* value was
# thrown away. Blank input is not a discard: an empty field is a legitimate way
# to say "no reading", and logging it would fire on half the Tap files on a box.

def _number(value: Any) -> tuple[float | int | None, bool]:
    """Coerce an Attribute to a number, or to None.

    Integral values come back as `int` so a hand-edited file reads `abv: 5`
    rather than `abv: 5.0` (ADR-0001 makes these files something operators
    open). This is `board._num` and `admin_ops._number` unified - minus the
    latter's raising, which stays in the Admin route's own layer where there is
    somebody to report an error to.
    """
    if value is None or value == "":
        return None, False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None, True
    return (int(f) if f.is_integer() else f), False


def _name(value: Any) -> str:
    """Coerce a Beer's name to a stripped string.

    Non-strings are rendered rather than dropped: `name: 42` in a hand-edited
    file is a name the operator meant, and it used to reach `.strip()` as an int
    and take the whole board down with an AttributeError.
    """
    if value is None:
        return ""
    return (value if isinstance(value, str) else str(value)).strip()


def _glass_key(value: Any) -> str | None:
    """Coerce a glassware key to a non-blank string, or None.

    Validity is not checked here - see the note on `Beer.glass`.
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _tri(value: Any) -> bool | None:
    """A tri-state Visibility override: True / False / None (inherit)."""
    if value is None or value == "":
        return None
    return bool(value)


@dataclass(frozen=True)
class Beer:
    """One beverage: what the board renders and what a Tap file stores.

    Frozen because a Beer is a value read out of a file, not a mutable record -
    two Beers with the same fields are the same beer, and nothing should be able
    to edit one in place after the store handed it out.

    Every field is coerced on construction, whoever builds it, so the rules hold
    for a future Source connector that has never read this docstring. Values are
    typed as they are *stored*: `saturation` is a 0..1 fraction, `color_override`
    a normalised `#rrggbb`, `ebc` always EBC even when the operator's display
    unit is SRM.

    `glass` is deliberately **not** validated against the known glassware keys.
    `beer_glass.normalize_glass` is the single expression of the glass fallback,
    and it distinguishes an unknown key (falls back to the built-in default) from
    no key at all (inherits the operator's global choice). Coercing an unknown
    key to None here would quietly move a hand-edited Tap from one of those
    branches to the other.
    """

    name: str = ""
    abv: float | int | None = None
    ibu: float | int | None = None
    ebc: float | int | None = None
    og: float | int | None = None
    fg: float | int | None = None
    saturation: float | None = None
    color_override: str | None = None
    glass: str | None = None

    # Which fields held something unusable. Excluded from equality and repr: it
    # is a note about *this construction*, not part of what the beer is, so a
    # round trip through YAML still compares equal. `tap_store.write` is what
    # reads it - see the module docstring on why the log belongs at the write.
    coerced: tuple[str, ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        dropped: list[str] = []
        values: dict[str, Any] = {}

        values["name"] = _name(self.name)
        for key in ("abv", "ibu", "ebc", "og", "fg"):
            number, lost = _number(getattr(self, key))
            values[key] = number
            if lost:
                dropped.append(key)
        # Saturation and the Colour override are coerced by colors.py, which
        # owns both formats. Both accept a blank as "not set", so only a
        # non-blank value that would not parse counts as dropped.
        values["saturation"] = parse_saturation(self.saturation)
        if self.saturation not in (None, "") and values["saturation"] is None:
            dropped.append("saturation")
        values["color_override"] = parse_hex_color(self.color_override)
        if self.color_override not in (None, "") and values["color_override"] is None:
            dropped.append("color_override")
        values["glass"] = _glass_key(self.glass)

        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "coerced", tuple(dropped))

    @classmethod
    def from_front_matter(cls, data: Mapping[str, Any] | None) -> Beer:
        """Build a Beer from a Tap file's front matter, coercing every field.

        Keys the type does not know are dropped rather than carried: see the
        module docstring on the type being closed.
        """
        data = data or {}
        return cls(**{key: data.get(key) for key in BEER_KEYS})

    def to_front_matter(self) -> dict[str, Any]:
        """The Beer's half of a Tap file's front matter, in written order.

        Every key is emitted even when its value is None, which is what the two
        real writers already did. An absent key and a null one read back
        identically, but a full key set is what makes a Tap file legible as a
        form somebody can fill in by hand.
        """
        return {key: getattr(self, key) for key in BEER_KEYS}


@dataclass(frozen=True)
class TapPresentation:
    """Per-Slot Visibility overrides, stored in the Tap file beside the Beer.

    Tri-states: True or False is a deliberate instruction for this Slot, None
    means inherit the global Setting. They are not on `Beer` because they are
    not facts about the beverage - the same beer moved to another Slot would not
    bring them along.

    Only the Admin writes these. Sync does not, which is why `tap_store.write`
    omits the keys entirely when no presentation is supplied rather than writing
    two nulls into every Brewfather Tap file.
    """

    show_og: bool | None = None
    show_fg: bool | None = None

    def __post_init__(self) -> None:
        for key in _PRESENTATION_KEYS:
            object.__setattr__(self, key, _tri(getattr(self, key)))

    @classmethod
    def from_front_matter(cls, data: Mapping[str, Any] | None) -> TapPresentation:
        data = data or {}
        return cls(**{key: data.get(key) for key in _PRESENTATION_KEYS})

    def to_front_matter(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in _PRESENTATION_KEYS}


@dataclass(frozen=True)
class SourceRevision:
    """A Source's cache-coherence record for one Tap file.

    Brewfather's is a Batch id, the Batch's recency value, and the mapping
    version that extracted it; sync compares the stored record against the one a
    fresh Batch would produce to decide whether a rewrite (and an image
    re-download) is needed at all. It is `None` on a Manual Tap because a Manual
    Tap is not a cache of anything.

    The fields stay `Any` on purpose. They are opaque tokens belonging to
    whichever Source minted them - only equality is meaningful, and they are
    written back exactly as they were given so an operator's `source_rev: 12345`
    does not become `'12345'` on the next sync.
    """

    batch_id: Any = None
    source_rev: Any = None
    map_rev: Any = None

    def matches(self, other: SourceRevision) -> bool:
        """Whether two records name the same Batch at the same revision.

        Compared as text because one side has been through YAML, which may have
        parsed a numeric id or revision back as an int while the other side
        holds the string the API returned.
        """
        return all(
            str(getattr(self, key)) == str(getattr(other, key))
            for key in _REVISION_KEYS
        )

    @classmethod
    def from_front_matter(cls, data: Mapping[str, Any] | None) -> SourceRevision | None:
        """Read a revision record, or None when the file carries none.

        None is the answer for a Manual Tap, and the caller is expected to treat
        it as "no cached revision" rather than as a record of blanks - which is
        the distinction that keeps `mapping.is_current` from ever calling a
        Manual Tap current.
        """
        data = data or {}
        if not any(key in data for key in _REVISION_KEYS):
            return None
        return cls(**{key: data.get(key) for key in _REVISION_KEYS})

    def to_front_matter(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in _REVISION_KEYS}
