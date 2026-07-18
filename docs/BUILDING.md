# Building TV Tap List from source

The recommended way to run TV Tap List is to pull the prebuilt image
(`ghcr.io/jceccato/tv-taplist:latest`) -- see [INSTALLATION.md](INSTALLATION.md).
Building from source is an advanced option for when you want to:

- Run the bleeding edge (unreleased `main`).
- Customise the application code or static assets.
- Build for an architecture without a prebuilt image.
- Develop and test changes locally.

## Building with Docker Compose

The repo ships with a `docker-compose.yml` that defaults to pulling the prebuilt
image. To build locally instead:

### 1. Clone the repo

```bash
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist
```

### 2. Configure your environment

```bash
cp .env.example .env
# Edit .env: set ADMIN_PASSWORD and SESSION_SECRET at minimum.
# SESSION_SECRET -- generate with:  openssl rand -hex 32
```

See [INSTALLATION.md -> Environment variables](INSTALLATION.md#environment-variables)
for the full set.

### 3. Switch the compose file to build

In `docker-compose.yml`, comment out the `image:` line and uncomment `build: .`:

```yaml
services:
  taplist:
    # image: ghcr.io/jceccato/tv-taplist:latest
    build: .
```

### 4. Build and start

```bash
docker compose up -d --build
```

The first build installs Python dependencies and copies the app code into the
image. Rebuild when you change Python code, templates, or static assets (CSS/JS).

## Guided installer (building from source)

The interactive installer (`scripts/setup.sh`) runs from inside a checkout and
honours whatever `docker-compose.yml` says. Switch the compose file to `build: .`
before running it, and it will build instead of pull:

```bash
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist
# Edit docker-compose.yml: comment `image:`, uncomment `build: .`
./scripts/setup.sh
```

## Updating a source build

```bash
cd tv-taplist
git pull
docker compose up -d --build
```

Your data directory and `.env` are untouched. After updating, hard-refresh the
TV's browser once so it picks up new CSS/JS instead of its cached copies.

## Building on Unraid

Use **Compose Manager** (Apps -> Community Applications -> Compose Manager):

```bash
# In the Unraid terminal:
git clone https://github.com/jceccato/tv-taplist.git /mnt/user/appdata/tv-taplist-src
cd /mnt/user/appdata/tv-taplist-src
cp .env.example .env
nano .env   # set ADMIN_PASSWORD, SESSION_SECRET, TZ, PUID=99, PGID=100,
            # and DATA_DIR_HOST=/mnt/user/appdata/tv-taplist
# Switch docker-compose.yml to build: .
docker compose up -d --build
```

Or add it as a managed stack: **Docker -> Compose -> Add New Stack**, name it
`tv-taplist`, set its directory to `/mnt/user/appdata/tv-taplist-src`, then
**Compose Up**. See [UNRAID.md](UNRAID.md) for the full Unraid walkthrough
(which prefers the prebuilt image for simplicity).

## Rebuilding after editing assets

CSS and JavaScript under `static/` are baked into the image at build time. After
changing them, you need a rebuild:

```bash
docker compose up -d --build
```

Static assets are cache-busted by mtime (`?v=<mtime>`), so the TV picks up new
versions on its next normal poll without a manual hard-refresh.

If you only changed Python in `app/`, a rebuild is also needed -- the `COPY app
./app` layer in the Dockerfile gets invalidated.

## Local development without Docker

For quick iteration on the app logic, you can run directly on the host:

```bash
# Install deps (once):
python -m pip install --user -r requirements.txt -r requirements-dev.txt

# Run with auto-reload:
DATA_DIR=./data ADMIN_PASSWORD=test SESSION_SECRET=dev DEMO_MODE=true \
  uvicorn app.main:app --reload --port 8080
```

- **Display:** <http://localhost:8080/>
- **Admin:** <http://localhost:8080/admin> (open -- `DEMO_MODE=true` with no password)

Uvicorn's `--reload` watches Python files but is not perfectly reliable; restart
manually for app-logic changes. Install dev dependencies (`-r requirements-dev.txt`)
to run the test suite:

```bash
python -m pytest -q
```

127 tests cover colours, beer-glass rendering, themes, storage, config coercion,
board resolution, Brewfather sync, cleanup, the admin HTTP surface, and a
server↔client constant-drift guard.
