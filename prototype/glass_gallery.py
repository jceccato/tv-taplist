"""PROTOTYPE - THROWAWAY. Beer glass silhouette gallery for issue #6.

Round 6. The shapes are now the maintainer's own hand-modelled paths, dropped in
verbatim. What is being decided has changed with them: the drawing is settled,
but the hand-drawn outlines are not quite symmetrical, so the question is

    **which symmetry correction to apply.**

Each glass is shown four ways, and the bottom bar's arrows flip between them in
place beside what main draws today:

    1 as supplied  - the hand-drawn path, only recentred on x=150
    2 mirror left  - keep the left profile, reflect it to make the right
    3 mirror right - keep the right profile, reflect it to make the left
    4 averaged     - each pair of points meets in the middle

The corrections are rules, not hand-nudged coordinates - see `symmetry.py`. The
head is refitted to whatever rim each correction produces rather than being
placed by hand, so no candidate is flattered by foam that happens to suit it.

    python prototype/glass_gallery.py

The chips switch beer Colour, theme background, and the stem tint - production's
near-white clear glass all but vanishes on the Daylight theme, which matters now
that the stemmed glasses have a substantial stem and foot. Deep-linkable:
`?variant=4&colour=stout&bg=light&tint=safe`.

NOT production code. Whichever candidate wins, its corrected path data is what
gets pasted into `app/beer_glass.py`; this file and `symmetry.py` stay here.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import symmetry  # noqa: E402
from app.beer_glass import (  # noqa: E402
    _GLASS_FILL,
    _GLASS_STROKE,
    _mix,
    beer_glass_svg,
)

LIQUID = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'

# Production's clear glass reads on the dark themes and disappears on Daylight.
# That was survivable when a stem was a 12px rect; it is not now the hand-
# modelled stems carry a wide foot. This mid-grey has enough body for both ends
# of the theme range - the comparison lives on the bar's tint chips.
_THEME_SAFE_FILL = "rgba(146,160,180,0.30)"
_THEME_SAFE_STROKE = "rgba(108,124,146,0.75)"


# ---------------------------------------------------------------------------
# The maintainer's hand-modelled paths, exactly as supplied. Nothing here is
# hand-edited: every candidate is produced from these by a rule in symmetry.py.
# ---------------------------------------------------------------------------
HAND: dict[str, dict[str, str]] = {
    "default": {
        "pour": "M 100 70 L 115 228 q 1 10 11 10 h 46 q 10 0 11 -10 L 199 70 Z",
    },
    "nonicpint": {
        "pour": (
            "M 105 88 L 106 110 Q 102 124 107.889 136.704 "
            "C 108.36 137.969 108.857 143.251 109 145 L 116 229 "
            "a 1 0.4 0 0 0 65 1 L 187.364 145.024 "
            "C 187.682 140.794 187.885 138.622 188.902 135.751 "
            "Q 194.261 126.047 191 110 L 192 88 Z"
        ),
    },
    "schooner": {
        "pour": (
            "M 192 76 C 193.005 85.621 193.319 90.863 193 100 "
            "C 191.309 138.684 184.131 167.944 183.013 184.305 "
            "C 181.79 203.049 181.472 220.859 182 236 "
            "A 1 0.11 0 0 1 119 236 "
            "C 118.093 219.304 117.13 205.122 115.477 184.607 "
            "C 112.448 155.692 104.814 135.89 103.205 100.275 "
            "C 103.097 93.415 102.91 86.036 104 76 Z"
        ),
    },
    "tulip": {
        "pour": (
            "M 107 71 C 103 100 96 111 93 126 C 85 180 119.085 196.943 136 203 "
            "Q 150 208 166 203 C 186.249 195.791 220 171 206 119 "
            "C 202 109 199 100 195 71 Z"
        ),
        "stem": (
            "M 199 255 C 167 252 160 240 160 225 "
            "C 160.35 214.266 160.295 207.997 166 203 Q 150 208 136 203 "
            "C 142 207 141.596 218.355 141.72 225.033 C 142 240 137 251 104 255 "
            "A 47 18 0 0 0 199 255 Z "
            "M 104 255 A 47 18 0 1 0 199 255 A 47 13 0 1 0 104 255 Z"
        ),
    },
    "teku": {
        "pour": (
            "M 114 52 C 117.682 57.404 117.032 65.884 116.099 68.267 "
            "C 109.834 83.765 101.123 120.567 98.912 134.147 "
            "C 96.246 146.669 116 157 138 167 C 145 170 153 170 161 167 "
            "C 182 158 202.126 144.955 200.288 134.577 "
            "C 195.756 115.79 189.108 85.169 182 67 "
            "C 180.707 62.965 182.139 57.704 184.644 51.986 Z"
        ),
        "stem": (
            "M 191 264 C 166 254 153 260 153 223 C 153 202 154 182 161.719 166.531 "
            "C 153.435 169.784 144.325 169.609 137.042 166.356 "
            "C 147 193 145 219 145 223 C 144 258 133 256 107 264 "
            "A 42 8 0 0 0 191 264 Z "
            "M 107 264 A 42 8 0 1 0 191 264 A 42 8 0 1 0 107 264 Z"
        ),
    },
}

MODES = [
    ("1", "as supplied", "as-is"),
    ("2", "mirror left", "left"),
    ("3", "mirror right", "right"),
    ("4", "averaged", "average"),
]


def _auto_foam(pour: str, foam: str) -> str:
    """Fit the head to the rim this path actually has.

    Placing foam by hand would flatter whichever candidate it was tuned against,
    so it is derived: the ellipse spans the mouth, and the three blobs mound over
    it in proportion to the mouth's width.
    """
    top, left, right = symmetry.rim(pour)
    rx = max(10.0, (right - left) / 2 - 3)
    ry = min(17.0, max(8.0, rx * 0.32))
    out = f'<ellipse cx="150" cy="{top:g}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{foam}"/>'
    for dx, dy, r in ((-0.55, -0.60, 0.30), (0.0, -0.95, 0.36), (0.55, -0.60, 0.30)):
        out += (f'<circle cx="{150 + rx * dx:.1f}" cy="{top + ry * dy:.1f}" '
                f'r="{rx * r:.1f}" fill="{foam}"/>')
    return out


def _auto_bubbles(pour: str, bubble: str) -> str:
    """Three bubbles placed off the path's own bounds, for the same reason."""
    x0, y0, x1, y1 = symmetry.bbox(pour)
    w, h = x1 - x0, y1 - y0
    spots = ((0.34, 0.42, 4.5, 0.6), (0.66, 0.60, 4.0, 0.55), (0.46, 0.78, 5.0, 0.5))
    return "".join(
        f'<circle cx="{x0 + w * fx:.1f}" cy="{y0 + h * fy:.1f}" r="{r}" '
        f'fill="{bubble}" opacity="{o}"/>'
        for fx, fy, r, o in spots
    )


def hand_glass(glass: str, mode: str, foam: str, bubble: str, safe_tint: bool) -> str:
    """One candidate: the supplied path under one symmetry rule, plus its head."""
    spec = HAND[glass]
    pour = symmetry.symmetrise(spec["pour"], mode)
    fill = _THEME_SAFE_FILL if safe_tint else _GLASS_FILL
    stroke = _THEME_SAFE_STROKE if safe_tint else _GLASS_STROKE
    out = ""
    if "stem" in spec:
        stem = symmetry.symmetrise(spec["stem"], mode)
        out += f'<path d="{stem}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    out += f'<path d="{pour}" {LIQUID}/>'
    return out + _auto_foam(pour, foam) + _auto_bubbles(pour, bubble)


GLASSES = [
    ("default", "Shaker pint (default)"),
    ("nonicpint", "Nonic pint"),
    ("schooner", "Conical schooner"),
    ("tulip", "Tulip"),
    ("teku", "Teku"),
]
COLOURS = [
    ("amber", "Mid amber (EBC ~25)", "#c3641a"),
    ("straw", "Pale straw (EBC ~5)", "#f5d97a"),
    ("stout", "Near-black stout (EBC ~80)", "#1a0d06"),
    ("unknown", "Unknown Colour (amber fallback)", "#e8a020"),
]
BACKGROUNDS = [
    ("dark", "Default dark", "#131a22", "#e8ecf2"),
    ("oled", "OLED black", "#000000", "#e8ecf2"),
    ("light", "Daylight", "#f4f6f8", "#1a2230"),
]
TINTS = [("prod", "stem: production"), ("safe", "stem: theme-safe")]
SIZES = [(64, "64px - dense 8-up"), (120, "120px - typical card"), (230, "230px - full")]
VARIANTS = [key for key, _label, _mode in MODES]
_MODE_OF = {key: mode for key, _label, mode in MODES}


def render(glass: str, variant: str, base: str, uid: str, safe_tint: bool = False) -> str:
    """Full SVG for one candidate, mirroring the production wrapper exactly."""
    if variant == "current":
        svg = beer_glass_svg(base, glass)
    else:
        top = _mix(base, "#ffffff", 0.30)
        bottom = _mix(base, "#000000", 0.28)
        foam = _mix(base, "#ffffff", 0.80)
        bubble = _mix(base, "#ffffff", 0.55)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" '
            'width="300" height="300" role="img" aria-label="Beer">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{top}"/>'
            f'<stop offset="55%" stop-color="{base}"/>'
            f'<stop offset="100%" stop-color="{bottom}"/>'
            '</linearGradient></defs>'
            + hand_glass(glass, _MODE_OF[variant], foam, bubble, safe_tint)
            + '</svg>'
        )
    # Every SVG here ships a gradient with id="g". Inline in ONE document those
    # ids collide and every pour borrows the first gradient on the page - which
    # is why the stout first rendered straw. Production never hits this (each
    # glass is its own /img response), so this rename is a harness fix.
    return svg.replace('id="g"', f'id="{uid}"').replace("url(#g)", f"url(#{uid})")


def build_html() -> str:
    scenes = []
    for ck, _clabel, chex in COLOURS:
        for tk, _tlabel in TINTS:
            for vk, vlabel, _mode in MODES:
                rows = []
                for gk, glabel in GLASSES:
                    cells = []
                    for col in ("current", vk):
                        uid = f"g-{ck}-{tk}-{gk}-{col}-{vk}"
                        svg = render(gk, col, chex, uid, safe_tint=(tk == "safe"))
                        sizes = "".join(
                            f'<div class="s"><div class="box" style="width:{px}px;'
                            f'height:{px}px">{svg}</div><span>{note}</span></div>'
                            for px, note in SIZES
                        )
                        cells.append(f'<td><div class="sizes">{sizes}</div></td>')
                    rows.append(f'<tr><th>{glabel}</th>{"".join(cells)}</tr>')
                scenes.append(
                    f'<section class="scene" data-colour="{ck}" data-variant="{vk}" '
                    f'data-tint="{tk}"><table><thead><tr>'
                    f'<th>{vk} - {vlabel}</th><th>Current (main)</th>'
                    f'<th>Candidate {vk} - {vlabel}</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></section>'
                )
    bg_buttons = "".join(f'<button data-bg="{k}">{lbl}</button>' for k, lbl, _, _ in BACKGROUNDS)
    colour_buttons = "".join(f'<button data-colour="{k}">{k}</button>' for k, _, _ in COLOURS)
    tint_buttons = "".join(f'<button data-tint="{k}">{lbl}</button>' for k, lbl in TINTS)
    bg_css = "".join(
        f'body[data-bg="{k}"]{{background:{bg};color:{fg}}}' for k, _, bg, fg in BACKGROUNDS
    )
    variants_js = "[" + ",".join(f'"{v}"' for v in VARIANTS) + "]"
    labels_js = "{" + ",".join(f'"{k}":"{lbl}"' for k, lbl, _m in MODES) + "}"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>PROTOTYPE - beer glass silhouettes (issue #6)</title>
<style>
  body {{ margin:0; padding:24px 24px 130px; font:14px/1.4 system-ui,sans-serif;
         background:#131a22; color:#e8ecf2; }}
  {bg_css}
  h1 {{ font-size:16px; font-weight:600; margin:0 0 4px; }}
  p.sub {{ margin:0 0 20px; opacity:.7; max-width:80ch; }}
  .scene {{ display:none; }} .scene.on {{ display:block; }}
  table {{ border-collapse:collapse; width:100%; table-layout:fixed; }}
  th, td {{ border:1px solid rgba(128,144,160,.35); padding:10px; vertical-align:top; }}
  thead th {{ text-align:left; font-weight:600; }}
  tbody th {{ text-align:left; width:150px; font-weight:500; }}
  .sizes {{ display:flex; gap:14px; align-items:flex-end; flex-wrap:wrap; }}
  .s {{ display:flex; flex-direction:column; align-items:center; gap:6px; }}
  .s span {{ font-size:10px; opacity:.55; }}
  .box svg {{ width:100%; height:100%; display:block; }}
  #bar {{ position:fixed; left:50%; bottom:16px; transform:translateX(-50%);
          display:flex; gap:8px; align-items:center; background:#ff3d7f; color:#fff;
          padding:10px 14px; border-radius:999px; box-shadow:0 6px 24px rgba(0,0,0,.45);
          font-weight:600; z-index:99; flex-wrap:wrap; justify-content:center;
          max-width:94vw; }}
  #bar button {{ font:inherit; font-size:12px; border:0; border-radius:999px;
                 padding:6px 10px; cursor:pointer; background:rgba(255,255,255,.22);
                 color:#fff; }}
  #bar button.on {{ background:#fff; color:#ff3d7f; }}
  #state {{ min-width:150px; text-align:center; font-size:13px; }}
  .sep {{ opacity:.55; }}
</style>
<body data-bg="dark">
<h1>PROTOTYPE - beer glass silhouettes (issue #6)</h1>
<p class="sub">The shapes are the hand-modelled paths, dropped in as supplied. The arrows
flip between four symmetry corrections of each - as supplied, mirror the left profile,
mirror the right, or average the two - applied by rule, with the head refitted to whatever
rim results. The chips switch beer Colour, theme background, and the stem tint.</p>
{"".join(scenes)}
<div id="bar">
  <button id="prev">&#8592;</button>
  <span id="state"></span>
  <button id="next">&#8594;</button>
  <span class="sep">|</span>
  {colour_buttons}
  <span class="sep">|</span>
  {bg_buttons}
  <span class="sep">|</span>
  {tint_buttons}
</div>
<script>
  const variants = {variants_js};
  const labels = {labels_js};
  const q = new URLSearchParams(location.search);
  let vi = Math.max(0, variants.indexOf(q.get('variant')));
  let colour = q.get('colour') || 'amber';
  let tint = q.get('tint') || 'prod';
  if (q.get('bg')) document.body.dataset.bg = q.get('bg');
  function draw() {{
    document.querySelectorAll('.scene').forEach(s => s.classList.toggle('on',
      s.dataset.variant === variants[vi] && s.dataset.colour === colour
      && s.dataset.tint === tint));
    document.getElementById('state').textContent =
      variants[vi] + '/' + variants.length + ' - ' + labels[variants[vi]];
    document.querySelectorAll('#bar button[data-colour]').forEach(
      b => b.classList.toggle('on', b.dataset.colour === colour));
    document.querySelectorAll('#bar button[data-tint]').forEach(
      b => b.classList.toggle('on', b.dataset.tint === tint));
    document.querySelectorAll('#bar button[data-bg]').forEach(
      b => b.classList.toggle('on', b.dataset.bg === document.body.dataset.bg));
    history.replaceState(null, '', '?variant=' + variants[vi] + '&colour=' + colour
      + '&bg=' + document.body.dataset.bg + '&tint=' + tint);
  }}
  document.getElementById('prev').onclick = () => {{ vi = (vi + variants.length - 1) % variants.length; draw(); }};
  document.getElementById('next').onclick = () => {{ vi = (vi + 1) % variants.length; draw(); }};
  document.querySelectorAll('#bar button[data-colour]').forEach(b => b.onclick = () => {{
    colour = b.dataset.colour; draw();
  }});
  document.querySelectorAll('#bar button[data-tint]').forEach(b => b.onclick = () => {{
    tint = b.dataset.tint; draw();
  }});
  document.querySelectorAll('#bar button[data-bg]').forEach(b => b.onclick = () => {{
    document.body.dataset.bg = b.dataset.bg; draw();
  }});
  addEventListener('keydown', e => {{
    if (e.key === 'ArrowLeft') document.getElementById('prev').click();
    if (e.key === 'ArrowRight') document.getElementById('next').click();
  }});
  draw();
</script>
"""


if __name__ == "__main__":
    out = pathlib.Path(__file__).with_name("glass_gallery.html")
    out.write_text(build_html(), encoding="utf-8")
    print("wrote " + str(out))
