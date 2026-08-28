# TV Tap List - how it works (FAQ)

A tour of what the app does and why. For getting it running, see
[INSTALLATION.md](INSTALLATION.md).

**Contents**

- [The big picture](#the-big-picture)
- [How do I display this on a TV?](#how-do-i-display-this-on-a-tv)
- [How does a beer get on the board?](#how-does-a-beer-get-on-the-board)
- [Brewfather sync](#brewfather-sync)
- [Upcoming beers](#upcoming-beers)
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
- [Snapshots: getting your board off the box and back on](#snapshots-getting-your-board-off-the-box-and-back-on)
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
| **Escapable fullscreen** | Default - just run `bash scripts/pi-kiosk.sh` | **F11** or **ESC** | Home bar, shared Pi where you want to use the desktop normally |
| **Locked kiosk** | `KIOSK_MODE=true bash scripts/pi-kiosk.sh` | **Alt+F4** (or kill the process over SSH) | Public venue, taproom where the display must stay locked |

To switch modes later, re-run the script with the desired setting - it overwrites
the launch script and asks before touching the autostart entry.

Both modes boot straight into Chromium when the Pi starts and poll the health
endpoint so they handle the Docker-on-boot startup race gracefully.

If you already have a display set up (a laptop, a smart TV's built-in browser,
anything), just point it at `http://<host>:8080` and go full-screen - no script
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

1. Lists your **Completed** batches (plus **Conditioning** and **Fermenting** ones
   if you enabled those - see below) in one paginated request per page (`complete=True`, 50 per
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
- `glass:teku` picks a glass silhouette (`willibecher`, `nonicpint`, `default`,
  `schooner`, `tulip`, `teku`, `dimpledmug`).

### Batch-note tokens reference

Put any of these in the **Batch Notes** field:

| Token | Effect |
|-------|--------|
| `tap:3` | Assign this batch to **tap 3**. Required for the beer to appear. |
| `colour:#780606` | Force an exact swatch + glass colour, overriding the EBC-derived colour. `color:` also works. |
| `glass:willibecher` | Glass silhouette: `willibecher` (the default), `nonicpint`, `default` (the shaker pint - a historical key name), `schooner`, `tulip`, `teku`, `dimpledmug`. |
| `saturation:60` | Mute the colour to 60 % (a percentage, or a `0`–`1` fraction). |
| `upcoming:` | Tease this beer as coming up, with no tap assigned. Takes no value, and is ignored on a batch that already carries a `tap:N`. See [Upcoming beers](#upcoming-beers). |

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
5. Untick the override checkbox for that tap (or delete the override) - the
   Brewfather batch takes the tap straight away, with the same look. No sync run
   needed.

The Name, ABV, IBU, OG and FG always come from Brewfather's own batch fields,
not from tokens - only the colour/glass/saturation overrides can be preset this
way.

### Which batches sync

**Completed** ones by default. Planning, Brewing, Fermenting and Archived batches
are ignored, so a beer you're still working on never appears until you mark it
Completed.

Two independent checkboxes on the Settings tab widen that:

- **Include Conditioning batches** also pulls batches still in **Conditioning**
  (lagering / maturing) - handy for a beer that's already on tap but too green to
  mark Completed.
- **Include Fermenting batches** also pulls batches still in **Fermenting**
  (primary fermentation) - handy for showing what's coming next.

Either way a batch still needs its `tap:N` note token to reach a tap, and a beer
pulled in this way looks exactly like any other on the board. Each status you add
is another paginated sweep of the API, so the call cost rises with the number of
statuses (still comfortably under the 500/hour limit at normal sync intervals).
When two batches (say a Conditioning and a Completed one) claim the same tap, the
one furthest along wins: Completed beats Conditioning beats Fermenting. Only if
both are at the same stage does the most recently updated one win. So tagging
next week's brew with a tap it will take over does not knock the beer that is
pouring off the board.

### Smart and safe

- **Change detection** skips rewriting files and re-downloading images for batches
  that haven't changed, so most syncs are nearly free.
- **Conflicts** (two batches claiming one tap) resolve to the batch furthest
  along its brew (Completed, then Conditioning, then Fermenting), falling back to
  the most recently updated one when both are at the same stage. The clash is
  logged either way.
- **A failed sync changes nothing** - the last good board stays exactly as it was.
- A rate-limit response (HTTP 429) is honoured (respecting `Retry-After`) and makes
  no changes.
- **Only your `tap:` tokens decide what's on the board.** A beer leaves a tap when
  you remove its token, and for no other reason. Your **tap count** is purely a
  display setting: lowering it hides the slots above the new number without
  discarding their beers, and raising it back shows them again.

> **Field-mapping note:** Brewfather's exact field names/units can vary by account.
> The sync maps defensively - it tries several field names, prefers *measured* over
> *estimated* values, handles EBC vs SRM colour, and keeps OG/FG only when they read
> as a plausible specific gravity - and logs what it found.

---

## Upcoming beers

The board can show what is coming next as well as what is pouring. An **upcoming
beer** is a beer that is not on a tap yet, drawn as a teaser card: the same
layout as a tap card, marked out by a dashed amber border and a ribbon reading
"Coming up".

None of this happens until **Show upcoming beer previews** is ticked on the admin
Settings tab. With it off the board behaves exactly as it does without the
feature at all.

### Which batches count as coming up

Two Batch Notes tokens decide it:

- **`tap:N` on a batch that is not Completed.** The batch names the tap it is
  headed for, and its teaser is *bound* to that tap. A conditioning batch that
  lost tap 3 to a completed one or to a manual override, and a fermenting batch
  tagged for tap 3 while something else pours there, both tease on tap 3.
- **`upcoming:` on a batch with no `tap:N`.** The token takes no value - its
  presence alone means "tease this beer". The teaser is *unbound*, and says
  plainly on the card that no tap is assigned yet.

`tap:N` wins on a batch carrying both, so a beer headed for a known tap is never
demoted to an unassigned teaser. A completed batch that is not the one holding
its tap is left out entirely: that is a beer which has been pulled, not one that
is coming up. Two beers may be tagged for the same tap, and both tease.
A beer tagged for a tap number higher than your tap count is treated as
unassigned rather than pointing at a tap nobody can see; raising the tap count
binds it again on the TV's next poll.

The sync scope still applies. **Conditioning** and **Fermenting** batches reach
the box only when **Include Conditioning batches** or **Include Fermenting
batches** is on, so a beer in one of those stages cannot be teased until the
matching setting is ticked. Turning previews on does not widen what is fetched
and costs no extra Brewfather calls, and Planning and Brewing batches are never
fetched at all. The Upcoming section of the admin says which scope setting is
standing in the way when one is.

### Where a teaser shows up

Three surfaces, in the order the board prefers them:

- **On its own tap, permanently.** A vacant tap with a beer bound to it
  advertises that beer instead of showing a Vacant card, and stays on the board
  even when "hide vacant taps" is on, because it has something to show. This
  is the case the feature was built for.
- **Cross-faded over the tap it will pour on.** When the bound tap is already
  pouring something, the teaser fades in over that card, holds, and fades back
  out again - the clearest thing the board can say, because the teaser
  appears exactly where the beer will pour. Switching **Allow a pouring beer to
  be cross-faded out** off means no pouring beer is ever covered, even briefly.
- **The optional overflow surfaces.** An **on-deck page** (a full page in the
  normal rotation, with its own dot, shown like any other page) and a
  **half-board panel** (an interruption sliding over the bottom half of the
  board on its own beat) each carry the beers the first two cannot reach: an
  unbound teaser has no card to fade over, and nothing bound to a pouring tap
  is reachable when cross-fading is off. Both are off by default and simply do
  not appear when there is nothing to carry.

**Surface carries** decides what those two list. *Overflow only*, the default,
lists just the beers nothing else is showing. *All upcoming* lists every one,
including a beer already sitting permanently on a vacant tap, so a surface that
claims to list everything really does. Under *All upcoming* the same beer can
appear in two places at different moments, which is expected rather than a fault.

One setting, **Upcoming beers appear every**, drives the cadence for the
cross-fade and the panel (20 seconds by default, 5 to 300). How long a teaser
holds on screen is worked out from that one number rather than being a second
thing to tune. The on-deck page needs no cadence of its own: it is an ordinary
carousel page and rotates on the same timer as every other page. The panel
takes its turn on a multiple of the cadence - every second turn by default,
adjustable from 1 to 6 - which is what stops it taking so many turns that a
beer late in the list never gets one. With both surfaces on, the panel skips
any turn that would land while the on-deck page is showing: the page already
lists everything the panel would, so the two never stack.

**Max upcoming previews shown** caps how many upcoming beers are shown at all
(3 by default, up to 20). A beer pinned to a vacant tap is exempt from the cap:
it fills a card that would otherwise just say "Vacant", so it adds nothing for
the cap to trim. The cap orders the rest most-ready-first and is applied when
the board is drawn, so changing it reaches the TV on its next poll with no
sync needed.

### What a teaser says, and why its ABV carries a `~`

Under the beer's name a status line answers the question a customer actually has,
which is not "is something coming" but "how soon": it reads **Ready**,
**Conditioning**, **Fermenting**, **Brewing** or **Planned**, in plain language
rather than Brewfather's own status words. That line can be switched off. The
ribbon's wording is the operator's choice - "Coming up", "Up next", "Coming
soon", "Just around the bend", or anything typed in up to 32 characters.

The same status words are available on ordinary tap cards through a separate
setting, off by default, which marks a beer that is pouring but is still
conditioning. **Ready** never appears on a tap card: a beer that is pouring is
self-evidently ready.

A beer still in the tank - fermenting, brewing or only planned - is described
**from its recipe**: colour, IBU, OG, FG and ABV all come from the recipe
together, never mixed with a half-finished measurement. A recipe ABV printed
beside a gravity taken mid-fermentation would describe a beer that never existed.
A beer that is actually pouring is untouched by this rule and still prefers its
measured readings, and so does a conditioning beer, which exists physically at
the reading it was measured at.

That is also why an upcoming beer's ABV is **off by default**, and why it always
carries a `~` when it is switched on: the number is a target rather than a
promise. Teaser cards otherwise obey the same stat visibility settings as tap
cards, so a board with OG and FG switched off does not sprout them on a teaser.

### The cache, and what happens to it

Upcoming beers are cached as plain files in `upcoming/` in the mapped data
directory, keyed by Brewfather batch, and rebuilt from scratch on every sync.
The directory is disposable by design:

- **Deleting it by hand is safe.** The next sync rebuilds it.
- **Turning previews off deletes it.** This is the only setting on the box that
  deletes files, so the admin says so at the toggle. Turning previews back on
  shows nothing until the next sync finishes, which is the same wait as any first
  sync and is visible in the admin's sync status.
- **Snapshots never carry it.** Restored onto a box that syncs, the upcoming
  beers would be rewritten within minutes anyway; restored onto a box with no
  Brewfather key, they would be a queue of teasers that can never update and
  never resolve, advertising beers that may already have poured and gone.
- **Nothing in it is ever archived** and the daily cleanup leaves it alone. When
  a batch stops qualifying, the next sync simply does not write it and the
  rebuild removes the stale file, so `old_beers/` stays a record of beers that
  really poured.
- **A failed sync changes nothing**, and with the internet down the last cached
  teasers keep showing, exactly as the taps do.

Editing a file in `upcoming/` by hand works until the next sync overwrites it.
There is no manual source above this cache the way there is for a tap, because an
upcoming beer is a projection of a Brewfather batch rather than a beer somebody
entered.

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
colour**, in one of several silhouettes - nonic pint (the default), shaker pint,
conical schooner, tulip or teku - chosen globally or per beer. Because it uses
the same colour as the swatch, the pour always matches the dot.

The nonic is the default because it still reads as a beer glass once a busy
board shrinks it to a thumbnail. Note the shaker pint's key is `default`, which
is a historical name rather than a claim about which glass is selected: set
`glass:default` to get the shaker.

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
slot back to Brewfather **immediately** - no sync run needed.

This is how you put a guest tap, a one-off, or anything not in Brewfather onto the
board.

### Two files for one tap is normal

While an override is up, the Brewfather beer for that tap is still there,
underneath it: the sync keeps it current even though nothing shows it. So an
overridden tap 3 legitimately has **both** a `custom_tap_3.md` and a
`bf_tap_3.md` in `taps/`. The manual one is what's on the board; the Brewfather
one is waiting. That's what makes unticking the override instant - the beer is
already on disk and up to date.

If a batch is waiting under an override, the tap's row in `/admin` names it, so
you can see what unticking will reveal before you click. Don't delete the
"extra" file by hand: you lose the instant switch-back, and the tap goes vacant
until the next sync rebuilds it.

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
directory](INSTALLATION.md#the-data-directory)). Your settings live in
`config.json` and the app's own bookkeeping - when it last synced, what the last
update check found - lives in `status.json`; each beer is a small Markdown file
in `taps/` with its image alongside. It's all
plain text and standard image files - open any of it in a text editor or file
browser to see exactly what the board is showing. Nothing is hidden in a database.

### What if the data directory is not really saving anything?

The appliance checks at startup and tells you in `/admin`, because the failure is
otherwise invisible: writes succeed, the board renders, and the loss only shows
up later as missing manual beers.

- **"Data is not being saved"** means no host directory is mapped to `/data`, so
  everything is written inside the container and goes when the container is
  recreated. Map a host directory and restart. The banner has no dismiss button
  on purpose: it describes something that is still true.
- **"The data directory changed"** means the mapped directory is empty, or is a
  different directory from the one this container last used - a host folder that
  was deleted, or storage that was not mounted before Docker started. It appears
  once, on the first boot that notices.

The appliance keeps a small file called `.data_dir_id` in the data directory to
tell those two situations apart. It holds a random identifier and nothing else.
Leave it alone; deleting it looks exactly like a wipe and costs you a warning.

`DEMO_MODE` suppresses both banners, since a demo box is meant to be disposable.

---

## Snapshots: getting your board off the box and back on

The **Snapshot** tab in `/admin` downloads the board as it stands right now, as a
single zip that mirrors your data directory. Because it mirrors the layout,
restoring one is just unpacking it.

A Snapshot contains:

- your settings (`config.json`),
- every tap file and photo, from **both** sources - the ones you entered by hand
  and the ones Brewfather synced,
- the archived beers in `old_beers/`,
- your venue logo and the placeholder image, which sit at the top of the data
  directory. The logo is in because your settings refer to it by filename, and
  restoring settings without it leaves a broken reference.

It never contains `status.json` or `.data_dir_id`. Both belong to the box rather
than to the data: the status values all regenerate on the next cycle, and the
identifier names *which directory this box is using*, not which beers are in it.
Copying it onto a second box would let two boxes claim one identity, which is the
exact confusion it exists to detect.

### The Brewfather credentials

The export offers an **Include the Brewfather credentials** checkbox, unticked,
and offers it only when your API key is stored in `config.json`. If your key
comes from the `BREWFATHER_API_KEY` environment variable, or you have not set one,
there is no checkbox - the export never reads environment values, it only carries
what `config.json` holds.

Leave it unticked and both Brewfather fields in the Snapshot are blank; you paste
the key in after restoring. Tick it and the key is written into the zip **in
plaintext**. A Snapshot is not encrypted and is not password-protected, so a
Snapshot that carries your key should be treated exactly like the key itself.

### Restoring

Choose the file on the Snapshot tab and click **Upload and check**. The whole zip
is examined before anything is written: a file that is not a Snapshot, or one
whose contents are damaged, is refused whole and nothing on the box changes.
Restoring then replaces any file the Snapshot carries and leaves everything else
alone. It is a restore, not a wipe.

You can also restore by hand: stop the container, unzip the Snapshot into your
data directory, and start it again. That is a supported path, and it is why there
is no manifest or marker file inside the zip - it would only end up as a stray
file in your data directory.

### Why importing asks about Brewfather

Before restoring, the box asks one question: **will this box have a working
Brewfather key when the import finishes?**

That matters because a box that syncs rewrites every Brewfather tap within
minutes. Importing the Snapshot's Brewfather beers onto a box that will sync
would show them briefly and then silently replace them - the import would appear
to work and then quietly undo itself. So keeping a working key and importing the
Brewfather beers are mutually exclusive, and the box asks rather than losing that
race invisibly.

You are asked only where you control the answer:

| Your situation | What happens |
|---|---|
| Your key comes from an environment variable | No question. An import cannot clear an environment variable, so the box will keep syncing. The Snapshot's Brewfather beers are skipped and the admin says so. |
| A key exists on either side - on the box, in the Snapshot, or both | You choose: **keep syncing** (the Brewfather beers are skipped, and the next sync fills them back in) or **stop syncing** (the key is cleared and the Snapshot's Brewfather beers are imported and stay). |
| Neither the box nor the Snapshot has a key | No question. Everything is imported. |

Your manually entered beers, the archive, the venue logo and every other setting
are restored in all cases.

One more rule worth knowing: **your box's own key always wins.** A Snapshot's
credential is only used to fill a field you have left empty, so an import can
never replace a working key with an older one and leave you wondering why sync
started failing.

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
