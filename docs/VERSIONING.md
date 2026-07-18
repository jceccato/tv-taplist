# Versioning

How TV Tap List is versioned, tagged, and released. This document is the single
authoritative reference -- it takes precedence over any fragmentary guidance in
other files.

---

## Scheme: Semver

This project follows [Semantic Versioning 2.0.0](https://semver.org/):

```
vMAJOR.MINOR.PATCH
```

| Component | When to increment |
|-----------|-------------------|
| **MAJOR** | Breaking changes -- a config migration users must perform, a data-layout change that invalidates existing `/data` directories, or a Docker/Compose change that requires manual intervention (new required env vars, port changes, volume remapping). |
| **MINOR** | New features that are backwards-compatible -- a new display option, new Brewfather token support, new theme preset, new API endpoint. Also used when `MAPPING_VERSION` is bumped (the extraction logic changed but old cached taps still work). |
| **PATCH** | Bug fixes, performance improvements, docs-only changes, dependency updates that don't alter behaviour. |

The `v` prefix is **required** on all tags (e.g. `v1.2.0`, not `1.2.0`). It is
the conventional GitHub format and the CI workflow triggers on the `v*` glob.

---

## Authoritative version source

**The git tag is the authoritative version.** Every public release is a signed
(or annotated) tag on `main`:

```bash
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

Pushing the tag triggers the CI workflow, which builds the Docker image, tags
it `:v1.0.0`, and bakes the string into the container as the `TVTAPLIST_VERSION`
environment variable. That env var is what the update-checker reads, and what
the admin panel reports as the running version.

### The `__version__` constant

`app/__init__.py` carries a `__version__ = "1.0.0"` string. It is a **soft
reference** -- human-readable, discoverable by tooling that inspects packages,
but not consumed by any runtime logic (the update checker, the CI, and the admin
panel all read `TVTAPLIST_VERSION` from the environment). It should be updated
to match the latest tag when convenient, but it is not a release gate and
nothing breaks if it drifts.

---

## Internal version: `MAPPING_VERSION`

`app/brewfather.py` defines `MAPPING_VERSION`, an integer (currently **6**).
This is **not** a Semver component -- it is an internal counter that tracks
changes to the Brewfather extraction logic.

| Trigger | Action |
|---------|--------|
| Field mapping changes (new fields, renamed keys, different fallback order) | Bump `MAPPING_VERSION` |
| New token support (`glass:`, `saturation:`, etc.) | Bump `MAPPING_VERSION` |
| A bug in the mapping that produced wrong data for cached taps | Bump `MAPPING_VERSION` |
| Changing `include_conditioning` behaviour or `PAGE_SIZE` | Do **not** bump |

When `MAPPING_VERSION` differs from the stored `map_rev` on a cached `bf_tap`
file, the sync treats that batch as changed and rewrites the file. This
produces a one-time full refresh of every cached tap, then settles back to
skipping genuinely unchanged batches.

**Bump `MAPPING_VERSION` in the same commit that changes the extraction logic.**
The PR template checklist includes this as a reminder.

---

## Pre-release and dev versions

Images that are **not** built from a versioned git tag carry non-release version
strings:

| Source | `TVTAPLIST_VERSION` | Behaviour |
|--------|---------------------|-----------|
| Local dev run (`uvicorn` directly) | `"dev"` | Update checker never reports "update available" |
| CI push to `main` (the `:latest` image) | `"main"` | Update checker never reports "update available" |
| CI push of any branch (non-tag) | Short commit SHA | Update checker never reports "update available" |
| CI push of a `v*` tag | `"v1.2.3"` | Update checker compares against GitHub releases |

This is deliberate: only pinned-version users (`docker compose` pointing at
`:v1.2.3`) see update notifications. Users tracking `:latest` or running dev
builds are already on the bleeding edge, so nagging them is spurious noise.

The gate is in `app/update_check.py` -- `_looks_like_release()` requires the
version string to contain at least one dot, which excludes `"dev"`, `"main"`,
and bare hex SHAs.

---

## Release checklist

Before tagging a release:

1. **All tests pass:** `python -m pytest -q` (127+ tests, 0 failures).
2. **Docker test passes:** `bash scripts/docker_test.sh` (builds, starts, asserts
   health, demo data, zero external origins, non-root PID 1).
3. **`MAPPING_VERSION` has been bumped** if any extraction logic changed since
   the last release.
4. **`__version__` in `app/__init__.py` is updated** to match the new tag
   (best-effort; not a gate).
5. **No secrets in the diff:** `.env` and `taplist_data/` are git-ignored and
   untracked. Verify with `git ls-files .env taplist_data/ data/` (must print
   nothing).
6. **Docs are current:** any user-facing change is reflected in the relevant
   `docs/*.md` file. Docs are present-tense and never contain "changed / is now
   better" framing.
7. **Commit, then tag, then push -- in that order:**
   ```bash
   git add <changed files>
   git commit -m "Release v1.2.0"
   git tag -a v1.2.0 -m "Release v1.2.0"
   git push origin main --tags
   ```

   The commit MUST land on `main` before the tag is pushed. The CI runs on tag
   push and expects the tagged commit to be on `main`.

### Post-release

- The CI builds `ghcr.io/<owner>/tv-taplist:v1.2.0` and `:latest` (since the
  tag is on `main`).
- Pinned users (`image: ...:v1.1.0`) receive an update notification on their
  next daily check, visible in the admin panel.
- If the release is a new **major** version, include migration instructions in
  the release notes (GitHub will prompt for these when publishing the release).

---

## Breaking changes

A change is breaking when it requires a user to do something beyond pulling a
new image. Examples:

- **Config schema change** -- a renamed or removed key in `config.json` that
  `config_store._coerce()` does not handle transparently.
- **Data directory layout change** -- renamed paths under `/data` that require
  migration or manual intervention.
- **Env var change** -- a new required variable or a renamed variable (the
  Dockerfile, `entrypoint.sh`, or `docker-compose.yml` is the source of truth
  for the env-var contract).
- **Port or volume change** -- the default `PORT` changes, or the `/data` volume
  path changes in a way that existing Compose files break.

Backwards-compatible changes (no major bump required):

- New optional env vars (defaults preserve existing behaviour).
- New config keys (treated as missing = default by `_coerce()`).
- New API endpoints or response fields.
- `MAPPING_VERSION` bumps (cached data refreshes seamlessly; old taps are
  rewritten, not invalidated).

### Deprecation policy

Avoid breaking changes. When one is unavoidable, deprecate first in a MINOR
release (with a logged warning or admin-panel notice for at least one release
cycle), then remove in the next MAJOR. Never silently break a running install.

---

## Branch and tag discipline

| Action | Where |
|--------|-------|
| Feature work | Feature branch off `main` |
| Bug fixes | Feature branch off `main` |
| Release tags | **Only on `main`** |
| Pre-release / RC tags | Not used (the project is small; `:latest` serves as the canary) |

Tags must be **annotated** (`-a`), not lightweight. This records the tagger,
date, and message -- useful when inspecting history.

Never delete or move a published tag. If a release is bad, increment the patch
and tag a new release; do not re-tag the same version.

---

## Changelog

This project does not currently maintain a `CHANGELOG.md`. Release notes are
written in the GitHub Releases UI when publishing a tag. If a changelog is
adopted later, it follows the [Keep a Changelog](https://keepachangelog.com/)
format and lives at the repo root.

---

## Update-check compatibility

The `app/update_check.py` module queries the GitHub Releases API once per day.
Its behaviour depends on accurate version strings:

- The **running version** comes from `TVTAPLIST_VERSION` (env var baked at build).
- The **latest version** comes from the `tag_name` field of the latest GitHub
  release.
- A version is only considered "newer than current" when **both** are release
  tags (`vX.Y[.Z]`).

Practical implications:

- Always use `v` prefix on tags (the CI, the Docker metadata action, and
  `_looks_like_release()` all expect it).
- A repo with no releases returns `"unreleased"`; the admin panel shows "No
  releases yet" instead of an error.
- The `GITHUB_OWNER` / `GITHUB_REPO` constants in `update_check.py` are
  hardcoded at build time. Forkers who rebuild the image get update checks
  against their own repo automatically.

---

## Relationship to other docs

- **`PUBLISHING.md`** -- describes *how* to push tags and publish images; this
  document describes *when* and *why*.
- **`CONTRIBUTING.md`** -- the PR checklist and code conventions; this document
  is the normative reference for versioning decisions.
- **PR template** (`PULL_REQUEST_TEMPLATE.md`) -- the `MAPPING_VERSION` checkbox
  is derived from the rules in this document.
