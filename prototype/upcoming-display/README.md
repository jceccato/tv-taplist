# Coming up - display prototype (issue #4)

Throwaway. The presentation model for the "Coming up" teaser cards - one
baseline plus optional surfaces, all composable from the controls above the TV.
Reads the real `display.css` visual
language (palette, card shape, swatch, stats, vacant stripes, dots, ticker) but
is fully self-contained - no external requests, openable via `file://`.

## Open

Just open `index.html` in a browser (Chrome/Edge/Firefox). No server needed.

The TV frame is a fixed 1280x720 (16:9) box so the feel matches a real display.

## The model: one baseline, optional surfaces

This stopped being three rival candidates. It is now **one composition**, and
every part of it is an operator choice.

**The baseline is the in-place cross-fade, always on when upcoming beers are
enabled.** A teaser bound to an occupied slot fades in over that slot and back.
It is the clearest statement the board can make, because the teaser sits exactly
where the beer will pour.

**Board-level rule, underneath all of it:** a VACANT slot with a bound Upcoming
Beer shows that teaser PERMANENTLY. There is no pouring beer to fade back to, so
an empty slot with a beer waiting on it simply advertises the beer. This is a
property of the board, not a presentation choice, so it applies under every
combination below.

**The baseline cannot reach every teaser.** That is what the optional surfaces
are for:

- An **unbound** teaser (an `upcoming:` token with no `tap:X`) has no slot to
  fade over. It can only render with a `?` for a tap number, which tells the
  customer nothing. This was the gap that broke the winner-takes-all framing.
- If the operator turns **"Rotate occupied slots" off**, every occupied-slot
  teaser becomes unreachable too.

Teasers in that position are the **overflow**. The **on-deck page** and the
**half-board panel** are homes for the overflow - one, both, or neither.

## Controls

| Control | Effect |
|---------|--------|
| Rotate occupied slots | May a pouring beer be rotated out at all? Off pushes every occupied-slot teaser into the overflow. |
| Also show: On-deck page | Adds a carousel page carrying the surface set. |
| Also show: Half-board panel | Adds the bottom-half panel carrying the surface set. |
| Surface carries | `Overflow only` (default, nothing appears twice) or `All upcoming`. |
| Panel every | The half-board panel's multiplier of the cadence (1x-6x, default 2x). |
| Upcoming every | The one cadence. The cross-fade runs at 1x, the deck page at 3x. |

## One cadence, three consumers, one interlock

The **"Upcoming every"** selector (6s / 12s / 20s / 45s) drives everything. An
operator tuning "how often do I see upcoming beers" is asking one question, not
three, so the real build gets one setting.

The surfaces run at **multiples** of it - panel `2x` by default and
**operator-configurable** (1x to 6x), deck page a fixed `3x`. Two reasons, and
the second one is the important one:

1. A surface that took over on every tick would mean the board almost never
   shows taps.
2. **The cross-fade cycles ONE teaser per tick.** With several bound teasers, a
   beer late in the list only comes round every few ticks. If a surface stole
   every other tick, that beer might never be seen at all. Slower surfaces leave
   the baseline room to work through its list.

A single interlock (`showing`) makes them safe together: **nothing starts while
something else is up.** A panel never lands on top of a cross-fade, and the deck
page never swaps out from under one. A surface whose turn arrives while the board
is busy simply skips that turn and takes the next one.

**A second, separate guard: the cross-fade only runs while the TAP PAGE is
showing.** An inactive carousel page is still laid out (it is only faded to
opacity 0), so without this the overlay lands in the right PLACE over the wrong
PAGE - a customer reading the on-deck page watches a tap-2 teaser slide over a
page that has no tap 2 on it. The interlock alone does not catch it, because
manual dot navigation changes the page without going through the scheduler. So
the guard is on the page index, and leaving the tap page also pulls any
in-flight cross-fade with it.

**The deck page's multiplier is not configurable yet.** It probably should be,
for symmetry - but the two surfaces together is the configuration the user
expects almost nobody to run, so it is not urgent. Decide in the spec.

The hold - how long a teaser stays up - is **derived**, not configured: ~58% of
the gap, floor 1.5s. A longer interval then reads as "occasionally, and lingers"
rather than "rarely, and flickers".

## The duplication question (tap 2 twice) - RESOLVED

With `Surface carries: All upcoming`, the panel shows the Imperial Stout as
upcoming on tap 2 while the Brown Ale pours on tap 2 in the row above.

**The user's verdict: this is fine, and both settings stay.** The differing
frequencies mean the same batch is never duplicated *at the same moment* on
screen - the panel is up while the cross-fade is not - so what a customer
actually sees is one beer at a time, not a contradiction.

The two settings suit different surfaces:

- **On-deck page: `All upcoming` is the better pairing.** The whole point of a
  dedicated page is to be all-encompassing; an on-deck page that quietly omitted
  the beers the cross-fade happens to cover would be a worse page.

  **`All upcoming` means ALL, permanent teasers included.** It used to exclude
  a teaser sitting permanently in a vacant slot, which made the setting quietly
  untrue - the Dubbel on vacant tap 5 was missing from the on-deck page even
  though the page is meant to be the complete list, and an operator reading it
  as complete would have been misled. `Overflow only` still leaves permanent
  teasers out, correctly: the board is already giving them a whole slot all the
  time, which is the strongest presentation available, so they are not
  overflow.
- **Half-board panel: either works.** `Overflow only` keeps it a tight catch-all;
  `All upcoming` makes it "everything coming to this bar, in one glance".

**Both surfaces enabled at once does get funky** - that is the configuration
where the scheduling gets busy and the duplication is most visible. The user's
call: that is an operator choice, few people will run it, and it does not need
designing around.

Still worth a look in the spec: if `All upcoming` is on, the surface card could
read "after the Brown Ale" rather than "coming up on tap 2", turning duplication
into a sequence a customer can read. The board already has the pouring beer's
name.

## An unfinished beer is read from its RECIPE

A batch that has not finished fermenting (`fermenting`, `brewing`, `planning`)
is read from its **recipe** for **every** attribute - colour, IBU, OG, FG and ABV
- not just for the ones that happen to be missing.

The FG on a beer still in primary is simply wherever the gravity sits today; the
ABV derived from it swings with it; and a "calculated" colour or IBU on an
unfinished batch is just as provisional. Mixing the two sources would be worse
than either: a card showing the recipe's ABV beside today's half-fermented FG
describes a beer that does not exist. The recipe is one coherent description of
the beer the customer will actually be handed.

In the fake data the Dubbel's live values are deliberately mid-ferment nonsense
(FG 1.030, so 3.3% ABV, IBU 11 and a pale colour). The card shows its recipe
instead: 7.2%, IBU 18, OG 1.056, FG 1.012, and the proper deep brown.

The ABV still carries `~` regardless of source - see below.

## Teaser card options

A row above the TV controls the teaser cards themselves (these are per-board
Settings in the real build, not presentation choices):



- **Label** - a preset drop-down ("Coming up" / "Up next" / "Coming soon" /
  "Just around the bend") plus a **Custom...** option with a free-text field
  capped at 32 characters. This
  previews a real Settings-level feature (an `upcoming_label` setting), not just
  a prototype switch: operators pick a preset or type their own, and the cap is
  what keeps the label inside one slot. The label drives both the ribbon and the
  "<label> on tap N" subtitle.
- **ABV** - off by default (the `show_upcoming_abv` toggle is off); toggles an
  ABV stat onto the teaser cards to preview the opt-in look. **Every upcoming
  ABV is marked `~`.** The source still depends on status (a fermenting batch has
  only its recipe target), but a hydrometer or a fermentation-device log swings
  widely while a beer is still working, so even a measured reading on an
  unfinished beer is an estimate. Marking only the fermenting ones would present
  the rest as final when they are not. In the real build this is a mapping-logic
  change and needs a `MAPPING_VERSION` bump.
- **Status** - on by default; prints the batch status ("Conditioning",
  "Fermenting", "Planned", "Brewing", "Ready") directly under the
  "<label> on tap N" subtitle. Answers the customer's actual question - not
  "is something coming" but "how soon". Spelled for a customer, not for
  Brewfather: a Completed batch reads "Ready", not "Completed".

**There is no size option.** A teaser is formatted exactly like every other slot,
from the same config settings. A teaser that sized itself differently would be
the one card on the board that ignores the operator's layout.

Changing any option re-renders and **forces the on-deck page visible**, so the
effect is immediate. (Previously the re-render restarted the carousel at page 0,
the tap page, and the change was invisible until the next rotation.)

## Teaser card differentiator

Dashed **amber** border + a "COMING UP" ribbon in the top-left, plus an
"on tap N" / "on deck" subtitle and the batch status under it. Amber is the board's existing accent
(`var(--accent)`), so a teaser reads as "related but provisional" without
inventing a new colour. The dashed border echoes the vacant card's dashed border
- "not poured yet" - while the ribbon makes the intent explicit. Teaser stats
show IBU, OG, FG and the colour swatch; **no ABV** (the `show_upcoming_abv`
toggle is off by default).

## Tools

- **Freeze animations** - stops every animation and clears the carousel timers
  so a screenshot captures a still frame (per CLAUDE.md's screenshot gotcha:
  continuous animations time out captures).

## Fake data

6 tap slots (4 poured, 2 vacant) + 4 teasers: a conditioning Saison bound to
VACANT slot 3, a conditioning Imperial Stout bound to OCCUPIED slot 2, a
fermenting Dubbel bound to VACANT slot 5, and an unbound Hefeweizen (the
`upcoming:` token path). So the board shows the Saison and the Dubbel
permanently, the Stout is the only teaser the baseline cross-fade can reach, and
the Hefeweizen is the overflow. Each teaser carries both a measured `abv` and a recipe
`abvTarget` so the status-dependent ABV source is visible; the Dubbel's measured
value is deliberately absent. Two teasers can bind to one slot in the real
model; not exercised here but the cards would simply stack on the on-deck page.

## What the prototype suggests (design tension)

1. **Amber-everywhere risk:** the accent is already used for the tap number and
   the ticker; the teaser reuses it for border + ribbon + subtitle. At a glance a
   teaser might read as "the highlighted tap" rather than "coming up". The dashed
   border is what disambiguates - worth keeping prominent.
2. **"Coming up" wording for a finished beer:** a Completed batch with the
   `upcoming:` token would show as "COMING UP" despite being ready. The label
   control lets the operator pick "Up next", "Coming soon" or "Just around the
   bend", or type their own, which is why the setting exists rather than a
   hardcoded string - but "Coming up" is the default and will be what most boards
   show.
3. **A permanent teaser in a vacant slot competes with the vacant card itself.**
   The board no longer tells the customer that tap 3 is empty - it tells them
   what is coming. That is the point, but the operator loses the "we are between
   beers" signal entirely on any slot with a bound upcoming beer. Decide in the
   spec whether that is always right or wants its own toggle.
4. **The "<label> on tap N" subtitle is redundant next to the ribbon.** With the
   ribbon reading "COMING UP" and the subtitle reading "Coming up on tap 3"
   directly beneath it, the label is printed twice within an inch, and the tap
   number is already in the head. It gets worse with a long label: "Just around
   the bend" prints twice at 20 characters. Candidates: drop the subtitle on
   BOUND teasers and keep it only for unbound ones ("On deck - no tap assigned",
   which the ribbon does not say); or drop the ribbon and let the dashed border
   plus the subtitle carry it; or collapse the subtitle into the status line,
   which now sits under it anyway. **Open for the spec.**
5. **The overflow can be empty.** With no unbound teasers and rotation on, both
   surfaces carry nothing and are simply not rendered. That is correct, but it
   means an operator who enables the on-deck page may see no change at all and
   conclude it is broken. The admin should say so ("no upcoming beers currently
   need this") rather than leaving a dead toggle.
