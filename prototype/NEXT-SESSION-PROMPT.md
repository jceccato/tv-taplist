# Prompt for resuming this work in a fresh session

Paste the block below into a new Claude Code session in `C:\misc\TVTapList`.
It is deliberately short: everything else is in `prototype/README.md`, the
issue, and the code.

---

```
Continuing issue #6 (beer glass silhouettes) on branch
prototype/glass-silhouettes-6. Read prototype/README.md first - it has the
workflow, the settled decisions and the traps.

State: the five existing glasses shipped to main (f6c4731, unreleased) as
hand-modelled paths corrected with the "averaged" symmetry rule, and the nonic
pint is now DEFAULT_GLASS. Issue #6 stays open for Phase 2: new glass types
(weizen, snifter, pilsner flute, kolsch stange, nitro stout, stein/dimpled mug).

I hand-draw each new glass as SVG path data in a 300x300 box - the pour, plus
stem+bowl-underside+foot as ONE path if stemmed. You paste it verbatim into HAND
in prototype/glass_gallery.py, regenerate, and show me the four symmetry
corrections at all three sizes. I pick, then you fold the winner into
app/beer_glass.py as one _SILHOUETTES row plus one GLASS_TYPES entry, update the
glassware lists in docs/FAQ.md, docs/INSTALLATION.md and CLAUDE.md, add a
CHANGELOG Unreleased line, run the suite, and commit.

Rules that already settled: the pour IS the silhouette (a drawn glass vessel was
rejected); never hand-place foam or bubbles (they derive from the corrected
path); check every shape at 64px and on the Daylight theme; don't bump
MAPPING_VERSION for drawing changes.

To preview: python prototype/glass_gallery.py, then open the HTML it writes.
Screenshots for you: msedge --headless=new --screenshot to a scratchpad path.
```

---

When the glass types are done: fold them in, close #6, delete this branch.
