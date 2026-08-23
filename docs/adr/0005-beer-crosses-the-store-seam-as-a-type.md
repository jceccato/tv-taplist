# Beer crosses the Tap file store seam as a type

Status: accepted

**Beer** is a frozen dataclass in `app/beer.py`, and it is what the Tap file
store hands out and takes in. It used to be a `dict[str, Any]` of front matter,
built independently by three writers and read back with `.get()` plus a
defensive coercion at every use.

Nothing enforced that the three writers agreed on the key set, and they did not.
The demo seeder wrote 7 keys of roughly 18 and nobody noticed for months,
because every reader defended itself. That is not a hypothetical: it is drift
that already happened, in a codebase where ADR-0003 explicitly designs for a
fourth writer - a second **Source** connector - which would have drifted the
same way.

The change is deliberately narrow. The on-disk format is unchanged: same
filenames, same YAML keys, still hand-editable, no migration. `/api/board` is
unchanged, byte for byte, for a given data directory. `MAPPING_VERSION` stays at
6, because the rule for bumping it is that *extraction logic* changed, and this
changes only the in-memory container the same values travel in - bumping would
force a rewrite of every cached Tap and a re-download of every image for no
gain.

## Decisions

### A bad value and an unreadable file are different questions

This is the load-bearing decision, and the one most likely to be "improved"
later.

`tap_store.resolve` already distinguishes two failures with different
dispositions. A file that *vanished* falls through: it genuinely is not there,
so precedence moves on. A file that *exists but will not read* stops the walk
and yields an empty Tap, because a transient read error on a bind mount must
never demote a Manual Tap and put another brewery's beer on the TV.

A bad **value** must land in neither bucket. `abv: banana` coerces to `None` and
the Tap resolves normally, under its own Source. The Beer keeps its name, its
description, its photo and every other field it got right.

The precedent is already written down. CONTEXT.md's _Known hazards_ records that
Settings bounds clamp rather than reject, because a hand-edited `config.json`
has no one to report an error to and must never stop the box booting. ADR-0001
makes Tap files hand-editable on exactly those terms. The argument applies here
verbatim, and the failure mode is worse: a typo that raised, or that fell
through to the next Source, would answer a mistyped ABV by putting a different
beer on the board.

`tests/test_beer.py::test_a_bad_value_does_not_disturb_source_precedence` is the
guard. It fails in both directions - the empty-Tap variant and the fall-through
variant - and both were watched to fail before the test was kept.

### The type is the single enforcement point, and it never raises

Coercion runs in `Beer.__post_init__`, so it holds for whoever constructs a
Beer, including a future connector whose author has not read the module. Blank
becomes `None`, junk becomes `None`, `0` stays `0` - a 0 IBU says the beverage
has no bittering component, which is a reading rather than an absence.

The Admin's Manual override is the one caller that still rejects, and only for
the five numeric Attributes. That is not an inconsistency: the Admin form has
somebody to tell, so it tells them, with a 422 the operator can act on. A
hand-edited file has no such audience.

### Absence is `None`, and only `None`

Because the seam coerces once, `board._is_missing` collapses from `value is None
or value == ""` to `value is None`, and the five `_num()` calls in
`board.resolve_tap` disappear. Those ran on every Attribute of every Tap on
every poll from every TV; now they run once per file read.

### Coercion is logged at the write, not at the read

The board is rebuilt on every poll from every TV, so a warning on the read path
is a firehose - the same reasoning ADR-0003 gives for not warning when a file's
`source:` key disagrees with its filename.

A Beer therefore records which fields it coerced away, on a `coerced` field
excluded from equality, and `tap_store.write` logs that once when a file is
written. The operator learns that a value was dropped at the moment something
acts on it, rather than sixty times a minute for as long as the typo sits there.

### Beer carries what the store reads back; the rest is garnish

`source`, `image` and `updated` are written into every Tap file and none of them
is read back as truth. The filename decides the Source (ADR-0003), the store
finds the photo by globbing the Slot's stem, and `updated` describes the file
rather than the beverage - a Manual Tap edited today did not change the beer.

They are still written, because a human opening a Tap file should see a complete
record. They are now written by the **store**, which is the component that knows
all three; a caller can no longer pass an `image:` key naming a photo that is
not beside the file.

Two more fields left the Beer for the same reason and got types of their own on
`TapFile`. `show_og` / `show_fg` are per-Slot Presentation, not properties of
the beverage: the same beer poured on another Slot would not carry them.
`batch_id` / `source_rev` / `map_rev` are a Source's cache-coherence record, and
`revision is None` on a Manual Tap is the type saying "Brewfather-only" rather
than a comment saying it.

## Consequences

- The three writers cannot disagree about what a Beer is. Two tests state this
  as an assertion about files on disk rather than as prose.
- The demo seeder's files **change**: it sets four fields and leaned on a
  hand-built seven-key dict, so its Tap files gain `og`, `fg`, `saturation`,
  `color_override` and `glass` as nulls, and an integral `abv: 5.0` is written
  `abv: 5`. This is the drift being closed, not a regression. Only DEMO_MODE on
  a fresh data directory writes these files, and the board resolves a null key
  and an absent key identically.
- Brewfather and Manual Tap files keep the same key set they always had. The
  garnish moves to the end of the front matter in a fixed order, so newly
  written files differ from old ones in key *order* only. Nothing reads front
  matter positionally, and existing files are not rewritten to match.
- A hand-edited `updated:` without quotes no longer takes the board down.
  YAML parses it into a `datetime`, `/api/board` serialises with plain
  `json.dumps`, and that raised `TypeError` on the public endpoint - blanking
  every TV in the venue. `TapFile.updated` is a string, coerced on the way in.
- Unknown front-matter keys are dropped when a file is rewritten. In practice
  this is a no-op: Manual files are rewritten only when the operator saves that
  Slot, and Brewfather files are cache that sync overwrites wholesale.

### A correction to ADR-0003

ADRs are append-only here, so this is recorded as a pointer rather than an edit.

ADR-0003's final _Consequences_ bullet reads that "`front_matter` stays an
untyped dict. Turning it into a typed Beer belongs to the Mapping/fetch split,
which this change unblocks and deliberately stops short of." Both halves are now
stale. `TapFile.front_matter` is gone, and the Mapping/fetch split (issue #10)
shipped without the typing, which became issue #32 and this ADR.

That bullet is sometimes read as ADR-0003 having decided *against* typing. It
did not. Typing appears there only under _Consequences_, as a scope boundary
drawn for reviewability - not a decision on the merits. This ADR supersedes
nothing.

## Considered options

- **Pydantic.** Rejected. Its native failure mode is to raise, and the
  disposition rule above forbids raising, so every field would need a
  suppressing validator - more code than doing it plainly, plus a dependency in
  a module whose whole claim is that it is reachable from the pure Mapping
  layer. The coercion needed is not new code either: it already existed,
  scattered, as `board._num`, `admin_ops._number`, `parse_saturation` and
  `parse_hex_color`.
- **Reject on a bad value.** Rejected: it merges value-level validity with
  file-level readability, which is the one thing this seam must not do. See the
  first decision above.
- **A `TypedDict`.** Rejected: it catches nothing at runtime, so the demo
  seeder's missing keys would have stayed exactly as invisible as they were.
- **An `extra: dict` passthrough for unknown keys.** Rejected: it would make
  Beer an untyped dict wearing a hat, and it would let a fifth writer add a key
  the type has never heard of and have it survive - which is the drift, back
  again, one level down.
- **Validating `glass` against the known glassware keys.** Rejected:
  `beer_glass.normalize_glass` is the single expression of the glass fallback,
  and it distinguishes an unknown key (use the built-in default) from no key at
  all (inherit the operator's global choice). Coercing an unknown key to `None`
  in the type would silently move a hand-edited Tap from one branch to the
  other, and it changes what `/api/board` sends.
- **Typing the board payload too.** Rejected as a separate want. The payload is
  a wire format on purpose - it carries `abv_visible` and `image_url`, and drops
  `saturation` entirely - so it is not this type wearing a different hat.
