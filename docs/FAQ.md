# TV Tap List - how it works (FAQ)

A tour of what the app does and why. For getting it running, see
[INSTALLATION.md](INSTALLATION.md).

**Contents**

- [The big picture](#the-big-picture)
- [How do I display this on a TV?](#how-do-i-display-this-on-a-tv)
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

## How do I display this on a TV?

The board is a web page at `http://<host>:8080`. Any browser in full-screen mode
can act as the display. Two hardware-specific guides cover the most common setups:

| Hardware | Guide | What you get |
|----------|-------|--------------|
| **Raspberry Pi** | [RASPBERRY_PI_KIOSK.md](RASPBERRY_PI_KIOSK.md) | Dedicated Pi plugged into the TV via HDMI. A script configures Chromium to launch on boot in full-screen. Supports Bookworm (labwc / wayfire) and Bullseye (LXDE). |
| **Android device** | [ANDROID_KIOSK.md](ANDROID_KIOSK.md) | Phone, tablet, Android TV, Chromecast, or Fire Stick. Uses the Screenlite Web Kiosk app for a boot-to-display experience. No Pi needed. |

### Raspberry Pi kiosk modes

The Pi setup script offers two modes so you can pick the right level of
lock-down for your venue:

| Mode | How to enable | Exit key | Use case |
|------|--------------|----------|----------|
| **Escapable fullscreen** | Default — just run `bash scripts/pi-kiosk.sh` | **F11** or **ESC** | Home bar, shared Pi where you want to use the desktop normally |
| **Locked kiosk** | `KIOSK_MODE=true bash scripts/pi-kiosk.sh` | **Alt+F4** (or kill the process over SSH) | Public venue, taproom where the display must stay locked |

To switch modes later, re-run the script with the desired setting — it overwrites
the launch script and asks before touching the autostart entry.

Both modes boot straight into Chromium when the Pi starts and poll the health
endpoint so they handle the Docker-on-boot startup race gracefully.

If you already have a display set up (a laptop, a smart TV's built-in browser,
anything), just point it at `http://<host>:8080` and go full-screen — no script
needed.

---

## How does a beer get on the board?

Each tap slot resolves in a fixed priority order:

1. **Manual override** - a beer you entered in `/admin` for that tap. Always wins.
2. **Brewfather** - the batch you assigned to that tap with a `tap:N` note.
3. **Vacant** - nothing assigned; the slot shows as empty (or is hidden if you turn
   on "hide vacant taps").

So a manual override lets you put anything on a tap - a guest beer, a cider, a
cocktail - even if Brewfather knows nothing about it, and the sync never touches it.

---

## Brewfather sync

**On a timer** (every `SYNC_INTERVAL_MINUTES`, default 15) and whenever you click
**Sync Brewfather now**, the app:

1. Lists your **Completed** batches (plus **Conditioning** ones if you enabled
   that - see below) in one paginated request per page (`complete=True`, 50 per
   page), so a single call carries all the data it needs - ABV, IBU, colour, notes
   and the image. Cost is `ceil(batches / 50)` calls per status, comfortably under
   Brewfather's limit of **500 calls/hour per key**.
2. Reads a `tap:N` token from each batch's **Batch Notes** to decide which tap it
   belongs to.
3. Writes a small Markdown file per tap (and downloads the beer's image).
4. Sets aside (archives) any Brewfather tap that no longer maps to a slot.

### Where to put things in Brewfather

Understanding which Brewfather fields feed which parts of the card lets you
control the board directly from the app.

**Batch Notes** - the key:value control field

Found by opening a batch and scrolling toward the bottom, just above the
**Attachments** section. The Batch Notes text field is available on **every tab**
of a batch (Planning, Brewing, Fermenting, Completed).

Put your `tap:X`, `colour:#XXXXXX`, `glass:X` and `saturation:X` tokens here -
one per line, or all on one line. **Batch Notes text is never shown on the tap card.**
The sync only scans this field for the control tokens and strips them from any
display text, so nothing you type there (besides the tokens themselves) ever
appears on the TV.

**Taste Notes** - the card description

Found on the **Completed** tab only, in the **Taste** section (below the rating
stars). Whatever you type here is synced **1:1 to the card's description / tasting
notes** - it ends up on the beer card verbatim.

If a batch has no Taste Notes, the beer's style name (e.g. "English Porter") is
used as a fallback so the card isn't blank.

> **Tip:** Make sure you are on the **Completed** tab when editing Taste Notes.
> The other tabs (Planning, Brewing, Fermenting) do not show the Taste section.

**Images** - the card photo

By default the board shows a tinted placeholder glass coloured to the beer's EBC.
You can replace it with an actual beer photo or logo:

1. Upload the image on the **original recipe** in Brewfather (not the batch).
   Batches inherit their image from the source recipe.
2. On the next sync the image is downloaded and used on that beer's card.

If you also want to update the beer **name** (which comes from the recipe name),
that is best done on the original recipe too, since batches pull their name and
image from their source recipe.

You can also customise the tinted placeholder itself directly in the tokens:

- `colour:#rrggbb` overrides the EBC-derived colour with an exact hex code (for
  the swatch dot AND the glass placeholder).
- `saturation:60` mutes the colour (use when a calculated EBC colour looks too
  vivid for the real beer).
- `glass:teku` picks a glass silhouette (`default`, `nonicpint`, `schooner`,
  `tulip`, `teku`).

### Batch-note tokens reference

Put any of these in the **Batch Notes** field:

| Token | Effect |
|-------|--------|
| `tap:3` | Assign this batch to **tap 3**. Required for the beer to appear. |
| `colour:#780606` | Force an exact swatch + glass colour, overriding the EBC-derived colour. `color:` also works. |
| `glass:nonicpint` | Glass silhouette: `default`, `nonicpint`, `schooner`, `tulip`, `teku`. |
| `saturation:60` | Mute the colour to 60 % (a percentage, or a `0`–`1` fraction). |

The sync scans the **Batch Notes** and **Taste Notes** for these tokens. Any token
found anywhere is applied, and all tokens are stripped from the description text
shown on the card. The same controls live in `/admin` -> **Manual overrides** for
beers you enter by hand.

### Tip: use the admin override to build tokens with a GUI

Getting the hex codes, saturation and glassware right by typing blind into
Brewfather's text field can be fiddly. A faster workflow:

1. Go to `/admin` -> **Manual overrides** and tick the override checkbox for a
   tap.
2. Set the colour override, saturation and glassware using the visual pickers
   and sliders - you get a **live colour preview** that shows exactly what the TV
   will display.
3. Scroll down to the **Brewfather batch-note tokens** block at the bottom of
   that tap's row. It shows the exact tokens (`tap:3`, `colour:#...`, `glass:...`,
   `saturation:...`) you need, built from what you configured above.
4. Click **Copy tokens** and paste them into the matching batch's **Batch Notes**
   in Brewfather.
5. Untick the override checkbox for that tap (or delete the override) and run a
   sync - the Brewfather batch now controls the tap with the same look.

The Name, ABV, IBU, OG and FG always come from Brewfather's own batch fields,
not from tokens - only the colour/glass/saturation overrides can be preset this
way.

### Which batches sync

**Completed** ones by default. Planning, Brewing, Fermenting and Archived batches
are ignored, so a beer you're still working on never appears until you mark it
Completed. Tick **Include Conditioning batches** on the Settings tab to *also* pull
batches still in **Conditioning** (lagering / maturing) - handy for a beer that's
already on tap but too green to mark Completed. When two batches (say a
Conditioning and a Completed one) claim the same tap, the most recent wins.

### Smart and safe

- **Change detection** skips rewriting files and re-downloading images for batches
  that haven't changed, so most syncs are nearly free.
- **Conflicts** (two batches claiming one tap) resolve to the most recently
  updated batch, and the clash is logged.
- **A failed sync changes nothing** - the last good board stays exactly as it was.
- A rate-limit response (HTTP 429) is honoured (respecting `Retry-After`) and makes
  no changes.

> **Field-mapping note:** Brewfather's exact field names/units can vary by account.
> The sync maps defensively - it tries several field names, prefers *measured* over
> *estimated* values, handles EBC vs SRM colour, and keeps OG/FG only when they read
> as a plausible specific gravity - and logs what it found.

---

## What happens when the internet goes down?

The board keeps running on the **last cached data**, and the served page makes
**zero outbound requests** - fonts, CSS, JavaScript and images are all local. With
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

- A per-beer **colour override** (an exact `#rrggbb`) wins everywhere - swatch and
  glass - for beers whose real colour the model doesn't nail.
- **Saturation** mutes a too-vivid colour toward grey (e.g. 60 %).
- The colour **stat number** shows in either **EBC** or **SRM** - your choice of
  unit, set once in the admin. Colour is always stored as EBC; the unit only
  changes how the number is displayed and entered.

---

## Themes

The whole palette is operator-selectable. Presets cover common screens:

- **Default** - balanced dark.
- **OLED** - true black, for OLED panels.
- **Local dimming** - slightly lifted blacks for FALD / edge-lit LCDs, to avoid
  blooming around bright text.
- **Midnight** - dark blue.
- **Daylight** - a light theme for bright rooms.
- **Custom** - pick every colour yourself.

Colours ship with the board and apply as CSS variables, so a theme change appears
on the next poll with no reload.

---

## Glassware

When a beer has no photo, its placeholder is a **beer glass tinted to the beer's
colour**, in one of several silhouettes - shaker pint, nonic pint, conical
schooner, tulip or teku - chosen globally or per beer. Because it uses the same
colour as the swatch, the pour always matches the dot.

---

## Stats

Each card can show **ABV, IBU, OG, FG** and **colour** (EBC or SRM). Every stat is
independently controllable:

- **Show / hide** globally - turn any stat off on every card.
- **Hide when empty** - keep a stat on, but drop it just for beers missing that
  value (so a card never shows a blank "OG --").
- **Per-tap overrides** for OG and FG - force them on or off for a single beer,
  regardless of the global setting.

---

## Pagination & the carousel

The board fits up to **8 cards per page** and lays them out to fill the screen. With
more beers than fit - or when you switch on pagination with a fixed page size - it
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
- A **bottom ticker** shows an announcement line - happy-hour text, an event, a
  welcome - without overlapping the grid.
- An optional **venue / company logo** sits at the top, with a configurable height
  (up to a third of the screen) and its own reserved row so it never collides with
  the cards.

The display polls the board every 30 seconds and updates only the cards whose data
changed - no full-page reloads, no flicker.

---

## Manual overrides

In `/admin` -> **Manual overrides**, each tap has a row. Tick it to control that tap
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
plain text and standard image files - open any of it in a text editor or file
browser to see exactly what the board is showing. Nothing is hidden in a database.

---

## Security

- **Admin login** is a signed, `HttpOnly`, `SameSite=Strict` session cookie
  (`Secure` when the request came over HTTPS). Login is **rate-limited** (5 failures
  / 5 minutes per client IP).
- **Secrets are plaintext on the host - a deliberate trade-off for this appliance.**
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
