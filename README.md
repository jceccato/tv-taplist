# TV Tap List

**An offline-first digital beer tap list for TVs.** Point a TV's browser at it and
it shows a clean, full-screen board of what's on tap — name, tasting notes, ABV,
IBU, gravity, and a colour swatch matched to each beer.

It pulls your beers automatically from **[Brewfather](https://brewfather.app)** and
keeps showing the **last known board even when the internet drops** — no spinners,
no blank screen, zero outbound requests. The container is the brain; the TV is just
a screen pointed at it.

```
┌──────────────────────────────────────────────┐
│  1  WEST COAST IPA        ●     2  HAZY PALE  ●│
│     Bright citrus & pine        Juicy stone…  │
│     6.8% · 65 IBU · 18 EBC      5.2% · 35 IBU │
│  3  MUNICH HELLES         ●     4  DRY STOUT  ●│
│     Clean malt, noble hop       Roasty, dry…  │
│     4.9% · 18 IBU · 7 EBC       4.4% · 40 IBU │
│            • Now pouring — ask staff •         │
└──────────────────────────────────────────────┘
```

- **One container.** Python + FastAPI inside; vanilla HTML/CSS/JS on the TV. No
  cloud, no build step, no CDNs.
- **Offline-first.** Beers are cached as plain text + images in a folder you map
  from the host, so the board survives reboots and outages and stays inspectable.
- **Runs anywhere Docker does** — a Raspberry Pi, an Unraid box, a NUC, a VM.

---

## Try it now (demo)

One command pulls the image and runs a self-contained demo with sample beers — no
Brewfather account, no config:

```bash
docker run -d --name tv-taplist-demo -p 8080:8080 \
  -e DEMO_MODE=true -e ADMIN_PASSWORD=demo -e SESSION_SECRET=demo \
  ghcr.io/OWNER/tv-taplist:latest
```

- **Display:** <http://localhost:8080/> — the TV board (no login).
- **Admin:** <http://localhost:8080/admin> — log in with the throwaway password
  `demo` to play with settings, themes and overrides.

Stop and remove it when you're done: `docker rm -f tv-taplist-demo`.

> Replace `OWNER` with the published image owner. The demo is for evaluation only —
> see [INSTALLATION.md](INSTALLATION.md) for a real setup.

---

## Set it up for real

Pick the path that matches where you're running it:

| Path | Best for | Guide |
|------|----------|-------|
| **Guided installer** | Linux / Raspberry Pi / NUC | [INSTALLATION.md → Guided installer](INSTALLATION.md#guided-installer-recommended) |
| **Unraid** | Unraid servers | [INSTALLATION.md → Unraid](INSTALLATION.md#unraid) · [UNRAID.md](UNRAID.md) |
| **Manual Docker Compose** | You already run Compose | [INSTALLATION.md → Manual](INSTALLATION.md#manual-docker-compose) |

The installer asks a handful of questions (admin password, timezone, your
Brewfather details), writes the config for you, and starts the container. Full env
var reference, reverse-proxy/HTTPS setup, and how to get your Brewfather API key all
live in [INSTALLATION.md](INSTALLATION.md).

---

## Getting beers onto the board

1. In Brewfather, open the batch for a beer that's on tap.
2. Add a line to the batch **notes**: `tap:1` (the tap number it's pouring on).
3. Set the batch **status to Completed**.

On its next sync the board picks it up. You can fine-tune the swatch colour,
glassware and more with extra note tokens or from the admin panel — see
[FAQ.md → Brewfather](FAQ.md#brewfather-sync). Beers that aren't Completed are
ignored, so works-in-progress never show up by accident.

---

## How it works

A short tour: the container syncs from Brewfather on a timer, resolves each tap to
a beer, computes its colour, and serves a board the TV polls and updates in place.
Manual overrides let you place beers Brewfather doesn't know about. Everything
persistent is plain text in a folder you can open.

The full explanation — sync, colours and themes, glassware, pagination, the offline
guarantee, archiving, and security — is in **[FAQ.md](FAQ.md)**.

---

## Guides

- **[INSTALLATION.md](INSTALLATION.md)** — set it up: demo, guided installer,
  Unraid, manual Compose, env vars, reverse proxy, Brewfather API key.
- **[FAQ.md](FAQ.md)** — how everything works, in depth.
- **[UNRAID.md](UNRAID.md)** — the deep-dive Unraid walkthrough.
- **[PUBLISHING.md](PUBLISHING.md)** — fork it and publish your own image safely.

## License

No license file yet — a permissive (MIT) template is ready in
[PUBLISHING.md](PUBLISHING.md).
