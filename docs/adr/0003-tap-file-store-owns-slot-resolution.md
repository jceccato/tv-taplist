# The Tap file store owns Slot resolution

Status: accepted

Callers name a **Slot** and a **Source** and get back the Tap file, its paired
image, and - for `resolve` - which Source won. Tap filenames are private to
`app/tap_store.py`, and **Source precedence** is a single ordered constant
(`SOURCE_PRECEDENCE`) rather than a property that emerges from a couple of `if`
branches and a filename prefix spread across six modules.

This does not supersede ADR-0001 and does not change it. Its decision - files on
a mapped data directory, not a database - stands untouched, and every Tap file
keeps the name it has today. ADR-0001's Consequences section forecast this exact
shape ("storage that owns Slot resolution - callers ask for a Slot and get the
winning Tap plus its Source, with filenames private") and named the price of
deferring it. This work pays that price off; the storage decision itself is
unchanged.

## Decisions

### `Source` keeps the legacy `custom` spelling in its values

The enum members read in the glossary's vocabulary - `Source.MANUAL` and
`Source.BREWFATHER` - while their values stay `custom` and `brewfather`. Those
values are what appears in filenames on disk (`custom_tap_3.md`) and in the
`source` field of the board payload.

Renaming the on-disk prefix to `manual_` would mean a data migration in an
appliance meant to run untouched for months, for a word. Operators also have
notes, scripts, and habits built on the current names, which ADR-0001 makes a
supported thing to have. The mismatch between member and value is deliberate and
is documented on the enum itself, so it is not mistaken for an oversight.

### `Source` mixes in `str` rather than using `StrEnum`

`enum.StrEnum` arrived in Python 3.11. The container runs 3.12, but the
development interpreter on this project is 3.10, so `StrEnum` is not available
where the suite runs. `class Source(str, Enum)` with `__str__` pinned to the
value gives the same behaviour on both: `f"{Source.MANUAL}"` is `"custom"`
everywhere, and the value serialises straight into the board payload.

This is a compatibility choice, not a stylistic one. Switching to `StrEnum` is
correct the day 3.10 stops being the interpreter the tests run on.

### The filename outranks the front-matter `source:` key

Every writer still writes `source:` into the front matter, and nothing ever
reads it back as truth. It exists so a human opening one file in a text editor
can see what it is - ADR-0001 makes that a supported activity - while the
filename decides which Source a Tap belongs to. Renaming a file is therefore a
complete and predictable act.

A mismatch between the two is **not** warned about. The board payload is rebuilt
on every poll from every TV, so a warning on resolution would be a firehose in
the log for a condition only a hand edit can create, and only the hand editor can
fix.

### Existence, not readability, decides precedence

`resolve` walks `SOURCE_PRECEDENCE` and takes the first Source whose markdown
file *exists*, then reads it. A file that exists but will not read yields an
empty Tap for that Source rather than falling through to the next one. Without
that rule, a transient read error - the exact failure mode the config store's
never-overwrite guard already exists for, on the same bind mounts - would
silently demote a Manual Tap and put a different brewery's beer on the board.

Implementation refined this into a distinction the interface has to make and the
public one does not. The private loader reports three outcomes:

- **missing** (`FileNotFoundError`): the file is genuinely not there, including
  the case where it vanished between the existence check and the read. Precedence
  moves on to the next Source.
- **unreadable** (any other `OSError`): the Slot belongs to this Source but its
  contents cannot be established. The walk stops with an empty Tap.
- a Tap file.

`read` collapses the first two to `None`, because the distinction only changes
the answer inside `resolve`.

One consequence was accepted rather than patched: an unreadable **Manual** Tap
with no Brewfather file underneath now renders an empty card - the name falling
back to `Tap N`, every Attribute absent - where it used to render a Vacant Slot.
Adding a board-level "empty front matter means Vacant" rule would re-create
exactly the second opinion about precedence that this store exists to remove.
An empty card is also the more diagnosable outcome: under `hide_vacant_taps` a
Vacant Slot is re-flowed away, so the operator would never learn that anything
was wrong.

### Two path-returning functions are kept, for archiving only

`existing_paths(slot, source)` and `archived_stem(slot, source, when)` hand out
filesystem paths and a destination name, which is otherwise precisely what this
module exists to stop doing. They are the one acknowledged crack in an otherwise
private interface, and they say so in their docstrings.

The alternative - absorbing archiving into the store - was rejected. Archiving is
a transition between two directories, not storage of current Taps, and it owns
genuine mechanics of its own: copy-to-temp, atomic replace, unlink, and tolerance
of files that are not there. Pulling that in would make the store the owner of a
second directory (`old_beers/`) and of a second naming convention, in exchange
for closing a crack that has exactly one caller. Cleanup already reads the
archive back generically by stem and needs to know nothing about either module.

The cost is real and is stated here so it is not discovered as a surprise: two
functions in the store's interface exist for one caller, and a future reader who
deletes them because "nothing should return paths" will break archiving.

### Sync's protection of Manual Taps is structural

`brewfather.py` defines `SYNC_SOURCE = taps.Source.BREWFATHER` and passes it to
every store call. Sync cannot name a Manual file, because it never names the
Manual Source at all. "The sync never touches a Manual Tap" stops being a rule to
remember at each new call site and becomes a property of the code.

Worth recording plainly, because it reads like a downgrade otherwise: the guards
this replaced never protected Manual data. Sync only ever addressed Brewfather
files, so the override checks it consulted were a cycle-saving skip - do not
bother writing a Slot nobody can see - and never a safety mechanism. Deleting
them removed a saving, not a protection.

### Brewfather's claim alone drives writes and archives

A Batch carrying a `tap:X` token claims Slot X. That claim is the only thing that
causes a Brewfather Tap to be written or archived. Neither the override state nor
the operator's tap count is consulted.

Two consequences follow.

**Both Sources may hold a file for one Slot.** Sync writes into Manual-occupied
Slots and keeps that Brewfather Tap warm underneath the override. Nothing
displays it while the Manual Tap stands, because `resolve` picks Manual first,
and clearing the override reveals a current Beer instantly rather than a Vacant
Slot that stays Vacant until the next sync cycle. The price is one image download
and one file write per cycle for Beers nobody is currently looking at; the
Brewfather batch-list fetch is unchanged, so the 500-calls-per-hour rate limit is
unaffected and the cost is bandwidth and disk. Shadowing is therefore a normal,
documented on-disk state rather than an anomaly, and it is written up in
`CONTEXT.md` and the FAQ so an operator does not tidy the "extra" file away and
lose the instant switch-back.

**The tap count no longer touches stored data.** Lowering it hides Slots and
discards nothing; raising it back shows the Beers that were there all along. Tap
tokens are bounded instead by `MAX_NUM_TAPS`, a system limit rather than an
operator display setting, so one fat-fingered `tap:9999` still cannot mint a file
nothing can display, and an out-of-range token is logged with its batch named.

## Consequences

- Adding a third Source is one entry in `SOURCE_PRECEDENCE` and one in the
  private prefix map, rather than edits to resolution, the naming helpers,
  archive, cleanup, and Admin. This is the consequence ADR-0001 recorded as
  outstanding.
- "Is this Slot Manual?", once answered four different ways, is
  `exists(slot, Source.MANUAL)`.
- Admin and the display resolve a Slot the same way, so a Manual Tap with no
  photo now shows the placeholder in both places instead of borrowing the
  Brewfather photo in Admin only.
- Source precedence is directly testable, rather than being inferred from board
  output.
- The test fixture that writes Tap files still builds filenames by hand. That is
  deliberate: it is the suite's independent statement of the on-disk convention,
  so a store that changed the naming fails loudly instead of grading its own
  homework.
- `front_matter` stays an untyped dict. Turning it into a typed Beer belongs to
  the Mapping/fetch split, which this change unblocks and deliberately stops
  short of.

## Considered options

- **Rename the on-disk prefix to `manual_`.** Rejected: a data migration for a
  word, in an appliance whose file names are a user-facing contract.
- **Read the front-matter `source:` key as truth, or warn on a mismatch.**
  Rejected: two answers to one question is the problem being closed, and the warn
  path fires on every poll from every TV.
- **Fall through to the next Source when a file will not read.** Rejected: it
  puts a different brewery's beer on the board in response to a disk hiccup.
- **Treat an empty resolved Tap as Vacant at the board.** Rejected: a second
  opinion about precedence, and it hides the fault under `hide_vacant_taps`.
- **Absorb archiving into the store.** Rejected: it makes the store the owner of
  a second directory and a second naming convention to close a crack with one
  caller.
- **Keep the sync-time override guards as a safety net.** Rejected: they were a
  cycle-saving check that never protected Manual data, and keeping them would
  have kept the Slot Vacant after clearing an override, which is the bug.
