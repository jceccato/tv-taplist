"""Brewfather API integration and the periodic sync job.

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

Auth: HTTP Basic Auth, username = User ID, password = API key (env vars
BREWFATHER_USER_ID / BREWFATHER_API_KEY take precedence over config.json).

Efficient fetch (rate limit is 500 calls/hour per key):
  GET /v2/batches?status=Completed&complete=True&limit=50  returns FULL batch
  objects in one call, paginated with `start_after`. This avoids the old
  N+1 (one detail call per batch) pattern, which would blow the hourly limit as
  Completed batches accumulate. Per sync we now make ceil(N/50) calls, and
  change-detection skips image downloads / file rewrites for unchanged batches.

Tap assignment: parse the batch notes text for a `tap:X` token. A token is
accepted from 1 to MAX_NUM_TAPS - a system bound, not the operator's tap count -
and an out-of-range one is logged with the batch named.

Desired-tap-map / archive: after a successful sync, any Brewfather-managed tap
whose batch no longer maps to it is archived. Brewfather's claim is the *only*
thing that drives a write or an archive here - not the operator's tap count, and
not whether a Manual override is sitting on the Slot. A Brewfather Tap under an
override is kept warm: nothing displays it while the override stands, but
clearing the override reveals a current Beer instead of a Vacant Slot. Manual
Taps are never read, written, or archived (see SYNC_SOURCE). A failed sync makes
NO destructive changes.

Storage: this module never spells a Tap filename. Every read, write, image save
and enumeration goes through the Tap file store addressed by Slot and Source
(see SYNC_SOURCE below, and app/tap_store.py).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from . import tap_store as taps
from .archive import archive_tap
from .atomic import JOB_LOCK
from .beer_glass import GLASS_KEYS
from .colors import EBC_PER_SRM, parse_hex_color, parse_saturation
from .config_store import (
    MAX_NUM_TAPS,
    brewfather_credentials,
    load_config,
)
from .paths import ensure_dirs
from .status_store import update_status
from .timezone import iso_now

log = logging.getLogger("taplist.sync")

# The one Source this module is ever allowed to address. Passing it to every
# store call is what makes "sync never touches a Manual Tap" *structural* rather
# than remembered: the store derives the filename from the Source, so there is
# no spelling of a call in here - not a typo, not a copy-paste - that could name
# a custom_tap_X file. Every read, write, image save, enumeration and archive
# below passes SYNC_SOURCE, and sync no longer asks about the Manual Source at
# all - so the rule holds without a single guard to forget.
SYNC_SOURCE = taps.Source.BREWFATHER


def _record_status(**changes: Any) -> None:
    """Persist the sync Status fields. Never raises; never touches Settings.

    Status lives in its own file now, so recording it cannot disturb
    config.json or the Brewfather key inside it. `update_status` is already
    tolerant of an unreadable status.json (the data is disposable), so the only
    thing left to swallow here is a genuine write failure - a full or read-only
    /data. Sync must not fail on account of its own bookkeeping.
    """
    try:
        update_status(**changes)
    except OSError as exc:
        log.warning("could not record sync status (%s)", exc)

API_BASE = "https://api.brewfather.app/v2"
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# Brewfather caps page size at 50; we paginate with start_after.
PAGE_SIZE = 50
MAX_PAGES = 50  # safety cap: 50 pages x 50 = 2500 completed batches

# Bumped whenever the field-extraction logic below changes in a way that should
# refresh already-cached bf_tap files. `_is_unchanged` treats a stored map_rev
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

CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


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


def _extract_name(batch: dict[str, Any]) -> str:
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


def _extract_abv(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    # Prefer measured over estimated/recipe so the board shows reality.
    return _first_number(batch, "measuredAbv", "abv") or _first_number(recipe, "abv")


def _extract_ibu(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return (
        _first_number(batch, "measuredIbu", "estimatedIbu", "ibu")
        or _first_number(recipe, "ibu")
    )


def _extract_ebc(batch: dict[str, Any]) -> float | None:
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
    ebc = _first_number(batch, "measuredEbc")
    if ebc is not None:
        return round(ebc, 1)
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


def _extract_og(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return _first_gravity(batch, "measuredOg", "og") or _first_gravity(recipe, "og")


def _extract_fg(batch: dict[str, Any]) -> float | None:
    recipe = batch.get("recipe") or {}
    return _first_gravity(batch, "measuredFg", "fg") or _first_gravity(recipe, "fg")


def _extract_notes_text(batch: dict[str, Any]) -> str:
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


def _extract_style(batch: dict[str, Any]) -> str:
    """Recipe style name (e.g. "English Porter"), used as a description fallback."""
    style = (batch.get("recipe") or {}).get("style")
    if isinstance(style, dict):
        return (style.get("name") or "").strip()
    if isinstance(style, str):
        return style.strip()
    return ""


def _extract_description(batch: dict[str, Any]) -> str:
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
    return _extract_style(batch)


def _extract_image_url(batch: dict[str, Any]) -> str | None:
    recipe = batch.get("recipe") or {}
    for src in (batch, recipe):
        for key in ("img_url", "imgUrl", "image", "imageUrl"):
            val = src.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def _extract_updated_ms(batch: dict[str, Any]) -> int:
    """A sortable recency value for conflict resolution (newest wins)."""
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


def _status_rank(batch: dict[str, Any]) -> int:
    """Sort key for Batch status - LOWER is more complete.

    An unrecognised or missing status ranks last, which deliberately makes a
    Batch the API did not label lose to one it did. It also means that if
    Brewfather ever stops sending `status`, every Batch ranks the same and
    conflict resolution falls back to recency - exactly the behaviour that
    shipped before this rule existed.
    """
    try:
        return STATUS_PRECEDENCE.index(_status_label(batch))
    except ValueError:
        return len(STATUS_PRECEDENCE)


def _status_label(batch: dict[str, Any]) -> str:
    """The Batch status, normalised for comparison and for the conflict log.

    Missing, empty, and non-string statuses all collapse to "unknown" so the
    logged conflict line never prints a bare '' or 'None' at the operator.
    """
    raw = batch.get("status")
    label = str(raw).strip().lower() if isinstance(raw, str) else ""
    return label or "unknown"


def _find_tap_number(batch: dict[str, Any]) -> int | None:
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
    m = TAP_TOKEN_RE.search(_extract_notes_text(batch))
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
        n, _extract_name(batch), MAX_NUM_TAPS,
    )
    return None


def _extract_saturation(batch: dict[str, Any]) -> float | None:
    """Per-tap colour saturation from a `saturation:NN` batch-note token.

    NN is a percentage (``60`` -> ``0.6``) or a fraction (``0.6``); see
    parse_saturation. None when no token is present, so the display falls back
    to its default saturation.
    """
    m = SATURATION_TOKEN_RE.search(_extract_notes_text(batch))
    if not m:
        return None
    return parse_saturation(m.group(1))


def _extract_color_override(batch: dict[str, Any]) -> str | None:
    """Exact colour from a `colour:#rrggbb` batch-note token (overrides EBC colour)."""
    m = COLOR_TOKEN_RE.search(_extract_notes_text(batch))
    return parse_hex_color(m.group(1)) if m else None


def _extract_glass(batch: dict[str, Any]) -> str | None:
    """Glassware key from a `glass:nonicpint` token, or None for the global default."""
    m = GLASS_TOKEN_RE.search(_extract_notes_text(batch))
    if not m:
        return None
    key = m.group(1).lower()
    return key if key in GLASS_KEYS else None


# ---- HTTP --------------------------------------------------------------

def _client(user_id: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        auth=(user_id, api_key),
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "tv-taplist/1.0"},
    )


def _image_client() -> httpx.Client:
    """A SEPARATE, UNAUTHENTICATED client for downloading batch images.

    Batch image URLs are absolute and off-host - Brewfather serves them from
    Google Firebase storage / a CDN, not from api.brewfather.app. httpx applies a
    client's ``auth`` to EVERY request it makes, with no host scoping, so reusing
    the Brewfather-authenticated ``_client`` here would transmit the HTTP Basic
    Auth header (User ID + API key) to those third-party hosts on the very first
    request. This client carries no credentials, so an image fetch can never leak
    the Brewfather key regardless of where the URL (or a redirect) points.
    """
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "tv-taplist/1.0"},
        follow_redirects=True,
    )


def _list_batches(client: httpx.Client, statuses: list[str]) -> list[dict[str, Any]]:
    """Return FULL batch objects for the wanted statuses, paginated + deduped.

    The Brewfather ``/batches`` ``status`` param takes a SINGLE status, so we fetch
    once per wanted status and merge, deduping by ``_id`` (a batch is only ever in
    one status, but the dedupe stays safe if the API ever returns one twice).
    ``complete=True`` means each page carries all the data we map
    (ABV/IBU/colour/notes/image), so there are no per-batch detail calls. Cost is
    ceil(N/50) calls **per status** - still far under the 500/hour key limit.
    """
    wanted = {str(s).lower() for s in statuses}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for status_name in statuses:
        start_after: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "status": status_name,
                "complete": "True",
                "limit": PAGE_SIZE,
            }
            if start_after:
                params["start_after"] = start_after
            resp = client.get("/batches", params=params)
            resp.raise_for_status()
            page = resp.json()
            if not isinstance(page, list) or not page:
                break
            for b in page:
                # Defensive: re-filter in case the server ignores the status param.
                if str(b.get("status", "")).lower() not in wanted:
                    continue
                bid = b.get("_id") or b.get("id")
                key = str(bid) if bid else None
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                out.append(b)
            if len(page) < PAGE_SIZE:
                break  # last page
            last_id = page[-1].get("_id") or page[-1].get("id")
            if not last_id:
                break
            start_after = str(last_id)
    return out


def _download_image(img_client: httpx.Client, url: str) -> tuple[bytes, str] | None:
    """Fetch a tap image. Returns (bytes, extension), or None if it failed.

    This is a pure HTTP concern: it downloads and works out what the bytes are,
    and hands both to the caller for `tap_store.save_image` to file. It does not
    know which Slot the image belongs to, does not build a filename, and does
    not touch the disk - so the sweep of a stale image with a different
    extension happens once, inside the store, on every write path rather than
    just this one.

    ``img_client`` MUST be the unauthenticated image client (see `_image_client`)
    so the Brewfather credentials are never sent to the third-party image host.
    A failed download returns None and must NOT delete an already-good cached
    image (the caller keeps the existing one).
    """
    try:
        resp = img_client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        log.warning("image download failed for %s: %s", url, exc)
        return None

    # Prefer the URL's own extension; fall back to content-type.
    ext = None
    url_path = url.split("?", 1)[0].lower()
    for known in taps.IMAGE_EXTS:
        if url_path.endswith(known):
            ext = known
            break
    if ext is None:
        ext = CONTENT_TYPE_EXT.get(resp.headers.get("content-type", "").split(";")[0].strip(), ".jpg")

    # The store normalises .jpeg -> .jpg and rejects anything it cannot store.
    return resp.content, ext


# ---- sync orchestration --------------------------------------------------

def _build_desired_map(batches: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
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
        tap = _find_tap_number(batch)
        if tap is None:
            continue
        candidate = {
            "batch": batch,
            "updated_ms": _extract_updated_ms(batch),
            "rank": _status_rank(batch),
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
            _extract_name(candidate["batch"]),
            _status_label(candidate["batch"]),
            _extract_name(existing["batch"]),
            _status_label(existing["batch"]),
            _extract_name(winner["batch"]),
            reason,
        )
        desired[tap] = winner
    return desired


def _write_bf_tap(img_client: httpx.Client, tap: int, batch: dict[str, Any], rev: int) -> None:
    """Write bf_tap_X.md (+ image) for one desired tap, recording the batch rev.

    ``img_client`` is the unauthenticated image client used for the (off-host)
    image download; the beer fields are already present in the batch object.
    """
    ebc = _extract_ebc(batch)
    image_url = _extract_image_url(batch)

    image_name: str | None = None
    if image_url:
        downloaded = _download_image(img_client, image_url)
        if downloaded is not None:
            data, ext = downloaded
            image_name = taps.save_image(tap, SYNC_SOURCE, data, ext)
    if image_name is None:
        # Keep any previously cached image; otherwise leave null (placeholder).
        existing = taps.image_for(tap, SYNC_SOURCE)
        image_name = existing.name if existing else None

    front_matter = {
        "name": _extract_name(batch),
        "abv": _extract_abv(batch),
        "ibu": _extract_ibu(batch),
        "ebc": ebc,
        "og": _extract_og(batch),
        "fg": _extract_fg(batch),
        "saturation": _extract_saturation(batch),
        "color_override": _extract_color_override(batch),
        "glass": _extract_glass(batch),
        "source": "brewfather",
        "batch_id": batch.get("_id") or batch.get("id"),
        "source_rev": rev,            # batch revision, used to skip unchanged syncs
        "map_rev": MAPPING_VERSION,   # extraction-logic version (forces one refresh)
        "image": image_name,
        "updated": iso_now(),
    }
    taps.write(tap, SYNC_SOURCE, front_matter, _extract_description(batch))
    log.info("wrote tap %d (%s) (name=%r image=%s)",
             tap, SYNC_SOURCE, front_matter["name"], image_name)


def _is_unchanged(tap: int, batch: dict[str, Any], rev: int) -> bool:
    """True if bf_tap_X already reflects this batch at this revision.

    Lets the sync skip a re-write (and image re-download) when nothing changed,
    keeping API/bandwidth use minimal and avoiding needless display churn.
    """
    cached = taps.read(tap, SYNC_SOURCE)
    if cached is None:
        return False
    existing = cached.front_matter
    bid = batch.get("_id") or batch.get("id")
    same_batch = str(existing.get("batch_id")) == str(bid)
    same_rev = str(existing.get("source_rev")) == str(rev)
    # A mapping-logic change (new MAPPING_VERSION) forces a one-time rewrite even
    # when the batch itself is unchanged, so cached files pick up the new fields.
    same_map = str(existing.get("map_rev")) == str(MAPPING_VERSION)
    has_image_if_needed = (not _extract_image_url(batch)) or (cached.image is not None)
    return same_batch and same_rev and same_map and has_image_if_needed


def run_sync() -> dict[str, Any]:
    """Execute one full sync. Returns a small status dict. Never raises."""
    ensure_dirs()
    creds = brewfather_credentials()
    user_id, api_key = creds["user_id"], creds["api_key"]
    cfg = load_config()
    # The configured tap count is deliberately NOT read here: it is a display
    # setting, and sync depends only on what Brewfather claims. See
    # `_find_tap_number` and `_archive_undesired`.
    # Statuses to pull: always Completed, plus Conditioning when the operator
    # opts in (a beer on tap but still lagering / too green to mark Completed),
    # plus Fermenting for an upcoming Beer still in primary. The two opt-ins are
    # independent, so all four combinations are valid.
    #
    # Each extra status costs another sweep: `_list_batches` pages the API once
    # per status, ceil(N/50) calls each, against a 500/hour key limit. Three
    # statuses on a normal sync interval stay comfortably inside it, but this is
    # the reason the status list is opt-in rather than "fetch everything".
    #
    # The status decides which Batches are FETCHED, and (in `_build_desired_map`)
    # which one wins a Slot two Batches both claim. It does not change EXTRACTION:
    # a Fermenting Batch still needs a `tap:X` note token to claim a Slot, and
    # maps to a Tap field-for-field like any other Batch. That is why
    # MAPPING_VERSION is deliberately NOT bumped for any of this - selection
    # changed, the mapping did not, so cached bf_tap files need no rewrite.
    statuses = ["Completed"]
    if bool(cfg.get("include_conditioning", False)):
        statuses.append("Conditioning")
    if bool(cfg.get("include_fermenting", False)):
        statuses.append("Fermenting")

    if not user_id or not api_key:
        msg = "sync skipped: Brewfather credentials not configured"
        log.info(msg)
        _record_status(last_sync_attempt=iso_now())
        return {"ok": False, "skipped": True, "message": msg}

    # Serialise against cleanup and admin writes for the whole job.
    with JOB_LOCK:
        log.info("sync starting (credentials from %s)",
                 "env" if creds["key_from_env"] else "config")
        try:
            # Two clients: the authenticated API client, and a separate
            # credential-free client for the off-host image downloads so the
            # Brewfather key is never sent to a third-party image host.
            with _client(user_id, api_key) as client, _image_client() as img_client:
                batches = _list_batches(client, statuses)
                log.info("fetched %d batches (statuses=%s)", len(batches), statuses)

                desired = _build_desired_map(batches)
                log.info("desired Brewfather tap map: %s", sorted(desired.keys()))

                written = 0
                unchanged = 0
                for tap, entry in desired.items():
                    # A Manual override on this Slot is deliberately NOT checked
                    # here. The Brewfather Tap is kept warm underneath it: the
                    # display never shows it while the override stands (resolve
                    # picks Manual first), but the instant the operator clears
                    # the override the Slot is current rather than Vacant for up
                    # to a whole sync interval. The cost is one image download
                    # and one file write per cycle for a beer nobody can see;
                    # the batch list fetch is unchanged, so the API rate limit is
                    # unaffected. The write can only ever address SYNC_SOURCE,
                    # so the Manual file is still untouchable from here.
                    rev = entry["updated_ms"]
                    if _is_unchanged(tap, entry["batch"], rev):
                        unchanged += 1
                        continue
                    _write_bf_tap(img_client, tap, entry["batch"], rev)
                    written += 1

                # Archive any existing bf_tap no Batch claims any more.
                archived = _archive_undesired(desired)

        except httpx.HTTPStatusError as exc:
            # Auth / API / rate-limit errors: make NO destructive changes.
            sc = exc.response.status_code
            if sc == 429:
                retry = exc.response.headers.get("Retry-After", "?")
                err = f"Brewfather rate limit hit (429); retry after {retry}s"
            else:
                err = f"Brewfather API error {sc}: {exc.response.text[:200]}"
            log.error("sync failed (no changes made): %s", err)
            _record_status(last_sync_error=err, last_sync_attempt=iso_now())
            return {"ok": False, "message": err}
        except (httpx.HTTPError, OSError) as exc:
            err = f"network/IO error during sync: {exc}"
            log.error("sync failed (no changes made): %s", err)
            _record_status(last_sync_error=err, last_sync_attempt=iso_now())
            return {"ok": False, "message": err}

        ts = iso_now()
        _record_status(last_sync_success=ts, last_sync_error=None, last_sync_attempt=ts)
        log.info(
            "sync finished: %d written, %d unchanged, %d archived",
            written, unchanged, archived,
        )
        return {
            "ok": True,
            "written": written,
            "unchanged": unchanged,
            "archived": archived,
            "timestamp": ts,
        }


def _archive_undesired(desired: dict[int, Any]) -> int:
    """Archive Brewfather Taps that no Batch claims any more.

    One condition, and deliberately only one: no Batch carries this Slot's
    `tap:` token. Two others used to sit here and both were wrong.

    * The Manual-occupancy skip is gone. Archiving no longer consults override
      state at all - a Brewfather Tap under an override is kept current, not set
      aside, so clearing the override reveals a live Beer. (A Manual Tap is
      still never archived from here: every call below passes SYNC_SOURCE.)
    * The tap-count filter is gone. It made a presentation choice destroy Beer
      data: lowering the tap count archived every Brewfather Tap above the new
      number, and raising it back did not bring them back.

    The store's enumeration stays deliberately unbounded by the configured tap
    count, which is what this scan needs: a Brewfather file stranded at a Slot
    above the tap count is exactly the orphan to find here once no Batch claims
    it. Do not bound it.
    """
    archived = 0
    for tap in taps.occupied_slots(SYNC_SOURCE):
        if tap in desired:
            continue  # still claimed by a Batch
        if archive_tap(tap, SYNC_SOURCE):
            archived += 1
    return archived
