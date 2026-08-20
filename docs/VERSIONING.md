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
| **MAJOR** | Breaking changes -- a config migration users must perform, a data-layout change that invalidates existing `/data` directories, or a Docker/Compose change that requires manual intervention (new required env vars, port changes, remapping the data directory). |
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

`app/__init__.py` exposes `__version__` for tooling that inspects packages, but
it holds no number of its own: it reads the same `TVTAPLIST_VERSION` env var the
update checker does, falling back to `"dev"` outside a container. There is
nothing to update at release time and nothing that can drift.

A hardcoded literal lived here until v1.3.1 and was two releases stale. Do not
put one back -- the running version is a build artefact, so the source can name
the env var but never the number.

---

## Internal version: `MAPPING_VERSION`

`app/mapping.py` defines `MAPPING_VERSION`, an integer (currently **6**).
This is **not** a Semver component -- it is an internal counter that tracks
changes to the Brewfather extraction logic.

| Trigger | Action |
|---------|--------|
| Field mapping changes (new fields, renamed keys, different fallback order) | Bump `MAPPING_VERSION` |
| New token support (`glass:`, `saturation:`, etc.) | Bump `MAPPING_VERSION` |
| A bug in the mapping that produced wrong data for cached taps | Bump `MAPPING_VERSION` |
| Which batches are selected (`include_conditioning`, `include_fermenting`) or `PAGE_SIZE` | Do **not** bump |

When `MAPPING_VERSION` differs from the stored `map_rev` on a cached `bf_tap`
file, the sync treats that batch as changed and rewrites the file. This
produces a one-time full refresh of every cached tap, then settles back to
skipping genuinely unchanged batches.

**Bump `MAPPING_VERSION` in the same commit that changes the extraction logic.**
The PR template checklist includes this as a reminder.

---

## The test gate

**A release requires a green test suite, and this is enforced in CI.**

`.github/workflows/tests.yml` defines one test job - checkout, Python 3.12,
both requirements files, `python -m pytest`. It runs on every pull request, and
the publish workflow calls the same definition as a `test` job that
`build-and-push` declares `needs: test` on. One definition, so the check a
contributor sees on a PR and the check that guards a publish cannot drift.

The gate covers **both** publish paths, `main` and `v*`. The tag path is the
one that matters most - `:latest` follows releases, so an unverified tag build
is what every default installation pulls - but the suite runs in seconds, so
exempting `main` would only leave the `:main` canary unverified.

Python is pinned to **3.12** to match `FROM python:3.12-slim` in the Dockerfile.
CI is the only place the suite runs on the interpreter the image ships; the
usual local interpreter on the maintainer's box is older.

`tests.yml` carries **no path filter**, deliberately. The publish workflow's
`paths` list mirrors `.dockerignore`, which excludes `tests/` - reusing it would
make a test-only change skip running the tests. The workflow says so in a
comment; do not add a filter for consistency.

### Recovering from a tag whose tests fail

A failed suite **skips** `build-and-push` rather than failing partway through
it, so no login, build, push or GitHub Release happens. That is the same state
the changelog gate produces - a tag in git and nothing published - and the
recovery is the same delete-and-re-tag path documented below.

---

## The changelog

**`CHANGELOG.md` at the repo root is required reading and required writing.**
Every release must have a `## vX.Y.Z - YYYY-MM-DD` section in it before the tag
is pushed.

This is enforced, not merely asked for. The publish workflow runs
`scripts/release_notes.sh <tag>` as its **first** step on a `v*` tag and fails
the whole job when there is no section for that tag - before any image is built
or pushed. The extracted section becomes the GitHub Release body.

### Work that has merged but not shipped

Changes land on `main` continuously; releases are cut deliberately. The gap
between them lives under a **`## Unreleased`** heading at the top of
`CHANGELOG.md`.

Write the entry when the change merges, not on release day. The person who made
the change knows what it means for an operator; the person cutting the release a
fortnight later is reconstructing it from commit subjects, which is how v1.1.0
and v1.3.0 ended up with no notes at all.

Cutting a release renames that heading to `## vX.Y.Z - YYYY-MM-DD` and starts a
fresh empty `## Unreleased` above it. Nothing else moves.

`scripts/release_notes.sh` matches a version heading only, so an Unreleased
section can never be published as a release body. If a tag is pushed while the
notes still sit under `## Unreleased`, the build fails on the missing section -
which is the safety net working, not a problem to route around.

### Why it is a gate rather than a fallback

`--generate-notes` was the fallback until v1.3.1, and the result was that
v1.1.0 and v1.3.0 shipped with a release body consisting of one compare link.
A list of commit subjects records what was typed; it does not tell an operator
what changed for them or whether upgrading will cost them anything. The
project's release notes are written for the person running the box.

### Recovering from a tag with no entry

The build fails before publishing anything, so nothing is live and there is
nothing to unpublish. **The identical recovery applies to a tag whose tests
fail** - substitute "fix the failing test" for "add the section":

```bash
# add the section to CHANGELOG.md (or fix the suite), commit it to main, then:
git push origin :refs/tags/v1.3.1        # delete the remote tag
git tag -d v1.3.1
git tag -a v1.3.1 -m "Release v1.3.1"
git push origin main --tags
```

This is the one case where deleting a tag is correct - the rule against moving
a tag protects *published* releases, and this one never published.

### What to write

Follow the shape of the existing entries: what an operator gains or must do,
in present tense, with the migration cost stated up front (usually "none: pull
and restart"). Bugs are described by what went wrong for the user, not by the
function that was fixed. Under-the-hood work gets a short section at the end
when it explains something an operator might otherwise trip over. Close with
`Closes #N` for the issues shipped, and the compare link.

---

## Registry tags

One image build can carry several tags. There is a single meaning of
"released" -- a `v*` tag -- and both the registry and the in-app update checker
use it.

| Tag | Moves when | For |
|-----|-----------|-----|
| `:latest` | a `v*` tag is pushed | The default. What every install guide and the shipped `docker-compose.yml` point at. |
| `:v1.2.3` | never (immutable) | Pinning to an exact release. |
| `:main` | every merge to `main` | An unreleased canary, for trying a merge before it is tagged. Nobody is steered here. |
| `:<short-sha>` | every build | Pinning one exact build, released or not. |

`:latest` used to track `main`, which meant an operator on `:latest` could be
running ahead of the version the app called newest -- the update checker has
only ever moved on a `v*` tag. Now both move together.

Docs-only pushes to `main` do not build at all. The workflow's `paths` filter
mirrors `.dockerignore`, which keeps `README.md` in the image, so a README
change still builds while `docs/**` and every other markdown file does not.
(It has to be `paths` rather than `paths-ignore`: the `!` exclusion character
works only in the former.)

---

## Pre-release and dev versions

Images that are **not** built from a versioned git tag carry non-release version
strings:

| Source | `TVTAPLIST_VERSION` | Behaviour |
|--------|---------------------|-----------|
| Local dev run (`uvicorn` directly) | `"dev"` | Not comparable -- reported as unknown |
| CI push to `main` (the `:main` canary image) | `"main"` | Not comparable -- reported as unknown |
| CI push of any branch (non-tag) | Short commit SHA | Not comparable -- reported as unknown |
| CI push of a `v*` tag (`:latest`, `:v1.2.3`) | `"v1.2.3"` | Compared against GitHub releases |

This is deliberate: a build that is not a release cannot be placed against the
release history, so it is never nagged with a spurious "update available".

The gate is in `app/update_check.py` -- `_looks_like_release()` requires the
version string to contain at least one dot, which excludes `"dev"`, `"main"`,
and bare hex SHAs.

**"Not comparable" is reported as such, not as "up to date".**
`update_check.update_state()` resolves one of four states -- `current`,
`behind`, `unknown`, `disabled` -- and the admin panel says plainly that an
untagged build cannot be compared, naming the latest release so the operator can
judge for themselves. Reporting the unknown case as an all-clear was a real bug
(issue #26): a container genuinely behind a release was reassured it was not.

---

## Release checklist

Before tagging a release:

1. **All tests pass:** `python -m pytest -q` (243 tests, 0 failures). CI runs
   the same suite on Python 3.12 and a red suite blocks the publish, so this
   step is now a way to find out early rather than the only thing checking.
2. **Docker test passes:** `bash scripts/docker_test.sh` (builds, starts, asserts
   health, demo data, zero external origins, non-root PID 1).
3. **`MAPPING_VERSION` has been bumped** if any extraction logic changed since
   the last release.
4. **`CHANGELOG.md` has a section for the new tag** (`## v1.2.0 - YYYY-MM-DD`).
   Normally this means renaming the `## Unreleased` heading to the new version
   and date, then starting a fresh empty `## Unreleased` above it. This is a
   hard gate -- the publish workflow fails without it. Check the rendering
   locally: `bash scripts/release_notes.sh v1.2.0`.
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

- The CI builds `ghcr.io/<owner>/tv-taplist:v1.2.0` and moves `:latest` to it.
  **`:latest` follows releases, not `main`** -- see "Registry tags" below.
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
- **Port or data directory change** -- the default `PORT` changes, or the
  container path the data directory is mapped to (`/data`) changes in a way that
  existing Compose files break.

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
| Pre-release / RC tags | Not used (the project is small; the `:main` image serves as the canary) |

Tags must be **annotated** (`-a`), not lightweight. This records the tagger,
date, and message -- useful when inspecting history.

Never delete or move a published tag. If a release is bad, increment the patch
and tag a new release; do not re-tag the same version.

---

## Changelog

`CHANGELOG.md` lives at the repo root and every release must have a section in
it before its tag is pushed. The publish workflow enforces this and uses that
section as the GitHub Release body. See [The changelog](#the-changelog) above
for the rules and the recovery path.

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
- A repo with no releases returns `"unreleased"`; that is the `unknown` state
  rather than an error, and the admin says so.
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
