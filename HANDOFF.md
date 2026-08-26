# Handoff: issue #4 "Coming up" teaser cards - display prototype refinement

**Repo:** C:\misc\TVTapList | **Branch:** `prototype/upcoming-logic-4` (off main, uncommitted)
**Issue:** https://github.com/jceccato/tv-taplist/issues/4 (label: `needs-triage`)
**Active skills:** `/i-have-adhd` (still on - lead with next action, number steps, restate state), `/grill-with-docs` (grilling DONE - frontier empty).
**Skill locations:** `~/.claude/skills/` holds `grill-with-docs`, `to-spec`,
`to-tickets`, `implement` and `handoff`. They are user-activated only - an agent
cannot invoke them; ask the user to run the slash command.

## Next action

Get the user's "go" for **ADR-0006** and the **marker feature-request issue**
(see "Pending write-ups"), then run `/to-spec`.

The five display refinements are DONE - see "Display feedback (all applied)".
Three of them carry a spec obligation, listed there; do not lose them in
`/to-spec`.

## Where we got to

Design grilling is COMPLETE. All 14 grilling questions (Q1-Q14) plus two
wrinkle decisions are locked (see "Locked decisions"). Two prototypes exist on
the branch: a logic prototype (resolution rules against fake data, with an
interactive TUI) and a display prototype (three presentation candidates). The
"Upcoming Beer" glossary entry has landed in `CONTEXT.md`. Nothing has been
committed; nothing has been pushed; no GitHub issues or comments have been
posted yet.

The user reviewed both prototypes and gave the feedback below; all five items
have now been applied to the display prototype. The prototyping phase is done -
the next step is the pending write-ups, then `/to-spec`.

## Files on this branch (all uncommitted)

- `prototype/upcoming-logic/main.py` - logic prototype, 12 scenarios, stdlib only.
- `prototype/upcoming-logic/tui.py` - interactive TUI over the logic prototype.
- `prototype/upcoming-logic/README.md` - logic prototype notes + wrinkles.
- `prototype/upcoming-display/index.html` - display prototype, 3 candidates.
- `prototype/upcoming-display/README.md` - display prototype notes + tensions.
- `CONTEXT.md` - MODIFIED: new "### Upcoming" subsection with the "Upcoming Beer" term.

## Display feedback (all applied 2026-08-26)

1. **DONE - Vacant slot + bound upcoming = permanent teaser card.** If a tap is Vacant
   and has a bound Upcoming Beer, the board renders the teaser card in that slot
   permanently (not a fade - just always the upcoming beer, in the teaser
   style). Only when a tap is OCCUPIED does the board fade between the pouring
   beer and the upcoming beer. So:
   - Add a `buildTapPage()` helper used by ALL three candidates: for each slot,
     if `t.vacant` AND a teaser is bound to it, render `teaserCard(teaser)`
     instead of the vacant card.
   - In candidate 3 (in-place cross-fade), the cycle should only animate teasers
     bound to OCCUPIED slots (the cross-fade candidates). Vacant+bound slots are
     now permanent, so they do not cycle. Unbound teasers are still skipped in
     candidate 3 (no slot).
2. **DONE - Half-panel (candidate 1) direction was wrong.** It should span the FULL
   horizontal width and sit in front of the BOTTOM HALF of the vertical space
   (not the right half). CSS change to `.overlay.half-panel`:
   `left:0; bottom:0; width:100%; height:50%;` with a horizontal grid
   (`grid-template-columns: repeat(var(--n), 1fr); grid-template-rows: 1fr;`)
   and a `border-top` instead of `border-left`.
3. **DONE (prototype) - On-deck label should be user-customisable.** The user prefers "Coming up"
   but wants end users to pick: a couple of drop-down preset options (e.g.
   "Coming up" / "On deck" / "Soon") PLUS a custom text field with a character
   limit "enough to fit in a single slot". This is a Settings-level feature (new
   setting, e.g. `upcoming_label`, with presets + custom text + a char cap) -
   capture it in the spec, not just the prototype. In the prototype, the current
   Label toggle (Coming up / On deck) already shows the mechanism; extend the
   options row with a text input for a custom label.
4. **DONE - Large toggle bug.** Toggling Size/Label/ABV in candidate 2 calls
   `candidate2()`, which restarts the carousel at page 0 (the tap page), so the
   user cannot see the on-deck page change. Fix: on option toggle, re-render
   then `showPage(1)` (force the deck page visible) so the effect is immediate.
   Or preserve the current page index across the re-render.
5. **DONE (prototype) - ABV source depends on status.** When `show_upcoming_abv` is on: for a
   FERMENTING teaser, show the Recipe ABV (the target, marked `~`); for
   CONDITIONING / COMPLETED teasers, use the measured values (the current
   mapping logic). This is a mapping-logic change (a `MAPPING_VERSION` bump) -
   capture in the spec. In the prototype, the ABV toggle currently shows a
   hardcoded `~5.0%`; make it status-aware (fermenting -> recipe target,
   conditioning -> measured) to preview the real behaviour.

### What landed, and what the spec still owes

- `buildTapPage()` is now the single tap-page builder for all three candidates,
  and `permanentTeasers()` keeps an already-permanent teaser from being animated
  a second time. Candidate 1's panel carries only the non-permanent teasers;
  candidate 3 cycles only teasers bound to OCCUPIED slots.
- `.overlay.half-panel` is now `left:0; bottom:0; width:100%; height:50%` with a
  horizontal grid and a `border-top`.
- The Label toggle is now a preset drop-down (Coming up / On deck / Soon) plus a
  **Custom...** free-text field capped at `LABEL_MAX = 32` chars, with a live
  counter. The label drives the ribbon AND the "<label> on tap N" subtitle.
- `teaserAbv(t)` picks the source by status (fermenting or missing measured ->
  recipe target; otherwise measured) but marks **every** upcoming ABV `~`.
  The fake teasers carry both `abv` and `abvTarget`; the Dubbel's measured value
  is deliberately absent to exercise the fallback.
- **No size option.** A teaser is formatted like every other slot, from the same
  config settings. The `.big` CSS and the Size button are gone.
- **New Status option (default ON):** the batch status prints under the
  "<label> on tap N" subtitle, spelled for a customer (`STATUS_LABEL`:
  completed -> "Ready", conditioning -> "Conditioning", fermenting ->
  "Fermenting", brewing -> "Brewing", planning -> "Planned").
- **ONE cadence drives every upcoming animation** (`upcomingSeconds`, selector
  6s/12s/20s/45s in the always-visible controls row): the in-place cross-fade
  at 1x, the half-board panel at 2x, the on-deck page at 3x. Hold is derived
  (`holdMs()`, ~58% of the gap, floor 1.5s), not configured. `showing` is the
  interlock.
- **The candidate switcher is GONE.** `render()` is the single entry point and
  reads the `view` object (`rotateOccupied`, `deck`, `panel`, `scope`).
  `candidate1/2/3` no longer exist.
- **Label presets are now** Coming up / Up next / Coming soon / Just around the
  bend, plus Custom. ("On deck" and "Soon" are gone.)
- **The half-board panel now reads as an interruption**: lighter ground than the
  board (`rgba(46,52,68,0.97)`), dashed border all round, 12px radius, inset
  margin, drop shadow. A single dashed top line read as a divider, so the bottom
  row looked like permanent board furniture.
- Option changes call `reapplyOptions()`, which re-renders candidate 2 and forces
  `showPage(1)` so the effect is visible immediately.
- Verified in a browser: no console errors; vacant slots 3 and 5 render teasers
  with their status lines ("Conditioning" / "Fermenting"); the panel is 1256x318
  at the bottom with 2 columns; candidate 3 cycles only "Stout Imperial" and
  honours a 20s selection; every deck ABV reads `~` (`~5.4%`, `~9.2%`, `~5.0%`,
  `~7.2%`); the Status toggle removes the line; a 32-char custom label renders
  and the counter reads 32/32; no Size button and no `.big` card remains.

**User verdict: the in-place cross-fade is accepted as-is** and is now the
BASELINE, not an option.

### FINAL PRESENTATION MODEL (supersedes the three-candidate framing)

The three candidates are **not rivals**. The prototype now implements one
composition, and this is what `/to-spec` should describe:

1. **Baseline, always on when upcoming beers are enabled: the in-place
   cross-fade.** A teaser bound to an OCCUPIED slot fades in over that slot and
   back. The user accepted it as-is.
2. **Board rule, underneath everything: a VACANT slot with a bound teaser shows
   it permanently.** Applies under every combination.
3. **The baseline cannot reach every teaser.** An UNBOUND teaser (`upcoming:`
   with no `tap:X`) has no slot to fade over - it can only render a `?` tap
   number, which is useless. That gap is what killed the winner-takes-all
   framing.
4. **Optional surfaces carry the OVERFLOW:** the on-deck page and/or the
   half-board panel. One, both, or neither - operator's choice.
5. **`rotate_occupied` toggle:** may a pouring beer be rotated out at all? Off
   pushes every occupied-slot teaser into the overflow, where a surface must
   carry it.

**Scheduling (`upcomingSeconds`, one setting):** surfaces run at MULTIPLES -
panel `2x`, deck page `3x`. Not only to keep taps on screen: **the cross-fade
shows ONE teaser per tick**, so a beer late in the list only comes round every
few ticks, and a surface stealing every other tick could starve it entirely.
A single interlock (`showing`) means nothing starts while something else is up;
a surface whose turn arrives during a cross-fade skips that turn.

**BUG FIXED - `All upcoming` was not all.** `surfaceTeasers()` excluded
PERMANENT teasers (bound to a vacant slot) under BOTH scopes, so the fermenting
Dubbel on vacant tap 5 was absent from the on-deck page and the panel. `all`
now returns every teaser; `overflow` still excludes permanent ones, correctly -
the board already gives them a whole slot all the time, so they are not
overflow. The spec must state this: `upcoming_surface_scope: all` means ALL.

**RESOLVED - the duplication (tap 2 twice).** Both `Surface carries` settings
stay. The differing frequencies mean the same batch is never on screen twice at
the same moment, so a customer sees one beer at a time. Recommended pairing:
the **on-deck page wants `All upcoming`** (a dedicated page should be
all-encompassing); the **panel works either way**. Both surfaces enabled at once
is acknowledged as funky and deliberately NOT designed around - it is an
operator choice few will make.
**Still open for the spec:** with `All upcoming` on, a surface card could read
"after the Brown Ale" instead of "coming up on tap 2", turning duplication into
a readable sequence. The board already has the pouring beer's name.

**6. UNFINISHED BATCHES READ FROM THE RECIPE - all attributes, not just ABV.**
`fermenting`/`brewing`/`planning` -> colour, IBU, OG, FG and ABV all come from
the recipe (`teaserFields()` / the `UNFINISHED` set). The FG mid-primary is
wherever gravity sits today, the ABV derived from it swings with it, and a
"calculated" colour or IBU on an unfinished batch is equally provisional. Mixing
sources is worse than either: recipe ABV beside today's half-fermented FG
describes a beer that does not exist. This is a MAPPING change on top of the ABV
one - same `MAPPING_VERSION` bump, wider scope. ABV keeps its `~` regardless of
source.

**Bug fixed: the cross-fade drew over whatever page was showing.** An inactive
carousel page is still laid out (opacity 0 only), so the overlay landed in the
right PLACE over the wrong PAGE - a tap-2 teaser sliding over the on-deck page,
which has no tap 2. The scheduler interlock did not catch it because manual dot
navigation bypasses the scheduler. Fix is a second guard on the page index
(`activePage !== 0` blocks a cycle) plus removing in-flight overlays on page
change. **In the real build this is `display.js`'s problem, and it is exactly
the kind of thing the existing carousel/poller separation should own** - the
cross-fade must be a property of the tap page, not of the stage.

**Panel multiplier is now operator-configurable** (`PANEL_EVERY`, 1x-6x, default
2x, which the user confirmed as a good default). The deck page's stays a fixed
3x - probably should be configurable too for symmetry, but "both surfaces on" is
the rare configuration, so it is a spec question, not a blocker.

**Five spec obligations from this pass (carry into `/to-spec`):**

1. **New setting `upcoming_label`** - preset choices plus custom text, capped at
   **32 characters** (`SETTINGS_BOUNDS`). Clamp, never reject, like every other
   Setting. Check 32 against the narrowest layout (8 cards across) before
   shipping: the ribbon is a single unwrapped line and will clip there.
2. **Reading an unfinished batch from its RECIPE is a MAPPING change** -
   `MAPPING_VERSION` bump. Not just ABV: colour, IBU, OG and FG too, for
   `fermenting`/`brewing`/`planning`. All-or-nothing per batch, never a mix.
   The estimate marker (`~`) is part of the resolved answer the board sends,
   not something `display.js` derives, and **every upcoming ABV carries it**
   whatever the source.
3. **Settings the presentation model needs:** `show_upcoming_status` (default ON - the batch
   status under the subtitle, spelled for a customer, so "Ready" not
   "Completed"); ONE `upcoming_interval_seconds` in `SETTINGS_BOUNDS` driving
   every upcoming animation (the hold is derived, not a second setting); and an
   `upcoming_surfaces` choice (on-deck page and/or half-board panel, or
   neither - NOT an enum, they compose), a `rotate_occupied` toggle, and a
   `upcoming_surface_scope` choice (overflow only / all upcoming), and
   `upcoming_panel_multiple` (1-6, default 2, in `SETTINGS_BOUNDS`). The
   cross-fade is the baseline and is not itself optional. Note the
   interaction with locked decision #10: that deferred a status marker for a
   conditioning beer ON TAP; this one is for teasers only, and the two should
   not be merged into one toggle.
4. **Open design questions (README "design tension" items 3, 4 and 5):** the
   "<label> on tap N" subtitle duplicates the ribbon above it (worse with "Just
   around the bend", printed twice at 20 chars); a permanent teaser removes the
   "we are between beers" signal from a vacant slot; and the overflow can be
   empty, so enabling a surface may visibly do nothing - the admin should say
   so rather than leaving a dead toggle. Decide all three in the spec.
5. **The vacant-slot rule is BOARD logic, not display logic** - `board.py`
   resolves "this slot shows a teaser permanently" and the wire carries the
   answer, per the payload contract. A new tension is written up as item 6 in
   the prototype README: a permanent teaser removes the "we are between beers"
   signal from that slot entirely. Decide whether that wants its own toggle.

The user's emerging synthesis (note for the spec): a hybrid - vacant slots show
the upcoming beer permanently (board-level), occupied slots cross-fade
(candidate 3), plus the on-deck page (candidate 2) as the home for UNBOUND
teasers (which have no slot to sit in). Candidates 2 and 3 are the liked
directions; candidate 1 (half-panel) is being refined but is the weaker of the
three.

## Locked decisions (do not re-grill)

1. **Toggle:** new `show_upcoming_previews` (default off). OFF = today's
   behaviour exactly.
2. **Occupancy (teasers ON):** Completed `tap:X` claimants occupy first; then
   Conditioning `tap:X` fills slots no Completed batch claimed AND no Manual
   tap; Fermenting and lower NEVER occupy; a Manual tap = slot occupied.
   **User's exception:** a Conditioning batch with `tap:X` for a slot no
   Completed batch and no Manual tap claims STILL occupies that tap (preserves
   today's behaviour for a conditioning beer physically on tap).
3. **Teaser set:** non-Completed `tap:X` batches not occupying a slot + batches
   with `upcoming:` token and no `tap:X` (any status, including Completed).
   `tap:X` beats `upcoming:` on a batch with both. Completed `tap:N` losers are
   NOT teasers. **Two teasers may bind to one slot - both show (no dedup).**
4. **Stats:** teasers show IBU/OG/FG + colour swatch (same visibility chain as
   taps). ABV behind new `show_upcoming_abv` toggle (default off), layered on
   `show_abv` + `hide_abv_when_empty`. ABV source by status (see feedback #5).
5. **Queue order:** status rank (completed=0 .. planning=4, unknown=5) then
   recency descending. Cap = `max_upcoming_previews` (default 3, bounds 0..20,
   in `SETTINGS_BOUNDS`).
6. **Store:** `/data/upcoming/` keyed by batch id, disposable, rebuilt every
   sync, written only while `show_upcoming_previews` is ON, cleared when OFF.
   Never in snapshots. Daily cleanup ignores it. Status policy, never
   operator-authored.
7. **Term + token:** "Upcoming Beer" (optional Slot, NOT a Tap) - in CONTEXT.md
   (done). Token `upcoming:`, valueless - presence = "tease me"; slot binding is
   `tap:X`'s job. `MAPPING_VERSION` bump; token stripped from descriptions.
8. **Manual tap interaction:** a Manual tap = slot occupied; a conditioning
   `tap:X` under a Manual tap becomes a teaser (its `bf_tap` stays warm
   underneath). Known wrinkle: if the Manual tap IS that same conditioning beer,
   it shows twice - self-inflicted, accepted.
9. **Confirm intent:** a fermenting `tap:N` that today pours (when
   `include_fermenting` is on) moves to the teaser queue and the tap goes
   Vacant when the toggle goes on. Intended.
10. **Conditioning-on-tap card:** identical to a Completed tap card (no status
    marker). The status marker is DEFERRED to its own feature request with its
    own toggle (see pending write-ups).
11. **Snapshot + sync gating:** snapshots never carry `/data/upcoming/`; sync
    only writes it while the toggle is ON.
12. **Demo:** seed 1-2 upcoming beers in DEMO_MODE so the teaser UI is visible.
13. **ADR-0006:** one ADR for the upcoming-store policy (why a third store, why
    disposable, why never in snapshots, why gated on the toggle).

## Pending write-ups (await user "go")

- **ADR-0006** - the upcoming-store policy (decision #13). Not yet written.
  This is the ONLY item still awaiting a go.

**DECIDED 2026-08-27 - the conditioning-on-tap marker is no longer a separate
feature request.** Decision #10 deferred it because it looked unrelated; it is
not, now that `show_upcoming_status` builds the customer-facing status
vocabulary, the marker rendering and the CSS. `/to-tickets` emits it as a
**sub-issue of the #4 spec**, with a native `blocked_by` edge on the ticket that
builds the status marker. No standalone feature request, no `needs-triage`, no
`> *This was generated by AI during triage.*` preamble.

It stays its OWN ticket rather than folding into the teaser ticket because the
blast radius differs: `show_upcoming_status` only renders inside a feature that
is off by default (`show_upcoming_previews`), while `show_conditioning_status`
renders on a beer pouring RIGHT NOW, on boards that never enable upcoming
previews. The logic differs too - the on-tap marker only ever fires for
Conditioning (and Fermenting, if `include_fermenting` is on and it pours);
**a Completed beer on tap gets no marker**, so "Ready" never renders there.
Narrower vocabulary, different resolution site in `board.py`.

*Would flip this:* wanting the conditioning marker shipped BEFORE the teaser
feature. It is independently useful and much smaller. Then it needs its own
issue after all, and the teaser work inherits the vocabulary from it.

## Road ahead

1. ~~Finish the 5 display feedback items.~~ DONE.
2. Get user "go" for ADR-0006 + the marker feature-request issue; write/file
   them.
3. `/to-spec` - collapse the locked decisions + the chosen presentation into a
   buildable spec.
4. `/to-tickets` - split the spec into tracer-bullet tickets.
5. `/implement` per ticket (TDD), then `/code-review`.

## Code to read (do not re-derive)

- `app/mapping.py` - `desired_map()` (the discarded runners-up), `STATUS_PRECEDENCE`,
  `status_rank()`, `slot_claim()`, `MAPPING_VERSION` (currently 6).
- `app/board.py` - `build_board()`, `resolve_tap()`, `resolve_visibility()`.
  Payload contract: "resolved answers, not inputs".
- `app/tap_store.py` - `Source`, `SOURCE_PRECEDENCE`, `resolve(slot)`.
  Filenames are private to this module (AST-enforced).
- `app/config_store.py` - `DEFAULT_CONFIG` is the schema, `SETTINGS_BOUNDS` is
  the one bounds table.
- `static/css/display.css`, `templates/display.html`, `static/js/display.js` -
  the visual language the display prototype matches.
- `app/beer_glass.py` - glass silhouettes (the nonic pint path is inlined in
  the display prototype).
- `prototype/upcoming-logic/main.py` - the resolution rules, modelled against
  fake data (the source of truth for the logic the display prototype renders).

Background already captured - do not restate: `CLAUDE.md`, `CONTEXT.md`
(incl. the new Upcoming section + Known hazards), `docs/adr/0001`-`0005`,
`docs/agents/issue-tracker.md` (Wayfinding operations - not needed now, the
grilling is done; the map was never created).

## Constraints

- No em dashes or arrows in prose, comments, or commit messages. Use `-` and
  `->`.
- Commit only when asked. Git stays local; no remote pushes.
- Plain ASCII in files.
- The display prototype is throwaway - keep it self-contained (inline CSS/JS,
  no external requests, openable via `file://`).
- System Python is 3.10; the display prototype is HTML/JS (no Python).

## Output style

`/i-have-adhd` is still active. Lead with the next action, number multi-step
work, cap lists at five, restate state each turn, no preamble or closers. Turn
off only on "stop adhd mode".
