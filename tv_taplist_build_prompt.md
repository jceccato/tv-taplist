# Build Prompt: Offline-First Digital TV Tap List (Single Docker Container)

## Role
You are an expert full-stack developer and Docker specialist. Build a complete, lightweight web application that runs inside a single Docker container and serves a digital beer "Tap List" to TV displays. It pulls data from the Brewfather API when the internet is available, but must run and display correctly with zero internet access.

Deliver real, runnable code for every file. Do not leave stubs, "TODO", or "implement this" placeholders. If you make an assumption, state it inline in a code comment.

---

## 0. Deployment Model (read first, this shapes everything)
- The container is the **server and the brain**. It holds all logic, data, and assets.
- The **TV is a thin client**. It is a smart TV browser, Chromecast/Fire TV, or a Raspberry Pi in kiosk mode that simply loads the display URL. It runs no app code of its own beyond the page you serve.
- Therefore the display page must be **fully self-contained from the local server**: every font, stylesheet, script, and image is served by the container. No request may go to any external origin.
- "Offline" means the **venue's internet may be down** while the local network (TV to container) still works. In that state, the Brewfather sync is simply skipped and the last successfully cached data keeps displaying unchanged. Nothing on the display should break, blank out, or show broken-image icons when the WAN is down.

---

## 1. Tech Stack (decided, do not substitute)
- **Backend:** Python 3.12 with **FastAPI**, served by **Uvicorn**.
- **Scheduling:** APScheduler (or FastAPI background tasks) for the two recurring jobs.
- **Frontend:** HTML5 + raw CSS + vanilla JavaScript only. No frameworks.
- **CSS:** Write **raw CSS**, or ship a single pre-built stylesheet committed to the repo. **Do NOT add a Node/Tailwind/PostCSS build step to the image.** The goal is a small appliance image with no front-end build toolchain. If you reference any web font, self-host the font file inside the container.
- **No CDNs anywhere.** Verify the final served HTML has zero `http(s)://` references to third-party hosts.
- **No heavy database.** Use a `config.json` file for settings and a flat file structure (described below) for tap data.

---

## 2. Storage and Data Model

### Directory layout (all under a Docker volume)
```
/data
  config.json
  taps/
    custom_tap_3.md      # manual override for tap 3
    custom_tap_3.jpg     # image for that tap (extension matches source)
    bf_tap_5.md          # Brewfather-sourced tap 5
    bf_tap_5.webp
  old_beers/             # archive of removed beers (md + image pairs)
  placeholder.(svg|png)  # fallback image when a tap has none
```

### Markdown files (YAML front matter + optional body)
Each tap file stores metadata in front matter; the body holds the description/tasting notes (may be empty).
```markdown
---
name: "West Coast IPA"
abv: 6.8
ibu: 65
ebc: 18
source: "brewfather"        # or "custom"
batch_id: "abc123"           # Brewfather only, used for mapping; omit for custom
image: "bf_tap_5.webp"       # filename only, or null
updated: "2026-06-24T15:30:00+10:00"
---
Bright citrus and pine, dry finish.
```

### Naming conventions
- Manual/custom taps: `custom_tap_X.md` and `custom_tap_X.<ext>`.
- Brewfather taps: `bf_tap_X.md` and `bf_tap_X.<ext>`.
- **Preserve the real image extension** from the source (jpg/png/webp). Do not force `.jpg`.

### Atomic writes and concurrency (required)
Three writers touch these files (the 10-minute sync, the daily cleanup, and admin edits) while the display reads them.
- Every write goes to a temp file in the **same directory**, then `os.replace()` onto the target (atomic rename).
- Guard the sync job and the cleanup job with a lock so they never run concurrently and the display never reads a half-written file.
- Reads must tolerate a file being renamed or missing mid-cycle (catch and skip, never 500 the display).

---

## 3. Brewfather Integration and Sync Logic

> Important: do not assume a single endpoint returns everything. Inspect a real payload and confirm exact field names and units before mapping. Treat the field mapping below as a starting point to verify against the **current** Brewfather API docs, not as gospel.

- **Auth:** Brewfather uses HTTP Basic Auth (User ID as username, API key as password). Confirm and implement accordingly.
- **Two-step fetch:**
  1. List batches via `GET /v2/batches` and filter to `status == "Completed"`.
  2. The list response is typically a trimmed summary. For each completed batch, fetch full detail (via the `include` parameter if supported, otherwise `GET /v2/batches/{id}`) to obtain ABV, IBU, color, tasting notes, batch notes, and the image URL.
- **Tap assignment:** parse the batch notes field for a token of the form `tap:X` (e.g. `tap:3`). Verify whether the field is named `notes`, `batchNotes`, or similar in the live payload.
- **Field mapping (verify names/units against live data):**
  - Name, ABV, IBU, color. Note color may be reported as **EBC or SRM**; if SRM, convert to EBC (`EBC ≈ SRM × 1.97`) and store EBC. Distinguish measured vs estimated values and pick one consistently.
  - Tasting notes -> description/body.
  - Image URL (often called `img_url`, and frequently **null**).
- **Write rule:** if a valid `tap:X` is found AND tap X is **not** a manual override, download the image locally (see image handling) and write `bf_tap_X.md`.
- **Manual override precedence:** the sync must never read from, write to, or archive any tap that is currently a manual override.

### Removal / archive trigger (define by mapping, not by absence)
A Completed batch stays Completed, so "no longer in the payload" rarely fires. Instead, after each successful sync:
- Compute the **desired tap map** from the currently-Completed batches and their `tap:X` tokens.
- For any Brewfather-managed tap whose batch no longer maps to it (status changed away from Completed, the `tap:X` token was removed or changed, or the batch is genuinely gone), archive its `.md` and image to `old_beers/`.
- **Conflict rule:** if two Completed batches both claim the same `tap:X`, the **most recently updated/completed** batch wins. Log a warning that names the conflicting batches.
- If a sync fails (network down, API error), make **no destructive changes**; leave existing cached files intact and record the error (see logging).

### Image handling
- Download to the taps directory using the matching name and the **source extension**.
- If the image URL is null or the download fails, keep any previously cached image; if none exists, use `placeholder`.
- A failed download must never delete an already-good cached image.

---

## 4. Automated Archive Cleanup (daily job)
Scan `old_beers/` once per day:
- **Condition 1 (age):** delete any file older than the configured "Max Archive Age" (days).
- **Condition 2 (size):** if total folder size exceeds "Max Archive Storage Limit" (MB), delete oldest-first by file modification time until under the limit.
- Treat each beer as a **pair**: when deleting by size or age, delete the markdown and its paired image together, and count **both** toward the folder total.
- Use the container timezone (see `TZ`) consistently for the "daily" boundary.

---

## 5. Admin Interface (`/admin`)
- Inputs: Brewfather User ID, Brewfather API Key, "Number of Taps" (integer).
- **Manual Overrides table:** one row per tap (1..X). Each row has a "Manual Override" checkbox. When checked, the user can enter Beer Name, ABV, IBU, EBC, Tasting Notes, and upload a custom image.
  - Saving an override immediately writes `custom_tap_X.md` (+ image) and archives/removes any `bf_tap_X.*` for that slot.
  - Unchecking an override releases the slot back to Brewfather control on the next sync.
- **Cleanup config:** "Max Archive Age" (days, e.g. 180) and "Max Archive Storage Limit" (MB, e.g. 2048).
- **Display toggles:** "Hide Vacant Taps" (see TV display) and an "Announcement Text" field for the bottom ticker.
- **Status panel:** show "Last successful Brewfather sync" timestamp and the last sync error (if any), so an unattended box is debuggable.
- Validate inputs (tap count is a positive integer, numeric fields are numeric). Persist all settings to `config.json` via atomic write.

---

## 6. TV Display Interface (`/`)
Designed for landscape TVs at 1080p and 4K. Content must fit cleanly with **no scrolling**.

### Layout and pagination
- Dark theme, grid of tap cards. Visual reference: clean, high-contrast digital tap boards in the style of **taplist.io** and **tapboard.beer** (large readable type, one card per tap, tap number, beer name, style/description, ABV / IBU / EBC stats, and a color swatch derived from EBC).
- **Maximum 8 cards per page.** If the configured tap count exceeds 8, split across pages and rotate with the carousel. With 8 or fewer, show a single static page.
- Size cards to fill the viewport for the **current page's** card count, so a page of 3 and a page of 8 both look intentional and fill the screen. Use a responsive grid (CSS grid) keyed off the number of cards on the page.

### Tap resolution (priority)
For each tap X: if `custom_tap_X.md` exists, display it; else if `bf_tap_X.md` exists, display it; else the tap is **Vacant**.
- **Hide Vacant Taps** toggle: if ON, omit vacant taps entirely and re-flow the grid (so remaining cards still fill the screen). If OFF, render a styled "Vacant" card.

### Carousel
- Vanilla JS, smooth transition, rotates pages every 30 seconds, only when there is more than one page (more than 8 visible taps).
- The carousel timer must be **independent** of the data-poll timer.

### Auto-refresh without flicker (important)
- Backend exposes `GET /api/board` returning the fully resolved board as JSON: per-tap resolved source, name, ABV, IBU, EBC, computed color hex, description, local image URL, and vacant/hidden flags. The frontend should not parse markdown.
- Frontend polls `/api/board` every 30 seconds, **diffs** the result against the current DOM, and updates **only changed cards in place**. No full-page reload, no full grid re-render.
- Carousel page position must **persist across polls** (do not reset to page 1 on every poll).

### EBC dynamic color (vanilla JS)
- Implement `ebcToHex(ebc)` using an established SRM/EBC-to-RGB reference rather than an invented formula. Convert EBC to SRM (`SRM = EBC / 1.97`), map SRM to a hex color via a reference table with interpolation between known points, and clamp out-of-range values.
- High-EBC beers converge to near-black, so apply a **contrast rule**: choose light or dark text/badge styling based on the swatch's luminance so values stay legible.

### Status ticker
- A static or scrolling banner pinned to the very bottom of the screen, showing the admin "Announcement Text". It must not overlap the grid (reserve its row in the layout).

### Offline robustness
- All images and CSS load from local backend endpoints only. The page must render fully from cache/local server with the WAN down.

---

## 7. Security and Reverse Proxy
- **Admin auth:** protect all `/admin` routes (and admin-mutating API routes) with a cookie/session token gated by an `ADMIN_PASSWORD` environment variable. No database auth.
  - Sign the session token with a separate `SESSION_SECRET` env var so sessions survive container restarts.
  - Set the cookie `HttpOnly`, `SameSite=Strict`, and `Secure` when the request arrived over HTTPS (this is the practical reason to honor `X-Forwarded-Proto`).
  - Add basic **login rate-limiting** (e.g. lock out after N failed attempts for a short window), since `/admin` may be exposed to the internet via the proxy.
- **Reverse proxy headers:** the app sits behind an external Nginx HTTPS reverse proxy and must correctly honor `X-Forwarded-For` and `X-Forwarded-Proto`.
  - Trust the proxy's IP **specifically**, not all clients. Use Uvicorn `--forwarded-allow-ips=<proxy-ip>` (make it an env var). Do not blanket-trust forwarded headers, or a directly-reachable container lets anyone spoof them.
- **Secrets note:** the Brewfather key lives in `config.json` and `ADMIN_PASSWORD`/`SESSION_SECRET` live in env, both plaintext on the host. That is acceptable for this scope, but document it as a conscious choice in the README.

---

## 8. Docker and Ops
- **`Dockerfile`:** small base (e.g. `python:3.12-slim`), no front-end build toolchain in the final image. Run as a **non-root** user.
- **Volume permissions:** running non-root commonly breaks writes to a host-mounted volume owned by root. Handle this with a `PUID`/`PGID` pattern (entrypoint adjusts ownership) or a documented `chown`, so `/data` is writable by the app user.
- **`docker-compose.yml`:**
  - Map a named volume or host path to `/data` for `config.json`, `taps/`, and `old_beers/` persistence.
  - Pass env vars: `ADMIN_PASSWORD`, `SESSION_SECRET`, `TZ`, `FORWARDED_ALLOW_IPS`, and the listen port.
  - Add a **healthcheck** (hit a lightweight endpoint) and `restart: unless-stopped`.
- **Timezone:** honor a `TZ` env var (container defaults to UTC otherwise). This matters for archive timestamps and the "daily" cleanup boundary.
- **Archive filename timestamps:** use a full date-time suffix, e.g. `bf_tap_3_20260624T1530.md`, so a tap that turns over twice in one day does not overwrite its own archive entry.
- **First-run bootstrap:** if `config.json` is missing on startup, create a sensible default (0 or a small tap count, empty credentials, default cleanup limits) so the app boots cleanly with no manual setup.
- **Demo / mock mode:** support a `DEMO_MODE` env var (or a config flag) that populates a few sample taps with bundled placeholder images, so the display can be built, demoed, and screenshotted fully offline with no Brewfather credentials.
- **Logging:** structured logs for sync start/finish, batches found, taps written/archived, conflicts, download failures, cleanup actions, and the last error. This is the difference between a debuggable appliance and a black box.

---

## 9. Deliverables
Provide the full file tree and complete contents for:
1. `Dockerfile` and `docker-compose.yml` with volumes, env vars, healthcheck, and restart policy.
2. The FastAPI backend: markdown file CRUD, image download/caching, the two-step Brewfather sync, the desired-tap-map/archive logic, the daily cleanup job, admin auth, proxy-header handling, the `/api/board` JSON endpoint, and local asset serving.
3. Frontend files for both the TV display (`/`) and the admin interface (`/admin`), in vanilla HTML/CSS/JS, including the `ebcToHex` color function with the contrast rule and the diff-based polling updater.
4. A short `README.md`: env vars, volume mapping, how to run, the Nginx proxy snippet (trusting the proxy IP and forwarding the two headers), and the plaintext-secrets note.

---

## 10. Definition of Done (self-check before finishing)
- The display page makes **zero external-origin requests** (verify in the served HTML).
- With the WAN unplugged, the display still renders the last cached data with no broken images.
- A manual override immediately wins over any Brewfather data for that slot and is never overwritten by sync.
- Two batches claiming the same tap resolve deterministically (newest wins) and log a warning.
- A failed sync makes no destructive file changes.
- Pages never exceed 8 cards; >8 taps paginate via the carousel; polling updates cards without resetting carousel position or reloading the page.
- The container runs as non-root and can still write to `/data`.
- Archive cleanup respects both age and size limits and deletes md+image pairs together.
