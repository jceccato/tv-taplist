# Changelog

What changed in each release, written for the operator running the box rather
than for the person who wrote the commit.

**Every release must have an entry here before its tag is pushed.** The publish
workflow uses the section matching the tag as the GitHub Release body, so a
missing entry means a release that only says "Full Changelog: compare/...".
See [docs/VERSIONING.md](docs/VERSIONING.md#the-changelog) for the rules.

Versions follow [Semver](https://semver.org/). Dates are the release date.

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
