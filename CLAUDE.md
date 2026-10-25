# TV Tap List - project guide for Claude

Durable knowledge for working in this repo. Read this first; it captures the
architecture, conventions, and the non-obvious gotchas learned while building it.

> **Location note:** This guide lives at the repo **root** (`CLAUDE.md`), so
> Claude Code auto-loads it every session. It is tracked in git (contributors
> and agents share one guide) but excluded from the Docker build context, so it
> never ships in the image.

> **This file is public. Write every edit accordingly:**
>
> - **No secrets, ever** - no API keys, passwords, tokens, or `.env` contents,
>   not even as examples. Use placeholders.
> - **No machine-local absolute paths** (user home dirs, temp dirs, a
>   particular machine's mount points) and no personal details beyond what git
>   history already shows.
> - **No unpublished or embargoed context** - venue names, customer details,
>   anything from a private conversation.
> - **Say "the maintainer" or "the operator", not "you"** where a public reader
>   would be confused about who is meant.
> - Keep the candour. The value here is the *why* and the traps - the tone does
>   not need sanitising, only the specifics that should not be published.

---

## What this is

An **offline-first digital beer tap list** for TV displays, shipped as a **single
Docker container**. It syncs beers from the **Brewfather** API when online and
keeps serving the last cached data - with zero external requests - when the
venue's internet is down. **The container is the brain; the TV is a thin client
that just loads `/`** in a full-screen browser.

- **Backend:** Python 3.12 · FastAPI · Uvicorn · APScheduler (two jobs: sync +
  daily cleanup) · httpx · Jinja2.
- **Frontend:** vanilla HTML/CSS/JS - **no framework, no build step, no CDNs**
  (offline guarantee). Static assets are baked into the image.
- **Storage:** `config.json` + flat markdown/image files under a mapped `/data`
  host directory (a bind mount, so admins can read/edit the files directly).

---

## Repo layout

```
app/                FastAPI app + all logic
  main.py           routes (display, admin, API, image serving, auth)
  config_store.py   config.json load/save/coerce; DEFAULT_CONFIG is the schema
  status_store.py   status.json: disposable runtime Status + the one-time migration
  board.py          builds the /api/board payload from one effective-settings input (never reads Settings itself); owns Tap, Colour and Visibility resolution
  brewfather.py     Brewfather fetch + sync job (Fetch only; no pure mapping logic)
  mapping.py        Batch -> Beer: note tokens, field extractors, Slot conflicts (PURE)
  beer.py           the Beer type + TapPresentation/SourceRevision; coerces, never rejects
  colors.py         EBC->hex colour model, hex/saturation parsing, text contrast
  beer_glass.py     tinted beer-glass SVG placeholders (9 glass silhouettes)
  theme.py          colour theme presets + resolution (-> CSS variables)
  tap_store.py      Tap files by Slot+Source; Source precedence; md/image pair
  snapshot.py       Snapshot export/import: streamed zip, layout validation, credential gate
  photo_library.py  The photo album: /data/photos, the Display copy cache, the scan and the one-at-a-time pipeline (Pillow); owns every photo path
  seen_screens.py   In-memory list of recently seen screens (User-Agent, profile, how chosen); lost on restart
  admin_ops.py      Manual-override domain ops (save/clear) and Display profile ops (create/rename/delete/save) behind the admin write routes
  persistence.py    startup durability checks: is /data mapped, and is it the same /data (DDI)
  archive.py        move retired beers to old_beers/ (md+image as a pair)
  cleanup.py        daily age/size archive pruning (03:30 local)
  demo.py           DEMO_MODE seeding (offline screenshots, fresh data dir only)
  atomic.py         atomic_write_*, JOB_LOCK, safe_unlink
  auth.py           signed-cookie admin sessions + login rate limiting
  paths.py          DATA_DIR-derived paths; ensure_dirs()
  timezone.py       TZ-aware iso_now()
static/             css/display.css, css/admin.css, js/display.js, js/admin.js, placeholder.svg
templates/          display.html, admin.html (tabbed), login.html
tests/              pytest suite (960 tests, 30 files) + conftest.py fixtures; tests/browser = Playwright page tests
Dockerfile · entrypoint.sh · docker-compose.yml · scripts/docker_test.sh
scripts/setup.sh (guided installer / .env wizard)
README.md           root landing page (embeds docs/venue_logo.png + docs/screenshot.png)
docs/               INSTALLATION.md · FAQ.md · UNRAID.md · WINDOWS.md · PUBLISHING.md · venue_logo.png · screenshot.png
```

---

## Data & storage model

`/data` (a mapped host directory) holds everything persistent:

```
/data
  config.json        Settings: operator config incl. the Brewfather key
  status.json        Status: sync timestamps + update-check findings (disposable)
  placeholder.svg    fallback image (seeded from the image; replaceable)
  .data_dir_id       Data Directory Identity (random id; do not edit or delete)
  taps/              custom_tap_X.md / bf_tap_X.md (+ per-tap images)
  old_beers/         archived md+image pairs, datetime-suffixed
  photos/            the photo album's ORIGINALS, untouched (ADR-0008): each
                     folder an Album, loose files the Album "Unsorted", folders
                     inside an album part of it. Operator-owned, hand-editable.
  photos_removed/    Removed photos: `<token>.<ext>` + `<token>.json` (album,
                     name, removal time). Purged by the daily cleanup.
  photo_cache/       DISPOSABLE: display/ (the Display copies), thumbs/ and
                     index.json (sizes, mtimes, content hashes, date taken).
                     Deleting it costs only reprocessing time.
```

- **Source precedence is owned by `tap_store.py`**, not by callers. Callers name a
  Slot and a Source; `resolve(slot)` walks `SOURCE_PRECEDENCE` (Manual, then
  Brewfather, then Vacant) and returns the winning `TapFile`. **Filenames are
  private to that module** - nothing else in `app/` constructs or parses one, and
  a grep for `custom_tap_`/`bf_tap_` outside it should find only prose. See
  `docs/adr/0003-tap-file-store-owns-slot-resolution.md`.
- **The filename decides the Source.** The front-matter `source:` key is written
  for a human reading the file and is never read back as truth.
- **Existence, not readability, decides precedence.** A file that *vanished*
  (`FileNotFoundError`) falls through to the next Source; a file that exists but
  will not read stops the walk with an empty Tap, so a disk hiccup can never
  demote a Manual Tap and put another brewery's beer on the board.
- **Both Sources may hold a file for one Slot.** The Brewfather Tap is kept warm
  under a Manual override - see the sync section below.
- **What crosses the store seam is a typed `Beer`, not a dict** (ADR-0005).
  `TapFile` carries `beer`, `presentation` (per-Slot show_og/show_fg),
  `updated`, `revision` (the Brewfather cache-coherence record, `None` on a
  Manual Tap) and `body`. `front_matter` is **gone** - do not reintroduce a
  dict here. `Beer` is closed: unknown keys are dropped on rewrite.
- **The disposition rule, and it is load-bearing.** File-level *readability*
  and value-level *validity* are different questions and the seam must never
  merge them. A bad **value** (`abv: banana`) coerces to `None` and the Tap
  resolves normally **under its own Source**; only a file that will not read
  stops the precedence walk. Coerce, never reject - a hand-edited file has no
  one to report an error to, the same reasoning as the Settings clamp. Log a
  coercion at **write** time (`Beer.coerced`), never at read time: the board
  is rebuilt on every poll from every TV. A test pins that a bad value does
  not demote Source precedence; it is the one that fails if somebody
  "improves" coercion into validation.
- **`None` is the only absence.** `""` never survives the seam and `0` is a
  value, not a missing reading. This is why `board._num` is gone and
  `_is_missing` is just `value is None`.
- **Tap files** are markdown with YAML-ish front-matter (name, abv, ibu, ebc, og,
  fg, saturation, color_override, glass, source, image, …) + a body = description.
- **Writes are atomic** (`atomic.py`) and serialized under `JOB_LOCK` so the sync
  job, cleanup, and admin writes never interleave.
- **`config.json` safety:** `config_store` *never* overwrites an existing-but-
  transiently-unreadable config with defaults (raises `ConfigUnreadable`). This
  matters because Docker Desktop/Windows bind mounts occasionally misreport reads.
  The sync no longer writes config every cycle (ADR-0002), which narrows the
  window - it does **not** close it, so do not remove the guard.
  `update_config()` returns the **coerced** saved dict (clamps/normalises
  applied) - rely on that, not the input.
- **`status.json` has the OPPOSITE policy, deliberately.** `status_store` reads
  tolerantly (unreadable -> defaults) and always writes, because every field
  regenerates on the next cycle and a guard there would strand a healthy box on
  "never synced". `load_status()` never bootstraps a file. Unifying the two
  stores "for consistency" breaks one of them - see ADR-0002 and GLOSSARY.md's
  Known hazards.

---

## Config schema (DEFAULT_CONFIG in `config_store.py`)

The dict literal **is** the schema; `_coerce()` clamps/normalises every field.
Notable bounds: `page_size` 1–8 (`MAX_PAGE_SIZE`), `rotation_seconds` 3–600,
`venue_logo_height_vh` 0–33 (`MAX_VENUE_LOGO_VH`), `num_taps` 0–200
(`MAX_NUM_TAPS`), archive age/size ≥0. **Every numeric bound lives in one table,
`config_store.SETTINGS_BOUNDS`** (`(min, max)`, `None` for the open cleanup
ceilings); `_coerce` clamps from it and the admin form renders each input's
`min`/`max` from the same entries via the `bounds_for()` Jinja macro.
**Clamping in the store is the single enforcement point** - routes carry no range
checks, deliberately (GLOSSARY.md, Known hazards): a hand-edited `config.json` has
no one to report an error to and must never stop the box booting. Do not restore
server-side rejection. FastAPI's 422 for non-numeric input is unrelated and stays.
`update_config()` also **drops env-managed Brewfather credentials before writing**
(`_drop_env_managed_credentials`), so the rule sits at the write seam and any
future writer inherits it. `config_store.apply_settings()` is the domain
operation behind the admin Save. Display toggles are tri-managed: global
`show_*` + `hide_*_when_empty` + per-tap overrides - **but these are Settings
only; the board resolves them and the wire carries the answer** (see Routes). Theme = a preset key or `"custom"` (+ `theme_custom` colours).
`color_unit` ∈ {ebc, srm} (stored as EBC always; unit only affects display).
The six machine-written Status fields are **not** in this schema - they live in
`status_store.DEFAULT_STATUS` / `status.json`. `update_check_enabled` stays in
Settings: whether to check is operator intent, what the check found is Status.
`include_conditioning` and `include_fermenting` (both bool, default False)
independently widen the sync from Completed-only to also pull Conditioning and/or
Fermenting Batches.

**What a Display profile may customise (ADR-0007) is declared once**, in
`config_store.py`: `NEVER_CUSTOMISABLE` (Brewfather credentials, sync scope,
`num_taps`, archiving, `venue_logo`, `color_unit`, `update_check_enabled`) and
`CUSTOMISABLE_UNITS` (unit name mapped to the keys customised together: Theme with
`theme_custom`; `paginate` with `page_size`; each size preset with its scale;
each stat's `show_*` with its `hide_*_when_empty`; every other customisable
setting is a unit of one). `CUSTOMISABLE_KEYS` is derived from the units. A
guard test fails if a `DEFAULT_CONFIG` key is in neither list, so a new Setting
must be classified when it is added.

**Display profiles are stored in `config.json` under `profiles`** (name to a dict
of Customised settings only; `profiles` itself is in `NEVER_CUSTOMISABLE`).
`_coerce_profiles` runs in `_coerce`: names are lowercased and must match
`[a-z0-9-]{1,32}` (and not be `default`), else the profile is dropped; each
profile's values go through the Default profile's own `_coerce`, so every
`SETTINGS_BOUNDS` clamp applies; non-customisable keys are dropped, and a
one-unit group that is not wholly present is dropped whole (followed).
**`resolve_effective(settings, name)`** is the pure resolution: it returns an
`Effective(settings, profile, chosen)` where `chosen` is `default` (no name),
`url` (an existing profile) or `unknown` (a name with no profile, which gets the
Default profile). The profile is read over the Settings at resolution time, so
followed settings track the Default live. `effective_settings()` (no arguments)
is the Default profile's, for callers with no screen in hand. `GET
/api/board?display=<name>` resolves and records unknown names in memory
(`config_store.unknown_profile_names()`, bounded to 50, most recent last, lost on
restart); `display.js` only forwards its own `?display=` on each poll.

**User-Agent fallback.** A profile may carry `match_user_agent` (a plain,
case-insensitive substring, trimmed, up to 200 characters) in its `profiles`
dict. It is stored beside the Customised settings but is not one: `_coerce_profile`
keeps units only, `_coerce_profiles` adds the text back, and `resolve_effective`
never merges it into the effective settings. `resolve_effective(settings, name,
user_agent)`: a name that is asked for always wins (an existing one is `url`;
a missing one is `unknown` and gets the Default profile **without** trying the
User-Agent, so a typo stays visible); with no name, the first profile in list
order whose text the User-Agent contains wins (`user-agent`); else `default`.
The text is saved by `admin_ops.save_profile(..., user_agent=)` (None keeps it,
blank clears it) from the editing profile's own card in the Admin. **Recently seen
screens** live in `app/seen_screens.py`, the single owner of that in-memory list
(bounded to 50, one entry per User-Agent, profile and how it was chosen; never
written to Settings or Status). `/api/board` records every request; the Admin lists
them and warns when a seen User-Agent matches two or more profiles' texts.

**"Show upcoming beers" is customisable per profile, but the cache is shared.**
`config_store.upcoming_wanted(settings)` is true while the Default profile or
any profile has `show_upcoming_previews` on; the sync's gate and convergence
clear and `apply_settings`' clear all ask it, so `/data/upcoming/` is cleared
only when no profile wants it (ADR-0006, ADR-0007). Whether a screen shows them is the
board's per-profile answer from its own effective settings. A profile that
turns the switch on or off needs no sync change.

**The photo album settings** (all customisable units, off in the Default profile):
`album_enabled`, `album_every_turns` (0-50; 0 = photos only), `album_photos_per_visit`
(1-50), `album_seconds_per_photo` (2-600), `album_order` (`shuffle`/`name`/`date`;
unknown coerces to shuffle), `album_all` + `album_selection` (one unit: every album,
or a ticked list of album NAMES; a name with no folder is skipped, never an error),
`album_keep_ticker` and `album_fade_seconds` (0-10). Bounds are in
`SETTINGS_BOUNDS`. Each is a `unit(...)` in the Settings tab's "Photo album"
fieldset with a `PROFILE_UNIT_LABELS` entry. The ticked names travel as ONE hidden
JSON field (`album_selection`), parsed by a `before` validator on `SettingsForm`,
because `admin.js` folds every checkbox of one name into a single true/false and an
album name may hold any character. `album_all` defaults to True on the form (unlike
the other booleans' "missing means off") so a missing field can never become "a
ticked list with nothing ticked".

---

## The photo album (`photo_library.py`, ADR-0008)

- **One module owns `/data/photos/`, `/data/photos_removed/` and `/data/photo_cache/`.** Nothing else in
  `app/` builds a path under any of them (a guard test walks the AST for
  `PHOTOS_DIR`/`PHOTO_CACHE_DIR`/`PHOTOS_REMOVED_DIR`, as the Tap store's filenames are guarded). Its
  operations: `list_albums`, `scan`, `begin_upload`/`Upload.commit`, `kick_processing`, `photos_in`, `thumb_file`, `process_next`/`process_pending`,
  `ready_photos(albums, order)`, `copy_file(filename)`, `status`.
- **Originals are never served or referenced.** The board carries only
  `/img/photo/<copy id>.webp` (`.jpg` in compatibility mode); the copy id is an opaque hash of the original's
  content and the pipeline settings, so a changed copy has a new URL and the route
  can cache for a day. `copy_file` accepts only `<32 hex>.webp` or `.jpg`, so traversal and
  original names are plain 404s (not the Placeholder: an unready photo must not be
  drawn as one).
- **A photo shows only once its copy file exists.** `ready_photos` stats the copy,
  so deleting `photo_cache/` (or just `display/`) takes photos off the board until
  they are rebuilt. A stale copy (other settings, or an older version of the file)
  stays listed until its replacement is written (`copy.settings`/`copy.src` in the
  index). **A replaced copy is not deleted at once**: screens poll the board only every 30 s,
  so one still on the previous list would 404 every photo replaced since its last poll (and a
  small album would blank to the taps). `_retire_copy` sets the old Display copy's mtime to now
  and the orphan sweep (every scan) removes it once it is older than `ORPHAN_GRACE_SECONDS`
  (10 minutes, well over two polls); its thumbnail goes at once. The index entry remembers the
  retired ids (`retired`), so **removing a photo still deletes its current and retired copies at
  once**. Other orphans are swept after the same 10 minutes.
- **Format and size (issue #60).** Two global, never-customisable settings, `photo_format`
  (`webp` default / `jpeg`, compatibility mode for TVs that cannot show WebP) and `photo_size`
  (`4k` default / `1080p`), set from the Photos tab's Display copies panel by
  `POST /admin/photos/pipeline` (not the Settings form, so a Save cannot reset them; it
  refuses other values with 422 and kicks the rebuild). `pipeline()` reads them from the
  config (lazily: config_store imports this module; cached against the config file's stamp,
  and `save_config` invalidates it) and `settings_key()` includes both, so a change marks
  every copy stale and the normal one-at-a-time machinery rebuilds them while the old
  copies keep being served. **Each index copy records its own `ext`** (`.webp`, or `.jpg`;
  a record without one is WebP), because during a change the old copies have the old
  extension: listing, serving (`copy_file` takes either extension) and removal
  (`_drop_copy_files_if_unused` unlinks the id under both) never assume the current
  format's. A pipeline is read once per photo, so a setting changed mid-render only makes
  that copy stale. JPEG is baseline, 95, no chroma subsampling, no metadata; transparency is
  flattened onto black. Thumbnails stay WebP (admin only).
- **Scan and pickup.** A scheduler job (`photo_scan`, every minute and once at
  start-up, `max_instances=1`) runs `scan()` then `process_pending()`; the loop
  re-scans every minute while it works, so a long batch never starves hand-made
  changes. `scan` re-hashes only files whose size or mtime changed, ignores files
  modified in the last 2 s (still being copied), skips hidden entries and symlinks,
  and never empties the index when the folder itself is unreadable. Only JPEG, PNG
  and WebP are photos here; other extensions are ignored, not reported.
- **The pipeline, one photo at a time** (`_PROCESS_LOCK`; deliberately NOT under
  `JOB_LOCK`, so a long decode never blocks an admin save): read the date taken
  from EXIF first, JPEG `draft()` for big files, `exif_transpose`, ICC to sRGB
  (a tag already called sRGB is left alone; CMYK converts through its profile;
  alpha is split off and put back), Lanczos fit into 3840x2160 up or down, WebP
  quality 99 with `info` cleared so no EXIF/GPS/ICC/XMP chunk survives, plus a 480
  px thumbnail (admin use, later). Pillow's pixel limit is set explicitly (200 MP)
  and a decompression-bomb warning is promoted to an error. Anything unreadable is
  recorded in the index with its reason (`status().failures`), shown in the Settings
  fieldset, and retried only when the file or the settings change.
- **Order.** `name` is natural and case-blind, `date` is newest first by date taken
  falling back to the file's mtime; `shuffle` returns the set in name order, since
  the page shuffles it (it also has to remember its place).
- **Uploads (issue #57).** The Admin's Photos tab sends each file as its own raw-body
  `POST /admin/photos/upload?album=&name=` (no multipart, so nothing is spooled to the
  system temp; the Snapshot stage uses the same trick), two or three at a time with a
  bar and Retry per file. `begin_upload()` refuses a declared size over **250 MB**
  (`MAX_UPLOAD_BYTES`, separate from the 10 MB beer-image cap) or a data disk under
  **1 GB free** (`MIN_FREE_BYTES`) before reading a byte; the `Upload` context streams to
  `.tmp_photo_uploads/` in the data directory, hashing as it goes, and removes the
  staged file on every exit path (stale ones are swept by the scan). `commit()` files it
  into an existing album (Unsorted = top level): a client name is reduced to a plain
  file name, a clash gets ` (2)` before the extension (case-blind), and a file whose
  **content** is already in the same album is skipped with a note naming the
  existing copy (the same photo may be uploaded into another album). The new file is indexed with its hash at once, so duplicates are caught
  before any scan settles, then `kick_processing()` starts the one-at-a-time pipeline
  in a background thread. The Photos tab polls `GET /admin/photos/status` (counts,
  albums, one album's photos with ready/waiting/failed + reason) and shows thumbnails
  from the Admin-only `GET /admin/photos/thumb/<copy id>.webp`; the Settings tab no
  longer carries the ready/pending/failed line.
- **Organising and Removed (issue #58).** `create_album`/`rename_album`/`delete_album`,
  `move_photos`, `remove_photos`, and `list_removed`/`restore_removed`/`delete_removed`/
  `empty_removed`/`purge_removed` all run under `_STORE_LOCK` (the lock an upload takes)
  and change the index at once. Photos are addressed by the `photo_id` the Photos tab
  lists (a hash of the path, so an id changes when a photo moves). Album names reject
  `/ \ < > : " | ? *`, leading/trailing dots, "Unsorted" and a case-blind clash.
  A rename moves the folder and re-keys the index (no re-hash, no new copy); the profiles
  that ticked the old name are updated by `admin_ops.rename_album`, because config sits
  a layer above this module (config_store imports it). Delete moves every photo to
  Removed, then removes the folders that are empty; a non-photo file keeps its folder.
  **Removed** is `photos_removed/<token>.<ext>` plus `<token>.json` `{album, name, ext,
  removed_at, copy_id}`; the record is written before the move. Removing deletes the
  public Display copy at once (unless an identical photo shares it) but keeps the thumbnail,
  which the orphan sweep also spares while a record names it. Restore recreates the album
  folder if needed (a hand-edited bad album falls back to Unsorted), suffixes a taken
  name, then scans so the photo is reprocessed. `purge_removed(days)` is called from
  `cleanup.run_cleanup` (daily 03:30) with the global `photo_removed_retention_days`
  (default 30, 0 to 3650, never customisable, set from the Photos tab's own route so a
  Settings Save cannot reset it; it is in config.json, so a Snapshot carries it and no
  photo). Routes: `/admin/photos/albums[/rename|/delete]`, `/move`, `/remove`, `/removed`
  `[/restore|/delete|/empty]`, `/retention`, all admin-only, as are thumbnails.
  The Photos tab has multi-select on the thumbnails (a checkbox per tile) with a Move /
  Remove bar, and a Removed panel with Restore, Delete forever, Empty removed and the
  retention days. A scan racing a move can drop and re-add one index entry, which only
  costs a re-process.
- **Runtime state is in memory only**: the library's index is a cache file, and the
  page's place in the album is lost on reload (a reload starts a fresh Shuffle pass).

---

## Brewfather sync (`brewfather.py`)

- **Efficient fetch:** `_list_batches(client, statuses)` does
  `GET /v2/batches?status=<S>&complete=True&limit=50` **once per status**,
  paginated with `start_after`, merging + deduping by `_id`. Cost = `ceil(N/50)`
  calls per status (limit is **500/hour per key**). Statuses = `["Completed"]`,
  plus `"Conditioning"` and/or `"Fermenting"` when the `include_conditioning` /
  `include_fermenting` config toggles are on (two independent admin checkboxes,
  all four combinations valid). Each extra status is another full sweep, so the
  call cost scales with the length of the status list - three statuses still sit
  well inside the limit at normal sync intervals, but this is why the wider scope
  is opt-in. The old N+1 (a detail call per batch) would blow the limit.
  Note: **do not bump `MAPPING_VERSION`** for these status toggles - extraction
  is unchanged; only *which batches* are selected changes. Nor is there any
  special-casing downstream: a Fermenting Batch still needs a `tap:X` token and
  maps to a Tap identically to a Completed one.
- **Mapping is PURE and lives in `app/mapping.py`** (issue #10): the note-token
  and name regexes, every field extractor, `STATUS_PRECEDENCE` and the Slot
  conflict resolver, plus `MAPPING_VERSION` itself. It imports nothing that opens
  a socket or touches disk; `MAX_NUM_TAPS` from `config_store` is the one
  sanctioned constant import. **An AST test pins its import list** - do not add a
  convenience import of `tap_store` there. `brewfather.py` keeps the API
  constants, both client factories, `_list_batches`, `_download_image` and the
  sync orchestration, and `CONTENT_TYPE_EXT` now sits beside its only consumer.
  The module logger stays `taplist.sync` on purpose: a Slot conflict is read as
  part of a sync cycle, and splitting the module must not split an operator's
  log filter.
- **Change-detection:** each `bf_tap` records `source_rev` (batch recency) and
  `map_rev` (= `MAPPING_VERSION`, defined in `app/mapping.py`). Unchanged batches skip rewrite + image
  re-download. **Bump `MAPPING_VERSION`** (currently **6**) whenever extraction
  logic changes - it forces a one-time rewrite of every cached tap to pick up new
  fields, then settles back to skipping.
- **A Slot's cached photo belongs to one Batch** (issue #46). A failed download
  keeps the photo only when the cached Tap's revision record names the *same*
  Batch (`mapping.is_same_batch`); a Batch with no photo, or a photo cached for
  a different Batch, is removed (`tap_store.delete_image`) so the Placeholder
  shows. `_is_unchanged` compares "wants a photo" with "holds a photo" both
  ways, so a Tap holding a photo its Batch does not offer is rewritten. Do not
  "simplify" the fallback back to "keep whatever image the Slot has": the Slot
  outlives its Batches, and that hands one beer's photo to the next.
- **Assignment:** a `tap:X` token in the batch notes routes a beer to slot X.
  Accepted range is 1..`MAX_NUM_TAPS` - a *system* bound. **The configured tap
  count never touches sync**: lowering it hides slots and discards nothing.
  An out-of-range token is ignored and logged with its batch named.
- **Conflict** (two batches claim one tap): the **most complete status wins**
  (`STATUS_PRECEDENCE`: Completed > Conditioning > Fermenting > Brewing >
  Planning), and recency only breaks a tie *within* one status. An unknown or
  missing status ranks last, so if Brewfather ever stops sending `status`
  everything ties and resolution degrades to plain newest-wins. Either way the
  clash is logged with both batches, both statuses, and which rule decided.
  Plain newest-wins was wrong once the Conditioning/Fermenting toggles made more
  than one status reachable for a Slot: a conditioning batch is edited far more
  often than a finished one, so the next brew would push the pouring beer off
  its tap. **Do not bump `MAPPING_VERSION`** for this - selection changed,
  extraction did not.
- **Brewfather's claim is the only thing that drives a write or an archive.** A
  `tap:X` token means the slot is claimed; no token means it is not. Sync writes
  into Manual-occupied slots too, keeping that `bf_tap_X` **warm underneath** the
  override: nothing displays it while the override stands, and clearing the
  override reveals a current beer instantly instead of leaving the slot vacant
  until the next cycle. The cost is one image download + one file write per cycle
  for beers nobody can see; the batch-list fetch is unchanged, so the rate limit
  is unaffected. **Do not "optimise" this back into a skip** - that skip was the
  bug (see ADR-0003).
- **Safety invariants:** a **failed sync makes no destructive changes** (last good
  cache stays); a 429 is reported honouring `Retry-After`. Manual Taps are never
  read, written, or archived by sync - now *structurally*, via
  `SYNC_SOURCE = Source.BREWFATHER` passed to every **mutating** store call, so
  there is no spelling of a write in there that could name a `custom_tap_` file.
  **One narrow read-only exception exists since issue #4:** `run_sync` calls
  `taps.occupied_slots(Source.MANUAL)` to learn which Slots a Manual Tap holds,
  because "a Manual Tap means the Slot is occupied" is part of the Occupancy
  rule deciding what becomes an Upcoming Beer. That is existence only, never
  content, and only while `config_store.upcoming_wanted` is true (the Default profile or any profile has `show_upcoming_previews` on). Writes, image saves
  and archives remain exclusively `SYNC_SOURCE` - do not "tidy" the exception
  away, and do not widen it into reading a Manual Tap's contents.
- **Image downloads use a SEPARATE unauthenticated client** (`_image_client`):
  batch image URLs are off-host (Firebase/CDN) and httpx applies a client's auth
  to *every* host, so the Brewfather Basic-Auth key must never ride along on an
  image fetch. **Two tests pin two different halves of this, and both are
  needed.** `test_image_client_carries_no_credentials` pins the **factories**:
  the API client keeps its auth, the image client carries none.
  `test_sync_downloads_images_with_the_unauthenticated_client` pins the
  **wiring**: it drives a real `run_sync` over a batch with an image URL and
  asserts the client `_download_image` was handed has `auth is None`. The second
  is what makes transposing the two clients in `run_sync`'s `with` statement fail
  the suite - until it existed, that transposition leaked the key and every test
  still passed. It asserts against the download seam (the `mock_network` fake
  records the client it receives), not against `run_sync`'s structure, so it
  survives issue #10's split of Mapping out of this module.
- **Field mapping is defensive** (tries several names, prefers *measured* over
  *estimated*). Colour: a *measured EBC* is taken at face value; everything else
  Brewfather exposes (estimatedColor, color, recipe.color) is **SRM** despite the
  name -> converted `EBC = SRM × 1.97` (`EBC_PER_SRM`). OG/FG kept only when they
  read as a plausible specific gravity (`1.0 < sg < 1.2`).

### Brewfather batch-note tokens (also settable per-beer in the admin)
| Token | Effect |
|-------|--------|
| `tap:3` | assign to tap 3 (required to appear) |
| `colour:#780606` / `color:#…` | exact swatch+glass colour, overrides EBC colour |
| `glass:willibecher` | glass silhouette: `willibecher` (default),`nonicpint`,`default` (= shaker pint),`schooner`,`pilsnerflute`,`weizen`,`tulip`,`teku`,`dimpledmug` |
| `saturation:60` | mute colour to 60% (percent, or 0–1 fraction) |
Tokens are stripped from any text shown on the card (`_clean_description`).

---

## Colour, themes, glassware

- **Colour is RESOLVED server-side, once**, by `colors.resolve_color(ebc,
  saturation, color_override)` - the only place the precedence (override, then
  EBC, then Unknown) is expressed. It returns a `ResolvedColor` (hex plus its
  contrasting text colour) or `None` meaning **Unknown**, and **never substitutes
  a fallback**: the colour drawn for Unknown belongs to the surface drawing it.
  The swatch declares grey (`colors.UNKNOWN_SWATCH_HEX`) and the Placeholder
  declares amber (`beer_glass._DEFAULT_HEX`), deliberately different - **this is
  not a drift bug, see ADR-0004** before "fixing" it. A *known* Colour is
  guaranteed identical on both because both read the one answer.
  `ebc_to_hex` (polynomial fit + luminance contrast) is unchanged and is now
  called only from inside `resolve_color`. **Saturation mutes the computed
  branch only** - an override plus a saturation yields the override untouched.
  `display.js` only converts EBC<->SRM for the *stat number*.
  `colors.display_color_to_ebc(value, unit)` is the one display-unit-to-stored-EBC
  conversion, used by both the override save and `/api/preview-color`.
  `color_known` is **gone** from the board payload - #9 replaced it with a
  resolved `swatch_visible` boolean.
- **Themes** (`theme.py`): presets `default, oled (true black), local_dimming
  (lifted blacks for FALD/edge-lit LCDs), midnight, daylight (light), custom`.
  `THEME_KEYS` map 1:1 to CSS custom properties; `display.js.applyTheme()` writes
  them onto `document.documentElement`. display.css ships the default palette as
  its `:root` fallback.
- **Glassware** (`beer_glass.py`): `beer_glass_svg(color, glass)` tints an
  **already-resolved** colour and derives nothing; passing no colour selects the
  amber Unknown fallback. Glass is chosen globally or per beer. The liquid uses a shared gradient (`#g`) with the base at the 55%
  stop (a test pins this).
- **A silhouette is DATA, not code.** Each glass is one `_SILHOUETTES` row: the
  pour's path, an optional stem path, the head's (cy, rx, ry) and the bubbles.
  Adding a glass is adding a row plus a `GLASS_TYPES` entry - tests pin that the
  two sets agree, that every pour is centred on x=150 (the head is drawn there
  unconditionally, so an off-centre pour wears its foam to one side), and that a
  stem is drawn behind the pour using the shared glass tint.
- **`stem` means "glass drawn behind the pour", not "a stem".** The dimpled mug
  uses it for its handle. The name is historical and says where the path is
  drawn, not what it depicts - see GLOSSARY.md's `stem` collision note.
- **`etch`/`sheen` are drawn OVER the pour and clipped to it** (the mug's facets
  and the light inside each). The clip is load-bearing, not tidiness: a course
  of dimples runs past the profile so the outer pair is cut in half by it, the
  way a real mug's are. A test pins the clip and the layering (over the liquid,
  under the foam). Both tints are deliberately faint - at full strength they
  stop reading as glass and start reading as a pattern.
- **The paths are hand-modelled, then made symmetrical BY RULE** (issue #6). The
  harness that did it lives on `main` in `prototype/glasses/`, kept because the
  loop it supports - the maintainer draws a shape, an agent folds it in - is hard
  to reconstruct from path data alone. It is **not maintained and not covered by
  tests**: assume it is stale and run it before trusting it. `prototype/` is in
  `.dockerignore` and in the publish workflow's path filter, so none of it
  reaches the image or triggers a build, and the generated `.html` pages are
  git-ignored because a committed one looks current and is not. Read
  `prototype/glasses/README.md` before touching a glass.
- **`DEFAULT_GLASS` is the Willi Becher, and the shaker's key is still `default`.**
  That key is a historical name, not a claim about which glass is selected, and
  it is written into operators' `config.json`, override files and Brewfather
  notes - renaming it would silently repoint every board using it. Nothing else
  spells a default glass as a literal: `board`, `config_store` and the admin
  form all read `DEFAULT_GLASS`.
- **The stem tint (`_GLASS_FILL`/`_GLASS_STROKE`) is a mid-grey on purpose.** It
  used to be a near-white translucent, which is invisible against the Daylight
  theme - survivable when a stem was a 12px rect, not once the stemmed glasses
  carried a full stem and a wide foot.

---

## Front-end behaviour (`static/js/display.js`)

- **The display implements NO domain logic - it renders what it is told.**
  Visibility is resolved on the server (`board.resolve_visibility`) and arrives as
  six booleans per Tap; the old `statHidden`/`effShow` helpers and the twice-written
  swatch special case are gone. **Do not reintroduce the chain here** - a drift
  guard in `test_frontend_constants.py` fails if display.js references a raw
  toggle, `color_known`, or either helper. The mirrored `DEFAULT_SETTINGS` is down
  to five keys (colour unit, source badge, and the three pagination/rotation
  values) and its values are pinned against `DEFAULT_CONFIG` by the same guard.
- **Diff-based poller:** `GET /api/board` every 30 s; updates only changed cards
  in place (no full reload). Full re-render only when the page layout or display
  settings change. `tapSignature`/`settingsSignature`/`layoutSignature` gate this.
  A visibility toggle is now observed through `tapSignature` and handled by the
  diff path, rather than forcing a full re-render via `settingsSignature`.
- **Carousel timer is separate** from the poll timer so a poll never resets the
  page position. Rotation is `rotation_seconds`; restarted on manual nav.
- **Pagination:** up to 8 cards/page; or a fixed `page_size` when `paginate`.
  **Page dots are clickable** and **Enter/Space advances a page** (resets timer).
- **Album visits** (`album` state, `startVisit`/`endVisit`): the board's `album`
  block exists only for a profile with the album on. A tap-page turn is one tick of
  the carousel timer (single-page boards count too); once `album.turnsShown >=
  every_turns` the next tick starts a visit instead of turning the page. A visit
  shows `min(photos_per_visit, photos)` photos, each `seconds_per_photo`, in two
  `<img>` layers cross-faded by opacity over `fade_seconds` (capped at half a photo
  so a short photo never fades its whole life), `object-fit: contain` on black
  (letterboxed; frames and mounts come later). `body.album-visit` hides the header,
  dots and (unless `body.album-keep-ticker`) the ticker with `visibility`, so the
  board's grid does not reflow underneath. The visit plan is frozen at its start
  (settings changes apply from the next visit). `upcomingTick` returns early during
  a visit and the visit start ends any cross-fade or panel in flight, so both pick up
  at their next tick afterwards. Board polls keep arriving during a visit but only
  the newest is kept (`album.pendingBoard`) and applied when the taps return, so a
  keg change is never lost; the photo list itself updates at once. Enter, Space, a
  click on the photo or a swipe (`nextPage`) advance one photo; the last one ends
  the visit. `every_turns` 0 is one endless visit (a photo frame) that only ends if
  the photos all go. Shuffle keeps a queue of ids for the pass (new photos join it at
  random places, a new pass never starts with the photo just shown); the ordered
  orders continue after the last shown id. A photo that fails to load is skipped, and
  the visit gives up when none will. No ready photos means `albumPlan()` is null and
  the screen is just a tap board. **display.js never reads a raw album Setting** (a
  guard test fails if it does); it runs the `album` block it is given.
- **Auto-scroll marquee:** long `.name`/`.desc` text scrolls within its box
  instead of truncating (measured per card; off under `prefers-reduced-motion`).
- **Layout = CSS grid** rows: venue header · stage · dots · ticker, so dots get
  their own row and nothing overlaps. `[hidden]` rows collapse.

## Admin UI (`templates/admin.html` + `static/js/admin.js`)

- (The Settings tab's **Photo album** fieldset: each album setting is a `unit(...)`,
  the album picker's ticks are unnamed and write one hidden JSON field, and the
  fieldset reports how many Display copies are ready, still processing, or could not
  be read, with the unreadable files named. Uploads, album management and Removed
  live on the Photos tab, see "The photo album".)
- Five tabs - **Settings · Theme · Manual overrides · Photos · Snapshot** - plus an
  always-visible status panel with **Sync Brewfather now**.
- **Cross-tab saving via HTML `form=` association:** theme pickers, glassware, and
  the venue-logo height live outside `<form id="settings-form">` but carry
  `form="settings-form"`, so one **Save** persists everything. `admin.js`
  normalises checkboxes by iterating `form.elements` (covers form-associated
  controls). Active tab persists in `localStorage`.
- **Reusable hex colour field** (`wireColorField` + `normalizeHex` in `admin.js`,
  markup `.color-field` = `<input type=color>` swatch + `[data-hex-text]`): two-way
  synced, `normalizeHex` mirrors server `parse_hex_color`. Used on the Theme tab
  (swatch is the submitted control) and each override row's Colour override (the
  **text** input carries `name="color_override"`, blank = "no override";
  `data-allow-empty` + a `[data-hex-clear]` ×). Invalid text sets `.invalid` and
  leaves the swatch unchanged.
- **Live colour preview + token block per override row:** `setupOverrideDynamic`
  debounces (150 ms) a `GET /api/preview-color` on colour/sat/override edits and
  paints `[data-color-indicator]` (a `previewSeq` guard drops out-of-order
  responses); `[data-token-block]` shows the Brewfather note tokens
  (`tap:`/`colour:`/`glass:`/`saturation:`, only when set - Feature 4A) with a
  Clipboard-API Copy (execCommand fallback).
- **Static assets are cache-busted** with `?v=<mtime>` (`_asset_version(*rels)`) on
  **both** the admin (`admin.js/css`) and the display (`display.js/css`), so an
  edited/rebuilt asset is picked up on the next normal load without a hard-refresh
  - important for the display, which is served on a hard-to-refresh TV.

---

## Routes (`app/main.py`)
`/` display · `/admin` (+ `/admin/login`,`/admin/logout`) · `GET /api/board`
· `GET /api/preview-color` · `POST /admin/settings` · `POST /admin/profiles`,`/{name}/rename`,`/{name}/delete`
· `POST /admin/override/{tap}`
· `POST /admin/venue-logo` · `POST /admin/sync` · `GET /admin/snapshot`
· `POST /admin/snapshot/stage`,`/discard`,`/import` · `/img/beer-glass`,
`/img/placeholder`,`/img/venue-logo`,`/img/<file>` · `GET /healthz`.

- **`GET /api/board`** (public) resolves from **effective settings** passed to
  `build_board(effective)` (the route passes `resolve_effective(load_config(),
  display).settings`, `display` being the optional `?display=` profile name);
  `board.py` does not import or call `load_config`, and a test pins that.
  It carries **resolved answers, not inputs**: six
  Visibility booleans per Tap (`abv_visible`, `ibu_visible`, `ebc_visible`,
  `og_visible`, `fg_visible`, `swatch_visible`) and a resolved Colour
  (`color_hex`/`text_color`, **null when Unknown**, absent on Vacant). The ten
  `show_*`/`hide_*_when_empty` flags, the per-Tap tri-states and `color_known`
  are **gone from the wire**. `color_unit` and `show_source_badge` stay raw - a
  unit conversion and a one-step toggle are not Visibility chains.
- **`GET /img/photo/{filename}`** (public) serves a Display copy with the standard
  `/img` security headers; see the photo album section. The board's **`album`**
  block (only with the album on) carries `every_turns`, `photos_per_visit`,
  `seconds_per_photo`, `fade_seconds`, `order`, `keep_ticker` and `photos`: ready
  copies only, each `{id, url, aspect, album, taken}` (`id` is unique per original, not per copy). With the album off the key is
  absent, like `upcoming`. Built by `board.resolve_album`.
- **`GET /api/preview-color`** (`?ebc=&sat=&hex=`) is **delegation** to
  `colors.resolve_color`; its only own step is the display-unit conversion
  (`colors.display_color_to_ebc`). A test asserts it returns what the board
  resolves for identical inputs - it is no longer a prose claim.
- **`/img/beer-glass?hex=<rrggbb>&glass=`** takes the **resolved** colour. No
  colour means Unknown, which selects the amber fallback. The old `ebc`/`sat`
  parameters are gone.
- **`POST /admin/settings`** parses `SettingsForm` - a Pydantic model that is the
  **single declaration** of the Settings field list, guarded by a test against
  `DEFAULT_CONFIG`, with `SETTINGS_NOT_ON_THE_FORM` naming the three deliberate
  exclusions. **`update_check_enabled` must stay off that model**: it has no
  control on the form, so a field declared there but never posted would take the
  model default on every Save and quietly switch the update check back on.
  **The typed form parameters are load-bearing** - `admin.js` posts checkboxes as
  the strings `"true"`/`"false"` and `bool("false")` is True, so raw form strings
  handed to the store would save every unchecked box as checked.
- **The profile editor** is the same form. `GET /admin?profile=<name>` renders the
  Settings and Theme tabs for that profile (unknown name: Default); every
  customisable unit sits in a `unit(...)` wrapper in `admin.html` with a
  `customise_<unit>` checkbox, and a test fails if a unit in
  `CUSTOMISABLE_UNITS` has no wrapper or no label in `PROFILE_UNIT_LABELS`.
  `POST /admin/settings` with a `profile` field calls `admin_ops.save_profile`
  (only customised units are stored; Follow drops the value; the Default profile
  and venue-wide settings cannot be written this way) through `apply_settings`,
  so the upcoming cache is cleared the same way. Global-only blocks stay in the
  markup with `hidden` (the required form fields still post) and the Manual
  overrides and Snapshot tabs are not rendered. `admin.js` keeps what was typed
  while a unit is on Follow. Start URLs are built from the request's own host.
- **`POST /admin/override/{tap}`** vets the upload, then calls
  `admin_ops.save_override` / `clear_override`, which own the JOB_LOCK section.

---

## Snapshot export / import (`app/snapshot.py`, issue #29)

A **Snapshot** is the Board as it stood, as a zip mirroring the data directory,
so it **restores by being unpacked** - hand-unpacking is a supported path
(ADR-0001), which is why there is deliberately **no manifest or version marker**
inside it (it would land as a stray file in `/data`).

- **Carries:** Settings, the Tap files and images of **both** Sources, the
  Archived beers, and the operator-supplied root images (venue logo, placeholder
  override). **Never carries** Status (regenerates every cycle, ADR-0002) or the
  DDI (it names *which directory this box uses*, so copying it is exactly the
  confusion it exists to detect).
- **The credential is opt-in, unchecked by default**, and the checkbox is
  *hidden* rather than disabled when the key is env-managed. One rule covers
  every combination: **the export never reads environment values - it optionally
  carries whatever `config.json` holds, and offers the choice only when the API
  key is one of them.** Both fields resolve independently via
  `config_store.brewfather_credentials()`; never re-read `os.environ` here. The
  gate is re-checked at export time, so a stale form posting `credentials=true`
  still gets blank fields.
- **Import asks whether the box will have a working key AFTERWARDS**, not
  whether it has one now. **Importing the Brewfather Taps and keeping a working
  key are mutually exclusive, and this reads as a bug.** It is not: a configured
  key means the next sync rewrites every `bf_tap`, so importing them onto a box
  that will sync shows the old beers for minutes and then silently undoes
  itself. **Do not "fix" this by keeping both.** The box's own key always beats
  the Snapshot's - an import must never replace a working credential.
- **Imported Settings clamp, never reject** - they go through `update_config`
  like any other write. A Snapshot's `config.json` is a hand-edited config file.
- **Staging exists because the decision is not expressible in one request.** The
  browser cannot see inside the zip (no JS zip parser - the offline guarantee),
  and re-uploading gigabytes to answer a 409 is not acceptable, so
  `POST /admin/snapshot/stage` receives, validates and returns the case the
  server resolved; `/import` carries only the answer. `plan_import` is the sole
  implementation of the rule - the client never re-derives it.
- **The job lock covers the enumeration only**, then is released and the files
  are streamed. `zipfile` genuinely streams to a non-seekable sink (it wraps in
  `_Tellable` and emits data descriptors), but **`zinfo.file_size` must be set
  from `stat()`** or a member over 4 GB raises at close. Images are
  `ZIP_STORED`, text is `ZIP_DEFLATED`.
  Known limit, more honest than the issue's wording: a Tap **archived** between
  enumeration and the stream reaching it is absent from the Snapshot entirely,
  because `old_beers/` was already enumerated. The guarantee is "never a torn
  file", not "nothing is missed".
- **Import validates the whole archive before writing anything**, and a refusal
  leaves existing data byte-for-byte unchanged (a test asserts bytes *and*
  mtimes). Member names are attacker-controlled: absolute paths, `..`,
  backslashes, symlinks, duplicate names (an extractor keeps the last, so a
  first-entry check can be walked past), oversized members and CRC failures are
  all refused by name. **Layout validation cannot prove an archive is ours** - a
  zip containing only a plausible `config.json` passes and imports as a no-op.
  "Refused whole" means structurally wrong or corrupt, not semantically foreign.
- **Filenames stay private to the Tap file store** (ADR-0003): validation asks
  `tap_store.identify()` / `identify_archived()` rather than pattern-matching.
  An AST test fails if any module outside the store spells `custom_tap_` or
  `bf_tap_` in a non-docstring string literal.
- The import route reads the **raw request body** (`request.stream()`), not a
  multipart upload: Starlette's `MultiPartParser` spools at a hard-coded 1 MB
  into the system temp dir with no knob, and `MAX_UPLOAD_BYTES` (10 MB) is a
  per-image cap that must not bind a multi-gigabyte Snapshot. Staged at a
  **fixed** path `DATA_DIR/.tmp_snapshot_upload`, so an abandoned import leaves
  at most one file; removed on every exit path and by `/admin/snapshot/discard`.

---

## Security model
- Admin auth = signed, `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS),
  login **rate-limited** 5 failures / 5 min per IP (`auth.py`; the per-IP map
  self-prunes expired windows so it can't grow unbounded).
- **Uploads & image serving:** admin image/logo uploads are extension-checked and
  size-capped (`MAX_UPLOAD_BYTES`, 10 MB) and validated **before** any filesystem
  change (a rejected upload never deletes existing data); every `/img/*` response
  carries `Content-Security-Policy: script-src 'none'; sandbox` + `nosniff` so an
  uploaded SVG can't run script if opened directly. `/api/board` is public and
  deliberately omits the sync-status/error fields.
- **Passwordless demo admin (eval only):** `auth.demo_admin_open()` = `DEMO_MODE`
  **and** no `ADMIN_PASSWORD`; `has_valid_session` returns `True` then, so `/admin`
  is open (a banner warns, startup logs a warning). The instant `ADMIN_PASSWORD`
  is set, normal auth applies; the non-demo no-password case stays **fail-closed**.
- **Plaintext-secrets trade-off (documented, intentional for appliance scope):**
  Brewfather key sits in `/data/config.json`; `ADMIN_PASSWORD`/`SESSION_SECRET`
  are env vars. Prefer `BREWFATHER_USER_ID`/`BREWFATHER_API_KEY` env vars to keep
  the key off disk (admin fields then lock to "managed via environment").
- Runs **non-root** via `gosu` with runtime `PUID`/`PGID` so a host-mounted
  `/data` stays writable (`entrypoint.sh`). Behind a proxy, set
  `FORWARDED_ALLOW_IPS` to the proxy IP only (never `*`).

---

## Build / deploy
- **Deploy:** `cp .env.example .env` (edit secrets) -> `docker compose up -d`.
  (The compose file defaults to prebuilt image; `--build` is only needed when
  building from source.)
- **Static assets are baked into the image** (`COPY static`). Changing CSS/JS
  requires a **rebuild** (`docker compose up -d --build`) to reach the live
  container. Both admin *and* display assets are cache-busted by mtime
  (`?v=<mtime>`), so a rebuilt asset is picked up on the next normal load - no
  manual hard-refresh of the TV needed.
- After a sync following a `MAPPING_VERSION` bump, cached `bf_tap` files rewrite
  once to populate new fields.
- `.dockerignore` excludes `*.md` except `README.md`, so the INSTALLATION/FAQ/UNRAID
  docs don't bloat the image.
- **The image declares no `VOLUME`, deliberately** (issue #28). With one, an
  unmapped `/data` silently receives an anonymous volume that is indistinguishable
  at runtime from a named one - both ext4 off the same device - which would
  disable the "not mapped" warning. `tests/test_persistence.py` **fails if the
  directive returns**; do not add it back for "Docker best practice".
  `/var/lib/taplist` holds the container-local DDI twin and is chowned in the
  Dockerfile **and again in `entrypoint.sh`**, because the entrypoint retags
  `appuser` to the runtime `PUID`/`PGID` afterwards - without the second chown an
  Unraid box (99:100) could not write it and signal (b) would die silently.

---

## Testing - IMPORTANT interpreter gotcha
- 960 tests, 30 files (the fifteen page tests skip without a browser):
  **the photo library** (`test_photo_library.py`: albums as folders, scan pickup,
  the pipeline on generated JPEG/PNG/WebP fixtures with EXIF orientation and GPS and a
  Display P3 profile - upright, sRGB, 4K fit up and down, WebP q99, no metadata in
  the copy or the thumbnail - plus stale copies, failures, the cache being deleted,
  and the guard that nothing else builds a photo path), **compatibility mode** (`test_photo_compat.py`: JPEG and 1080p
  copies - format, dimensions, no metadata - rebuilt in the background with the old copy served, and removal during and
  after a format change), **organising photos** (`test_photo_removed.py`: album create/rename/delete, profile
  ticks following a rename, move, remove, restore, delete forever, the nightly purge,
  login on every route), **the album on the board and
  in the Admin** (`test_album_board.py`: payload only with the album on, order,
  selection, clamping, no original referenced, the public copy route, the profile
  editor), and **the page tests** (`tests/browser/`, Playwright - see below), colours (incl. the single Colour resolution point -
  swatch-vs-Placeholder agreement, preview-vs-board equivalence, Unknown
  fallbacks, `display_color_to_ebc`), beer-glass, themes, storage, the Tap file
  store (precedence walk, unreadable-file handling, the four on-disk filenames),
  the Status store (disposable-read policy, the Settings/Status split, the
  one-time migration incl. the interrupted case), config coercion and
  `SETTINGS_BOUNDS` clamping, board resolution **and the Visibility chain**
  (all three steps, the swatch/EBC divergence, payload shape), **Mapping**
  (`test_mapping.py`: pure Batch-to-Beer with no client or fake, the
  MAPPING_VERSION rewrite path, Slot conflicts, and an import-purity guard),
  **the Beer type** (`test_beer.py`: the coercion table, a YAML round trip,
  the disposition guard, a structural test that all three writers produce a
  `Beer` - the one that would have caught the demo seeder - and its own
  import-purity guard), **the Snapshot** (`test_snapshot.py`: the credential
  matrix, the import decision table, layout rejection incl. traversal and
  symlinks with a bytes-and-mtime assertion that nothing changed, and a
  round trip that restores by plain `extractall` so the hand-unpack path
  cannot drift from the import route),
  **Display profiles** (`test_display_profiles.py`: storage, unit coercion,
  clamping, names, `?display=` through `/api/board`, the unknown-name list,
  Snapshot round trip, the display.js forwarding guard), **the User-Agent
  fallback and recently seen screens** (`test_user_agent_profiles.py`: URL beats
  User-Agent, first match wins, the seen list, the clash warning, and a guard that
  `scripts/pi-kiosk.sh` and the server share one name pattern), **the profile editor**
  (`test_admin_profiles.py` over HTTP, plus the profile ops in `test_admin_ops.py`),
  Brewfather sync (conflicts, override precedence, token parsing, OG/FG, archive,
  Conditioning and Fermenting status selection, image-client credential isolation
  at both the factories and the download call site), **the admin domain ops**
  (`test_admin_ops.py`, no HTTP), **the persistence checks**
  (`test_persistence.py`: mount detection, all five DDI truth-table rows plus the
  unreadable case, verdict precedence, demo suppression, and a Dockerfile guard
  against `VOLUME ["/data"]`), cleanup, the HTTP/admin surface (incl.
  `/api/preview-color`, passwordless demo admin, `/img` SVG-CSP, upload caps,
  validate-before-write, board sync-status omission, the Settings field-set
  guard and both checkbox directions), and server<->client constant-drift guards
  (`test_frontend_constants.py`: the settings mirror's values, no Visibility flag
  in the mirror, no chain in the display, and the swatch grey).
- **Page tests** (`tests/browser/`, Python Playwright, Chromium) drive the real
  display against the app served in a thread (lifespan off) with fixture photos, and
  assert DOM state and geometry from a 100 ms recorded timeline, not pixels
  (`ALBUM_SHOTS=<dir>` keeps screenshots as artefacts). They **skip when no browser
  can be launched**, so the plain suite passes on a box without one; CI runs them
  after the suite with `REQUIRE_BROWSER=1`, which turns a missing browser into a
  failure, after `playwright install --with-deps chromium`. On a box with only a
  system Chrome: `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome
  python -m pytest tests/browser`. The board-update test uses Playwright's fake
  clock to reach the 30 s poll; the others run in real time with 3 s pages and 2 s
  photos (about a minute for the nine).
- **The sync fixture fakes the httpx TRANSPORT, not `_list_batches`**, so the real
  listing, filtering, dedupe and pagination run in tests. Do not reintroduce a
  hand-written batch-list fake.
- **Local dev may run the suite on an older Python than the image ships**
  (3.10 is the known floor; there may be no project venv). On a fresh box:
  ```bash
  python -m pip install --user -r requirements.txt -r requirements-dev.txt
  python -m pytest
  ```
  **Do not add `-q`.** `pytest.ini` already sets `-ra -q` in `addopts`, so a
  second `-q` becomes `-qq` and suppresses the `N passed` summary line
  entirely - which reads as a silent run rather than a quiet one. Use
  `python -m pytest -p no:warnings` when the warnings plugin is burying the
  summary.

  **CI runs the same suite on 3.12**, the version the image ships, so a green
  local run on an older interpreter is a good signal but not the last word.
- **Container test** (Linux/WSL with Docker): `bash scripts/docker_test.sh` --
  builds, runs in DEMO_MODE, asserts health, demo data, zero external origins,
  non-root PID 1 + writable `/data`.
- `conftest.py` provides a `write_tap(source, tap, **fields)` fixture and points
  `DATA_DIR` at a tmp dir per test.

## Local preview gotchas (dev, no Docker)
- Run `DATA_DIR=./data ADMIN_PASSWORD=test SESSION_SECRET=dev DEMO_MODE=true
  uvicorn app.main:app --reload --port 8080`. **`--reload` watches Python but not
  reliably**; restart for app-logic changes.
- Screenshots **time out on continuous animations** (ticker + marquee + carousel)
  - freeze with injected `*{animation:none!important}` and clear intervals before
  capturing. Render the TV at 1280×720 (16:9).

---

## Operating rules & conventions (project-specific)
- **The repo is public and a push to `main` triggers the publish workflow** -
  push only when the maintainer asks, and scrub secrets first: `.env`,
  `taplist_data/`, and the Brewfather key in `config.json` must never leave
  the box.
- **`.env` holds real secrets and is git-ignored - never commit it.**
  `taplist_data/` (live data incl. the Brewfather key) is git-ignored too. Both
  are confirmed untracked; history is clean.
- **Commit only when asked.** Commit before major changes; do larger work on a
  feature branch.
- **Code style:** match the surrounding code - defensive type coercion, and
  docstrings/comments that explain the *why* (this codebase is heavily commented
  with rationale; keep that up).
- **No em dashes or arrows in writing:** use a hyphen `-` (spaced, as a
  separator) and `->` instead of `—` and `→` - in docs, code comments, and
  commit messages. Keeps the prose plain-ASCII and consistent.
- Now a **single `main` branch** - the `feature/display-options` work was merged
  (fast-forward) into `main` on 2026-07-01. Keep larger work on a feature branch,
  and **don't merge to `main` without explicit user confirmation.**

## Docs map
`README.md`, `GLOSSARY.md` and `CHANGELOG.md` stay at the repo root (the GitHub
landing page, the glossary where agent tooling expects it, and the changelog
where GitHub and every convention expects it); every other guide lives under
`docs/` and README links to them with a `docs/` prefix. Intra-`docs/` links are
bare filenames (all guides share the folder).
- `README.md` (root) - lean front door: purpose, one-command demo, links out.
  Present tense, no change-history framing. Embeds the `docs/venue_logo.png`
  project logo + `docs/screenshot.png`.
- `docs/INSTALLATION.md` - all setup paths (demo, guided installer, manual Compose,
  Unraid summary), env-var table, Brewfather key, batch tagging, reverse proxy.
- `docs/FAQ.md` - how everything works (sync, colour, themes, glassware, pagination,
  offline, archive, security). The deep "how-it-works" content lives here, not README.
- `scripts/setup.sh` - interactive `.env` wizard + optional `compose up`; writes
  `DATA_DIR_HOST`/`PORT`/secrets; safe to re-run.
- `docs/PUBLISHING.md` - publish to public GitHub safely (secret scrub, AGPLv3
  licence, GHCR image + CI).
- `docs/UNRAID.md` - Unraid setup (Compose Manager or template XML; PUID 99/PGID 100;
  `/mnt/user/appdata`).
- `docs/WINDOWS.md` - Docker Desktop on Windows: the `DATA_DIR_HOST` forms Compose
  accepts on Windows, a deliberate persistence check (`docker inspect` says
  `"Type":"bind"` and the files show up in Explorer), the anonymous-volume failure
  mode (`VOLUME ["/data"]` means an unmapped `/data` silently gets one, and only
  Manual Taps are lost because Brewfather Taps rebuild on the next sync),
  PUID/PGID being inert on a Windows-filesystem bind mount, WSL2 versus Windows
  placement, and firewall/LAN access for the TV.
- The original build spec (`tv_taplist_build_prompt.md`) was removed from the
  repo on 2026-07-01 and survives only as a maintainer-local note.
- `CHANGELOG.md` (root) - one section per release, written for the operator.
  **Required before a tag is pushed**; `scripts/release_notes.sh` extracts the
  section and the workflow fails without it. See the Releases section above.
- `GLOSSARY.md` (root) - the **ubiquitous language**: Slot, Beer, Tap, Source,
  Batch, Colour, Visibility, the lifecycle states, and the terms to *avoid*. Use
  these words in issues, tests, and comments. Ends with "Known hazards" - the
  traps a reader would otherwise hit (the `stem` collision, the two stores'
  deliberately opposite read policies).
- `docs/adr/` - **architecture decision records**: what was decided, why, and what
  was rejected. Append-only and numbered; supersede an old ADR with a new one
  rather than editing it. If a change contradicts an ADR, say so explicitly
  instead of silently overriding it. Current: **0001** files-as-the-Source-seam,
  **0002** Settings and Status are separate files, **0003** the Tap file store
  owns Slot resolution, **0004** Colour resolves to a Colour or to Unknown (and
  why the swatch's grey and the Placeholder's amber are not a drift bug),
  **0005** Beer crosses the store seam as a type (and why a bad *value*
  coerces to None rather than being rejected or made unreadable; also carries
  the pointer correcting ADR-0003's stale Consequences bullet).
- **Docs convention:** user docs are **present tense** ("this is how it works"),
  never "this changed / is now better", and refer to a **mapped data directory**,
  never a Docker named volume. `jceccato` is the image-owner placeholder in
  `ghcr.io/jceccato/tv-taplist`.

## Known/pending
- Field mapping should be re-verified if a Brewfather account's payload differs.
- Issues #9, #10, #11, #13 and #28 landed on 2026-08-21; #29 and #32 landed on
  2026-08-22. None are pending. #32 completed the typed-Beer half that #10
  deliberately stopped short of, so `front_matter` no longer exists.
- **Pre-existing prose violations** of the no-em-dash / no-arrow rule exist across
  the kiosk guides, the FAQ, and a few source comments (roughly forty sites). None
  were introduced by recent work. The kiosk guides' navigation arrows
  (`Settings -> System -> About`) are the debatable group; decide whether to
  convert or exempt them before doing a blanket sweep.

## Releases (how a change reaches users)
- **Nothing publishes without a green suite.**
  `.github/workflows/tests.yml` runs the tests on Python 3.12 (matching the
  image) on **every pull request**, and `docker-publish.yml` calls that same
  workflow as a `test` job that `build-and-push` declares `needs: test` on. A
  red suite **skips** `build-and-push` entirely, so no login, build, push or
  Release happens - on a tag that leaves a tag in git and nothing published,
  recoverable by the same delete-and-re-tag path as the changelog gate.
  `tests.yml` deliberately has **no path filter**: the publish workflow's
  `paths` list mirrors `.dockerignore`, which excludes `tests/`, so copying it
  "for consistency" would skip the tests on a test-only change. The workflow
  says so in a comment; leave it alone.
- **Pushing to `main`** triggers `.github/workflows/docker-publish.yml`, which
  builds and pushes `ghcr.io/jceccato/tv-taplist` tagged `main` and the short SHA.
  It does **not** move `latest`, create a GitHub Release, or bump a version.
  Docs-only pushes skip the build entirely (`paths-ignore` mirrors
  `.dockerignore`, which keeps `README.md`, so a README change still builds).
- **Only a `v*` tag push** moves `:latest` and publishes a GitHub Release
  (`--generate-notes`, `--latest`).
- **One meaning of "released", used by both the registry tag and the in-app
  update checker** (which reads `/releases/latest`). Nothing a user is steered at
  moves until a version tag is pushed, so **tag deliberately** - a merge to `main`
  reaches only `:main`, the unreleased canary.
- **Merged-but-unreleased work is written up under `## Unreleased`** at the top
  of `CHANGELOG.md`, as it lands, by whoever lands it. Cutting a release renames
  that heading to `## vX.Y.Z - YYYY-MM-DD` and starts a fresh empty one.
  `scripts/release_notes.sh` matches a version heading only, so an Unreleased
  section is never published as a release body, and a tag pushed while the notes
  still sit under it fails the build. **Do not wait for release day** - the agent
  that made the change knows what it means for an operator; the one cutting the
  release later is guessing from commit subjects, which is how v1.1.0 and v1.3.0
  shipped with no notes.
- **Every release needs a `CHANGELOG.md` entry, and this is enforced.** The
  workflow's first step on a `v*` tag runs `scripts/release_notes.sh <tag>` and
  **fails the job** if there is no `## vX.Y.Z - YYYY-MM-DD` section, before
  building anything; that section becomes the GitHub Release body. Write it for
  the operator running the box (what they gain, what upgrading costs them), not
  as a list of commit subjects - `--generate-notes` was the old fallback and it
  is why v1.1.0 and v1.3.0 shipped with nothing but a compare link. Recovery
  from a failed tag is in `docs/VERSIONING.md` ("The changelog").
- The update check resolves four states server-side (`current` / `behind` /
  `unknown` / `disabled`). An untagged build is `unknown`, and the admin says so
  rather than claiming "up to date" - see `update_check.update_state()`.

---

## Agent skills

### Issue tracker

GitHub Issues on `jceccato/tv-taplist`. See `docs/agents/issue-tracker.md`.

**A finished effort takes no new work.** Before adding anything to an effort
(a ticket, a Comment, a task that looks related), check its spec's state:
related work starts its own effort, and only the maintainer reopens one. See
"A finished effort takes no new work" in `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`GLOSSARY.md` + `docs/adr/` at repo root). See `docs/agents/domain.md`.
