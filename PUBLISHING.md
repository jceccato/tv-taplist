# Publishing to public GitHub

This guide walks through putting **TV Tap List** on a public GitHub repo so other
homebrewers can use it — **without leaking your secrets**. The repo currently
lives only on your machine, and your real Brewfather key / admin password live in
files that are *git-ignored* (`.env`, `taplist_data/`). Publishing is the one
operation where a mistake exposes them, so do the safety pass first.

> **You** run the push — these are instructions, not something that happens
> automatically. Nothing here sends data anywhere until you create the remote and
> `git push`.

---

## 1. Pre-flight: make sure no secrets are tracked

Run these from the repo root. Each one should report what's noted in **bold**.

```bash
# .env must NOT be tracked (it holds ADMIN_PASSWORD + SESSION_SECRET):
git ls-files --error-unmatch .env 2>/dev/null && echo "TRACKED — STOP" || echo "OK: .env untracked"

# Your live data (config.json holds the Brewfather key in plaintext) must NOT be tracked:
git ls-files taplist_data data | head   # -> **prints nothing**

# Full list of what WILL be published — eyeball it for anything private:
git ls-files
```

The `.gitignore` already excludes `.env`, `.env.*` (keeping `.env.example`),
`taplist_data/`, `/data/`, and `.claude/launch.json`. The only secret-bearing
files are git-ignored, so a normal `git push` won't include them — **but verify
the history too**, because a secret committed once stays in history even after
you delete the file:

```bash
# Did .env (or a data dir) EVER get committed?
git log --all --oneline -- .env .env.* taplist_data/ data/
#  -> **prints nothing** = clean. Any output = secret is in history; see §1a.

# Grep history for secret-shaped strings (adjust patterns to taste):
git log -p --all | grep -niE 'api[_-]?key|password|session_secret|brewfather_api' | grep -v 'example\|changeme\|env-var\|environment'
```

For a thorough automated scan, use **gitleaks** (no install needed via Docker):

```bash
docker run --rm -v "$(pwd):/repo" zricethezav/gitleaks:latest detect --source=/repo -v
```

### 1a. If a secret IS in history

Don't just delete the file in a new commit — it's still in the old ones. Rewrite
history with [`git filter-repo`](https://github.com/newren/git-filter-repo)
(recommended) **before** adding any remote:

```bash
pip install git-filter-repo
git filter-repo --path .env --invert-paths            # purge the file from all history
git filter-repo --path taplist_data --invert-paths    # ...and any data dir
```

Then **rotate the exposed credentials regardless**, since they were on disk in a
shareable form:

- **Brewfather API key** — Brewfather → *Settings → API* → regenerate the key.
- **`ADMIN_PASSWORD`** — pick a new one in your `.env`.
- **`SESSION_SECRET`** — regenerate (`openssl rand -hex 32`); existing logins drop.

---

## 2. Add a licence

Without a licence, "public" still means *nobody may legally reuse it*. For a
community homebrew tool a permissive licence (MIT) is the usual choice. Create it:

```bash
cat > LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Joshua Ceccato

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
```

Prefer a patent grant? Use **Apache-2.0**. Want derivatives kept open? Use
**GPL-3.0**. GitHub can also add one for you via *Add file → Create new file →*
name it `LICENSE` → *Choose a license template*.

---

## 3. Optional tidy-up before going public

- **`tv_taplist_build_prompt.md`** is the AI build-spec that seeded this project.
  It's currently tracked. Harmless, but most people remove it from a public repo:
  ```bash
  git rm tv_taplist_build_prompt.md && git commit -m "Drop internal build prompt"
  ```
- **Double-check `.env.example`** ships only safe placeholders (`changeme`,
  `change-this-long-random-secret`) — it does. Never replace them with real ones.
- **Squash WIP history (optional).** If your local history has messy commits you'd
  rather not publish, you can start fresh: `rm -rf .git && git init` then make one
  clean initial commit. (Only do this if you don't need the history.)

---

## 4. Create the repo and push

### Option A — GitHub CLI (`gh`)

```bash
gh auth login                      # one-time, in a terminal
git add -A && git commit -m "Prepare for public release"   # if you made changes above
gh repo create tv-taplist --public --source=. --remote=origin --push
```

### Option B — web UI

1. On GitHub: **New repository** → name `tv-taplist`, **Public**, *don't* add a
   README/licence (you already have them) → **Create**.
2. Link and push:
   ```bash
   git branch -M main
   git remote add origin https://github.com/<you>/tv-taplist.git
   git push -u origin main
   ```

> If you've been working on a feature branch (e.g. `feature/display-options`),
> merge it into `main` first, or push it and open a PR — whatever your workflow
> prefers. Public repos default to showing `main`.

---

## 5. Make it usable for others

- **Description + topics.** Add a one-line description and topics like
  `homebrew`, `brewfather`, `tap-list`, `fastapi`, `docker`, `raspberry-pi`,
  `digital-signage` so it's discoverable.
- **Publish a prebuilt image (recommended).** So others can `docker pull` instead
  of building, push to GitHub Container Registry on each version tag. Add
  `.github/workflows/release-image.yml`:
  ```yaml
  name: release-image
  on:
    push:
      tags: ["v*"]
  permissions:
    contents: read
    packages: write
  jobs:
    build:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: docker/login-action@v3
          with:
            registry: ghcr.io
            username: ${{ github.actor }}
            password: ${{ secrets.GITHUB_TOKEN }}
        - uses: docker/build-push-action@v6
          with:
            context: .
            push: true
            tags: |
              ghcr.io/${{ github.repository_owner }}/tv-taplist:latest
              ghcr.io/${{ github.repository_owner }}/tv-taplist:${{ github.ref_name }}
  ```
  Then `git tag v1.0.0 && git push --tags` builds and publishes
  `ghcr.io/<you>/tv-taplist:1.0.0`. (First push: make the package public under the
  repo's *Packages* settings.)
- **Run the tests in CI.** Add `.github/workflows/tests.yml`:
  ```yaml
  name: tests
  on: [push, pull_request]
  jobs:
    pytest:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with: { python-version: "3.12" }
        - run: pip install -r requirements.txt -r requirements-dev.txt
        - run: pytest -q
  ```
- **Tell users where to start.** The [README](README.md) leads with a one-command
  demo and links [INSTALLATION.md](INSTALLATION.md) (guided installer, manual
  Compose, Unraid) and [FAQ.md](FAQ.md). Once your GHCR image is published,
  replace `OWNER` in those docs with your GitHub owner so the `docker run` /
  `image:` lines work as-is.

---

## 6. After publishing — keep secrets out

- Keep doing your real config in `.env` and the admin UI; both stay git-ignored.
- If you ever need to share a config sample, copy it to `.env.example` and replace
  every value with a placeholder.
- Treat `taplist_data/` as private forever — it contains `config.json` with your
  Brewfather key in plaintext (a documented appliance-scope trade-off; see the
  README **Security notes**).
