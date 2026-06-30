# TV Tap List — how it works (FAQ)

A tour of what the app does and why. For getting it running, see
[INSTALLATION.md](INSTALLATION.md).

**Contents**

- [The big picture](#the-big-picture)
- [How does a beer get on the board?](#how-does-a-beer-get-on-the-board)
- [Brewfather sync](#brewfather-sync)
- [What happens when the internet goes down?](#what-happens-when-the-internet-goes-down)
- [Colours](#colours)
- [Themes](#themes)
- [Glassware](#glassware)
- [Stats](#stats)
- [Pagination & the carousel](#pagination--the-carousel)
- [Scrolling text, ticker & venue logo](#scrolling-text-ticker--venue-logo)
- [Manual overrides](#manual-overrides)
- [Archiving & cleanup](#archiving--cleanup)
- [Where is my data?](#where-is-my-data)
- [Security](#security)

---

## The big picture

The container is a small web server. It syncs your beers from Brewfather, resolves
each tap to a beer, computes that beer's colour, and serves a single board page.
The TV just loads that page in a full-screen browser and polls for updates. All the
logic, data and assets live in the container and its mapped data directory, so the
TV needs nothing but a browser and a network path to the host.

---

## How does a beer get on the board?

Each tap slot resolves in a fixed priority order:

1. **Manual override** — a beer you entered in `/admin` for that tap. Always wins.
2. **Brewfather** — the batch you assigned to that tap with a `tap:N` note.
3. **Vacant** — nothing assigned; the slot shows as empty (or is hidden if you turn
   on "hide vacant taps").

So a manual override lets you put anything on a tap — a guest beer, a cider, a
cocktail — even if Brewfather knows nothing about it, and the sync never touches it.

---

## Brewfather sync

**On a timer** (every `SYNC_INTERVAL_MINUTES`, default 15) and whenever you click
**Sync Brewfather now**, the app:

1. Lists your **Completed** batches in one paginated request per page
   (`complete=True`, 50 per page), so a single call carries all the data it needs
   — ABV, IBU, colour, notes and the image. Cost is `ceil(batches / 50)` calls,
   comfortably under Brewfather's limit of **500 calls/hour per key**.
2. Reads a `tap:N` token from each batch's notes to decide which tap it belongs to.
3. Writes a small Markdown file per tap (and downloads the beer's image).
4. Sets aside (archives) any Brewfather tap that no longer maps to a slot.

**Which batches sync:** only **Completed** ones. Planning, Brewing, Fermenting and
Archived batches are ignored, so a beer you're still working on never appears until
you mark it Completed.

**Batch-note tokens** — put any of these anywhere in a batch's notes:

| Token | Effect |
|-------|--------|
| `tap:3` | Assign this batch to **tap 3**. Required for the beer to appear. |
| `colour:#780606` | Force an exact swatch + glass colour, overriding the EBC-derived colour. `color:` also works. |
| `glass:nonicpint` | Glass silhouette: `default`, `nonicpint`, `schooner`, `tulip`, `teku`. |
| `saturation:60` | Mute the colour to 60 % (a percentage, or a `0`–`1` fraction). |

Tokens are stripped from any text shown on the card. The same controls live in
`/admin` → **Manual overrides** for beers you enter by hand.

**Smart and safe:**

- **Change detection** skips rewriting files and re-downloading images for batches
  that haven't changed, so most syncs are nearly free.
- **Conflicts** (two Completed batches claiming one tap) resolve to the most
  recently updated batch, and the clash is logged.
- **A failed sync changes nothing** — the last good board stays exactly as it was.
- A rate-limit response (HTTP 429) is honoured (respecting `Retry-After`) and makes
  no changes.

> **Field-mapping note:** Brewfather's exact field names/units can vary by account.
> The sync maps defensively — it tries several field names, prefers *measured* over
> *estimated* values, handles EBC vs SRM colour, and keeps OG/FG only when they read
> as a plausible specific gravity — and logs what it found.

---

## What happens when the internet goes down?

The board keeps running on the **last cached data**, and the served page makes
**zero outbound requests** — fonts, CSS, JavaScript and images are all local. With
the WAN unplugged the display keeps rendering, shows no broken images (anything
missing falls back to the placeholder), and a sync that can't reach Brewfather
simply leaves the cache intact and tries again next cycle.

This is the whole point: a venue's flaky or down internet never blanks the screen.

---

## Colours

Each beer's colour is computed **on the server** from its **EBC** value using the
SRM reference chart, with a luminance rule that keeps overlaid text legible. That
one colour drives both the **swatch** and the **no-photo glass placeholder**, so
they always match.

- A per-beer **colour override** (an exact `#rrggbb`) wins everywhere — swatch and
  glass — for beers whose real colour the model doesn't nail.
- **Saturation** mutes a too-vivid colour toward grey (e.g. 60 %).
- The colour **stat number** shows in either **EBC** or **SRM** — your choice of
  unit, set once in the admin. Colour is always stored as EBC; the unit only
  changes how the number is displayed and entered.

---

## Themes

The whole palette is operator-selectable. Presets cover common screens:

- **Default** — balanced dark.
- **OLED** — true black, for OLED panels.
- **Local dimming** — slightly lifted blacks for FALD / edge-lit LCDs, to avoid
  blooming around bright text.
- **Midnight** — dark blue.
- **Daylight** — a light theme for bright rooms.
- **Custom** — pick every colour yourself.

Colours ship with the board and apply as CSS variables, so a theme change appears
on the next poll with no reload.

---

## Glassware

When a beer has no photo, its placeholder is a **beer glass tinted to the beer's
colour**, in one of several silhouettes — shaker pint, nonic pint, conical
schooner, tulip or teku — chosen globally or per beer. Because it uses the same
colour as the swatch, the pour always matches the dot.

---

## Stats

Each card can show **ABV, IBU, OG, FG** and **colour** (EBC or SRM). Every stat is
independently controllable:

- **Show / hide** globally — turn any stat off on every card.
- **Hide when empty** — keep a stat on, but drop it just for beers missing that
  value (so a card never shows a blank "OG —").
- **Per-tap overrides** for OG and FG — force them on or off for a single beer,
  regardless of the global setting.

---

## Pagination & the carousel

The board fits up to **8 cards per page** and lays them out to fill the screen. With
more beers than fit — or when you switch on pagination with a fixed page size — it
**rotates through pages** on a timer you set (`rotation_seconds`).

- **Page dots** show how many pages there are and which one you're on; they're
  clickable.
- Pressing **Enter** or **Space** jumps to the next page.
- Manual navigation restarts the rotation timer so the page you chose isn't flipped
  away immediately.

The data poll and the page-rotation timer are independent, so refreshing the data
never disturbs which page is on screen.

---

## Scrolling text, ticker & venue logo

- **Long names and tasting notes auto-scroll** within their box instead of being
  truncated, so nothing is cut off. (This is disabled when the device requests
  reduced motion.)
- A **bottom ticker** shows an announcement line — happy-hour text, an event, a
  welcome — without overlapping the grid.
- An optional **venue / company logo** sits at the top, with a configurable height
  (up to a third of the screen) and its own reserved row so it never collides with
  the cards.

The display polls the board every 30 seconds and updates only the cards whose data
changed — no full-page reloads, no flicker.

---

## Manual overrides

In `/admin` → **Manual overrides**, each tap has a row. Tick it to control that tap
by hand: set the name, ABV, IBU, colour (with saturation and an exact override),
OG/FG (with per-tap show/hide), glassware, tasting notes and a custom image. A
manual tap is **never touched by the Brewfather sync**. Unticking it releases the
slot back to Brewfather on the next sync.

This is how you put a guest tap, a one-off, or anything not in Brewfather onto the
board.

---

## Archiving & cleanup

When a beer leaves a tap, its files are moved aside into an archive rather than
deleted outright. A daily cleanup (03:30 local time) keeps the archive tidy:

- It removes archived beers older than your **Max Archive Age** (days).
- If the archive still exceeds your **Max Archive Storage Limit** (MB), it removes
  the oldest first until it's under the limit.
- Each beer's Markdown and image are treated as a pair and removed together.

---

## Where is my data?

In the **host directory you mapped to `/data`** (see [The data
directory](INSTALLATION.md#the-data-directory)). Settings live in `config.json`;
each beer is a small Markdown file in `taps/` with its image alongside. It's all
plain text and standard image files — open any of it in a text editor or file
browser to see exactly what the board is showing. Nothing is hidden in a database.

---

## Security

- **Admin login** is a signed, `HttpOnly`, `SameSite=Strict` session cookie
  (`Secure` when the request came over HTTPS). Login is **rate-limited** (5 failures
  / 5 minutes per client IP).
- **Secrets are plaintext on the host — a deliberate trade-off for this appliance.**
  The Brewfather key sits in `config.json`, and `ADMIN_PASSWORD` / `SESSION_SECRET`
  are environment variables. That's reasonable for a small on-prem box but means
  anyone with host/file access can read them, so protect the host (file
  permissions, restricted SSH) and rotate the API key if the box is exposed. Setting
  `BREWFATHER_USER_ID` / `BREWFATHER_API_KEY` as env vars keeps the key out of
  `config.json` entirely.
- The app runs **non-root** inside the container, taking the host `PUID`/`PGID` so
  your mapped data directory stays writable without giving the process root.
- Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's IP only (never
  `*`) so forwarded headers can't be spoofed.
