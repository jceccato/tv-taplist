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
_Avoid_: record, row, tap data

**Source precedence**:
The rule deciding which Source wins for a Slot: Manual, then Brewfather, then
vacant. Currently emergent from filename order rather than implemented anywhere.

### Colour

**Colour**:
The resolved colour of a Beer, painted on both the swatch and the glassware
placeholder so the two always agree.
_Avoid_: color hex, swatch colour, beer colour

**EBC**:
The measured colour attribute of a Beer, and the only stored form. One input to
Colour, not a synonym for it. SRM is a display unit of EBC, never a stored value.
_Avoid_: SRM (as a stored value), color value

**Colour override**:
A manual hex that replaces the EBC-derived Colour wholesale, set per Beer.
_Avoid_: custom colour, color_override (in prose)

**Saturation**:
A muting factor applied after Colour is resolved. Not part of resolution.

**Value precedence**:
The rule deciding an attribute's value: override, then computed, then default.
Distinct from Source precedence, which picks the whole Tap.

### Presentation

**Attribute**:
A displayable measured property of a Beer - ABV, IBU, EBC, OG, FG. Has a value,
a unit, and a Visibility.
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
results. Regenerable on the next cycle, and written constantly by scheduled
jobs, which is why it does not belong in the same file as Settings.
_Avoid_: sync state, metadata

### Lifecycle

A Tap moves through four states:

**Vacant**:
A Slot with no Tap.

**Poured**:
A Slot with a Tap that the board is currently rendering.
_Avoid_: active, live, on

**Archived**:
A Tap moved to `old_beers/` as a datetime-suffixed markdown-plus-image pair,
either by sync retiring a Brewfather Tap or by Admin saving a Manual Tap over
one. Only Brewfather Taps are ever archived automatically.
_Avoid_: retired, removed

**Purged**:
Permanently deleted from the archive by the daily cleanup job - by age first,
then oldest-first until under the size limit.
_Avoid_: cleaned, pruned, expired

## Known hazards

**`stem` means two unrelated things.** In storage it is a filename stem
(`bf_tap_3`); in the glassware SVG it is the stem of a glass. Different areas,
no bug - but a grep for `stem` hits both.

**Source precedence is implemented nowhere.** It emerges from two `if` branches
in `resolve_tap` plus a filename prefix, and "is this Manual?" is answered by a
path existence check from several modules. Adding a third Source means editing
resolution, the naming helpers, archive, cleanup, and Admin. This is a deliberate
trade-off, recorded in ADR-0001.

**Settings and Status share `config.json` today.** They should not - Status is
written every sync cycle while Settings holds the Brewfather key, which is the
reason the never-overwrite-with-defaults guard exists. Splitting them is agreed
but not yet implemented; the ADR gets written when the code lands.
