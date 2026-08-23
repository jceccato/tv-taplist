# Glass silhouette prototype (issue #6)

Throwaway harness for designing the beer-glass placeholders. **Nothing here
ships** - it is not in the image, not on `main`, and not covered by tests. It
exists so the next hand-drawn glass can be judged and corrected the same way the
first five were.

Branch: `prototype/glass-silhouettes-6`. Keep it until issue #6 closes.

```bash
python prototype/glass_gallery.py     # writes prototype/glass_gallery.html
```

Open that file. The pink bar's arrows flip between candidates **in place**
beside what `main` draws; the chips switch beer Colour, theme background and
stem tint. Every state is deep-linkable, which is also how the screenshots were
taken: `?variant=4&colour=stout&bg=light&tint=safe`.

## What shipped, and what is still open

Five glasses shipped on `main` (`f6c4731`): shaker pint, nonic pint, conical
schooner, tulip, teku - all hand-modelled, all corrected with the **averaged**
symmetry rule, with the nonic as `DEFAULT_GLASS`.

Still open, and why the branch stays:

- **More glass types** - weizen, snifter, pilsner flute, kolsch stange, nitro
  stout, stein/dimpled mug. The maintainer hand-draws each one; this harness
  corrects and previews it.
- **The tulip's foot is slightly egg-shaped.** Its two arcs carry different
  `ry` (18 and 13), so the ellipse is not symmetrical top-to-bottom. That is a
  *vertical* asymmetry, which `symmetry.py` deliberately does not touch - it
  may be intentional perspective. Decide it if the tulip is ever redrawn.

## Adding a hand-drawn glass

1. **Draw the pour**, and the stem+foot as a second path if it is stemmed, in a
   300x300 box. Draw the stem, the underside of the bowl and the foot as ONE
   path - that is what stops the foot floating clear of the stem, which is the
   bug the first five shipped with for months.
2. **Paste both into `HAND` in `glass_gallery.py`**, exactly as drawn. Do not
   tidy the numbers: the whole point is that the correction is a rule applied to
   the raw drawing, so it can be re-run and re-judged.
3. **Regenerate and look.** Compare the four corrections - as supplied, mirror
   left, mirror right, averaged - at all three sizes, on all three backgrounds,
   against the pale/amber/stout/Unknown colours. Averaged won every time so far;
   it moves each point half as far as mirroring, but it cannot rescue a side you
   actively dislike, which is when mirroring earns its place.
4. **Emit the production values** once a correction is chosen:

   ```bash
   python -c "import sys; sys.path.insert(0,'prototype'); \
     import symmetry as sy, glass_gallery as gg; \
     print(sy.symmetrise(gg.HAND['<key>']['pour'],'average')); \
     print(gg._auto_foam(sy.symmetrise(gg.HAND['<key>']['pour'],'average'),'FOAM')); \
     print(gg._auto_bubbles(sy.symmetrise(gg.HAND['<key>']['pour'],'average'),'BUBBLE'))"
   ```

5. **Add one `_SILHOUETTES` row and one `GLASS_TYPES` entry** in
   `app/beer_glass.py`, using the emitted path, the head's `(cy, rx, ry)` and
   the bubbles. Nothing else in the app needs to change - a test fails if the
   two lists disagree, another if the pour is not centred on x=150.
6. **Document it**: the glassware lists in `docs/FAQ.md`, `docs/INSTALLATION.md`
   and the token table in `CLAUDE.md` all name the valid keys.

## How the symmetry correction works

`symmetry.py` parses path data to absolute coordinates, then folds the point
list of each subpath onto itself: these outlines are drawn as one closed loop
down one side and back up the other, so **the Nth point from the start is the
mirror partner of the Nth point from the end**. Three rules act on each pair -
keep the left, keep the right, or average - and the result is recentred on
x=150.

Two properties worth keeping if it is ever rewritten:

- **Commands are never rewritten, only the points they carry move.** An arc
  keeps its radii and its sweep flag, so nothing has to be re-reasoned.
- **A loop that closes with an arc back to its own start** (both hand-modelled
  stems do) repeats that point at each end of the list. It sits out of the fold
  and is copied back afterwards - without that it folds against itself and
  collapses onto the axis.

The head and bubbles are **derived** from the corrected path's own rim and
bounds, never hand-placed. Foam tuned against one candidate flatters it.

## Traps this prototype hit, so the next one does not

- **Inline SVGs on one page share `id="g"`.** Every glass ships the same
  gradient id, so all of them borrowed the first one on the page - the stout
  rendered straw and it looked like a colour bug. The harness renames per cell.
  Production never hits this: each glass is its own `/img/beer-glass` response.
- **A shape that only works at 300px is a fail.** Always look at 64px, where a
  bell's waist and a teku's rim lip are the first things to disappear.
- **The Daylight theme is where clear glass goes to die.** Anything drawn in the
  near-white translucent vanishes on it. Use the shared `_GLASS_FILL` /
  `_GLASS_STROKE`, which are a mid-grey for exactly this reason.
- **The vessel approach was rejected** in round 1 - drawing a translucent glass
  around the pour, rather than letting the pour be the silhouette. Do not
  re-propose it; the pour is the shape.

## History

Six rounds, one commit each, all on this branch:

| Round | What it settled |
|-------|-----------------|
| 1 | Liquid-only vs a drawn vessel - **vessel rejected** |
| 2 | Three shapes per glass; shaker and schooner from reference photos |
| 3 | Per-glass alterations; teku rebuilt as a wine bowl |
| 4 | Floating foot found and fixed; schooner's square base; teku from photo 2 |
| 5 | Schooner tangent flow; inverted tulip bowl; teku stem = bowl height |
| 6 | All five hand-modelled; symmetry as a rule; stem-tint experiment |
