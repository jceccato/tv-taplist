# Handoff - issue #6, glass silhouettes (Phase 2 in progress)

Written 2026-08-27, at the end of a session that ran long. Start here, then read
`prototype/README.md` for the workflow and the traps. Everything below is the
state of the world, not a plan.

## Where things stand

Branch `prototype/glass-silhouettes-6`, checked out as a worktree at
`../TVTapList-glass` (the repo's own working tree is on other work - do not
check this branch out there and do not switch that tree's branch).

**On `main` and pushed** (`8883e4a`): seven glasses, 460 tests green.

| Landed | What |
|--------|------|
| `d61c188` | Dimpled mug - handle, and facets etched into the pour |
| `3a26147` | Willi Becher, and it becomes `DEFAULT_GLASS` |
| `0e759f9` | The head reaches the lip, and has depth |

**Not on `main`, deliberately:** everything under `prototype/`. The production
commits were cherry-picked so the labs stay here. The changelog's `## Unreleased`
section already describes all of it for the operator. **No version tag has been
pushed**, so nothing has reached `:latest` - only the `:main` canary.

## Still open

1. **Two more glasses**: pilsner flute, then weizen. The maintainer hand-draws
   each; see `prototype/README.md` for the loop. Willi Becher was the easy one -
   the weizen's waist and flared bulb are the only remaining shape that will
   fight back at thumbnail size.
2. **Generalise the labs into one `glass_lab.py`.** Agreed, not started. Today
   there are three overlapping harnesses (`glass_gallery.py` for the four
   symmetry corrections, `mug_lab.py` for the dimple grid, `foam_lab.py` for the
   head) that each re-implement the same two moves: flatten a corrected path to
   a polyline, then ask it for its width at a given height. One lab, per-glass
   panels that appear only for glasses that declare that feature.
3. **Close #6 and delete this branch** once the glasses are done and the labs
   have found their permanent home.

## What the labs are for

Run any of them with `python prototype/<name>.py`, then open the `.html` it
writes. Every knob is in the URL, so a setting worth keeping is a link worth
pasting.

- **`foam_lab.py`** - the head, on all seven glasses. Keep this one; it is how
  the head was settled and it is the only place the rim is measured rather than
  typed. Every knob is per glass at the moment.
- **`mug_lab.py`** - the dimpled mug's facets. Keep. Derives the whole grid from
  the corrected pour's own profile.
- **`glass_gallery.py`** - the original round-by-round candidate gallery.
- **`symmetry.py`** - the fold. Not a lab; the rule every hand-drawn path goes
  through.
- **`glassware.html`** - generated comparison of all seven as production renders
  them, four colours by three themes, down to 40px. Regenerate it from the
  snippet in the session or just look at it; it is the fastest way to judge a
  new glass against the set.

## Things that will bite

- **A silhouette is data.** Adding a glass is one `_SILHOUETTES` row plus one
  `GLASS_TYPES` entry. Tests pin that the two sets agree, that every pour is
  centred on x=150, and that the head matches the pour's real mouth.
- **Do not bump `MAPPING_VERSION`** for drawing changes.
- **`stem` means "glass drawn behind the pour"**, not "a stem" - the mug's
  handle uses it.
- **`etch`/`sheen`/`foam` are clipped to the pour.** That clip is load-bearing:
  it is what cuts the mug's outer dimples in half at the profile.
- **Inline SVGs on one page share `id="g"`** and now `id="p"` too. Every
  comparison page must rename them per cell or every glass borrows the first
  one's gradient and the stout renders straw. Production is unaffected - each
  glass is its own `/img/beer-glass` response.
- **Check every shape at 64px and on the Daylight theme** before believing it.
- **Long heredocs fail in this shell.** Write a script to the scratchpad and run
  it, or use the file-writing tools.

## The one judgement call worth revisiting

The head's underside **fade** settled at zero on all seven glasses, so it is not
implemented in `app/beer_glass.py` at all - the foam meets the beer on a hard
edge. It looked right at every size tested, but it is the first thing to try if
the head ever reads as painted on. The lab still has the knob.
