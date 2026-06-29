# TV Tap List — Offline-First Digital Beer Tap List

A lightweight, single-container web app that serves a digital beer **tap list**
to TV displays. It syncs from the **Brewfather** API when the internet is up, and
keeps displaying the last cached data — with **zero external requests** — when the
venue's internet is down.

- **Backend:** Python 3.12 + FastAPI + Uvicorn, APScheduler for the two jobs.
- **Frontend:** vanilla HTML/CSS/JS — no frameworks, no build step, no CDNs.
- **Storage:** `config.json` + flat markdown/image files under a Docker volume.
- **The container is the brain;** the TV is a thin client that just loads `/`.

---

## Quick start

```bash
cp .env.example .env          # then edit ADMIN_PASSWORD + SESSION_SECRET
docker compose up -d --build
```

Open:
- **TV display:** `http://<host>:8080/`
- **Admin:** `http://<host>:8080/admin` (log in with `ADMIN_PASSWORD`)

### Offline demo (no Brewfather account needed)

```bash
DEMO_MODE=true docker compose up -d --build
```

On a **fresh** volume this seeds six sample taps with bundled placeholder images
so you can see/screenshot the display fully offline. It never overwrites existing
data.

### Run locally without Docker (development)

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Point DATA_DIR somewhere writable since /data won't exist locally:
DATA_DIR=./data ADMIN_PASSWORD=test SESSION_SECRET=dev DEMO_MODE=true \
  uvicorn app.main:app --reload --port 8080
```

---

## Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `ADMIN_PASSWORD` | **yes** | — | Password for `/admin`. Admin is denied if unset. |
| `SESSION_SECRET` | recommended | derived from password | Signs session cookies so logins survive restarts. |
| `TZ` | no | UTC | IANA timezone. Drives archive timestamps + the daily cleanup boundary. |
| `FORWARDED_ALLOW_IPS` | yes (behind proxy) | `127.0.0.1` | IP(s) of the trusted reverse proxy allowed to set `X-Forwarded-*`. |
| `PORT` | no | `8080` | Listen port inside the container. |
| `PUID` / `PGID` | no | `1000` | Host uid/gid that owns `/data`, so the non-root app can write. |
| `DEMO_MODE` | no | `false` | Seed sample taps on a fresh volume. |
| `BREWFATHER_USER_ID` | no | — | Brewfather User ID (overrides config; keeps it off disk). |
| `BREWFATHER_API_KEY` | no | — | Brewfather API key (overrides config; keeps it off disk). |
| `SYNC_INTERVAL_MINUTES` | no | `15` | Minutes between Brewfather syncs. |
| `DATA_DIR` | no | `/data` | Data root (override for local dev). |

Brewfather **User ID** and **API Key** can be entered in the admin UI *or* set
via the `BREWFATHER_*` env vars. When both env vars are set, the admin fields
are locked ("managed via environment") and the key is **never written to
`config.json`** — the recommended way to keep the secret off disk.

---

## Volume mapping

Everything persistent lives under `/data`:

```
/data
  config.json          # settings + sync status
  placeholder.svg      # fallback image (seeded from the image; replaceable)
  taps/                # current beers: custom_tap_X.md / bf_tap_X.md (+ images)
  old_beers/           # archived md+image pairs (datetime-suffixed)
```

The compose file uses a named volume `taplist_data`. To inspect data from the
host, swap it for a bind mount in `docker-compose.yml`:

```yaml
    volumes:
      - ./data:/data
```

…and set `PUID`/`PGID` to your host user (`id -u` / `id -g`) so writes succeed.

---

## How it works

### Brewfather sync (every `SYNC_INTERVAL_MINUTES`, default 15)
1. Lists Completed batches with `complete=True` + pagination (`limit=50`,
   `start_after`), returning **full batch data in one call per page** (HTTP
   Basic Auth: User ID / API key).
2. Reads a `tap:X` token from the batch notes to assign a tap.
3. Writes `bf_tap_X.md` (+ downloaded image, preserving the source extension).
4. Builds the **desired tap map** and archives any Brewfather tap that no longer
   maps to its slot.

- **API-friendly:** Brewfather's limit is **500 calls/hour per key**. Using
  `complete=True` avoids the old N+1 (a detail call per batch), so each sync
  costs only `ceil(completed_batches / 50)` calls. **Change-detection** (a stored
  batch revision) skips image re-downloads and file rewrites for unchanged
  beers, and a **429** is reported (honouring `Retry-After`) with no changes.
- **Manual overrides win and are never touched** by sync.
- **Conflicts** (two Completed batches claiming one tap) resolve to the most
  recently updated batch and log a warning.
- A **failed sync makes no destructive changes** — the last good cache stays.

> **Field-mapping caveat:** Brewfather's exact field names/units should be
> verified against a live payload. `app/brewfather.py` maps defensively (trying
> several field names, preferring *measured* over *estimated*, and handling
> EBC vs SRM) and logs what it found. Adjust there if your account differs.

### Daily archive cleanup (03:30 local)
- Deletes archived beers older than **Max Archive Age** (days).
- If `old_beers/` still exceeds **Max Archive Storage Limit** (MB), deletes
  oldest-first until under the limit.
- Each beer is treated as a **pair** (markdown + image) deleted together.

### TV display (`/`)
- Dark, high-contrast card grid; up to **8 cards per page**; >8 taps paginate
  and rotate every 30 s via a carousel.
- Polls `GET /api/board` every 30 s and **diffs** the result, updating only
  changed cards in place — no full reload, and the carousel position persists.
- `ebcToHex()` maps EBC → colour via the SRM reference chart with a luminance
  contrast rule for a legible colour swatch (the swatch is colour-only; the
  numeric value lives in the stats).
- A bottom **ticker** shows the announcement text without overlapping the grid.
- An optional **venue/company logo** sits at the top (height configurable up to
  a third of the screen; reserved row so it never overlaps the grid).

### Display options (admin)
- **Colour unit:** show colour as **EBC or SRM** (stored as EBC; admin colour
  input and the display both follow the selected unit).
- **Per-stat visibility:** for ABV, IBU and Colour independently — a **Show**
  toggle (hide that stat for every beer) and a **Hide when empty** toggle (drop
  it only for beers missing that value).

---

## Reverse proxy (Nginx)

The app expects to sit behind an external Nginx HTTPS proxy. Trust the proxy IP
**specifically** via `FORWARDED_ALLOW_IPS` (don't blanket-trust forwarded
headers, or a directly-reachable container could be spoofed).

```nginx
server {
    listen 443 ssl http2;
    server_name taps.example.com;

    ssl_certificate     /etc/ssl/certs/taps.crt;
    ssl_certificate_key /etc/ssl/private/taps.key;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;

        # The two headers the app honours:
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;   # lets the app set Secure cookies
        proxy_set_header   Host              $host;
    }
}
```

Set `FORWARDED_ALLOW_IPS` to the proxy's IP **as seen by the container**. With
the Docker bridge network this is usually the gateway, e.g. `172.17.0.1`; for a
proxy on the same host using `network_mode: host` it's `127.0.0.1`.

> Tip: you can lock the admin down further by only proxying `/admin` from an
> internal location/VPN, while exposing `/` publicly.

---

## Security notes

- **Admin auth** is a signed, `HttpOnly`, `SameSite=Strict` session cookie
  (`Secure` when the request arrived over HTTPS). Login is **rate-limited**
  (5 failures / 5 min per client IP).
- **Plaintext secrets — a conscious trade-off for this appliance scope:** the
  Brewfather key is stored in `/data/config.json`, and `ADMIN_PASSWORD` /
  `SESSION_SECRET` are passed as environment variables. Both are plaintext on
  the host. This is acceptable for a small on-prem appliance but means anyone
  with host/file access can read them. Protect the host accordingly (file
  permissions, restricted SSH) and rotate the API key if the box is exposed.

---

## Testing

**Unit / integration suite (pytest)** — 56 tests covering colours, atomic
storage, config, board resolution, the Brewfather sync (conflict resolution,
override precedence, archive logic, "failed sync = no destructive changes"),
archive cleanup (age + size, paired deletion), and the HTTP/admin surface:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

**Container test** — builds the image, runs it in `DEMO_MODE` on a fresh volume,
waits for the healthcheck, and asserts health, demo board data, zero external
origins, the `/admin` redirect, **non-root PID 1 + writable `/data`**, and the
Docker healthcheck:

```bash
# Linux / WSL with Docker:
bash scripts/docker_test.sh
```

(The same checks pass via Docker Desktop on Windows against `docker compose`.)

---

## Offline guarantees (verify before going live)

- The served display HTML references **only local origins** — no `http(s)://`
  third-party hosts (system fonts, local CSS/JS/images).
- With the WAN unplugged, the display keeps rendering the last cached data and
  shows **no broken images** (missing files fall back to the placeholder).
- A failed sync leaves all cached files intact.
