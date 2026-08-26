# Upcoming teaser logic prototype (issue #4)

Throwaway. Models the resolution rules agreed for "Coming up" teaser cards
against fake batch dicts. Imports nothing from `app/`, writes nothing, and is
meant to be read by a human in a few minutes - the output answers two
questions:

1. Does the ON-toggle resolution shape feel right?
2. Do the two teaser acquisition paths merge into one ordered queue?

## Run

```bash
python prototype/upcoming-logic/main.py
```

Python 3.10+, stdlib only. Run from the repo root.

## Interactive TUI

For a live feel instead of the fixed scenarios:

```bash
python prototype/upcoming-logic/tui.py
```

A menu loop over the same resolution functions. Every change re-renders the
board, the teaser queue and the log, so the rules can be prodded rather than
just read. Commands (type `help` inside for the full list):

| Command | Effect |
|---------|--------|
| `add` | add a batch, prompted for name/status/recency/notes |
| `add <name> <status> <updated> <notes...>` | add in one line; quote multi-word names: `add "Golden Pils" conditioning 500 tap:3` |
| `rm <n>` / `batches` | remove / list batches by index |
| `load <n>` | load scenario n's data into the live state, then mutate it (e.g. `load 11 (bonus)`) |
| `manual <slot>` | toggle a Manual tap on a slot |
| `taps [n]` / `cap [n]` | show / set num_taps and max_upcoming_previews |
| `toggle` | flip `show_upcoming_previews` OFF/ON - the fastest way to feel the difference |
| `scenarios` | run the 12 fixed scenarios from main.py |

Suggested sequence to get a feel in a minute: `load 3` (fermenting tap:5) ->
`toggle` -> `toggle` -> `add "Next One" fermenting 900 tap:5` -> `manual 2`.

## What it models

A fake batch is `_id`, `name`, `status` (completed / conditioning / fermenting
/ brewing / planning / unknown), `updated` (recency in ms) and a `notes`
string that may carry `tap:N` and/or `upcoming:` tokens.

- **OFF world (today):** Manual taps win their slots; every remaining `tap:N`
  batch competes, most-complete status wins, recency (newest) breaks a tie
  within one status. Losers are discarded and logged. No teasers exist.
- **ON world:**
  1. Occupancy pass: Completed `tap:N` batches claim first (recency
     tie-break); then Conditioning `tap:N` batches fill slots no Completed
     batch claimed and no Manual tap holds (recency tie-break). Fermenting
     and lower never occupy.
  2. Teaser set: (a) non-Completed `tap:N` batches that did not occupy, plus
     (b) batches with `upcoming:` and no `tap:N`, any status. Both tokens on
     one batch: `tap:N` wins, `upcoming:` ignored. Completed `tap:N` losers
     are not teasers.
  3. One merged queue sorted by status rank ascending, then recency
     descending, capped at `max_upcoming_previews` (default 3). Each entry
     prints name, status, bound slot (or None), its acquisition path, and its
     sort key.

Each scenario prints the ON board, the teaser queue, the ON log, and a
compact OFF board line so the flip from today's behaviour is visible.
Scenario 1 prints both worlds side by side.

## Scenario table

| # | Scenario | Proves |
|---|----------|--------|
| 1 | Conditioning tap:3, no Completed, no Manual | The exception: OFF and ON agree - Conditioning still occupies an unclaimed tap and is not a teaser |
| 2 | Conditioning tap:3 + Completed tap:3 | Completed wins the tap; the Conditioning loser teases bound to slot 3 |
| 3 | Fermenting tap:5, slot vacant | Non-Completed statuses never occupy; tap stays Vacant, beer teases bound to slot 5 |
| 4 | Manual tap on 2 + Conditioning tap:2 | Manual wins the board; Conditioning teases bound to slot 2 |
| 5 | Two Completed claim tap:4 | Recency winner pours; the loser is NOT a teaser |
| 6 | upcoming: on Conditioning, no tap:N | Unbound teaser from path (b) |
| 7 | upcoming: on Completed, no tap:N | Unbound teaser sorts first (rank 0, most ready) |
| 8 | Both tokens on one batch | tap:N always wins; upcoming: ignored - loser teases bound, winner pours |
| 9 | Six teasers, cap 3 | Top three by (rank, recency) survive, rest dropped |
| 10 | Mixed statuses and recencies, both paths | conditioning before fermenting before brewing before planning, newest first within a status, paths interleave on the same key |
| 11 | Two Fermenting claim one vacant slot | Bonus wrinkle: both become teasers bound to the same slot |

## What the prototype suggests

The two acquisition paths merge cleanly: both contribute a
(batch, bound slot, path) triple, the sort key ignores path, and no ordering
or cap conflict between them surfaced in any scenario. The shape feels right
apart from the wrinkles below, which the settled rules leave open.

1. **A Fermenting `tap:N` flips from poured to vacant when the toggle comes
   on.** Today (OFF), a fermenting batch with `tap:N` occupies its tap when
   `include_fermenting` pulls it in. With teasers ON the same tap becomes
   Vacant and the beer moves to the queue (scenario 3). That is a change to a
   currently-poured tap, not just a new card - is it intended?
2. **Two teasers can be bound to one slot.** Nothing stops two non-Completed
   batches claiming one tap from both becoming teasers for it (scenario 11).
   The queue would read "coming up on tap 5" twice. Decide whether that is
   fine, deduplicated, or whether the recency tie-break should also pick one
   teaser per slot.
3. **The tie-break exists for occupancy but not for the teaser queue.** Two
   teasers with the same status AND recency sort arbitrarily (stable order of
   the fetch). If wrinkle 2 stands, the queue order decides which bound
   teaser is seen first, and that order can be arbitrary.
4. **`upcoming:` carries no payload.** Anything after the token is ignored
   prose. If the design later wants `upcoming: <eta>` or a priority, the token
   needs a shape now; retrofitting means re-reading old notes.
5. **A Completed `upcoming:` batch ranks first.** Correct per the rules
   (rank 0 = most ready), but "coming up" for a finished beer reads oddly -
   the token is really doing "not on a tap yet, please tease me". Worth a
   name check before operators see it.
6. **A teaser can point at a tap the board cannot show.** The rules are
   silent on `tap:N` beyond `num_taps`; the prototype guards board writes past
   the board but keeps the teaser. The real system accepts `tap:1..MAX_NUM_TAPS`
   regardless of the configured tap count, so a teaser bound to a hidden slot
   is possible.
7. **The operator log is asymmetric.** Completed and Conditioning claimants
   each get a log line; Fermenting and lower claimants that become teasers are
   silent unless two bind to the same slot (the prototype adds that one line).
   Decide whether every claim that becomes a teaser should log, or none.
