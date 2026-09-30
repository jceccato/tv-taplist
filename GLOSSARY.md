# TV TapList

An offline-first digital beer tap list for TV displays. The container holds all
state and logic; the TV is a thin client that loads `/`. This glossary is the
project's ubiquitous language - use these terms in issues, tests, comments, and
refactor proposals rather than drifting to the synonyms listed under _Avoid_.

The repo is a single context, but the language clusters into five areas:
**TapList** (slots and their beers), **Sources** (where beer data comes from),
**Settings** (operator configuration), **Presentation** (how the board renders),
and **Admin** (the management surface that writes commands).

## Language

### Core

**Slot**:
A numbered position on the board, 1 to `num_taps`. Exists whether or not a beer
occupies it.
_Avoid_: tap number, position, line

**Beer**:
The beverage itself - name, ABV, IBU, colour, description, image. Independent of
where it is served.
_Avoid_: brew, product, item

**Tap**:
A Beer in a Slot. The unit the display renders as one card.
_Avoid_: keg, tap slot, entry

**TapList**:
The full set of Slots and the Taps occupying them. The aggregate root.
_Avoid_: menu, list, taps

**Board**:
The resolved payload the display consumes - every Slot with its Tap if any, plus
the display settings needed to render them.
_Avoid_: board payload, display data, API response

### Sources

**Source**:
A system that supplies Beers for Slots. Two exist: Manual and Brewfather. Every
Tap records which Source it came from.
_Avoid_: provider, backend, feed, integration

**Manual**:
The Source where an operator enters a Beer directly through Admin. Beats
Brewfather for the same Slot, and is never read, written, or archived by sync.
_Avoid_: custom, override (as a noun for the whole tap)

**Brewfather**:
The Source that fetches Batches from the Brewfather API and maps them to Beers.
Replaceable in principle; a second connector would supply Batches too.

**Batch**:
The external entity Brewfather exposes - one brew of one recipe, with its own
status and measured readings. Not a Beer; a Batch becomes a Beer via Mapping.
_Avoid_: recipe, brew, beer (inside the Brewfather area)

**Mapping**:
The transformation from a Batch to a Beer - field selection, measured-over-
estimated preference, SRM to EBC conversion, note-token parsing. Versioned
(`MAPPING_VERSION`), so cached Taps rewrite once when it changes.
_Avoid_: extraction, parsing, conversion

**Tap file**:
The markdown-plus-image pair on disk holding one Tap, named by Source and Slot
(`custom_tap_3.md`, `bf_tap_3.md`). Deliberately hand-editable - see ADR-0001.
Both Sources may hold a file for one Slot: the Manual one wins and the
Brewfather one stays current underneath, which is what makes clearing an
override instant.
_Avoid_: record, row, tap data

**Source precedence**:
The rule deciding which Source wins for a Slot: Manual, then Brewfather, then
Vacant. Owned by the Tap file store, which resolves a Slot to the winning Tap
and keeps filenames private - see ADR-0003.

### Upcoming

**Upcoming Beer**:
A Beer not yet on a physical Tap but destined for one. Not a Tap - a Tap is a
Beer in a Slot - and rendered as a teaser card, never as a Tap card. Its Slot
is optional: bound when a non-Completed Batch carries a `tap:X` token but does
not occupy that Slot (the Slot may be held by a more-complete Batch or a Manual
Tap, or vacant because Fermenting and lower never occupy); unbound when a Batch
carries an `upcoming:` note token and no `tap:X`. Two Upcoming Beers may bind
to one Slot. Derived afresh on every sync and cached disposably under
`/data/upcoming/` - never operator-authored, so it follows the Status policy
rather than Settings'. See issue #4.
_Avoid_: teaser (as the entity's name), coming soon, on deck

### Colour

**Colour**:
The resolved colour of a Beer - or _Unknown_, when the Beer has neither an EBC
nor a Colour override. Resolution answers with one or the other and stops there:
the colour shown for Unknown belongs to the surface rendering it, because a
swatch and a Placeholder want different ones (a grey swatch reads as "no data";
a grey pour reads as a broken image). Both surfaces read the same resolution, so
a *known* Colour always agrees between them. See ADR-0004.
_Avoid_: color hex, swatch colour, beer colour

**EBC**:
The measured colour attribute of a Beer, and the only stored form. One input to
Colour, not a synonym for it. SRM is a display unit of EBC, never a stored value.
_Avoid_: SRM (as a stored value), color value

**Colour override**:
A manual hex that replaces the EBC-derived Colour wholesale, set per Beer.
_Avoid_: custom colour, color_override (in prose)

**Saturation**:
A muting factor applied to the *computed* Colour only. A Colour override is an
exact instruction and is never muted, so an override plus a Saturation yields the
override untouched - otherwise there would be no way to ask for exactly one
colour. Not part of resolution.

**Value precedence**:
The rule deciding an attribute's value: override, then computed, then default.
Distinct from Source precedence, which picks the whole Tap. Colour is the
exception: it resolves to override, then computed, then _Unknown_ - the default
is the renderer's choice, not the model's.

### Presentation

**Attribute**:
A displayable measured property of a Beer - ABV, IBU, EBC, OG, FG. Has a value,
a unit, and a Visibility.

The colour swatch is **not** an Attribute - it is Presentation of Colour. The two
are gated by one operator toggle but ask different Empty questions: the swatch
asks whether Colour is *known* (EBC or override), the EBC Attribute asks whether
*EBC* is present. That is why a beer with only a Colour override shows a swatch
and no EBC number. They resolve to two separate answers.
_Avoid_: stat, field, metric

**Visibility**:
The resolved answer to whether an Attribute renders on a card, computed in a
fixed order: per-Tap override, then the global toggle, then Empty suppression.
A third precedence chain alongside Source and Value precedence.
_Avoid_: show flag, hidden, display toggle

**Empty suppression**:
The rule hiding an already-enabled Attribute on beers whose value is missing.
A per-beer refinement of a Visibility toggle, not a toggle itself.
_Avoid_: hide when empty, auto-hide

**Occupancy**:
Whether a Slot has a Tap. Unoccupied Slots can be omitted from layout and
re-flowed away - an axis entirely separate from Visibility.
_Avoid_: hidden, empty tap

**Theme**:
The colour palette for the display, resolved to CSS custom properties. A preset
key or `custom` plus per-colour values.

**Glassware**:
The glass silhouette used for the Placeholder - chosen globally or per Beer.
Follows Value precedence.
_Avoid_: glass type, glass shape

**Placeholder**:
The fallback image shown when a Beer has no photo: a glass silhouette tinted to
the Beer's Colour. Glassware is an input to it, not a synonym for it.

### Settings

**Settings**:
Operator configuration - deliberate, human-authored, changed rarely. Lives in
`config.json`.
_Avoid_: config (in prose), options, preferences

**Status**:
Machine-written runtime state - sync timestamps, last error, update-check
results. Regenerable on the next cycle and written constantly by scheduled jobs,
which is why it lives in `status.json` rather than beside the Brewfather key in
`config.json`. Disposable: deleting the file costs a stale-looking admin panel
until the next cycle, nothing more. See ADR-0002.
_Avoid_: sync state, metadata

**Data Directory Identity (DDI)**:
A random identifier naming *which* data directory this box is using. Written to
two places: `.data_dir_id` in the data directory, which is always the authority,
and a container-local copy outside it, which is only the appliance's memory of
what it last saw there. Comparing them at startup separates a container recreate
over intact data (silent) from data that was wiped or swapped underneath a
surviving container (one warning, once).

It is a **third kind of state, deliberately neither Settings nor Status**, and
lives in its own file for that reason. It is not operator intent, so it does not
belong in `config.json`; and it is not a fact about the last cycle that a job
regenerates, so putting it in `status.json` would break that store's stated
contract that every field is rewritten by a job and losing the file costs
nothing. ADR-0002 stands unchanged - this sits alongside it.

DDI is **not a general wipe detector**. See _Known hazards_.
_Avoid_: data id, volume id, install id

**Snapshot**:
The Board as it stood at one moment, packaged as a zip mirroring the data
directory's layout - so restoring one is unpacking it. Carries Settings, the Tap
files and images of both Sources, and the Archived beers. Never carries Status
or the DDI. Carries the Brewfather credential only when the operator
opts in at export time, and that choice is offered only when the key sits in
`config.json` rather than the environment. Produced and consumed by the admin's
export and import. See issue #29 for the reasoning behind each inclusion.
_Avoid_: backup, dump, archive (which already means the Archived beers)

### Lifecycle

A Tap moves through four states:

**Vacant**:
A Slot with no Tap.

**Poured**:
A Slot with a Tap that the board is currently rendering.
_Avoid_: active, live, on

**Archived**:
A Tap moved to `old_beers/` as a datetime-suffixed markdown-plus-image pair.
Sync retiring an unclaimed Brewfather Tap is the only automatic cause; Admin
archives the Manual Tap when an override is cleared.
_Avoid_: retired, removed

**Purged**:
Permanently deleted from the archive by the daily cleanup job - by age first,
then oldest-first until under the size limit.
_Avoid_: cleaned, pruned, expired

## Known hazards

**`stem` means two unrelated things.** In storage it is a filename stem
(`bf_tap_3`); in the glassware SVG it is the stem of a glass. Different areas,
no bug - but a grep for `stem` hits both.

**Settings and Status have opposite read policies, on purpose.** They are now
two files (ADR-0002). `config_store` refuses to write over an existing-but-
unreadable `config.json`, because the operator's settings and API key are
irreplaceable. `status_store` does the reverse and rebuilds from defaults,
because every Status field regenerates on the next cycle and a guard there would
strand a healthy box on "never synced" forever. A reader who unifies the two
stores for consistency will break one of them.

**DDI catches less than its name suggests.** It cannot see the failure it
descends from - a Docker Desktop VM reset - because both copies of the
identifier live inside that same VM disk, so a reset takes both and the next
boot reads as a first run. The "not mapped" check is what catches that case,
because such a box is unmapped. DDI covers a narrower set: a data directory on
host tmpfs or a RAM disk, a mapped directory the operator deleted, storage that
failed to mount before Docker started, and remapping mistakes. Silence from DDI
is not proof the data is safe, and it must not be "improved" into a claim that
it is.

**Whether a field is Settings or Status is decided by who authored it**, not by
its name prefix. `update_check_enabled` is operator intent and stays in
Settings; the three `update_*` fields recording what the check found are Status.

**Settings bounds are enforced by clamping, not by rejection - deliberately.**
An out-of-range value is clamped to the bound and saved; nothing raises. This
looks like missing validation and is not. Settings arrive from two places, and
only one of them can be told anything: a hand-edited `config.json` (ADR-0001
makes it editable) has no one to report to and must never stop the box booting,
so clamping is the only safe disposition there. The operator-facing limits live
on the Admin form's inputs, taken from the same constants the clamp uses, so the
browser refuses the value at the point of typing rather than after a round trip.
A reader who adds server-side rejection duplicates the form's job and gains
nothing the clamp does not already guarantee.

**A bad value in a Tap file is not a bad Tap file, and the store must never
confuse the two.** Sitting beside the entry above, and for the same reason: a
hand-edited file has no one to report an error to. `abv: banana` coerces to
nothing and the Tap resolves normally, under its own Source, keeping every field
it got right. That is deliberately different from the two file-level failures
`tap_store.resolve` already tells apart - a file that vanished (precedence moves
on) and a file that will not read (the walk stops, so a disk hiccup cannot put
another brewery's beer on the TV). Merging value validity into either of those
would answer a mistyped ABV by changing which beer is on the board. See
ADR-0005.
