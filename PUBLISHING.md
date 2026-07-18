# Publishing TV Tap List

How to publish **your own fork** of TV Tap List to a public GitHub repo with an
automated Docker image on GitHub Container Registry, so other homebrewers can run
it with one command. The CI does the build and push — you just tag and push code.

> **You** run the push — these are instructions, not something that happens
> automatically. Nothing here sends data anywhere until you create the remote and
> `git push`.

---

## Quick start for other brewers

After your fork and image are published, anyone can run it:

```bash
# Option 1: clone and build
git clone https://github.com/OWNER/tv-taplist.git
cd tv-taplist
cp .env.example .env   # edit secrets
docker compose up -d --build

# Option 2: prebuilt GHCR image (skip the build)
# In docker-compose.yml, comment out `build: .` and set:
#   image: ghcr.io/OWNER/tv-taplist:latest
# Then:
cp .env.example .env   # edit secrets
docker compose up -d
```

Replace `OWNER` with the GitHub username where the repo and image live.

---

## How the CI/CD works

The repo ships with `.github/workflows/docker-publish.yml`. On every push to
`main` or a version tag (`v*`), it builds the Docker image, tags it, and pushes
to `ghcr.io/<repository>`.

**Tags the CI produces:**

| Trigger | Image tag |
|---------|-----------|
| Push to `main` | `latest` |
| Version tag `v1.0.0` | `v1.0.0` |
| Any push | Short commit SHA (`abc1234`) |

**What the workflow uses:**

- **`secrets.GITHUB_TOKEN`** — built-in; no setup needed. It authenticates to
  `ghcr.io` with `packages: write` permission, scoped to the current repo.
- **`github.repository`** — the owner/repo slug (e.g. `jceccato/tv-taplist`), so
  the image path is always correct.

There are no custom secrets to configure. Fork the repo, and the workflow just
works.

---

## Publishing your own fork

After forking, make these changes so your fork is a self-contained publishable
project pointing at your own images:

### 1. Replace `OWNER` placeholders

The repo uses `OWNER` as a placeholder for the GitHub username in:

- `docker-compose.yml` — the commented-out `image:` line
- `README.md` / `INSTALLATION.md` — the `docker run` and `git clone` lines

Search for `OWNER` and replace with your GitHub username.

### 2. Update docker-compose.yml

Switch the compose file from building locally to pulling your prebuilt image
(optional — you can ship it with `build: .` as the default; both work):

```yaml
services:
  taplist:
    #build: .
    image: ghcr.io/YOUR_USERNAME/tv-taplist:latest
```

### 3. Make the GHCR package public

After the first successful CI run, the package is private by default. Go to
**GitHub → your repo → Packages**, find `tv-taplist`, open **Package settings**,
and change visibility to **Public**.

### 4. Keep or replace the licence

Without one, "public" still means *nobody may legally reuse it*. The repo ships
with the **GNU AGPLv3**, which permits use, modification, and redistribution,
but requires anyone offering a network-accessible service based on the code to
also make their changes public. If you prefer a more permissive licence for your
fork, replace `LICENSE` with MIT, Apache 2.0, or use GitHub's built-in licence
picker via the web UI.

### 5. Set repo description + topics

Add a one-line description and topics — `homebrew`, `brewfather`, `tap-list`,
`fastapi`, `docker`, `raspberry-pi`, `digital-signage` — so the repo is
discoverable.

---

## Versioning conventions

Use **semver** tags pushed to `main`:

```bash
git tag v1.0.0
git push --tags
```

Rules of thumb:

- Increment the **major** version (e.g. `v2.0.0`) for breaking changes or
  migrations users must perform.
- Increment the **minor** version (e.g. `v1.1.0`) for new features that are
  backwards-compatible.
- Increment the **patch** version (e.g. `v1.0.1`) for bugfixes.

Only tag on `main`. The CI builds and publishes a tagged image for every `v*`
tag pushed; users pin to `ghcr.io/OWNER/tv-taplist:v1.0.0` for stable releases
or track `latest` for the bleeding edge.

The Dockerfile accepts a `VERSION` build arg. To embed the version in the
running image, add `ARG VERSION` inside the Dockerfile and write/print it
where you need it. The CI already passes `VERSION=${{ steps.meta.outputs.version
|| github.sha }}` as a build arg.

---

## Security: pre-publish safety checklist

The repo stores your real secrets — Brewfather API key, admin password — in
git-ignored files (`.env`, `taplist_data/`). Publishing is the one operation
where a mistake exposes them. Run these checks **before** every public push.

### Verify no secrets are tracked

Run each from the repo root. Each should report what's noted in **bold**.

```bash
# .env must NOT be tracked:
git ls-files --error-unmatch .env 2>/dev/null && echo "TRACKED — STOP" || echo "OK: .env untracked"

# Your live data directory must NOT be tracked:
git ls-files taplist_data data | head   # -> **prints nothing**

# Full list of what WILL be published — eyeball it for anything private:
git ls-files
```

### Check history for secrets that were ever committed

A secret committed once stays in history even after the file is deleted:

```bash
# Did .env or a data dir EVER get committed?
git log --all --oneline -- .env .env.* taplist_data/ data/
#  -> **prints nothing** = clean.

# Grep history for secret-shaped strings:
git log -p --all | grep -niE 'api[_-]?key|password|session_secret|brewfather_api' |
  grep -v 'example|changeme|env-var|environment'
```

### Automated scan with gitleaks

No install needed — run via Docker:

```bash
docker run --rm -v "$(pwd):/repo" zricethezav/gitleaks:latest detect --source=/repo -v
```

### If a secret IS in history

Don't just delete the file in a new commit — it's still in the old ones. Rewrite
history with [`git filter-repo`](https://github.com/newren/git-filter-repo)
**before** adding any remote:

```bash
pip install git-filter-repo
git filter-repo --path .env --invert-paths
git filter-repo --path taplist_data --invert-paths
```

Then **rotate everything**, because the secret was on disk in a shareable form:

- **Brewfather API key** — Brewfather → *Settings → API* → regenerate.
- **`ADMIN_PASSWORD`** — pick a new one.
- **`SESSION_SECRET`** — regenerate (`openssl rand -hex 32`).

### Everyday hygiene

- Keep doing your real config in `.env` and the admin UI; both stay git-ignored.
- If you need to share a config sample, copy to `.env.example` and replace every
  value with a placeholder — never put a real key there.
- Treat `taplist_data/` as private forever — it contains `config.json` with your
  Brewfather key in plaintext. It's git-ignored and in `.dockerignore`; never
  un-ignore it.
