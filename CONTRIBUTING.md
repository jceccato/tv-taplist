# Contributing to TV Tap List

Thanks for your interest in contributing. This guide will help you set up the
dev environment, run the tests, and submit changes.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you agree to uphold its terms.

---

## Development environment

### Prerequisites

- **Python 3.10+** (the project targets 3.12 in Docker; 3.10 works for local dev).
- **Docker** with the Compose plugin (for container testing).
- **Git**

### Setup

```bash
# Clone the repo
git clone https://github.com/jceccato/tv-taplist.git
cd tv-taplist

# Install dependencies (app + dev)
python -m pip install --user -r requirements.txt -r requirements-dev.txt
```

No virtual environment is strictly required for local dev, but if you prefer one:

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  (Windows)
pip install -r requirements.txt -r requirements-dev.txt
```

### Running locally (no Docker)

```bash
# Unix-like (Linux, macOS, WSL):
DATA_DIR=./data ADMIN_PASSWORD=test SESSION_SECRET=dev DEMO_MODE=true \
  uvicorn app.main:app --reload --port 8080

# Windows PowerShell:
$env:DATA_DIR="./data"; $env:ADMIN_PASSWORD="test"; $env:SESSION_SECRET="dev"
$env:DEMO_MODE="true"; uvicorn app.main:app --reload --port 8080
```

- **Display:** <http://localhost:8080/>
- **Admin:** <http://localhost:8080/admin> (open in demo mode with no password)

> **Note:** Uvicorn's `--reload` flag watches Python files but is not 100%
> reliable on all platforms. Restart the server manually if app-logic changes
> aren't picked up.

### Running with Docker

```bash
cp .env.example .env   # edit secrets
docker compose up -d --build
```

Changing static assets (CSS/JS) requires a rebuild (`docker compose up -d --build`)
to copy them into the image.

---

## Testing

### Python test suite

```bash
# Run all tests (from repo root)
python -m pytest -q

# Run a single test file
python -m pytest tests/test_colors.py -q

# Run a specific test
python -m pytest tests/test_colors.py::test_ebc_to_hex -q
```

The suite has **127 tests** across 10 files covering colours, beer-glass rendering,
themes, storage, config coercion, board resolution, Brewfather sync (conflicts,
override precedence, token parsing, OG/FG, archive, Conditioning-status selection,
image-client credential isolation), cleanup, the HTTP/admin surface (preview-color,
passwordless demo admin, `/img` SVG-CSP, upload caps, validate-before-write, board
sync-status omission), and a server to client constant-drift guard.

Tests use `conftest.py` fixtures that point `DATA_DIR` at a temporary directory
per test, so they never touch real data.

### Docker container test

```bash
# Requires Docker. On WSL:
bash scripts/docker_test.sh
```

This builds the image, runs it in `DEMO_MODE` on a fresh volume, and asserts:
- `/healthz` returns ok
- `/api/board` serves demo taps
- The display HTML has zero external-origin references
- `/admin` redirects unauthenticated users to login
- The app process runs as non-root and `/data` is writable
- Docker reports the container "healthy"

---

## Code style

The codebase has established conventions. When contributing, match the
surrounding code:

- **Comments explain *why*, not *what*.** The codebase is heavily commented with
  rationale and gotchas. Keep that up.
- **Defensive type coercion** at system boundaries. Internal functions may
  assume valid input; validate at the edges.
- **Docstrings** explain the purpose and behaviour of modules and functions.
- **Prefer functions over classes** unless state plus multiple related methods
  truly belong together.
- **Names** are descriptive. No single-letter variables except in tight loops.
- **Frontend:** vanilla HTML/CSS/JS. No frameworks, no build steps, no CDN
  imports. The offline-first guarantee depends on this.

There is no formal linter configured yet. A PR that adds `ruff` or similar
configuration is welcome.

---

## Directory map

| Directory | Purpose |
|-----------|---------|
| `app/` | FastAPI application -- routes, sync logic, colour model, storage, auth |
| `static/` | CSS, JS, and placeholder SVG -- baked into the Docker image |
| `templates/` | Jinja2 templates (display, admin, login) |
| `tests/` | pytest suite (mirrors `app/` structure) + `conftest.py` fixtures |
| `scripts/` | `setup.sh` (guided installer), `docker_test.sh` |
| `docs/` | User-facing documentation (INSTALLATION, FAQ, UNRAID, PUBLISHING) |

Inside `app/`, each module has a clear responsibility:

| Module | Responsibility |
|--------|---------------|
| `main.py` | Routes: display, admin, API, image serving, auth |
| `config_store.py` | `config.json` load/save/coerce; `DEFAULT_CONFIG` is the schema |
| `board.py` | Builds the `/api/board` payload (tap resolution + display opts) |
| `brewfather.py` | Brewfather API fetch + sync job + note-token parsing |
| `colors.py` | EBC to hex colour model, hex/saturation parsing, text contrast |
| `beer_glass.py` | Tinted beer-glass SVG placeholders |
| `theme.py` | Colour theme presets + resolution to CSS variables |
| `markdown_store.py` | Flat-file tap storage (front-matter markdown + images) |
| `archive.py` | Move retired beers to `old_beers/` |
| `cleanup.py` | Daily age/size archive pruning |
| `demo.py` | `DEMO_MODE` seeding |
| `atomic.py` | Atomic file writes, job lock, safe unlink |
| `auth.py` | Signed-cookie admin sessions + login rate limiting |
| `paths.py` | `DATA_DIR`-derived paths; directory creation |
| `timezone.py` | Timezone-aware `iso_now()` |

---

## Pull request process

1. **Fork the repo** and create a feature branch from `main`.
2. **Make your changes.** Keep commits focused -- one logical change per commit.
3. **Add or update tests** for any new functionality or bugfix.
4. **Run the full test suite:** `python -m pytest -q`. All tests must pass.
5. **Run the Docker container test** if your changes touch the Dockerfile,
   entrypoint, or app startup: `bash scripts/docker_test.sh`.
6. **Open a PR against `main`.** Fill in the PR template.
7. **Reference related issues** in the PR description.

### Commit messages

- Use present tense, imperative mood ("Add feature" not "Added feature").
- Reference issue numbers where applicable.
- If you used AI assistance, end with a `Co-Authored-By: Claude ...` trailer
  (or equivalent for other tools).

### What makes a good PR

- **Small and focused.** A PR that does one thing is easier to review.
- **Tested.** New code has tests; existing tests still pass.
- **Documented.** If you add a feature, update the relevant docs in `docs/`.
- **No secrets.** Never commit `.env` files, `taplist_data/` directories, or
  Brewfather API keys. These are git-ignored; verify with `git status` before
  committing.

---

## Project conventions

- **Git stays local.** No pushes to public remotes unless explicitly setting up
  publishing (see `docs/PUBLISHING.md`).
- **Docs are present tense** ("this is how it works"), never "this changed / is
  now better."
- **The data directory is a mapped host directory**, never a Docker named volume
  (in docs and examples).
- **`MAPPING_VERSION`** in `brewfather.py` must be bumped whenever extraction
  logic changes, forcing a one-time rewrite of cached tap files.
- **Larger work happens on a feature branch.** Do not merge to `main` without
  explicit confirmation from a maintainer.

---

## Getting help

- **Docs:** Start with `docs/FAQ.md` for how the system works and
  `docs/INSTALLATION.md` for setup.
- **Issue tracker:** Search existing issues before opening a new one.
- **Discussions:** Use GitHub Discussions for questions and ideas that aren't
  bugs or feature requests.
