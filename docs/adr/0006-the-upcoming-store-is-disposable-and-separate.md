# The Upcoming store is disposable and separate

Status: accepted

Issue #4 adds the **Upcoming Beer** (`CONTEXT.md`): a Beer destined for a Tap but
not on one, rendered as a teaser card and never as a Tap card. Its Slot is
optional. It is derived entirely from Brewfather Batches on every sync, and no
operator ever authors one.

Upcoming Beers are cached in `/data/upcoming/`, keyed by Batch id, owned by a
store of their own. The directory is written only while `show_upcoming_previews`
is on, cleared when it goes off, never carried in a Snapshot, and never touched
by the daily cleanup.

This does not supersede ADR-0001 and does not weaken it: `/data/upcoming/` is
more plain files in the mapped data directory, readable and editable by hand like
everything else there. It sits alongside ADR-0002 as a second application of the
same lifecycle test, and alongside ADR-0003 as a deliberate refusal to extend the
Tap file store.

## Decisions

### An Upcoming Beer is not a Tap, so it is not a fourth Source

The obvious move is to add `Source.UPCOMING` to `tap_store.SOURCE_PRECEDENCE`
after Brewfather, reusing the existing store, filenames and resolution walk. It
is wrong, and it is wrong in a way that would be hard to see later.

`resolve(slot)` exists to answer one question: **which Beer is pouring from this
Slot.** Every Source in that walk is a candidate answer. An Upcoming Beer is not
a candidate answer - it is a beer that is explicitly *not* pouring. Putting it in
the walk means a Slot with no Manual and no Brewfather Tap resolves to an
Upcoming Beer, and the board would then render a beer nobody can order as a Tap
card, with a Vacant Slot presented as occupied. Every caller downstream of
`resolve()` would have to learn the difference, which is precisely the knowledge
ADR-0003 put inside the store so that callers would not need it.

The Slot is also **optional** here, and a Source that may resolve to no Slot at
all is not a Source. Two Upcoming Beers may bind to one Slot and both show; a
precedence walk exists to pick one winner.

Rejected: **`Source.UPCOMING` in the Tap file store.** It makes "not pouring" a
kind of pouring.

Rejected: **a `upcoming: true` flag on the existing Tap files.** Same defect
wearing a smaller hat, plus it would put a disposable field inside a file whose
Manual variant is operator-authored and irreplaceable.

### It follows the Status policy, not the Settings policy

ADR-0002's dividing line is **whether a human authored the value**. Nothing here
is authored. An Upcoming Beer is a projection of a Brewfather Batch plus the note
tokens on it, recomputed from scratch on every sync cycle. Delete the directory
and the next sync rebuilds it exactly.

So the store reads tolerantly and always writes, like `status_store` and unlike
`config_store`: an unreadable file yields nothing and is overwritten on the next
cycle. Carrying the config store's never-overwrite guard across would mean one
truncated file could strand a teaser out of the queue permanently, with no way
out but deleting a file the operator has never been told about, in exchange for
protecting data that regenerates in minutes.

This is the same asymmetry ADR-0002 records, reached by the same test. There are
now three stores with two policies, and the trap for a future reader is
unchanged: unifying them for consistency breaks one of them.

Rejected: **holding Upcoming Beers in `status.json`.** They are Status-like in
lifecycle but they are not six scalar fields - they carry descriptions and
images, they are keyed by Batch, and there may be twenty of them. Status is a
small fixed record; growing it into a collection would give one file two shapes.

Rejected: **holding them in memory only.** The board must survive a restart with
the venue offline, which is the whole premise of the appliance. An in-memory
cache means a restart during an outage silently empties the teaser queue, and the
operator cannot tell that from "there is nothing coming up".

### Keyed by Batch id, not by Slot

A Slot is optional here and not unique: an unbound Upcoming Beer has none, and
two may bind to the same Slot. A Slot-keyed filename cannot express either case
without inventing a suffix scheme, which is a second identity for a thing that
already has one.

The Batch id is the identity Brewfather gives it and the one the sync already
holds. Filenames stay **private to this store**, as ADR-0003 requires of the Tap
file store: nothing outside it constructs or parses one, and the AST test that
pins that rule for `custom_tap_`/`bf_tap_` extends to cover this store's prefix.

### The directory exists only while the feature is on

Sync writes `/data/upcoming/` only while `show_upcoming_previews` is on, and
turning the toggle off clears it.

The alternative - always maintain the cache, and let the board ignore it when the
toggle is off - is cheaper to write and is the wrong default for this project.
The toggle's contract is "off means today's behaviour exactly". A box with the
feature off would otherwise accumulate a directory of beers it never displays,
downloading an image per Batch per cycle for them, and an operator inspecting
`/data` by hand would find a directory whose contents contradict what the TV
shows. Clearing on the way off also means the operator's next look at `/data` is
honest about what the box is doing.

The cost is stated rather than defended against: turning the toggle back on shows
nothing until the next sync completes. That is seconds to minutes, it is visible
in the admin's sync status, and it is the same wait as any other first sync.

Rejected: **maintaining the cache regardless of the toggle.** It makes "off"
mean "off for the display only", which is not what the setting says.

### Snapshots never carry it

A Snapshot is the Board as it stood, restorable by being unpacked (ADR-0001). It
carries Settings, both Sources' Tap files, the Archived beers and the operator's
root images. It never carries Status.

`/data/upcoming/` is on the Status side of that line for the reason above, and
there is a sharper argument too: a Snapshot restored onto a box **with** a
working Brewfather key would have its Upcoming Beers rewritten by the next sync
anyway, and one restored onto a box **without** a key would show a teaser queue
that can never update and never resolve, advertising beers that may already have
poured and gone. That is worse than showing nothing.

This is the same reasoning that makes importing Brewfather Taps and keeping a
working key mutually exclusive in the Snapshot import, and it needs no new
machinery: the export enumerates what it carries, and this directory is not on
the list.

Rejected: **carrying it for a box with no key, so a teaser queue survives a
migration.** It ships a queue with no way to expire.

### The daily cleanup ignores it, and nothing here is ever Archived

`cleanup.py` prunes `old_beers/` by age and size. `archive.py` moves a retired
Beer's md and image there as a pair, so an operator can find a beer that left the
board.

An Upcoming Beer is never Archived and never pruned. It was never on the board as
a Tap, so there is nothing to retire; when its Batch stops qualifying, the next
sync simply does not write it and the stale file is removed by the rebuild. Its
whole directory is disposable, so the age and size ceilings that protect a
long-running box from an unbounded `old_beers/` have nothing to do here.

The bound is structural instead: the queue is capped by `max_upcoming_previews`
at display time, and the cache is bounded by the number of qualifying Batches,
which is bounded by the Brewfather account.

Rejected: **archiving an Upcoming Beer when it stops qualifying.** It would fill
`old_beers/` with beers that never poured, and an operator looking there for a
beer that left the board would have to sort real history from projections.

## Consequences

- `/data` gains a directory. It is documented in the installation guide's data
  directory listing, alongside the note that deleting it is safe - which is the
  point, and is the same treatment `status.json` got.
- There are now three stores and two read policies. Each store's docstring states
  its own policy and names the others, so the asymmetry is discoverable from any
  of them rather than only from this ADR.
- Sync gains a write path gated on a Setting. That gate lives at the sync seam,
  not in the store, so the store stays a plain cache and the "off means today's
  behaviour exactly" contract is enforced in one place.
- Turning `show_upcoming_previews` off deletes files. It is the only Setting that
  does, so the admin says so at the point of the toggle rather than leaving the
  operator to discover it.
- The board reads a third source of truth when building its payload. It still
  emits resolved answers and not inputs, so the wire gains teaser entries and
  their resolved Visibility and Colour, not the store's shape.
- An operator hand-editing `/data/upcoming/` will have their edit overwritten on
  the next sync. This is true of `bf_tap` files today and is the same bargain;
  the difference is that there is no Manual Source above it to override with, by
  design, because an Upcoming Beer is a projection and not a Tap.

## Considered options

- **Add `Source.UPCOMING` to the Tap file store.** Rejected: it makes "not
  pouring" a kind of pouring, and the Slot is optional and non-unique here.
- **An `upcoming: true` flag on existing Tap files.** Rejected: same defect, and
  it puts a disposable field in an operator-authored file.
- **Hold Upcoming Beers in `status.json`.** Rejected: Status is a small fixed
  record, not a keyed collection with images.
- **Hold them in memory only.** Rejected: a restart during an outage empties the
  queue indistinguishably from "nothing is coming".
- **Key the files by Slot.** Rejected: the Slot is optional and two Beers may
  share one.
- **Give the store the config store's never-overwrite guard.** Rejected: it
  protects nothing irreplaceable and converts a transient read fault into a
  permanent gap.
- **Maintain the cache regardless of the toggle.** Rejected: "off" would mean
  "off for the display only".
- **Carry the directory in Snapshots.** Rejected: rewritten immediately on a box
  that syncs, and unexpirable on one that does not.
- **Archive an Upcoming Beer when it stops qualifying.** Rejected: it fills
  `old_beers/` with beers that never poured.
