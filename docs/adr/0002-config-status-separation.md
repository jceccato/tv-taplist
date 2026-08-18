# Settings and Status are separate files

Status: accepted

**Settings** and **Status** (`CONTEXT.md`) shared `config.json`. Settings is the
operator's deliberate configuration, including the Brewfather API key, and is
irreplaceable. Status is six machine-written fields - three sync timestamps and
what the daily update check found - that the scheduled jobs rewrite on every
cycle and that regenerate on the next one regardless.

Status now lives in `/data/status.json`, owned by `app/status_store.py`.
`config.json` is written only when a human presses Save.

This does not supersede ADR-0001 and does not weaken it. `status.json` is one
more plain file in the mapped data directory, readable and editable by hand like
everything else there; the storage decision is unchanged. `CONTEXT.md` recorded
the shared file under "Known hazards" and said the split was agreed but not yet
implemented. This is that implementation, and the hazard entry goes with it.

## Decisions

### The split is by lifecycle, not by who reads the field

`update_check_enabled` stays in Settings even though every other `update_*`
field moves. The dividing line is not the prefix and not which module reads it:
it is whether a human authored the value. Whether to check for updates at all is
operator intent, and on an air-gapped box it is the difference between a working
appliance and a daily failed request. What the check *found* is a fact about the
last cycle.

The same line puts `include_conditioning` and `include_fermenting` in Settings
and `last_sync_attempt` in Status, without either being a special case.

Rejected: **splitting on prefix** (`update_*` to Status, everything else to
Settings). It reads as tidier and is wrong: it would make an operator's
deliberate opt-out disposable, so a `status.json` deleted to clear a stale error
would silently re-enable update checks on an air-gapped box.

### Status has the opposite read policy to Settings

`config_store` refuses to write when the existing `config.json` is present but
will not read (`ConfigUnreadable`), because writing would mean overwriting the
operator's settings and their API key with defaults. `status_store` does the
reverse: an unreadable `status.json` yields defaults on read and is rebuilt on
write.

The asymmetry follows from what the data is worth. Nothing in `status.json` is
authored, secret, or irrecoverable - the sync job rewrites all three
`last_sync_*` fields on its next run and the update check rewrites the other
three on its next daily pass. Carrying the config store's guard across would
mean that a single corrupt or truncated `status.json` left a box that syncs
perfectly well reporting "never synced" for good, with no way out but deleting a
file the operator has never been told about. That is a worse failure than losing
six regenerable values, and it is the failure this whole change exists to
prevent.

`load_status` also never bootstraps a file. A fresh box has no `status.json`
until a job writes one, which keeps rendering `/admin` a pure read.

Rejected: **the same never-overwrite guard on both stores**, for symmetry. It
protects nothing here and converts a transient read fault into a permanent
display fault.

### The config store's guard stays

The issue notes that the never-overwrite-with-defaults failure mode "narrows
considerably" once `config.json` is written only by deliberate admin saves.
Narrows, not disappears. The bind mounts that motivated the guard are the same
ones, `update_config` still does a read-modify-write, and the file still holds
the API key. Removing a guard because its trigger got rarer is how a credential
goes missing on a Windows host at a venue with no one to notice.

### Migration writes `status.json` before it touches `config.json`

An upgraded box has the six fields inside `config.json` and would otherwise
report "never synced" until the next cycle. `migrate_legacy_status()` runs once
at startup, before the scheduler starts, in this order:

1. Read `config.json` **raw**. Coercion drops keys the schema no longer knows,
   so the legacy values are visible only before it. An unreadable config aborts
   the migration with nothing written; the next start retries.
2. Stop if `status.json` already exists - it is then the authority.
3. Write `status.json`. Until this lands, `config.json` holds the only copy.
4. Only then rewrite `config.json` without the legacy keys.

Every interruption point is safe. A crash before 3 leaves the original file
untouched. A crash between 3 and 4 leaves the values in both files, which is
harmless rather than ambiguous: the config store drops unknown keys on every
read, so nothing can reach the stale copy, and the next start finishes step 4.
Both writes are atomic (`atomic.py`), so neither file can be observed half
written.

Rejected: **stripping `config.json` first, then writing `status.json`.** The
mirror image, and it has a window where the values exist nowhere.

Rejected: **doing the migration lazily on the first `load_status()`.** It would
put a `config.json` read behind every Status read for the life of the box, and
would let a job's write race the migration. Running it at startup ahead of the
scheduler is what makes step 2's rule sound.

### An existing `status.json` is never overwritten by the migration

Step 2 declines rather than merging field by field. Merging looks more careful
and is worse: a successful sync sets `last_sync_error` to `None` deliberately,
and any "fill in the blanks from the old config" rule would read that `None` as
a gap and resurrect a stale error under a green sync.

The only way to reach step 2 with un-migrated values is a `config.json` that was
unreadable at the one startup that should have migrated, with a job then writing
`status.json` first. The cost in that case is a thin status panel that refills
within a day, and the legacy keys are still pruned. That is stated here rather
than defended against.

### The Status store depends on the config store, not the reverse

`status_store` imports `config_store` for the migration; `config_store` knows
nothing about Status. A one-way dependency keeps the credential-holding module
free of any reason to open the other file, and it keeps all four migration steps
readable in one place instead of split across a module boundary or hidden in the
startup path.

`config_store` gained two helpers for this: `read_raw_config()`, which sees past
the coercion that drops unknown keys, and `prune_unknown_keys()`, which makes
step 4 a deliberate act rather than a side effect of the operator's next Save.

## Consequences

- The file holding the Brewfather API key is no longer rewritten every sync
  cycle. Writes to it are now exactly the deliberate ones: admin saves, the
  venue-logo route, first-run bootstrap, and this one-time migration.
- `/data` has one more file. It is documented in the installation guide's data
  directory listing so an operator does not read it as debris and delete it -
  though deleting it is in fact safe, which is the point.
- Backing up an appliance by copying `config.json` no longer captures Status.
  That is the intended outcome; restoring a config no longer restores a stale
  "last synced" claim from whenever the backup was taken.
- Two stores now exist with deliberately opposite read policies. The asymmetry
  is a trap for a future reader who unifies them for consistency, so each
  store's docstring states its policy and names the other.
- The admin template takes a `status` variable of its own. A sync timestamp is
  no longer reachable through `cfg`, so the two cannot be confused in a template
  and `/api/board`'s omission of Status is structural rather than remembered:
  `board.py` never opens `status.json`.
- Anything reading `config.json` externally - an operator's script, a
  monitoring check - stops finding `last_sync_success` there. There is no
  compatibility shim; the migration is one-way.

## Considered options

- **Split on the `update_` prefix.** Rejected: it makes an air-gapped box's
  opt-out disposable.
- **Give both stores the same never-overwrite guard.** Rejected: it protects
  nothing disposable and turns a transient read fault into a permanent one.
- **Drop the config store's guard now that Settings is written rarely.**
  Rejected: rarer is not never, on the same bind mounts, in the file holding the
  API key.
- **Strip `config.json` before writing `status.json`.** Rejected: a window where
  the values exist in neither file.
- **Migrate lazily on first read.** Rejected: a config read on every Status read
  forever, and it races the scheduled jobs.
- **Merge legacy values into an existing `status.json` field by field.**
  Rejected: it resurrects a stale error over a successful sync.
- **Keep one file and simply write it less often.** Rejected: it leaves the
  credential in the file the jobs write, which is the whole hazard, and leaves
  Settings and Status sharing one coercion schema and one read policy when they
  want opposite ones.
