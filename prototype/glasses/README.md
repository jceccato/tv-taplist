# Glass silhouette harness

The tools the nine beer-glass placeholders in `app/beer_glass.py` were designed
with (issue #6, closed). **Nothing here ships** - `prototype/` is in
`.dockerignore` and in the publish workflow's path filter, so none of it reaches
the image or triggers a build.

It is kept, rather than deleted with the branch, for one reason: **the loop it
supports is a human drawing a shape and an agent folding it into production**,
and that loop is hard to reconstruct from the path data alone. If another glass
is ever wanted, this is the difference between an afternoon and a week.

---

## Read this before you trust any of it

**Assume every page here is stale.** None of it is covered by the test suite, so
production can move underneath it and nothing fails. Each file carries a banner
at the top saying what specifically will rot in it; the shared risks are:

- **The generated HTML is not committed.** Every page is one command away, and a
  committed page is worse than no page - it looks current and is not.
  `.gitignore` drops `prototype/*/*.html`.
- **They import from `app/`.** An `ImportError` or a `KeyError` on a renamed
  constant is the cheap failure, and the reason to run a page before reading it.
  The expensive failure is a page that renders fine while quietly disagreeing
  with what the app ships.
- **The shape of a `_Silhouette` row is a contract here.** `contact_sheet.py`
  renders it back out and `foam_lab.py` decodes the foam band **by position**.
  Add a field, or change how `foam` is written, and both need a look.
- **`glass_gallery.py` is already stale**, on purpose: its `HAND` dict holds
  five of the nine glasses, because the other four arrived after the round it
  was built to judge. It still does its job for what it holds.

The one thing here that will not rot is `symmetry.py`. It is a rule, not a page:
a path in, a path out, nothing from `app/`.

---

## What each one is for

| File | What it answers |
|------|-----------------|
| `symmetry.py` | **The rule**, not a page. Makes a hand-drawn path symmetrical about x=150. Every shipped glass went through it. |
| `contact_sheet.py` | Every glass at 220/120/64/40px, any theme, any beer Colour, each with its production row in a copyable block. **Start here.** |
| `foam_lab.py` | The head: how wide, how deep, where it sits, and the shape of its underside. Opens on what each glass ships. |
| `mug_lab.py` | The dimpled mug's facet grid, derived from the pour's own profile. |
| `glass_gallery.py` | Four symmetry corrections side by side, for judging a freshly drawn path. |
| `mug-dimples.md` | Working notes from the mug. |

```bash
python prototype/glasses/contact_sheet.py   # then open the .html it writes
```

Every knob on every page is in the URL, so a setting worth keeping is a link
worth pasting.

---

## Adding a glass

The loop the last four were built with. **The maintainer draws; the agent folds
in.** Do not skip step 1 by generating a shape parametrically - it was tried,
and a drawn weizen beat a computed one on the first attempt.

1. **Draw the pour** in a 300x300 box, and the stem+bowl-underside+foot as ONE
   path if it is stemmed. Drawing those three as one path is what stops the foot
   floating clear of the stem - the bug the first five shipped with for months.

   Mirror the structure down each side: `M`, *n* commands down the right, one
   command across the bottom, *n* commands back up the left, `Z`. The fold pairs
   the Nth point from the start with the Nth from the end, so an uneven count
   pairs the wrong points. `A 1 0.35 0 0 1 <x> <y>` is the idiom for a rounded
   base: SVG scales the radii up to span the endpoints, so the ratio is the
   curvature.

2. **Paste it verbatim into `HAND` in `glass_gallery.py`.** Do not tidy the
   numbers - the whole point is that the correction is a rule applied to the raw
   drawing, so it can be re-run and re-judged.

3. **Regenerate and compare the four corrections** at every size, on every
   background, against pale/amber/stout/Unknown. Averaged has won every time; it
   moves each point half as far as mirroring, but it cannot rescue a side you
   actively dislike, which is when mirroring earns its place.

4. **Emit the production values.** Everything but the outline is derived - the
   head's `rx` is the measured mouth, the foam band's underside is the pour's
   real width at that depth, the bubbles come off the path's own bounds. Foam
   tuned against one candidate flatters it, so never hand-place it.

5. **Add one `_SILHOUETTES` row and one `GLASS_TYPES` entry** in
   `app/beer_glass.py`. Nothing else in the module changes: a test fails if the
   two sets disagree, another if the pour is not centred on x=150, another if
   the head does not fill the measured mouth.

6. **Tune the head in `foam_lab.py`**, which opens on what the glass now ships,
   and fold the result back into the row.

7. **Document it**: the glassware lists in `docs/FAQ.md`, `docs/INSTALLATION.md`
   and the token table in `CLAUDE.md` all name the valid keys.

---

## Decisions that are settled - do not re-litigate

- **The pour IS the silhouette.** Drawing a translucent glass vessel around it
  was rejected in round 1. Do not re-propose it.
- **A shape that only works at 300px is a fail.** Check 40px, where a bell's
  waist and a teku's rim lip are the first things to go.
- **The Daylight theme is where clear glass dies.** Use the shared `_GLASS_FILL`
  / `_GLASS_STROKE` mid-greys, never a near-white translucent.
- **Never hand-place foam or bubbles.** Derive them from the corrected path.
- **Do not bump `MAPPING_VERSION`** for a drawing change - extraction is
  untouched.
- **`stem` means "glass drawn behind the pour"**, not "a stem". The mug's handle
  uses it.
- **`etch`/`sheen`/`foam` are clipped to the pour**, and that clip is
  load-bearing: it is what cuts the mug's outer dimples in half at the profile.
- **Inline SVGs on one page share `id="g"` and `id="p"`.** Every comparison page
  must rename them per cell or every glass borrows the first one's gradient and
  the stout renders straw. Production is unaffected - each glass is its own
  `/img/beer-glass` response.
- **The head's underside fade settled at zero** on all nine, so production does
  not implement it at all: the foam meets the beer on a hard edge. The knob is
  still in `foam_lab.py`, and it is the first thing to try if a head ever reads
  as painted on.

## Known, and not done

- **These are four overlapping harnesses.** `glass_gallery`, `mug_lab` and
  `foam_lab` each re-implement the same two moves: flatten a corrected path to a
  polyline, then ask it for its width at a given height. Folding them into one
  lab with per-glass panels was agreed and never started. If you are about to
  add a fifth page, do that first.
- **The tulip's foot is slightly egg-shaped.** Its two arcs carry different `ry`
  (18 and 13), so the ellipse is not symmetrical top to bottom. That is a
  *vertical* asymmetry, which `symmetry.py` deliberately does not touch - it may
  be intentional perspective. Decide it if the tulip is ever redrawn.

## History

Issue #6 ran in rounds, one commit each, on `prototype/glass-silhouettes-6`
(merged and deleted). Rounds 1-6 settled liquid-only over a drawn vessel, then
five hand-modelled shapes and symmetry as a rule. Phase 2 added the Willi Becher
(and made it the default), the dimpled mug with its facets etched into the pour,
a head that reaches the lip and has depth, and finally the pilsner flute and the
weizen. Nine glasses.
