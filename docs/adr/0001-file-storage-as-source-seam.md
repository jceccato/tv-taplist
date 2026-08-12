# File storage as the Source seam

Status: accepted

Tap data is stored as hand-editable markdown-plus-image pairs under `/data/taps`,
and Source precedence is encoded in the filename prefix: `custom_tap_3.md` beats
`bf_tap_3.md` for Slot 3 because resolution checks one path before the other. We
chose this over a database or a manifest file because the mapped `/data`
directory being directly readable and editable by a venue admin is a feature of
the appliance, not an implementation detail - an operator can fix a typo, drop in
a photo, or recover a board with a text editor and no running container, and the
whole thing survives being copied to a USB stick.

## Consequences

Source precedence is not implemented anywhere. It emerges from two `if` branches
in `resolve_tap` plus the prefix convention, and "is this Slot Manual?" is
answered by a path existence check made independently from several modules.
Adding a third Source therefore means editing resolution, the naming helpers,
archive, cleanup, and Admin rather than registering a layer in one place.

If a third Source ever arrives, the shape to move toward is storage that owns
Slot resolution - callers ask for a Slot and get the winning Tap plus its Source,
with filenames private. That change is compatible with keeping the files
human-readable; only the precedence logic relocates.

## Considered options

- **Database (SQLite).** Rejected: kills direct editability and adds a migration
  story to an appliance meant to run untouched for months.
- **Manifest file listing Slot to Source.** Rejected: a second source of truth
  that can disagree with the files on disk, for no gain while there are two
  Sources.
