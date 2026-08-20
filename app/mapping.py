"""Mapping: turn a Brewfather **Batch** into the fields of a **Beer**.

This module is named for the glossary term (see CONTEXT.md, *Mapping*). The
short name is for readability, not a claim of generality: everything here maps
Brewfather's Batch entity specifically - its field names, its note tokens, its
lifecycle statuses. A second Source would bring its own mapping, not reuse this
one.

**Nothing here performs I/O.** No HTTP client, no filesystem, no Tap file store,
no archive. Every function takes plain data - a Batch dict, a cached front-matter
dict - and returns plain data, so "what Beer does this Batch map to?" is a
question that can be asked and answered without a network client. That is the
whole point of the split (issue #10); keep it that way. The one import that
reaches outside the pure layer is `MAX_NUM_TAPS`, a constant, deliberately left
where the operator-facing Settings schema keeps it.

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
  description <- tasteNotes, else the recipe style name. The batch notes are NOT
                 used for the body - they only carry the control tokens
                 (tap:X / saturation:NN / colour:#hex / glass:type), which are all
                 stripped from any text we do show.

The helpers still try several field-name/unit variants defensively and log what
they found. Bump MAPPING_VERSION when changing the mapping so already-cached
files are refreshed on the next sync.

Tap assignment: parse the batch notes text for a `tap:X` token. A token is
accepted from 1 to MAX_NUM_TAPS - a system bound, not the operator's tap count -
and an out-of-range one is logged with the batch named.
"""
from __future__ import annotations

import logging
import re
from typing import Any

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
MAPPING_VERSION = 6

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
    """Strip the control tokens (tap / saturation / colour / glass) and tidy whitespace."""
    cleaned = TAP_TOKEN_RE.sub(" ", text)
    cleaned = SATURATION_TOKEN_RE.sub(" ", cleaned)
    cleaned = COLOR_TOKEN_RE.sub(" ", cleaned)
    cleaned = GLASS_TOKEN_RE.sub(" ", cleaned)
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


# ---- the whole Beer, and the cache question about it ---------------------

def front_matter(
    batch: dict[str, Any], *, rev: int, image: str | None, updated: str,
) -> dict[str, Any]:
    """The Tap file front matter one Batch maps to. Pure: no client, no disk.

    `rev`, `image` and `updated` are supplied by the caller rather than derived
    here because none of them is a fact about the Batch: `rev` is the recency
    value the sync already resolved a Slot conflict with, `image` is whatever the
    Tap file store ended up holding after the (possibly failed) download, and
    `updated` is the moment of the write. Passing them in keeps this function
    deterministic - the same Batch and the same three arguments always produce
    the same dict - so it can be asserted against directly in a test.

    The `source` key is written for a human reading the file in a text editor.
    Nothing reads it back as truth: the filename decides the Source (ADR-0003).
    """
    return {
        "name": beer_name(batch),
        "abv": abv(batch),
        "ibu": ibu(batch),
        "ebc": ebc(batch),
        "og": og(batch),
        "fg": fg(batch),
        "saturation": saturation(batch),
        "color_override": color_override(batch),
        "glass": glass(batch),
        "source": "brewfather",
        "batch_id": batch_id(batch),
        "source_rev": rev,            # batch revision, used to skip unchanged syncs
        "map_rev": MAPPING_VERSION,   # extraction-logic version (forces one refresh)
        "image": image,
        "updated": updated,
    }


def is_current(cached: dict[str, Any], batch: dict[str, Any], rev: int) -> bool:
    """True if cached front matter already reflects this Batch at this revision.

    The *pure* half of the sync's freshness check: same Batch, same revision,
    same mapping version. The remaining half - whether the store actually holds
    the image this Batch wants - is a storage question and stays with the caller
    (see `wants_image`).

    Comparisons are made on `str()` of both sides because the cached values come
    back from YAML, which may have parsed a numeric id or revision into an int.

    A mapping-logic change (a new MAPPING_VERSION) makes this False even when the
    Batch itself is untouched, which is what forces the one-time rewrite that
    lets cached files pick up new fields.
    """
    same_batch = str(cached.get("batch_id")) == str(batch_id(batch))
    same_rev = str(cached.get("source_rev")) == str(rev)
    same_map = str(cached.get("map_rev")) == str(MAPPING_VERSION)
    return same_batch and same_rev and same_map


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
