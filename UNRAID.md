# Running TV Tap List on Unraid

Two supported ways to run this on an Unraid server:

- **[Option A — Compose Manager](#option-a--compose-manager-build-from-source)** —
  build the image from the repo on the Unraid box. Closest to the bundled
  `docker-compose.yml`; no external registry needed.
- **[Option B — Docker template](#option-b--docker-template-prebuilt-image)** —
  point Unraid's **Add Container** at a prebuilt GHCR image
  (`ghcr.io/OWNER/tv-taplist`). No building on the server.

Both end up at the same place: the admin at `http://<tower-ip>:8080/admin` and the
TV display at `http://<tower-ip>:8080/`.

---

## Unraid essentials (read once)

| Setting | Unraid value | Why |
|---------|--------------|-----|
| `PUID` | **99** (`nobody`) | The container's entrypoint chowns `/data` to this uid so the non-root app can write. 99/100 is the standard Unraid owner for `appdata`. |
| `PGID` | **100** (`users`) | Matching gid for `appdata`. |
| Data path | `/mnt/user/appdata/tv-taplist` → `/data` | Persists `config.json`, `taps/`, `old_beers/` on the array. |
| Port | `8080` (host) → `8080` (container) | The web UI. Change the host side if 8080 is taken. |
| `TZ` | e.g. `Australia/Sydney` | Archive timestamps + the 03:30 daily cleanup boundary. |

Secrets you must set: **`ADMIN_PASSWORD`** and **`SESSION_SECRET`** (a long random
string — generate one in the Unraid terminal with `openssl rand -hex 32`).

---

## Option A — Compose Manager (build from source)

This uses the repo's own `docker-compose.yml`, which builds the image locally.

### 1. Install the plugin
**Apps** (Community Applications) → search **Compose Manager** → Install. This
adds the `docker compose` binary and a **Docker → Compose** UI.

### 2. Get the code and configure it
Open the Unraid **terminal** (`>_` in the top bar):

```bash
# Clone into a share (keep the build context on the array, not on the flash/boot):
git clone https://github.com/OWNER/tv-taplist.git /mnt/user/appdata/tv-taplist-src
cd /mnt/user/appdata/tv-taplist-src

# Secrets + ops, from the template:
cp .env.example .env
nano .env          # set ADMIN_PASSWORD, SESSION_SECRET, TZ, PUID=99, PGID=100
```

In `.env` set at least:

```ini
ADMIN_PASSWORD=your-strong-password
SESSION_SECRET=<paste output of: openssl rand -hex 32>
TZ=Australia/Sydney
PUID=99
PGID=100
# If you put a reverse proxy in front, set this to the proxy's IP (see below).
FORWARDED_ALLOW_IPS=127.0.0.1
```

Point the data directory at `appdata` so it's separate from the source checkout —
add this to `.env` (the compose file maps `DATA_DIR_HOST` to `/data`):

```ini
DATA_DIR_HOST=/mnt/user/appdata/tv-taplist
```

### 3. Bring it up
Either from the terminal in that directory:

```bash
docker compose up -d --build
# First run only, to seed demo taps and verify it works offline:
#   DEMO_MODE=true docker compose up -d --build
```

…or add it as a managed stack: **Docker → Compose → Add New Stack**, name it
`tv-taplist`, set its directory to `/mnt/user/appdata/tv-taplist-src`, then
**Compose Up**. The container then appears on the **Docker** tab like any other.

### 4. Updating later
```bash
cd /mnt/user/appdata/tv-taplist-src
git pull
docker compose up -d --build
```

---

## Option B — Docker template (prebuilt image)

Use this if a prebuilt GHCR image is available (e.g.
`ghcr.io/OWNER/tv-taplist:latest`). No building on the server, and Unraid shows an
**update ready** badge when the image changes. Replace `OWNER` throughout with the
owner of the image you're pulling.

### Add the container by hand
**Docker → Add Container**, switch the toggle to **Advanced View**, then:

- **Name:** `tv-taplist`
- **Repository:** `ghcr.io/OWNER/tv-taplist:latest`
- **Network Type:** `bridge`
- **Port:** add → Name `WebUI`, Container Port `8080`, Host Port `8080`, TCP
- **Path:** add → Container Path `/data`, Host Path
  `/mnt/user/appdata/tv-taplist`, Access `Read/Write`
- **Variables** (add one each):
  | Name | Value | Notes |
  |------|-------|-------|
  | `ADMIN_PASSWORD` | *your password* | required |
  | `SESSION_SECRET` | *random 32+ chars* | required |
  | `TZ` | `Australia/Sydney` | your timezone |
  | `PUID` | `99` | |
  | `PGID` | `100` | |
  | `FORWARDED_ALLOW_IPS` | `127.0.0.1` | set to proxy IP if proxied |
  | `BREWFATHER_USER_ID` | *(optional)* | keeps the key off disk |
  | `BREWFATHER_API_KEY` | *(optional)* | keeps the key off disk |
  | `SYNC_INTERVAL_MINUTES` | `15` | optional |
  | `DEMO_MODE` | `false` | `true` to seed a demo on a fresh data directory |

Click **Apply**. Unraid pulls the image and starts it.

### Or drop in a template file
Save the XML below as
`/boot/config/plugins/dockerMan/templates-user/my-tv-taplist.xml` (replace
`OWNER`). It then shows up under **Add Container → Template:
tv-taplist** with all fields pre-filled:

```xml
<?xml version="1.0"?>
<Container version="2">
  <Name>tv-taplist</Name>
  <Repository>ghcr.io/OWNER/tv-taplist:latest</Repository>
  <Registry>https://github.com/OWNER/tv-taplist/pkgs/container/tv-taplist</Registry>
  <Network>bridge</Network>
  <Privileged>false</Privileged>
  <Overview>Offline-first digital beer tap list for TVs. Syncs from Brewfather when online and keeps serving the last cached data when the internet is down.</Overview>
  <Category>Other: HomeAutomation:</Category>
  <WebUI>http://[IP]:[PORT:8080]/</WebUI>
  <ExtraParams/>
  <Config Name="WebUI Port" Target="8080" Default="8080" Mode="tcp" Type="Port" Display="always" Required="true">8080</Config>
  <Config Name="Data (/data)" Target="/data" Default="/mnt/user/appdata/tv-taplist" Mode="rw" Type="Path" Display="always" Required="true">/mnt/user/appdata/tv-taplist</Config>
  <Config Name="ADMIN_PASSWORD" Target="ADMIN_PASSWORD" Default="" Type="Variable" Display="always" Required="true" Mask="true"/>
  <Config Name="SESSION_SECRET" Target="SESSION_SECRET" Default="" Type="Variable" Display="always" Required="true" Mask="true"/>
  <Config Name="TZ" Target="TZ" Default="Australia/Sydney" Type="Variable" Display="always" Required="true">Australia/Sydney</Config>
  <Config Name="PUID" Target="PUID" Default="99" Type="Variable" Display="advanced" Required="true">99</Config>
  <Config Name="PGID" Target="PGID" Default="100" Type="Variable" Display="advanced" Required="true">100</Config>
  <Config Name="FORWARDED_ALLOW_IPS" Target="FORWARDED_ALLOW_IPS" Default="127.0.0.1" Type="Variable" Display="advanced" Required="false">127.0.0.1</Config>
  <Config Name="BREWFATHER_USER_ID" Target="BREWFATHER_USER_ID" Default="" Type="Variable" Display="advanced" Required="false"/>
  <Config Name="BREWFATHER_API_KEY" Target="BREWFATHER_API_KEY" Default="" Type="Variable" Display="advanced" Required="false" Mask="true"/>
  <Config Name="SYNC_INTERVAL_MINUTES" Target="SYNC_INTERVAL_MINUTES" Default="15" Type="Variable" Display="advanced" Required="false">15</Config>
  <Config Name="DEMO_MODE" Target="DEMO_MODE" Default="false" Type="Variable" Display="advanced" Required="false">false</Config>
</Container>
```

---

## First-run check

1. Browse to `http://<tower-ip>:8080/` — you should see the display (empty taps,
   or demo beers if you set `DEMO_MODE=true`).
2. Go to `/admin`, log in with `ADMIN_PASSWORD`, enter your Brewfather **User ID**
   + **API key** (or set them as env vars), set the **tap count**, and hit
   **Sync Brewfather now**.
3. Point the TV's browser at `http://<tower-ip>:8080/` in kiosk/full-screen.

If `/data` ever fails to persist, it's almost always a `PUID`/`PGID` mismatch —
confirm they're **99/100** and that the host path is `/mnt/user/appdata/tv-taplist`.

---

## Reverse proxy (SWAG / Nginx Proxy Manager)

Direct LAN access on `:8080` needs nothing extra. To expose it over HTTPS with a
proxy already running on Unraid:

- Put the proxy and this container on the **same custom Docker network** and proxy
  to `http://tv-taplist:8080`.
- Set **`FORWARDED_ALLOW_IPS`** to the **proxy container's IP** as seen by this
  container (find it on the Docker tab, or `docker inspect <proxy> | grep IPAddress`).
  The app only honours `X-Forwarded-*` headers from that IP, so HTTPS cookies work
  without trusting spoofed headers. See the README **Reverse proxy** section for
  the Nginx `location` block (the same headers apply in SWAG/NPM advanced config).
- Optionally expose only `/` publicly and keep `/admin` on the LAN/VPN.

> Avoid `FORWARDED_ALLOW_IPS=*` — it trusts forwarded headers from anywhere, which
> defeats the spoofing protection.
