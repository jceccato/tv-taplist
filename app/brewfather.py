"""The Brewfather Source: fetch Batches from the API, and the periodic sync job.

This module owns the parts that are **not** pure - the API constants, the two
HTTP clients, the paginated batch listing, the image download - plus the sync
orchestration that ties fetching to the Tap file store. The transformation from
a Batch to a Beer lives next door in `app/mapping.py` and imports nothing that
opens a socket or touches disk, so "what Beer does this Batch map to?" can be
asked without a network client (issue #10). Fetch hands over Batches; Mapping
turns them into Beers; this module coordinates the two and files the result.

Auth: HTTP Basic Auth, username = User ID, password = API key (env vars
BREWFATHER_USER_ID / BREWFATHER_API_KEY take precedence over config.json).

Efficient fetch (rate limit is 500 calls/hour per key):
  GET /v2/batches?status=Completed&complete=True&limit=50  returns FULL batch
  objects in one call, paginated with `start_after`. This avoids the old
  N+1 (one detail call per batch) pattern, which would blow the hourly limit as
  Completed batches accumulate. Per sync we now make ceil(N/50) calls, and
  change-detection skips image downloads / file rewrites for unchanged batches.

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
from typing import Any

import httpx

from . import mapping
from . import tap_store as taps
from .archive import archive_tap
from .atomic import JOB_LOCK
from .config_store import (
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

# Used only by `_download_image`, to name bytes whose URL carries no extension.
# It sits on the fetch side with its one consumer rather than among the Mapping
# constants, where it used to be filed and read as though it were part of the
# Batch-to-Beer transformation.
CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


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

def _store_image(img_client: httpx.Client, tap: int, batch: dict[str, Any]) -> str | None:
    """Download this Batch's photo and file it; return the stored image name.

    The fetch-and-store half of writing a Tap, kept apart from deciding WHAT to
    write (that is `mapping.beer`, which needs no client). A failed
    download is not an erasure: the previously cached image is kept, and only a
    Slot that never had one ends up with None (the placeholder).
    """
    url = mapping.image_url(batch)
    image_name: str | None = None
    if url:
        downloaded = _download_image(img_client, url)
        if downloaded is not None:
            data, ext = downloaded
            image_name = taps.save_image(tap, SYNC_SOURCE, data, ext)
    if image_name is None:
        # Keep any previously cached image; otherwise leave null (placeholder).
        existing = taps.image_for(tap, SYNC_SOURCE)
        image_name = existing.name if existing else None
    return image_name


def _write_bf_tap(img_client: httpx.Client, tap: int, batch: dict[str, Any], rev: int) -> None:
    """Write the Brewfather Tap file (+ image) for one desired Slot.

    ``img_client`` is the unauthenticated image client used for the (off-host)
    image download. It is needed for the photo alone - every Beer field comes
    from Mapping, which is handed the batch object and nothing else.
    """
    image_name = _store_image(img_client, tap, batch)
    beer = mapping.beer(batch)
    # The image is stored first so the store's own `image:` key names the photo
    # that is actually on disk beside the file. batch_status mirrors the
    # revision record: a fact about this Batch at write time, not about the
    # beverage, so it rides beside `revision` rather than on the Beer itself
    # (issue #35 - nothing renders it yet, but writing it now saves the ticket
    # that does a second MAPPING_VERSION bump).
    taps.write(tap, SYNC_SOURCE, beer, mapping.description(batch),
               revision=mapping.source_revision(batch, rev),
               batch_status=mapping.status_label(batch))
    log.info("wrote tap %d (%s) (name=%r image=%s)",
             tap, SYNC_SOURCE, beer.name, image_name)


def _is_unchanged(tap: int, batch: dict[str, Any], rev: int) -> bool:
    """True if the stored Brewfather Tap already reflects this batch at this revision.

    Lets the sync skip a re-write (and image re-download) when nothing changed,
    keeping API/bandwidth use minimal and avoiding needless display churn.

    Two separable answers, deliberately: Mapping says whether the cached
    revision record is this Batch at this revision under the current
    MAPPING_VERSION, and whether the Batch offers a photo at all; the store says
    whether it actually holds one. Neither half needs to know the other's
    business.
    """
    cached = taps.read(tap, SYNC_SOURCE)
    if cached is None:
        return False
    if not mapping.is_current(cached.revision, batch, rev):
        return False
    return (not mapping.wants_image(batch)) or (cached.image is not None)


def run_sync() -> dict[str, Any]:
    """Execute one full sync. Returns a small status dict. Never raises."""
    ensure_dirs()
    creds = brewfather_credentials()
    user_id, api_key = creds["user_id"], creds["api_key"]
    cfg = load_config()
    # The configured tap count is deliberately NOT read here: it is a display
    # setting, and sync depends only on what Brewfather claims. See
    # `mapping.slot_claim` and `_archive_undesired`.
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
    # The status decides which Batches are FETCHED, and (in `mapping.desired_map`)
    # which one wins a Slot two Batches both claim. It does not change MAPPING:
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

                desired = mapping.desired_map(batches)
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
