# Changelog

What changed in each release, written for the operator running the box rather
than for the person who wrote the commit.

**Every release must have an entry here before its tag is pushed.** The publish
workflow uses the section matching the tag as the GitHub Release body, so a
missing entry means a release that only says "Full Changelog: compare/...".
See [docs/VERSIONING.md](docs/VERSIONING.md#the-changelog) for the rules.

**Merged work that is not released yet goes under `## Unreleased`.** Add to that
section as changes land on `main`, rather than saving it all up for release day.
At release time the heading becomes `## vX.Y.Z - YYYY-MM-DD` and a fresh empty
`## Unreleased` takes its place. `scripts/release_notes.sh` matches a version
heading only, so an Unreleased section is never published as a release body -
and a tag pushed while the notes still sit under `## Unreleased` fails the
build, which is the intended safety net.

Versions follow [Semver](https://semver.org/). Dates are the release date.

---

## Unreleased

Merged to `main` and shipping in the next release.

Closed so far: [#6](https://github.com/jceccato/tv-taplist/issues/6),
[#9](https://github.com/jceccato/tv-taplist/issues/9),
[#10](https://github.com/jceccato/tv-taplist/issues/10),
[#11](https://github.com/jceccato/tv-taplist/issues/11),
[#13](https://github.com/jceccato/tv-taplist/issues/13),
[#28](https://github.com/jceccato/tv-taplist/issues/28),
[#29](https://github.com/jceccato/tv-taplist/issues/29),
[#30](https://github.com/jceccato/tv-taplist/issues/30),
[#32](https://github.com/jceccato/tv-taplist/issues/32),
[#33](https://github.com/jceccato/tv-taplist/issues/33).

### Coming up: teaser cards for beers that are not pouring yet ([#4](https://github.com/jceccato/tv-taplist/issues/4))

The board can now advertise what is coming as well as what is pouring. The whole
feature sits behind one switch, **Show upcoming beer previews**, off by default -
and off means the board behaves exactly as it did before this release.
(Issues [#34](https://github.com/jceccato/tv-taplist/issues/34) through
[#45](https://github.com/jceccato/tv-taplist/issues/45), the sub-issues of #4.)

Brewfather sync understands a valueless `upcoming:` note token, and each
Brewfather tap file records its batch's status - the groundwork the teaser
cards below are built on, landed together so the whole feature costs one cache
rewrite rather than several.

There is a new setting, **Show upcoming beer previews**, off by default. With it
on, each sync works out which batches are coming up - bound to a tap by the
usual `tap:X` note, or unassigned via the new `upcoming:` note - and caches them
under `/data/upcoming/` in the mapped data directory.

Two things worth knowing about that cache. Turning the setting off **deletes**
it, which is the only setting on the box that deletes files, so the admin says
so at the toggle. And it is disposable by design: deleting the directory by hand
is safe, the next sync rebuilds it, snapshots never carry it, and nothing in it
is ever archived to `old_beers/`.

Conditioning and fermenting beers still need the matching sync-scope setting
before they can be teased. Turning previews on does not widen what the box
fetches from Brewfather, so it costs no extra API calls.

The board API now serves those upcoming beers, worked out the same way a tap
card is: the same stat visibility settings, the same colour rules. They are
ordered most-ready first, then newest first, and a new **Max upcoming previews
shown** setting caps how many appear (3 by default, up to 20). That cap is
applied when the board is drawn rather than when it syncs, so changing it takes
effect on the next refresh of the TV without waiting for a sync. A beer pinned
to a vacant tap is exempt from the cap: it fills a card that would otherwise
just say "Vacant", so it adds nothing for the cap to trim - without the
exemption, a nearly-ready beer waiting on its empty tap could be pushed off the
board by finished beers that have no tap at all.

An empty tap with a beer assigned to it is the case the feature was built for,
and it is settled here: that tap advertises the beer permanently, and it stays
on the board even when "hide vacant taps" is on, because it now has something to
show. A beer assigned to a tap number higher than the board actually has is
treated as unassigned rather than pointing at a tap nobody can see; raising the
tap count re-attaches it on the next refresh.

The first thing a customer sees now works: **a vacant tap with a beer waiting on
it shows that beer** instead of the plain "Vacant" card. The teaser is drawn
exactly like every other card on the board - same size, same stats, same colour
swatch, same photo or tinted glass - and is marked out only by a dashed amber
border, so it reads as "not pouring yet" without shouting. A vacant tap with
nothing coming still shows the ordinary Vacant card.

Teaser cards obey the same stat visibility settings as tap cards, so a board with
OG and FG switched off does not sprout them on a teaser.

The card now says what it means. A ribbon carries wording the operator chooses -
"Coming up", "Up next", "Coming soon", "Just around the bend", or anything typed
in, up to 32 characters with a live counter in the admin. Under the beer's name
a status line answers the question a customer actually has, which is not "is
something coming" but "how soon": it reads **Ready**, **Conditioning**,
**Fermenting**, **Brewing** or **Planned**, in plain language rather than
Brewfather's own words. That line can be switched off.

Two smaller choices. An upcoming beer's ABV is **off by default**, because a
number on an unfinished beer is a target rather than a promise; switched on, it
always carries a `~` so nobody reads it as final. And a beer with no tap assigned
yet always says so on the card, since nothing else on it would. A beer that does
have a tap does not repeat itself by default, but there is a setting for
operators who want the tap number spelled out in words.

**The cross-fade.** A beer coming up on a tap that is currently pouring now
fades in over that tap's card for a few seconds and fades back out again. It is
the clearest thing the board can say, because the teaser appears exactly where
the beer will pour. One setting, **Upcoming beers appear every**, drives the
timing (20 seconds by default, between 5 and 300). How long each teaser holds is
worked out from that one number rather than being a second setting to tune, so a
slower cadence reads as "occasionally, and lingers" instead of "rarely, and
flickers".

Operators running a busy service can switch **Allow a pouring beer to be
cross-faded out** off, and no pouring beer is ever covered, even briefly. Those
beers then wait for one of the optional surfaces described below.

**Demo mode now shows the feature.** A fresh demo start turns previews on and
seeds two upcoming beers - one on an empty tap, visible immediately with no
waiting, and one with no tap assigned - so the feature can be evaluated offline
without a Brewfather account.

**The on-deck page.** The cross-fade cannot reach every beer: one with no tap
assigned has no card to fade over, and if cross-fading is switched off then
nothing bound to a pouring tap can be reached either. Those beers are the
overflow, and an optional **on-deck page** now carries them - a full page in
the normal rotation, with its own dot, reachable by hand and shown by the
carousel exactly like every other page. There is nothing extra to tune: it
simply takes its place in the rotation.

A scope setting decides what it lists. **Overflow** (the default) shows only the
beers nothing else is showing. **All** shows every upcoming beer, including ones
already sitting on an empty tap - so a page that claims to list everything really
does. A beer may then appear in both places, which is expected: the differing
timings mean it is never in two places on screen at the same moment. With nothing
to carry, the page does not appear at all rather than showing up empty.

**The half-board panel.** The second optional surface: instead of a whole page,
a panel slides over the bottom half of the board, carrying the same overflow
set. It reads as an interruption - lighter ground, a dashed border all round, an
inset margin and a shadow - not as a permanent part of the layout, and it takes
its turn every second beat by default (adjustable 1 to 6). Like the page, it
simply does not appear when there is nothing to carry. With both surfaces on,
the panel skips any turn that would land while the on-deck page is showing:
the page already lists everything the panel would, so the two never stack.

**A conditioning beer on tap can say so.** A separate setting, off by default,
adds a small status line to a pouring tap card whose batch is not finished -
"Conditioning", or "Fermenting" when fermenting batches are included and one is
pouring. This works on boards that never enable upcoming previews at all. A
finished beer is never marked, because a beer that is pouring is self-evidently
pouring, and a manually entered tap has no batch behind it so it is never marked
either.

**The admin now says what is actually resolved.** The Upcoming section of the
settings shows live numbers from the board's own reckoning: how many upcoming
beers exist, how many sit pinned on empty taps, how many the cross-fade reaches,
and how many are overflow. An enabled surface with an empty overflow is correct
behaviour that used to look like a broken toggle; the admin now explains it
instead. It also points out when Include Conditioning or Include Fermenting must
be switched on before a tagged batch can appear at all.

**What upgrading costs the operator:** the mapping version moves from 6 to 7, so
the first sync after upgrading rewrites every cached Brewfather tap file once
and re-downloads their images once. That is a single slower sync cycle; after it
the sync settles back to skipping batches that have not changed. Nothing needs
doing by hand, and nothing on the TV changes while it happens.

### A mistyped value in a tap file no longer changes which beer is shown ([#32](https://github.com/jceccato/tv-taplist/issues/32))

Tap files in the data directory are meant to be editable by hand. A beer's
fields now go through one definition of what a beer is, so a value that cannot
be read - `abv: banana`, a colour that is not a colour - is simply dropped, and
the rest of that beer still pours on its own tap. It is logged once, when
something writes the file, rather than on every refresh of every TV. One real
crash goes with it ([#33](https://github.com/jceccato/tv-taplist/issues/33)):
a date typed without quotes used to blank every screen in the venue.

This otherwise changes nothing about running the box. Same file names, same
keys, no migration, and the display sees exactly what it saw before. What it
changes is what it takes to add a second beer source without the same quiet
drift that left demo taps missing half their fields for months.

### Take your whole board off the box, and put it back ([#29](https://github.com/jceccato/tv-taplist/issues/29))

There was no way to get your data off the box or back onto it. A new **Snapshot**
tab in the admin downloads the board as it stands - your settings, every tap file
and photo from both sources, the archived beers, and your venue logo - as one zip
that mirrors your data directory. Restoring is unpacking it: import it on the same
tab, or unzip it straight into the data directory of a stopped container. The sync
status and the `.data_dir_id` file are deliberately left out; both belong to the box
rather than to the data.

Your Brewfather key is **not** included unless you tick the box, and that box only
appears when the key is stored in `config.json` rather than in an environment
variable. A Snapshot is not encrypted, so one carrying your key should be treated
exactly like the key itself.

Importing asks one question first: will this box have a working Brewfather key when
the import finishes? If it will, the Snapshot's Brewfather beers are skipped and the
admin says why - the next sync would rewrite them within minutes anyway, so importing
them would appear to work and then quietly undo itself. Your manual beers, the
archive, the venue logo and every other setting are restored either way, and your
box's own Brewfather key is never replaced by the Snapshot's. A file that is not a
Snapshot is refused whole, with nothing on the box changed.

### Redrawn beer glasses, four new ones, and a new default ([#6](https://github.com/jceccato/tv-taplist/issues/6))

The five glass silhouettes drawn for taps with no photo have been remodelled by
hand and redrawn from scratch: the shaker pint has a proper straight-sided
taper, the nonic pint is stubbier with a wider mouth and a gentler bump, the
conical schooner is the Australian bell that meets the table square instead of a
plain cone, the tulip has a straight collar over a high-shouldered bowl, and the
teku is a wine-glass bowl on a full-length stem rather than an hourglass. They
read as recognisable glassware from across a room, which the old shapes did not.

**Four glasses have been added**, taking the set to nine. A **Willi Becher** -
the tapered glass most venues pour into now - and a **dimpled mug**: a squat
barrel with a handle whose facets are cut into the beer rather than drawn over
it, so the courses interlock and the outer ones are sliced in half by the edge
of the glass the way a real mug's are.

Then a **pilsner flute** and a **weizen**, the two the list was most obviously
missing. The flute is the tallest and narrowest of the set, one long taper from
a wide mouth to a base a third that width. The weizen is a vase: a narrow rim
over shoulders carrying the widest point, a long waisted shaft, and a base that
flares back out. Both are drawn to survive being shrunk - on the weizen the
shoulders and the waist are what stop it reading as just another tall glass, and
they are what the shape was tuned against. Pick any of them globally in the
admin, or per beer with `glass:willibecher`, `glass:dimpledmug`,
`glass:pilsnerflute` or `glass:weizen`.

**The default glassware is now the Willi Becher.** Both it and the nonic pint
survive being shrunk to a thumbnail on a busy board, where the shaker's straight
sides start to look like a tumbler. This only affects boxes that never chose
one: an operator who picked a glass - globally or per beer - keeps exactly what
they picked, and every previous glass is still there under its own name.

**The beer now reaches the lip of the glass, and the head has depth.** Each
glass's foam was sized by eye and every one but the mug came out a little
narrow, so the pour stopped short of the rim and looked like it had gone flat.
The head is now measured to each glass's real mouth, and it is a band of foam
sitting on top of the beer with a curved underside rather than a lid laid over
it - the difference between a drawn glass and a poured one.

Two more things that were quietly broken are fixed with them. Stemmed glasses
drew their foot a few pixels clear of the stem, so a tulip or teku always had a
base floating under it. And the clear glass of a stem was a near-white tint that
disappeared on the Daylight theme; it is now a mid-grey that reads on both the
dark themes and the light one.

Nothing to do after upgrading - the placeholders redraw themselves. Uploaded
beer photos are untouched.

### Nothing publishes without a green suite ([#30](https://github.com/jceccato/tv-taplist/issues/30))

The test suite has never gated a publish. A merge to `main` or a version tag
built and pushed an image whether or not the 243 tests passed, or whether anyone
had run them. Since `:latest` started following releases in v1.3.1, that
unverified build is what every default installation pulls.

Now the suite runs on **every pull request**, and the publish workflow calls the
same definition as a gate: a red suite skips the build entirely, so no image is
pushed and no release is created. On a version tag that leaves the tag in git
with nothing published, recoverable the same way a missing changelog entry is.

The tests run on **Python 3.12**, the version the image ships, rather than
whatever the runner defaults to.

This changes nothing about running the box. It changes what it takes for a
change to reach it.

### The admin warns when your data is not actually being saved ([#28](https://github.com/jceccato/tv-taplist/issues/28))

Nothing used to check that the mapped data directory was real. An operator whose
data was not persisting found out when their manual beers were gone - and only
the manual ones, because Brewfather taps rebuild on the next sync.

Two checks now run at startup. The first notices that no host directory is
mapped to `/data`, which means settings, the Brewfather key and manual beers are
written inside the container and vanish the next time it is recreated, including
on the next update. The second notices that the mapped directory is empty, or is
a different directory from the one the container last used - what a deleted host
folder, or storage that was not mounted before Docker started, looks like.

Either one puts a banner on `/admin` and the same message in the container log.
Nothing appears on the TV, and the box never refuses to start: a durability
warning should not become an outage on an appliance whose whole point is serving
through failures. `DEMO_MODE` suppresses both banners, since a demo box is meant
to be disposable.

A small `.data_dir_id` file now sits in the data directory. It holds a random
identifier and nothing else, and it is how the second check tells a container
recreate apart from a wipe. Leave it alone - deleting it looks like a wipe and
costs you one warning.

**The image no longer declares `/data` as a Docker volume.** This is what makes
the first check reliable: an unmapped data directory is now plainly unmapped
rather than silently receiving a throwaway volume that accepts every write and
survives nothing. An operator who mapped a host directory as documented does
nothing. An operator who did not was already losing data on every container
recreate; this does not make that worse, it makes it visible.

### A beer with no colour data no longer looks broken ([#11](https://github.com/jceccato/tv-taplist/issues/11))

A beer with neither a colour nor an EBC reading used to render a grey swatch
beside an amber glass on the same card, because the colour was worked out
separately in four places and they did not agree. Colour is now resolved once,
so the swatch and the placeholder glass always match when the beer has a colour,
and each falls back to its own sensible default when it does not.

A colour override combined with a saturation now behaves as documented: the
override is used exactly as written and is never muted. Saturation was only ever
meant to tame the computed colour.

### The display is told what to show, not how to work it out ([#9](https://github.com/jceccato/tv-taplist/issues/9))

The five show/hide settings, their "hide when empty" partners and the per-tap
overrides were all sent to the TV, which then applied the rules itself. The
board now applies them and sends the answer. The same settings produce the same
board, and the admin is unchanged - the per-tap override is still yours to set.

### Settings limits now match between the form and the box ([#13](https://github.com/jceccato/tv-taplist/issues/13))

The number-of-taps field had a minimum but no maximum, so a value above the
supported limit appeared to save and then snapped back to 200 when the page
reloaded, with nothing explaining why. Every numeric Settings field now takes
its limits from the server, so an out-of-range value is refused as you type and
what the form accepts is what the box stores.

Editing `config.json` by hand still clamps rather than refusing to start, which
is deliberate - a file has no one to report an error to.

### Changed: the `/api/board` payload ([#11](https://github.com/jceccato/tv-taplist/issues/11), [#9](https://github.com/jceccato/tv-taplist/issues/9))

Only the built-in display consumes this, but the endpoint is public, so if you
read it directly:

- The five `show_*` and five `hide_*_when_empty` flags are gone from the top
  level, and each tap now carries `abv_visible`, `ibu_visible`, `ebc_visible`,
  `og_visible`, `fg_visible` and `swatch_visible` instead of `color_known` and
  its per-tap `show_og` / `show_fg` values.
- `color_hex` and `text_color` are `null` for a beer with no colour data, where
  they previously carried a grey, and are omitted on vacant taps.
- The no-photo glass image is now `/img/beer-glass?hex=<rrggbb>`; the `ebc` and
  `sat` parameters on that route are gone.

`color_unit` and `show_source_badge` are unchanged.

### Also

- **Documentation fix** ([#30](https://github.com/jceccato/tv-taplist/issues/30)):
  the versioning guide contained two contradictory
  answers about whether this project keeps a changelog, the wrong one nearer
  the end. It now has one.
- **Internal** ([#10](https://github.com/jceccato/tv-taplist/issues/10)): the
  Brewfather integration is split into a fetch half and a mapping half. Nothing changes for the operator - beers map to taps by exactly
  the same rules, cached tap files are not rewritten, and no setting moves.

---

## v1.3.2 - 2026-08-20

A small release. **No migration, no settings change:** pull and restart.

### Swipe between pages on a touch screen

The display has always rotated through its pages on a timer, and an operator
could jump to a page with the dots or the keyboard. On a touch screen the dots
were the only option, and on a TV-sized layout they are a small target.

Now a **horizontal swipe anywhere on the board changes page**: swipe left for
the next page, swipe right for the previous one. Both wrap, so a swipe back
from the first page lands on the last. The rotation timer restarts on a swipe,
so the page you chose is not flipped away a moment later.

Nothing to turn on and nothing to configure. Tapping a page dot still works
exactly as before, a short or mostly-vertical drag leaves the page alone, and
keyboard navigation is unchanged.

### A Windows install guide

Docker Desktop on Windows is a common way to run this, and until now the docs
had nothing about it. [WINDOWS.md](docs/WINDOWS.md) covers the data directory
and the path forms that work, how to confirm it is really persisting before you
commit real data to it, what `PUID` and `PGID` actually do on Windows, where to
put the files relative to WSL2, and reaching the display from the TV.

### Also

- An internal test now covers a piece of the Brewfather sync that was only
  covered by convention. No behaviour changed.

---

## v1.3.1 - 2026-08-19

Housekeeping on how a release reaches you. **No migration, no settings change:**
pull and restart.

### `latest` now means the newest release

`ghcr.io/jceccato/tv-taplist:latest` used to move on every merge, so a box
tracking it could be running unreleased work while the admin panel still named
the last release as current. Two ideas of "current" that disagreed.

Now there is one: **a version tag.** `:latest` moves when a release is
published, which is the same moment the in-app update checker notices it. The
version the admin reports is the version `:latest` serves.

Nothing to change on your end - every install guide already points at
`:latest`, and it now delivers what those guides promise. If you want the
unreleased edge, that moved to `:main`, rebuilt on every merge.

| Tag | What you get |
|-----|--------------|
| `:latest` | The newest release. The default. |
| `:v1.3.1` | One exact release, pinned. |
| `:main` | Unreleased work. Expect rough edges. |
| `:<short-sha>` | One exact build, released or not. |

### "Check for updates" stops claiming things it cannot know

A container built from `main`, a local dev run, or a SHA-pinned build cannot be
compared against the release history - the running version is not a release
number. The checker has always known that and refused to compare, which is
correct. It then reported the refusal as **"Up to date"**, which was not: a box
genuinely behind a release was being reassured it was fine.

The admin now says what is actually true, and names the newest release so you
can judge for yourself:

> Running an untagged build (main). Update checks only apply to tagged
> releases; latest release is v1.3.1.

A tagged release still reports "Up to date" or offers the update, unchanged.

### Also

- **Docs-only changes no longer rebuild the image.** A typo fix in the FAQ used
  to produce a full build and a new image version.
- **The version string in `app/__init__.py`** no longer carries a number of its
  own - it reads the same value the running container was built with, so it
  cannot go stale. It had been wrong since v1.1.0.

Closes #24, #25, #26.

**Full changelog:** https://github.com/jceccato/tv-taplist/compare/v1.3.0...v1.3.1

---

## v1.3.0 - 2026-08-19

Control over how big the cards are, beers that appear before they are finished,
and a fix for taps changing under you. **No migration:** your `config.json` and
tap files are read as they are, and downgrading works the same way.

### Card sizing is yours to set

Photos and description text are now two independent controls on the **Settings**
tab, each with presets and a slider.

- **Photo:** Tiny, Small, Medium, Default. Default is exactly what you have
  today - nothing shrinks unless you ask it to.
- **Text:** Small, Default, Large.

One thing to expect, because it is the layout working as intended: **a photo can
only ever shrink.** The beer name and description claim their space first and the
photo takes what is left, which is what stops a photo from covering the text. So
at the Large text preset photos get smaller regardless of the photo setting.

Two things that were quietly broken in this area are fixed. Photo settings above
0.85 used to do nothing at all on a landscape photo, because the cap was measured
against the box rather than the picture painted inside it - the whole top of the
range was dead. And text scaling did nothing on a 4K panel, where the text was
already pinned at its ceiling before the setting was applied. Both work now, and
the photo re-measures itself when the screen or window changes size, so rotating
a tablet no longer leaves the old size in place.

### Beers can appear before they are finished

Two independent toggles on the **Settings** tab widen what the sync pulls from
Brewfather:

- **Include Conditioning batches**
- **Include Fermenting batches**

Both are off by default, so nothing changes unless you turn them on. A batch
still needs a `tap:` token to appear - a fermenting beer is treated exactly like
a finished one. Each extra status is another sweep of the Brewfather API, which
is why they are opt-in, though three statuses still sit comfortably inside the
rate limit at normal sync intervals.

### A pouring beer no longer gets pushed off its tap

With those toggles on, two batches can claim the same tap - typically the beer
currently pouring and the next one already fermenting. The tap used to go to
whichever batch was **edited most recently**, which is exactly the wrong answer:
a fermenting batch gets edited constantly while a finished one sits still, so the
next brew would take the tap from the beer actually on it.

**The most complete status now wins:** Completed, then Conditioning, Fermenting,
Brewing, Planning. Recency only decides between two batches at the same status.
Either way the clash is logged with both batch names, both statuses, and which
rule decided it.

### Under the hood

Six machine-written fields - sync timestamps, the last sync error, update-check
findings - moved out of `config.json` into a new `/data/status.json`. Your
settings file now holds only settings.

The reason is a safety rule that cannot apply to both kinds of data at once.
`config.json` is never overwritten with defaults when it cannot be read, because
it holds things only you can recreate. Status wants the opposite: it regenerates
every cycle, and the same guard there could strand a healthy box reporting "never
synced" forever. Splitting the files lets each have the policy it needs. The
reasoning is in `docs/adr/0002-config-status-separation.md`.

The move happens once, automatically, on first start. Verified against a real
data directory: three fields relocated, no setting value altered.

Test suite: 173 to 233 tests.

Closes #1, #3, #7.

**Full changelog:** https://github.com/jceccato/tv-taplist/compare/v1.2.0...v1.3.0

---

## v1.2.0 - 2026-08-17

A rework of how taps are stored and resolved, a guided installer, and kiosk
guides for Raspberry Pi and Android TV. Clearing a manual override now reveals
the Brewfather beer immediately instead of leaving the tap blank until the next
sync; changing the tap count became purely a display decision, archiving
nothing; and a beer leaves the board only when you remove its `tap:` token.

Note that an overridden tap legitimately has **two files** from this release on
(`custom_tap_3.md` and `bf_tap_3.md`) - that is what makes switching back
instant. Do not delete the "extra" one.

Full notes: https://github.com/jceccato/tv-taplist/releases/tag/v1.2.0

---

## v1.1.0 - 2026-07-18

No written notes; this changelog started at v1.2.0 and v1.1.0 was not
reconstructed.

**Full changelog:** https://github.com/jceccato/tv-taplist/compare/v1.0.0...v1.1.0

---

## v1.0.0

First release.
