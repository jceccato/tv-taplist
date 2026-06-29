"""Brewfather API integration and the periodic sync job.

Field mapping (verified against a live /v2/batches?complete=True payload):
  name        <- recipe.name      (a batch's own name is Brewfather's generic
                                    "Batch" / "Batch #12" default)
  abv / ibu   <- measured* first, then recipe.*; 0 is treated as "not provided"
                 (Brewfather returns 0, not null, for unset values)
  colour      <- measuredEbc (EBC); else estimatedColor / color, which are SRM
                 (verified via style colour bounds) -> converted to EBC
  description <- tasteNotes, else the recipe style name. The batch notes are NOT
                 used for the body — they only carry the tap:X control token.

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

Tap assignment: parse the batch notes text for a `tap:X` token.

Desired-tap-map / archive: after a successful sync, any Brewfather-managed tap
whose batch no longer maps to it is archived. Manual overrides are never read,
written, or archived. A failed sync makes NO destructive changes.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from . import markdown_store as md
from .archive import archive_tap
from .atomic import JOB_LOCK, atomic_write_bytes
from .colors import EBC_PER_SRM
from .config_store import (
    ConfigUnreadable,
    brewfather_credentials,
    load_config,
    update_config,
)
from .paths import TAPS_DIR, ensure_dirs
from .timezone import iso_now

log = logging.getLogger("taplist.sync")


def _record_status(**changes: Any) -> None:
    """Persist sync-status fields, tolerating a transiently unreadable config.

    Sync status is non-critical, so if config.json can't be read right now we
    skip the update rather than let it bubble up (or risk clobbering settings).
    """
    try:
        update_config(**changes)
    except ConfigUnreadable as exc:
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
MAPPING_VERSION = 4

# `tap:3`, `tap: 3`, `Tap:3`, etc.
TAP_TOKEN_RE = re.compile(r"tap\s*:\s*(\d+)", re.IGNORECASE)

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
    Brewfather exposes for colour — estimatedColor, color, recipe.color — is in
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
    """Strip the `tap:X` control token and tidy whitespace in notes text."""
    cleaned = TAP_TOKEN_RE.sub(" ", text)
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
    no tasting notes). The batch notes are deliberately NOT used for the body —
    they hold the `tap:X` control token, not display text — and any such token is
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


def _find_tap_number(batch: dict[str, Any]) -> int | None:
    m = TAP_TOKEN_RE.search(_extract_notes_text(batch))
    if not m:
        return None
    try:
        n = int(m.group(1))
        return n if n >= 1 else None
    except ValueError:
        return None


# ---- HTTP --------------------------------------------------------------

def _client(user_id: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        auth=(user_id, api_key),
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "tv-taplist/1.0"},
    )


def _list_completed_batches(client: httpx.Client) -> list[dict[str, Any]]:
    """Return FULL Completed batch objects, paginated with start_after.

    Uses complete=True so a single call per page carries all the data we map
    (ABV/IBU/colour/notes/image), eliminating per-batch detail calls.
    """
    out: list[dict[str, Any]] = []
    start_after: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {
            "status": "Completed",
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
        # Defensive: re-filter in case the server ignores the status param.
        out.extend(b for b in page if str(b.get("status", "")).lower() == "completed")
        if len(page) < PAGE_SIZE:
            break  # last page
        last_id = page[-1].get("_id") or page[-1].get("id")
        if not last_id:
            break
        start_after = str(last_id)
    return out


def _download_image(client: httpx.Client, url: str, stem: str) -> str | None:
    """Download a tap image, preserving the source extension. Returns filename.

    A failed download returns None and must NOT delete an already-good cached
    image (caller keeps the existing one).
    """
    try:
        resp = client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        log.warning("image download failed for %s (%s): %s", stem, url, exc)
        return None

    # Prefer the URL's own extension; fall back to content-type.
    ext = None
    url_path = url.split("?", 1)[0].lower()
    for known in md.IMAGE_EXTS:
        if url_path.endswith(known):
            ext = ".jpg" if known == ".jpeg" else known
            break
    if ext is None:
        ext = CONTENT_TYPE_EXT.get(resp.headers.get("content-type", "").split(";")[0].strip(), ".jpg")

    # Remove any previously cached image of a *different* extension for this stem
    # so we don't end up with bf_tap_5.jpg AND bf_tap_5.webp.
    for old in md.IMAGE_EXTS:
        old_path = TAPS_DIR / f"{stem}{old}"
        if old_path.exists() and old_path.suffix != ext:
            from .atomic import safe_unlink
            safe_unlink(old_path)

    dest = TAPS_DIR / f"{stem}{ext}"
    atomic_write_bytes(dest, resp.content)
    return dest.name


# ---- sync orchestration --------------------------------------------------

def _build_desired_map(batches: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Map tap number -> chosen batch, resolving conflicts (newest wins)."""
    desired: dict[int, dict[str, Any]] = {}
    for batch in batches:
        tap = _find_tap_number(batch)
        if tap is None:
            continue
        candidate = {"batch": batch, "updated_ms": _extract_updated_ms(batch)}
        existing = desired.get(tap)
        if existing is None:
            desired[tap] = candidate
            continue
        # Conflict: two Completed batches claim the same tap. Newest wins.
        winner, loser = (
            (candidate, existing)
            if candidate["updated_ms"] >= existing["updated_ms"]
            else (existing, candidate)
        )
        log.warning(
            "tap:%d conflict between '%s' and '%s'; keeping more recent '%s'",
            tap,
            _extract_name(candidate["batch"]),
            _extract_name(existing["batch"]),
            _extract_name(winner["batch"]),
        )
        desired[tap] = winner
    return desired


def _write_bf_tap(client: httpx.Client, tap: int, batch: dict[str, Any], rev: int) -> None:
    """Write bf_tap_X.md (+ image) for one desired tap, recording the batch rev."""
    stem = f"bf_tap_{tap}"
    ebc = _extract_ebc(batch)
    image_url = _extract_image_url(batch)

    image_name: str | None = None
    if image_url:
        image_name = _download_image(client, image_url, stem)
    if image_name is None:
        # Keep any previously cached image; otherwise leave null (placeholder).
        existing = md.find_image_for(stem)
        image_name = existing.name if existing else None

    front_matter = {
        "name": _extract_name(batch),
        "abv": _extract_abv(batch),
        "ibu": _extract_ibu(batch),
        "ebc": ebc,
        "source": "brewfather",
        "batch_id": batch.get("_id") or batch.get("id"),
        "source_rev": rev,            # batch revision, used to skip unchanged syncs
        "map_rev": MAPPING_VERSION,   # extraction-logic version (forces one refresh)
        "image": image_name,
        "updated": iso_now(),
    }
    md.write_tap_file(md.bf_md_path(tap), front_matter, _extract_description(batch))
    log.info("wrote %s (name=%r tap=%d image=%s)", stem, front_matter["name"], tap, image_name)


def _is_unchanged(tap: int, batch: dict[str, Any], rev: int) -> bool:
    """True if bf_tap_X already reflects this batch at this revision.

    Lets the sync skip a re-write (and image re-download) when nothing changed,
    keeping API/bandwidth use minimal and avoiding needless display churn.
    """
    existing = md.read_tap_file(md.bf_md_path(tap))
    if not existing:
        return False
    bid = batch.get("_id") or batch.get("id")
    same_batch = str(existing.get("batch_id")) == str(bid)
    same_rev = str(existing.get("source_rev")) == str(rev)
    # A mapping-logic change (new MAPPING_VERSION) forces a one-time rewrite even
    # when the batch itself is unchanged, so cached files pick up the new fields.
    same_map = str(existing.get("map_rev")) == str(MAPPING_VERSION)
    has_image_if_needed = (not _extract_image_url(batch)) or (md.find_image_for(f"bf_tap_{tap}") is not None)
    return same_batch and same_rev and same_map and has_image_if_needed


def run_sync() -> dict[str, Any]:
    """Execute one full sync. Returns a small status dict. Never raises."""
    ensure_dirs()
    creds = brewfather_credentials()
    user_id, api_key = creds["user_id"], creds["api_key"]
    num_taps = int(load_config().get("num_taps", 0) or 0)

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
            with _client(user_id, api_key) as client:
                batches = _list_completed_batches(client)
                log.info("fetched %d completed batches", len(batches))

                desired = _build_desired_map(batches)
                # Only manage taps within the configured count.
                desired = {t: v for t, v in desired.items() if 1 <= t <= num_taps}
                log.info("desired Brewfather tap map: %s", sorted(desired.keys()))

                written = 0
                unchanged = 0
                skipped_overrides = 0
                for tap, entry in desired.items():
                    if md.is_manual_override(tap):
                        # Never read/write/archive a manual override.
                        skipped_overrides += 1
                        continue
                    rev = entry["updated_ms"]
                    if _is_unchanged(tap, entry["batch"], rev):
                        unchanged += 1
                        continue
                    _write_bf_tap(client, tap, entry["batch"], rev)
                    written += 1

                # Archive any existing bf_tap that is no longer desired and is
                # not a manual override.
                archived = _archive_undesired(desired, num_taps)

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
            "sync finished: %d written, %d unchanged, %d archived, %d override slots skipped",
            written, unchanged, archived, skipped_overrides,
        )
        return {
            "ok": True,
            "written": written,
            "unchanged": unchanged,
            "archived": archived,
            "skipped_overrides": skipped_overrides,
            "timestamp": ts,
        }


def _archive_undesired(desired: dict[int, Any], num_taps: int) -> int:
    """Archive bf_tap_X files whose batch no longer maps to tap X."""
    archived = 0
    # Look at every existing bf_tap_*.md, not just 1..num_taps, so shrinking the
    # tap count also retires orphaned files.
    for path in TAPS_DIR.glob("bf_tap_*.md"):
        m = re.match(r"bf_tap_(\d+)\.md$", path.name)
        if not m:
            continue
        tap = int(m.group(1))
        if md.is_manual_override(tap):
            continue  # never touch a manual override slot
        if tap in desired:
            continue  # still wanted
        if archive_tap(f"bf_tap_{tap}"):
            archived += 1
    return archived
