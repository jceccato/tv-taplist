"""Mapping: turn a Brewfather **Batch** into the fields of a **Beer**.

This module is named for the glossary term (see CONTEXT.md, *Mapping*). The
short name is for readability, not a claim of generality: everything here maps
Brewfather's Batch entity specifically - its field names, its note tokens, its
lifecycle statuses. A second Source would bring its own mapping, not reuse this
one.

**Nothing here performs I/O.** No HTTP client, no filesystem, no Tap file store,
no archive. Every function takes plain data - a Batch dict, a cached
`SourceRevision` - and returns plain data, so "what Beer does this Batch map
to?" is a question that can be asked and answered without a network client. That
is the whole point of the split (issue #10); keep it that way. `app/beer.py`,
which holds the Beer type this module builds, is dependency-free for the same
reason (issue #32). The one import that reaches outside the pure layer is
`MAX_NUM_TAPS`, a constant, deliberately left where the operator-facing Settings
schema keeps it.

Field mapping (verified against a live /v2/batches?complete=True payload):
  name        <- recipe.name      (a batch's own name is Brewfather's generic
                                    "Batch" / "Batch #12" default)
  abv / ibu   <- measured* first, then recipe.*; 0 is treated as "not provided"
                 (Brewfather returns 0, not null, for unset values)
  colour      <- measuredEbc (EBC); else estimatedColor / color, which are SRM
                 (verified via style colour bounds) -> converted to EBC
  og / fg     <- measuredOg/Og, measuredFg/Fg, else recipe.og/fg; kept only when a
                 plausible specific gravity (1.0 < sg < 1.2), else None
  saturation  <- optional `saturation:NN` note token (NN% -> 0..1 fraction)
  colour      <- optional `colour:#rrggbb` note token; an exact override that wins
                 over the computed EBC colour
  glass       <- optional `glass:nonicpint` note token (glassware silhouette)
  upcoming    <- optional valueless `upcoming:` note token; presence alone means
                 "tease this beer" (issue #35). No payload, no ETA.
  description <- tasteNotes, else the recipe style name. The batch notes are NOT
                 used for the body - they only carry the control tokens
                 (tap:X / saturation:NN / colour:#hex / glass:type / upcoming:),
                 which are all stripped from any text we do show.

The helpers still try several field-name/unit variants defensively and log what
they found. Bump MAPPING_VERSION when changing the mapping so already-cached
files are refreshed on the next sync.

Tap assignment: parse the batch notes text for a `tap:X` token. A token is
accepted from 1 to MAX_NUM_TAPS - a system bound, not the operator's tap count -
and an out-of-range one is logged with the batch named.

An unfinished Batch (fermenting / brewing / planning) has no trustworthy
measured reading, so `beer(batch, provisional=True)` reads colour, IBU, OG, FG
and ABV from the recipe together instead of measured-first - see `beer()` and
`_recipe_attributes`. The flag defaults to False and is only ever set by a
caller building an Upcoming Beer, never for a pouring Tap.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from .beer import Beer, SourceRevision
from .beer_glass import GLASS_KEYS
from .colors import EBC_PER_SRM, parse_hex_color, parse_saturation
from .config_store import MAX_NUM_TAPS

# Deliberately the *sync* channel, not a channel of this module's own. Every
# message below is emitted while a sync cycle is running and is read as part of
# that cycle's output ("why did this beer not appear?"), so splitting the module
# in two must not split the operator's log filter in two as well.
log = logging.getLogger("taplist.sync")

# Bumped whenever the field-extraction logic below changes in a way that should
# refresh already-cached bf_tap files. `is_current` treats a stored map_rev
# different from this as "changed", so the next sync rewrites every tap once with
# the new mapping, then settles back to skipping genuinely unchanged batches.
#
# 7 covers three changes together, landed in one bump so operators pay one
# cache rewrite rather than three (issue #35): the `upcoming:` note token, the
# recipe-only rule for a provisional (Upcoming) Beer built from an unfinished
# Batch, and the `batch_status` field now stamped on every Brewfather Tap file.
MAPPING_VERSION = 7

# `tap:3`, `tap: 3`, `Tap:3`, etc.
TAP_TOKEN_RE = re.compile(r"tap\s*:\s*(\d+)", re.IGNORECASE)

# `saturation:60` (= 60% = 0.6) - an optional per-tap colour-saturation override
# in the batch notes, parsed the same way as the tap token.
SATURATION_TOKEN_RE = re.compile(r"saturation\s*:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)

# `colour:#780606` / `color:#780606` - force an exact swatch/glass colour,
# overriding the computed EBC colour.
COLOR_TOKEN_RE = re.compile(r"colou?r\s*:\s*(#?[0-9a-fA-F]{6})", re.IGNORECASE)

# `glass:nonicpint` - choose the glassware silhouette for this beer's placeholder.
GLASS_TOKEN_RE = re.compile(r"glass\s*:\s*([a-zA-Z]+)", re.IGNORECASE)

# `upcoming:` - valueless: presence alone means "tease this beer" (issue #35).
# No payload, no ETA, no priority - the token's shape is deliberately closed
# for now. A colon is required, same as every other token here, so the plain
# English word "upcoming" inside a tasting note is never mistaken for it.
UPCOMING_TOKEN_RE = re.compile(r"upcoming\s*:", re.IGNORECASE)

# A Brewfather batch's own `name` defaults to a generic "Batch" / "Batch #12";
# the real beer name lives on the embedded recipe, so we skip these.
GENERIC_BATCH_NAME_RE = re.compile(r"^\s*batch\s*#?\s*\d*\s*$", re.IGNORECASE)


# ---- low-level field extraction (defensive) ------------------------------

def _first_number(obj: dict[str, Any], *keys: str) -> float | None:
    """Return the first present *positive* numeric value among keys.

    Callers list measured fields before estimated/recipe ones. Brewfather sends
    0 (not null) for an unset ABV / IBU / colour, so any non-positive value is
    treated as "not provided": callers then store None and the display hides
    that stat (and the colour swatch) instead of showing a 0.
    """
    for key in keys:
        if key not in obj or obj[key] in (None, ""):
            continue
        try:
            num = float(obj[key])
        except (TypeError, ValueError):
            continue
        if num > 0:
            return num
    return None


def beer_name(batch: dict[str, Any]) -> str:
    recipe = batch.get("recipe") or {}
    batch_name = (batch.get("name") or "").strip()
    recipe_name = (recipe.get("name") or "").strip()
    # A batch's own `name` is usually Brewfather's generic default ("Batch" /
    # "Batch #12"); the real beer name lives on the embedded recipe. Use the
    # batch name only when the user has renamed it to something specific,
    # otherwise prefer the recipe (beer) name.
    if batch_name and not GENERIC_BATCH_NAME_RE.match(batch_name):
        return batch_name
    if recipe_name:
        return recipe_name
    # Generic/blank batch name and no recipe name: build the most specific
    # generic label we can from the batch number.
    batch_no = batch.get("batchNo")
    if batch_no not in (None, ""):
        return f"Batch {batch_no}"
    return batch_name or "Batch"


def abv(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    # Prefer measured over estimated/recipe so the board shows reality.
    return _first_number(batch, "measuredAbv", "abv") or _first_number(recipe, "abv")


def ibu(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return (
        _first_number(batch, "measuredIbu", "estimatedIbu", "ibu")
        or _first_number(recipe, "ibu")
    )


def ebc(batch: dict[str, Any]) -> float | None:
    """Return colour as EBC (our internal storage unit).

    A *measured EBC* reading (explicit unit) is used as-is. Everything else
    Brewfather exposes for colour - estimatedColor, color, recipe.color - is in
    SRM despite the generic name. This was verified against a live payload: an
    English Porter's styleColorMin/Max come back as 20/30, which is the BJCP
    *SRM* range (the EBC range would be ~39/59). So those are converted with
    EBC = SRM * 1.97, otherwise every beer renders about half as dark as reality.
    """
    recipe = batch.get("recipe") or {}
    # Measured EBC wins and is taken at face value.
    measured = _first_number(batch, "measuredEbc")
    if measured is not None:
        return round(measured, 1)
    # All the estimated/recipe colour fields are SRM -> convert to EBC.
    srm = (
        _first_number(batch, "measuredSrm", "estimatedColor", "color", "srm")
        or _first_number(recipe, "color", "srm")
    )
    if srm is not None:
        return round(srm * EBC_PER_SRM, 1)
    # Rare explicit recipe EBC field.
    rebc = _first_number(recipe, "ebc")
    return round(rebc, 1) if rebc is not None else None


def _first_gravity(obj: dict[str, Any], *keys: str) -> float | None:
    """First plausible specific-gravity value (1.0 < sg < 1.2) among keys.

    Brewfather sends OG/FG as specific gravity (e.g. 1.052). An unset value comes
    back as 0 or 1.0, and a Plato-stored field would be out of the SG range - both
    are rejected so the display hides the stat rather than showing nonsense.
    """
    for key in keys:
        if key not in obj or obj[key] in (None, ""):
            continue
        try:
            num = float(obj[key])
        except (TypeError, ValueError):
            continue
        if 1.0 < num < 1.2:
            return round(num, 3)
    return None


def og(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return _first_gravity(batch, "measuredOg", "og") or _first_gravity(recipe, "og")


def fg(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return _first_gravity(batch, "measuredFg", "fg") or _first_gravity(recipe, "fg")


def _notes_text(batch: dict[str, Any]) -> str:
    """Concatenate every free-text notes field we might find a tap token in."""
    parts: list[str] = []
    for key in ("batchNotes", "notes", "note", "tasteNotes"):
        val = batch.get(key)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            # Brewfather `notes` can be a list of {note: "..."} objects.
            for item in val:
                if isinstance(item, dict) and isinstance(item.get("note"), str):
                    parts.append(item["note"])
                elif isinstance(item, str):
                    parts.append(item)
    return "\n".join(parts)


def _clean_description(text: str) -> str:
    """Strip the control tokens (tap / saturation / colour / glass / upcoming) and tidy whitespace."""
    cleaned = TAP_TOKEN_RE.sub(" ", text)
    cleaned = SATURATION_TOKEN_RE.sub(" ", cleaned)
    cleaned = COLOR_TOKEN_RE.sub(" ", cleaned)
    cleaned = GLASS_TOKEN_RE.sub(" ", cleaned)
    cleaned = UPCOMING_TOKEN_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s*\n\s*", "\n", cleaned)
    return cleaned.strip()


def _style(batch: dict[str, Any]) -> str:
    """Recipe style name (e.g. "English Porter"), used as a description fallback."""
    style = (batch.get("recipe") or {}).get("style")
    if isinstance(style, dict):
        return (style.get("name") or "").strip()
    if isinstance(style, str):
        return style.strip()
    return ""


def description(batch: dict[str, Any]) -> str:
    """Card body text: Brewfather tasting notes, else the beer style.

    The dedicated tasting-note field wins when present; otherwise we fall back to
    the recipe's style name so the card isn't blank (most Brewfather batches have
    no tasting notes). The batch notes are deliberately NOT used for the body --
    they hold the `tap:X` control token, not display text - and any such token is
    stripped from whatever text we do show.
    """
    for key in ("tasteNotes", "tastingNotes", "taste_notes", "tasting_notes"):
        val = batch.get(key)
        if isinstance(val, str) and val.strip():
            cleaned = _clean_description(val)
            if cleaned:
                return cleaned
    return _style(batch)


def image_url(batch: dict[str, Any]) -> str | None:
    recipe = batch.get("recipe") or {}
    for src in (batch, recipe):
        for key in ("img_url", "imgUrl", "image", "imageUrl"):
            val = src.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def wants_image(batch: dict[str, Any]) -> bool:
    """True when this Batch offers a photo at all.

    The pure half of the cache-freshness question: whether a cached Tap *should*
    have an image. Whether it actually *has* one is a storage question and is
    answered by the Tap file store, not here.
    """
    return image_url(batch) is not None


def batch_id(batch: dict[str, Any]) -> Any:
    """The Batch's own identifier, whichever of the two spellings it carries.

    Returned raw rather than coerced: it is written into the Tap file as-is, and
    every comparison against it is done on `str()` of both sides, so a Batch
    whose id arrives as a number still matches its cached copy.
    """
    return batch.get("_id") or batch.get("id")


def revision(batch: dict[str, Any]) -> int:
    """A sortable recency value for conflict resolution (newest wins).

    Doubles as the change-detection key stored as `source_rev`: a Batch edited in
    Brewfather comes back with a newer value, which is what makes the next sync
    rewrite its Tap instead of skipping it.
    """
    for key in ("_timestamp_ms", "updated", "completedDate", "brewDate", "_created"):
        val = batch.get(key)
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, dict) and isinstance(val.get("ms"), (int, float)):
            return int(val["ms"])
    return 0


# Brewfather's batch lifecycle, most complete first. Only the first three can
# reach a sync today (see the include_conditioning / include_fermenting
# toggles); Brewing and Planning are listed anyway so this reads as the whole
# lifecycle rather than an arbitrary subset if the toggles ever widen.
STATUS_PRECEDENCE: tuple[str, ...] = (
    "completed", "conditioning", "fermenting", "brewing", "planning",
)


def status_rank(batch: dict[str, Any]) -> int:
    """Sort key for Batch status - LOWER is more complete.

    An unrecognised or missing status ranks last, which deliberately makes a
    Batch the API did not label lose to one it did. It also means that if
    Brewfather ever stops sending `status`, every Batch ranks the same and
    conflict resolution falls back to recency - exactly the behaviour that
    shipped before this rule existed.
    """
    try:
        return STATUS_PRECEDENCE.index(status_label(batch))
    except ValueError:
        return len(STATUS_PRECEDENCE)


def status_label(batch: dict[str, Any]) -> str:
    """The Batch status, normalised for comparison and for the conflict log.

    Missing, empty, and non-string statuses all collapse to "unknown" so the
    logged conflict line never prints a bare '' or 'None' at the operator.
    """
    raw = batch.get("status")
    label = str(raw).strip().lower() if isinstance(raw, str) else ""
    return label or "unknown"


def slot_claim(batch: dict[str, Any]) -> int | None:
    """The Slot a batch claims via its `tap:X` note token, or None for no claim.

    The accepted range is 1..MAX_NUM_TAPS, which is a *system* bound, not the
    operator's configured tap count. Sync deliberately never consults the tap
    count: that is a display setting, and letting it decide what gets written or
    archived is what used to make lowering the tap count silently destroy Beer
    data. The system bound still stops one fat-fingered token (`tap:9999`) from
    minting files nothing can ever display.

    An out-of-range token is logged with the batch named, because silence is how
    a mistyped token stays mistyped - the operator otherwise has no way to find
    out why a beer never appeared.
    """
    m = TAP_TOKEN_RE.search(_notes_text(batch))
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    if 1 <= n <= MAX_NUM_TAPS:
        return n
    log.warning(
        "ignoring out-of-range token 'tap:%d' on batch %r (valid range is 1-%d)",
        n, beer_name(batch), MAX_NUM_TAPS,
    )
    return None


def saturation(batch: dict[str, Any]) -> float | None:
    """Per-tap colour saturation from a `saturation:NN` batch-note token.

    NN is a percentage (``60`` -> ``0.6``) or a fraction (``0.6``); see
    parse_saturation. None when no token is present, so the display falls back
    to its default saturation.
    """
    m = SATURATION_TOKEN_RE.search(_notes_text(batch))
    if not m:
        return None
    return parse_saturation(m.group(1))


def color_override(batch: dict[str, Any]) -> str | None:
    """Exact colour from a `colour:#rrggbb` batch-note token (overrides EBC colour)."""
    m = COLOR_TOKEN_RE.search(_notes_text(batch))
    return parse_hex_color(m.group(1)) if m else None


def glass(batch: dict[str, Any]) -> str | None:
    """Glassware key from a `glass:nonicpint` token, or None for the global default."""
    m = GLASS_TOKEN_RE.search(_notes_text(batch))
    if not m:
        return None
    key = m.group(1).lower()
    return key if key in GLASS_KEYS else None


def is_upcoming(batch: dict[str, Any]) -> bool:
    """Whether a Batch carries the valueless `upcoming:` note token.

    Independent of `slot_claim`: a Batch can carry both `tap:X` and
    `upcoming:`, and the two acquisition paths for an Upcoming Beer (issue #4)
    decide for themselves which wins for a given Slot. This function only
    reports what the note says, not what it means for occupancy.
    """
    return bool(UPCOMING_TOKEN_RE.search(_notes_text(batch)))


# Statuses read as "not finished yet" for the recipe rule below: a Batch still
# in the tank has no trustworthy measured reading for anything, so a
# provisional Beer built from one of these reads its Attributes from the
# recipe instead. Completed and Conditioning are deliberately excluded - both
# describe a beer that exists, physically, at the reading it was measured at.
_UNFINISHED_STATUSES = frozenset({"fermenting", "brewing", "planning"})


def _recipe_ebc(recipe: dict[str, Any]) -> float | None:
    """Colour read from the recipe alone (no measured/estimated batch fields).

    Mirrors `ebc()`'s SRM handling for the recipe half only: `recipe.color` /
    `recipe.srm` are SRM despite the generic name and are converted, while a
    rare explicit `recipe.ebc` is taken at face value. There is no "measured"
    branch here - that is the whole point of the recipe-only rule.
    """
    srm = _first_number(recipe, "color", "srm")
    if srm is not None:
        return round(srm * EBC_PER_SRM, 1)
    rebc = _first_number(recipe, "ebc")
    return round(rebc, 1) if rebc is not None else None


def _recipe_attributes(batch: dict[str, Any]) -> dict[str, float | int | None]:
    """The five Attributes read from the recipe alone, all together.

    An unfinished Batch's own measured/estimated fields describe a beer that
    does not exist yet at its finished reading - a recipe ABV printed beside a
    half-fermented FG would describe a beer that never existed at all. So a
    provisional Beer takes colour, IBU, OG, FG and ABV from the recipe as one
    group; mixing a measured field in for just one of them is the bug this
    guards against. Any Attribute the recipe itself omits is still None - "all
    together" names where the values come from, not that every one is present.
    """
    recipe = batch.get("recipe") or {}
    return {
        "abv": _first_number(recipe, "abv"),
        "ibu": _first_number(recipe, "ibu"),
        "ebc": _recipe_ebc(recipe),
        "og": _first_gravity(recipe, "og"),
        "fg": _first_gravity(recipe, "fg"),
    }


def _measured_attributes(batch: dict[str, Any]) -> dict[str, float | int | None]:
    """The five Attributes as mapped today: measured/estimated first, recipe as fallback."""
    return {
        "abv": abv(batch),
        "ibu": ibu(batch),
        "ebc": ebc(batch),
        "og": og(batch),
        "fg": fg(batch),
    }


# ---- the whole Beer, and the cache question about it ---------------------

def beer(batch: dict[str, Any], *, provisional: bool = False) -> Beer:
    """The **Beer** one Batch maps to. Pure: no client, no disk.

    Everything a Beer is comes from the Batch alone, so this needs no arguments
    beyond it (plus the one flag below) and is deterministic - the same Batch
    always maps to an equal Beer, which is what lets a test assert against the
    value directly.

    `provisional` is False by default, which keeps today's measured-first
    behaviour for every status including Fermenting - a pouring Tap is never
    provisional, whatever its Batch's status, because a fermenting Batch with
    `tap:X` pours today when `include_fermenting` is on and
    `show_upcoming_previews` is off. Only a caller building an Upcoming Beer
    (issue #4, not yet wired up by this ticket) sets it True, and only then -
    and only for an unfinished status - does the recipe rule in
    `_recipe_attributes` replace the measured-first one. Making this
    unconditional would change the board with the Upcoming feature turned off,
    which breaks the toggle's contract that off means today's behaviour
    exactly.

    The Tap file also carries `source`, `image`, `updated` and `batch_status`,
    and none of them is here: they are facts about the file or about the Batch
    at write time rather than about the beverage, the store owns them, and
    nothing reads them back as truth (ADR-0003, and docs/adr/0005 for where the
    line falls).
    """
    if provisional and status_label(batch) in _UNFINISHED_STATUSES:
        attrs = _recipe_attributes(batch)
    else:
        attrs = _measured_attributes(batch)
    return Beer(
        name=beer_name(batch),
        abv=attrs["abv"],
        ibu=attrs["ibu"],
        ebc=attrs["ebc"],
        og=attrs["og"],
        fg=attrs["fg"],
        saturation=saturation(batch),
        color_override=color_override(batch),
        glass=glass(batch),
    )


def source_revision(batch: dict[str, Any], rev: int) -> SourceRevision:
    """The cache-coherence record stamped on this Batch's Tap file.

    Named apart from `revision` above, which answers a different question: that
    one reads the Batch's own recency value out of Brewfather's payload, this
    one builds the record the Tap file stores.

    `rev` is passed in rather than derived because it is not a fact about the
    Batch on its own - it is the recency value the sync already resolved a Slot
    conflict with. `MAPPING_VERSION` rides along so that a change to the
    extraction logic above invalidates every cached Tap exactly once.
    """
    return SourceRevision(
        batch_id=batch_id(batch),
        source_rev=rev,
        map_rev=MAPPING_VERSION,
    )


def is_current(cached: SourceRevision | None, batch: dict[str, Any], rev: int) -> bool:
    """True if a cached Tap already reflects this Batch at this revision.

    The *pure* half of the sync's freshness check: same Batch, same revision,
    same mapping version. The remaining half - whether the store actually holds
    the image this Batch wants - is a storage question and stays with the caller
    (see `wants_image`).

    `None` means the Tap carries no revision record at all, which is what a
    Manual Tap looks like: never current, because it is not a cache of anything.

    A mapping-logic change (a new MAPPING_VERSION) makes this False even when the
    Batch itself is untouched, which is what forces the one-time rewrite that
    lets cached files pick up new fields.
    """
    return cached is not None and cached.matches(source_revision(batch, rev))


def _pick_by_recency(claims: dict[int, dict[str, Any]], tap: int,
                      batch: dict[str, Any], *, kind: str) -> None:
    """Fold one more same-status claimant into `claims[tap]`, newest winning ties.

    A smaller sibling of `desired_map`'s conflict resolution: this only ever
    compares Batches of the *same* status against each other (Completed
    against Completed, Conditioning against Conditioning), because the
    cross-status precedence for issue #4's Occupancy pass is "Completed
    always beats Conditioning outright", not a recency comparison at all -
    see `resolve_occupancy`.
    """
    candidate = {"batch": batch, "updated_ms": revision(batch)}
    existing = claims.get(tap)
    if existing is None:
        claims[tap] = candidate
        return
    if candidate["updated_ms"] != existing["updated_ms"]:
        winner = candidate if candidate["updated_ms"] > existing["updated_ms"] else existing
        log.warning(
            "tap:%d %s conflict between '%s' and '%s'; keeping '%s' (more recent)",
            tap, kind, beer_name(candidate["batch"]), beer_name(existing["batch"]),
            beer_name(winner["batch"]),
        )
        claims[tap] = winner
    # Equal recency: keep whichever was already there (matches desired_map's
    # ">=" tie behaviour without a pointless log line for a genuine tie).


def resolve_occupancy(batches: list[dict[str, Any]],
                       manual_slots: Iterable[int] = ()) -> dict[int, dict[str, Any]]:
    """Slot -> the Batch occupying it, under issue #4's Occupancy pass.

    Only reachable meaning while `show_upcoming_previews` is on - with the
    feature off, `desired_map` alone still decides what pours, which is what
    keeps "off" meaning today's behaviour exactly. Two independently
    recency-tie-broken sub-passes:

    1. Completed Batches with `tap:X` claim their Slots first.
    2. Conditioning Batches then fill any Slot pass 1 left unclaimed, AND
       that is not in `manual_slots` - the deliberate exception that keeps a
       Conditioning beer physically on tap from being pushed into the
       Upcoming queue by this feature. A Slot a Completed Batch already
       claimed is not reopened for Conditioning to compete for; completeness
       beats recency outright, it does not merely go first in a tie-break.

    Fermenting, Brewing and Planning Batches never occupy a Slot, whatever
    `tap:X` they carry - that is the whole point of the feature: a Batch
    still in primary describes a beer that does not exist at a servable
    reading yet.

    `manual_slots` is plain data (Slot ints), not a live look at the Tap file
    store, so this function stays as pure and clientless as the rest of the
    module (the import-purity AST guard forbids importing tap_store here);
    the caller, which does own the store, computes it and passes it in.
    """
    manual = {int(s) for s in manual_slots}

    completed: dict[int, dict[str, Any]] = {}
    for batch in batches:
        if status_label(batch) != "completed":
            continue
        tap = slot_claim(batch)
        if tap is not None:
            _pick_by_recency(completed, tap, batch, kind="Completed occupancy")

    conditioning: dict[int, dict[str, Any]] = {}
    for batch in batches:
        if status_label(batch) != "conditioning":
            continue
        tap = slot_claim(batch)
        if tap is not None:
            _pick_by_recency(conditioning, tap, batch, kind="Conditioning occupancy")

    occupied = dict(completed)
    for tap, candidate in conditioning.items():
        if tap in occupied or tap in manual:
            continue
        occupied[tap] = candidate
    return occupied


def upcoming_beers(batches: list[dict[str, Any]],
                    occupied: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The Upcoming Beer set for one sync cycle - see CONTEXT.md, Upcoming Beer.

    Unordered on purpose: ordering and the display cap (`max_upcoming_previews`)
    are display-time per ADR-0006, not sync-time, so they are #37's job and do
    not belong here. Each entry is ``{"batch": ..., "slot": int | None}``.

    Two acquisition paths merge into one list:

    (a) A non-Completed Batch carrying `tap:X` that did not win its claimed
        Slot in `occupied` - bound to that Slot. Includes a Conditioning
        Batch that lost to a Completed claimant or to a Manual Tap, and any
        Fermenting/Brewing/Planning Batch (which never occupies at all).
    (b) A Batch carrying `upcoming:` and no `tap:X` - unbound, whatever its
        status, including Completed.

    `tap:X` beats `upcoming:` on a Batch carrying both: such a Batch is
    judged only by path (a) - whether it occupies, loses, or is excluded for
    having been Completed and lost - and never falls through to check
    `upcoming:` at all.

    **Completed `tap:X` losers are excluded outright**, from both paths: a
    beer that was pulled off a Slot by a fresher Completed Batch is not
    "coming up" just because it lost. **Two Upcoming Beers may bind to one
    Slot and both survive - there is no dedup here or anywhere else in this
    function.**
    """
    entries: list[dict[str, Any]] = []
    for batch in batches:
        tap = slot_claim(batch)
        if tap is not None:
            occupant = occupied.get(tap)
            if occupant is not None and str(batch_id(occupant["batch"])) == str(batch_id(batch)):
                continue  # this Batch IS the occupant: a Tap, not a teaser
            if status_label(batch) == "completed":
                continue  # a pulled beer is not coming up
            entries.append({"batch": batch, "slot": tap})
            continue
        if is_upcoming(batch):
            entries.append({"batch": batch, "slot": None})
    return entries


def desired_map(batches: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Map tap number -> chosen batch, resolving conflicts.

    The most COMPLETE Batch wins, and recency only breaks a tie within one
    status. A beer that is pouring should not be pushed off its Slot by the
    next brew that happens to carry the same `tap:X` token while it is still
    conditioning - and because a conditioning Batch is edited more often than a
    finished one, plain recency reliably picked the wrong beer once the
    Conditioning and Fermenting toggles made more than one status reachable.
    """
    desired: dict[int, dict[str, Any]] = {}
    for batch in batches:
        tap = slot_claim(batch)
        if tap is None:
            continue
        candidate = {
            "batch": batch,
            "updated_ms": revision(batch),
            "rank": status_rank(batch),
        }
        existing = desired.get(tap)
        if existing is None:
            desired[tap] = candidate
            continue
        # Status first, then recency. `>=` on the tie keeps the previous
        # newest-wins behaviour for two Batches sharing one status.
        if candidate["rank"] != existing["rank"]:
            winner = candidate if candidate["rank"] < existing["rank"] else existing
            reason = "more complete status"
        else:
            winner = (
                candidate
                if candidate["updated_ms"] >= existing["updated_ms"]
                else existing
            )
            reason = "more recent"
        log.warning(
            "tap:%d conflict between '%s' (%s) and '%s' (%s); keeping '%s' (%s)",
            tap,
            beer_name(candidate["batch"]),
            status_label(candidate["batch"]),
            beer_name(existing["batch"]),
            status_label(existing["batch"]),
            beer_name(winner["batch"]),
            reason,
        )
        desired[tap] = winner
    return desired
