# Contributing to TV Tap List

Thanks for your interest in contributing. This guide covers the mechanics:
setting up a dev environment, running the tests, and submitting changes.

For everything deeper - the architecture, the module map, the conventions and
the non-obvious traps - read [CLAUDE.md](CLAUDE.md). It is the shared guide
for contributors and coding agents alike, and it explains the *why* behind
decisions this file only gestures at. [GLOSSARY.md](GLOSSARY.md) defines the
project's ubiquitous language (Slot, Beer, Tap, Source, Colour, Visibility);
use those words in issues, tests, and comments. Design decisions live in
[docs/adr/](docs/adr/) as append-only records.

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

HEIC photos need `pillow-heif` built from source, never from a wheel (ADR-0009).
Without it everything else works and the HEIC tests skip; see
[docs/BUILDING.md](docs/BUILDING.md#local-development-without-docker) to build it.

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
python -m pytest

# Run a single test file
python -m pytest tests/test_colors.py

# Run a specific test
python -m pytest tests/test_colors.py::test_ebc_to_hex
```

> **Do not add `-q`.** `pytest.ini` already sets `-ra -q` in `addopts`, so a
> second `-q` becomes `-qq` and suppresses the `N passed` summary line
> entirely - which reads as a silent run rather than a quiet one. If the
> warnings plugin is burying the summary, use `python -m pytest -p no:warnings`.

The suite is large (hundreds of tests mirroring `app/` module by module) and
runs in seconds. What each file covers, and which tests deliberately pin
behaviour against "improvements", is described in CLAUDE.md's Testing section.

Tests use `conftest.py` fixtures that point `DATA_DIR` at a temporary directory
per test, so they never touch real data. CI runs the suite on Python 3.12 (the
version the image ships) on every pull request, and nothing publishes without
a green run.

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
  assume valid input; validate at the edges. (Coerce, never reject - see
  CLAUDE.md's disposition rule for why.)
- **Docstrings** explain the purpose and behaviour of modules and functions.
- **Prefer functions over classes** unless state plus multiple related methods
  truly belong together.
- **Names** are descriptive, drawn from GLOSSARY.md where a term exists. No
  single-letter variables except in tight loops.
- **Frontend:** vanilla HTML/CSS/JS. No frameworks, no build steps, no CDN
  imports. The offline-first guarantee depends on this.
- **Prose style** (docs, comments, commit messages): no em dashes or arrows -
  use a spaced hyphen `-` as a separator and `->` for arrows. User docs are
  present tense ("this is how it works"), never "this changed / is now better".

There is no formal linter configured yet. A PR that adds `ruff` or similar
configuration is welcome.

### Where things live

The repo layout and per-module responsibilities are documented once, in
CLAUDE.md's "Repo layout" section, rather than duplicated here. The short
version: `app/` holds the FastAPI app and all logic, `static/` and
`templates/` the no-build frontend, `tests/` the pytest suite, `scripts/` the
installer and container test, and `docs/` the user-facing guides.

---

## Pull request process

1. **Fork the repo** and create a feature branch from `main`.
2. **Make your changes.** Keep commits focused - one logical change per commit.
3. **Add or update tests** for any new functionality or bugfix.
4. **Run the full test suite:** `python -m pytest`. All tests must pass.
5. **Run the Docker container test** if your changes touch the Dockerfile,
   entrypoint, or app startup: `bash scripts/docker_test.sh`.
6. **Update `CHANGELOG.md`** if the change is operator-visible: add it under
   the `## Unreleased` heading, written for the operator running the box (what
   they gain, what upgrading costs them), as part of the same PR.
7. **Open a PR against `main`.** Fill in the PR template.
8. **Reference related issues** in the PR description.

### Commit messages

- Use present tense, imperative mood ("Add feature" not "Added feature").
- Reference issue numbers where applicable.

### What makes a good PR

- **Small and focused.** A PR that does one thing is easier to review.
- **Tested.** New code has tests; existing tests still pass.
- **Documented.** If you add a feature, update the relevant docs in `docs/`.
- **No secrets.** Never commit `.env` files, `taplist_data/` directories, or
  Brewfather API keys. These are git-ignored; verify with `git status` before
  committing.

---

## Project conventions

These are the ones contributors trip over; CLAUDE.md carries the full set with
rationale.

- **Merging to `main` publishes.** A push to `main` builds and pushes the
  `:main` container image; only a `v*` tag moves `:latest` and cuts a GitHub
  Release. Maintainers merge - do not merge to `main` without explicit
  confirmation from a maintainer.
- **The data directory is a mapped host directory**, never a Docker named volume
  (in docs and examples - and the image deliberately declares no `VOLUME`).
- **`MAPPING_VERSION`** in `mapping.py` must be bumped whenever *extraction*
  logic changes (not when batch *selection* changes), forcing a one-time
  rewrite of cached tap files.
- **If a change contradicts an ADR**, say so explicitly and supersede the ADR
  with a new one; never silently override it.
- **Larger work happens on a feature branch.**

---

## Getting help

- **Docs:** Start with `docs/FAQ.md` for how the system works and
  `docs/INSTALLATION.md` for setup.
- **Issue tracker:** Search existing issues before opening a new one.
- **Discussions:** Use GitHub Discussions for questions and ideas that aren't
  bugs or feature requests.
