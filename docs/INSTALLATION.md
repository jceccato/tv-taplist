# Installing TV Tap List

Everything you need to get the tap list running, from a 30-second demo to a
production box behind HTTPS. For *how it all works* once it's running, see
[FAQ.md](FAQ.md).

**Contents**

- [Prerequisites](#prerequisites)
- [Demo (no account needed)](#demo-no-account-needed)
- [Guided installer (recommended)](#guided-installer-recommended)
- [Manual Docker Compose](#manual-docker-compose)
- [Unraid](#unraid)
- [Windows (Docker Desktop)](#windows-docker-desktop)
- [Getting your Brewfather API key](#getting-your-brewfather-api-key)
- [Putting beers on the board](#putting-beers-on-the-board)
- [Environment variables](#environment-variables)
- [The data directory](#the-data-directory)
- [Reverse proxy (HTTPS)](#reverse-proxy-https)
- [Updating](#updating)
- [First-run checklist](#first-run-checklist)

---

## Prerequisites

- A host that runs **Docker** with the **Compose** plugin (Linux, a Raspberry Pi,
  a NUC, Unraid, a VM - anything Docker supports). Install Docker from
  <https://docs.docker.com/engine/install/>.
- A **TV or screen** with a browser you can put into kiosk / full-screen mode and
  point at the host.
- A **[Brewfather](https://brewfather.app)** account (for the real thing; the demo
  needs none).

---

## Demo (no account needed)

One command pulls the image and runs a self-contained demo with six sample beers:

```bash
docker run -d --name tv-taplist-demo -p 8080:8080 \
  -e DEMO_MODE=true \
  ghcr.io/jceccato/tv-taplist:latest
```

- **Display:** <http://localhost:8080/>
- **Admin:** <http://localhost:8080/admin> (open - no login)

In demo mode with **no `ADMIN_PASSWORD`**, the admin is intentionally open so the
demo is genuinely one command. **Set `ADMIN_PASSWORD` the moment you expose the box
to anyone** - the instant a password is set, normal login applies again.

Remove it with `docker rm -f tv-taplist-demo`. The demo is for evaluation only --
use a real install below for anything that stays up.

---

## Guided installer (recommended)

One command from any directory. It asks a few questions, writes your `.env`,
installs Docker if needed, pulls the image, and starts the container.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jceccato/tv-taplist/main/setup)
```

Or if you prefer the long way:

```bash
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist
bash scripts/setup.sh
```

It prompts for:

- **Admin password** (or generates one for you).
- **Timezone, host port, data directory, PUID/PGID** - sensible defaults are
  detected from your system.
- **Brewfather User ID + API key** - optional; you can skip and add them in `/admin`
  later. (See [Getting your Brewfather API key](#getting-your-brewfather-api-key).)

It generates a strong `SESSION_SECRET`, writes a `.env` (owner-readable only), and
offers to pull and start the container. When it finishes, open
`http://<host>:<port>/admin`, set your **tap count**, and click **Sync Brewfather
now**.

Run the same one-liner any time to change settings - it detects your existing
directory and prefills from your `.env`. The installer pulls the prebuilt image by
default; if you want to build from source instead, see [BUILDING.md](BUILDING.md).

---

## Manual Docker Compose

Prefer to do it by hand:

```bash
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist
cp .env.example .env
# Edit .env: set ADMIN_PASSWORD and SESSION_SECRET at minimum.
#   SESSION_SECRET - generate with: openssl rand -hex 32
docker compose up -d
```

The compose file pulls `ghcr.io/jceccato/tv-taplist:latest` by default. Key `.env`
values are documented inline and in [Environment variables](#environment-variables).

### Which image tag to use

| Tag | What you get |
|-----|--------------|
| `:latest` | The newest release. The default, and what you want. |
| `:v1.3.0` | One exact release, pinned. It never moves, so an update is always a deliberate edit. |
| `:main` | Unreleased work, rebuilt on every merge. For trying a change before it is released - expect rough edges. |

`:latest` and the in-app update checker agree: both move only when a release is
published, so the version the admin panel reports is the version `:latest`
serves. An image built from `:main` reports its version as `main`, and the admin
panel says plainly that an untagged build cannot be compared against releases.

To build from source instead, see [BUILDING.md](BUILDING.md).

The compose file maps `${DATA_DIR_HOST:-./taplist_data}` on the host to `/data` in
the container - that host folder is where your beers live (see [The data
directory](#the-data-directory)).

---

## Unraid

The essentials:

| Setting | Value | Why |
|---------|-------|-----|
| `PUID` / `PGID` | **99** / **100** | Standard Unraid `appdata` owner so `/data` stays writable. |
| Data path | `/mnt/user/appdata/tv-taplist` -> `/data` | Where your beers persist. |
| Port | `8080` (host) -> `8080` (container) | The web UI. |
| `ADMIN_PASSWORD`, `SESSION_SECRET` | *yours* | Required. Generate the secret with `openssl rand -hex 32`. |

The recommended Unraid path is a **Docker template** pointing at the prebuilt
image. Building from source with **Compose Manager** is also supported. The full
step-by-step -- plugin install, a ready-to-paste template XML, and the
reverse-proxy notes for SWAG / Nginx Proxy Manager -- is in
**[UNRAID.md](UNRAID.md)**, and source-build details are in
**[BUILDING.md](BUILDING.md)**.

---

## Windows (Docker Desktop)

The standard Compose path above works on Docker Desktop for Windows. The essentials:

| Setting | Value | Why |
|---------|-------|-----|
| `DATA_DIR_HOST` | `C:\taplist\data` (or `C:/taplist/data`) | An absolute Windows path, or a relative one starting `./`. A bare name with neither is read as a volume name and Compose refuses to start. |
| `PUID` / `PGID` | leave at `1000` | A Windows-filesystem bind mount carries no Linux ownership. Docker Desktop presents it as writable to any uid, so these values change nothing there. |
| File sharing | nothing to configure on the WSL2 backend | On the Hyper-V backend, add the folder under **Settings -> Resources -> File sharing** first. |
| Port | `8080` (host) -> `8080` (container) | Allow it through Windows Defender Firewall on **Private** networks so the TV can reach it. |

Before entering real settings, confirm the mapped data directory is genuinely a
bind mount: `docker inspect tv-taplist --format '{{json .Mounts}}'` reports
`"Type":"bind"`, and `config.json` plus `taps\` appear in the folder on the Windows
side. When `/data` is not mapped to a host folder nothing errors - the board works,
then loses manual beers and settings the next time the container is recreated,
while Brewfather beers rebuild themselves on the next sync.

The full step-by-step - path forms, the persistence check, PUID/PGID, WSL2 versus
Windows filesystem placement, and reaching the display from the TV - is in
**[WINDOWS.md](WINDOWS.md)**.

---

## Getting your Brewfather API key

1. In Brewfather, go to **Settings > Integration > Generate API-Key**.
2. Give the key at least the **Read Batches** scope (add **Read Recipes** too
   so colour/recipe fields are available).
3. Copy your **User ID** and the **API key**.
4. Provide them either:
   - in the installer / `.env` as `BREWFATHER_USER_ID` and `BREWFATHER_API_KEY`
     (kept off disk - never written to `config.json`), **or**
   - in `/admin` -> **Settings -> Brewfather** (stored in `config.json`).

When both env vars are set, the admin fields lock and show *"managed via
environment"* - the recommended way to keep the key off disk.

---

## Putting beers on the board

The board shows Brewfather batches you mark as on tap:

1. Open the **batch** for the beer in Brewfather.
2. In the batch's **Batch Notes** field, add `tap:N` where `N` is the tap number it's pouring on.
3. Set the batch **status to Completed**.

On the next sync (every `SYNC_INTERVAL_MINUTES`, or click **Sync Brewfather now**)
the beer appears on tap `N`. **Completed** batches sync by default - Planning,
Brewing, Fermenting and Archived batches are ignored, so works-in-progress never
show up by accident. Two checkboxes on the admin Settings tab widen that: tick
**Include Conditioning batches** for a beer that's on tap but still **Conditioning**
(lagering / maturing), and **Include Fermenting batches** for one still in
**Fermenting** (primary). They work independently, and a batch still needs its
`tap:N` note either way.

You can drive the swatch and glass straight from the **Batch Notes** field with
extra tokens:

| Token | Effect |
|-------|--------|
| `tap:3` | Assign this batch to **tap 3** (required for it to appear). |
| `colour:#780606` | Force an exact swatch + glass colour. `color:` also works. |
| `glass:willibecher` | Glass silhouette: `willibecher` (the default), `nonicpint`, `default` (the shaker pint - a historical key name), `schooner`, `tulip`, `teku`, `dimpledmug`. |
| `saturation:60` | Mute the colour to 60 % (a percentage, or a `0`–`1` fraction). |

**Tasting notes** go in the **Taste Notes** field on the batch's Completed tab
(in the Taste section below the rating). That text syncs 1:1 to the card
description. Batch Notes text is never shown on the card - it is only scanned for
the tokens above, which are stripped from anything that reaches the display.

The same controls - plus beers Brewfather doesn't know about - are available in
`/admin` -> **Manual overrides**. More detail in [FAQ.md](FAQ.md#brewfather-sync).

---

## Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `ADMIN_PASSWORD` | **yes** | - | Password for `/admin`. Admin is denied if unset. |
| `SESSION_SECRET` | recommended | derived from password | Signs session cookies so logins survive restarts. |
| `TZ` | no | `UTC` | IANA timezone. Drives archive timestamps + the daily cleanup boundary. |
| `PORT` | no | `8080` | Host port for the web UI. |
| `DATA_DIR_HOST` | no | `./taplist_data` | Host directory mapped to `/data`. |
| `FORWARDED_ALLOW_IPS` | yes (behind proxy) | `127.0.0.1` | IP(s) of the trusted reverse proxy allowed to set `X-Forwarded-*`. |
| `PUID` / `PGID` | no | `1000` | Host uid/gid that owns the data directory, so the non-root app can write. |
| `DEMO_MODE` | no | `false` | Seed sample taps on a fresh data directory. |
| `BREWFATHER_USER_ID` | no | - | Brewfather User ID (overrides config; keeps it off disk). |
| `BREWFATHER_API_KEY` | no | - | Brewfather API key (overrides config; keeps it off disk). |
| `SYNC_INTERVAL_MINUTES` | no | `15` | Minutes between Brewfather syncs. |

---

## The data directory

The container keeps **everything persistent in plain files** under the host folder
you map to `/data` (`DATA_DIR_HOST`, default `./taplist_data`). Map it to a real
host path so an admin can read and edit it directly:

```
taplist_data/
  config.json        # your settings (tap count, theme, Brewfather key)
  status.json        # sync + update-check timestamps, written by the app
  placeholder.svg    # fallback image (replaceable)
  taps/              # current beers: custom_tap_N.md / bf_tap_N.md (+ images)
  old_beers/         # archived beers (markdown + image pairs)
```

Each tap is a small Markdown file with front-matter (name, ABV, IBU, colour, …)
and a body of tasting notes - open one in any text editor to see exactly what the
board will show. `status.json` is written by the app rather than by you, and is
safe to delete: every value in it is regenerated on the next sync and the next
daily update check. Set `PUID`/`PGID` to the host user that should own these files so
the non-root container can write them.

To copy this whole directory off the box - before an upgrade, or to move to new
hardware - use the **Snapshot** tab in `/admin`. It downloads a zip that mirrors
the layout above, so restoring it is either an import on the same tab or simply
unzipping it back into the data directory of a stopped container. See
[Snapshots](FAQ.md#snapshots-getting-your-board-off-the-box-and-back-on) in the
FAQ for what a Snapshot carries, what it deliberately leaves out, and the one
question the import asks about Brewfather.

---

## Reverse proxy (HTTPS)

Direct LAN access on `:8080` needs nothing extra. To serve it over HTTPS, put it
behind an Nginx (or SWAG / Nginx Proxy Manager) reverse proxy and trust **only**
that proxy's IP:

```nginx
server {
    listen 443 ssl http2;
    server_name taps.example.com;

    ssl_certificate     /etc/ssl/certs/taps.crt;
    ssl_certificate_key /etc/ssl/private/taps.key;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;   # lets the app set Secure cookies
        proxy_set_header   Host              $host;
    }
}
```

Set **`FORWARDED_ALLOW_IPS`** to the proxy's IP **as the container sees it** (often
the Docker gateway `172.17.0.1`, or `127.0.0.1` for a host-network proxy). Never
use `*` - that trusts forwarded headers from anywhere and defeats the spoofing
protection. You can also expose only `/` publicly and keep `/admin` on the LAN/VPN.

---

## Updating

```bash
cd tv-taplist
git pull
docker compose pull
docker compose up -d
```

Your data directory and `.env` are untouched. The TV picks up new CSS/JS on its
next poll (assets are cache-busted by mtime) so no manual hard-refresh is needed.

If you would rather have a copy in hand first, download a Snapshot from the
**Snapshot** tab in `/admin` before you pull.

If you are building from source instead of pulling the prebuilt image, see
[BUILDING.md](BUILDING.md) for the update procedure.

---

## First-run checklist

1. Open `http://<host>:<port>/` - you should see the display (empty taps, or demo
   beers if `DEMO_MODE=true`).
2. Open `/admin`, log in, enter your Brewfather **User ID + API key** (or set them
   as env vars), set the **tap count**, and click **Sync Brewfather now**.
3. Mark your on-tap batches **Completed** with a `tap:N` note (see [Putting beers
   on the board](#putting-beers-on-the-board)).
4. Point the TV's browser at `http://<host>:<port>/` in kiosk / full-screen.
   For a dedicated, auto-launching display see:
   - [Raspberry Pi Kiosk](RASPBERRY_PI_KIOSK.md) - Pi plugged into the TV via HDMI.
   - [Android Kiosk](ANDROID_KIOSK.md) - tablet, Android TV, Chromecast, or Fire Stick.

If `/data` won't persist, it's almost always a `PUID`/`PGID` mismatch with the host
owner of the data directory.
