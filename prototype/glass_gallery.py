"""PROTOTYPE - THROWAWAY. Beer glass silhouette gallery for issue #6.

Round 2. Round 1 asked "liquid-only, or draw a translucent glass vessel around
the pour?" - the maintainer looked and **rejected the vessel outright**, so all
of that code is gone. The pour stays the silhouette, which is how
`app/beer_glass.py` has always drawn it.

The question now: **which shape, per glass.** Three candidate silhouettes for
each of the five glasses, and the bottom bar's arrows flip between them in
place, beside what main draws today. Flipping one shape back and forth in the
same spot is a sharper comparison than reading three of them side by side.

    python prototype/glass_gallery.py

Shaker pint and conical schooner are redrawn from reference photos the
maintainer supplied. The schooner reference is the Australian bell-shaped
"conical" schooner - wide rim, a curve inward to a waist low down, then a slight
flare onto a heavy base - NOT the straight cone the old code drew.

Colour and theme background are still switchable (the smaller chips), because a
shape that only reads on one background is a fail. Deep-linkable:
`?variant=2&colour=stout&bg=light`.

NOT production code: no tests, coordinates hand-tuned by eye. The winning path
data gets rewritten properly into `app/beer_glass.py`; this file stays on the
prototype branch.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.beer_glass import _bubbles, _mix, _stem, beer_glass_svg  # noqa: E402

LIQUID = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'


def _foam(cy: int, rx: int, ry: int, blobs, foam: str) -> str:
    """The head: a surface ellipse plus three blobs mounding over the rim."""
    out = f'<ellipse cx="150" cy="{cy}" rx="{rx}" ry="{ry}" fill="{foam}"/>'
    return out + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{foam}"/>' for x, y, r in blobs
    )


def _pour(d: str) -> str:
    return f'<path d="{d}" {LIQUID}/>'


# ---------------------------------------------------------------------------
# Candidates. Three per glass; each is (label, builder). Builders take the
# already-tinted foam and bubble colours, exactly like the production module.
# ---------------------------------------------------------------------------

# --- Shaker pint: straight sides with a real taper, flat floor, wide rim. ---
def shaker_1(foam: str, bubble: str) -> str:
    """Faithful to the reference: strong taper, tall, tight base corners."""
    return (
        _pour("M106 72 L120 226 q1 10 11 10 h38 q10 0 11 -10 L194 72 Z")
        + _foam(72, 44, 15, [(124, 63, 13), (150, 57, 16), (176, 63, 13)], foam)
        + _bubbles(bubble, [(138, 132, 5, 0.7), (160, 172, 4, 0.6), (146, 202, 6, 0.6),
                            (158, 108, 3, 0.7)])
    )


def shaker_2(foam: str, bubble: str) -> str:
    """Softer taper, taller body - more pint-like, less mixing-tin."""
    return (
        _pour("M108 66 L116 228 q1 12 12 12 h44 q11 0 12 -12 L192 66 Z")
        + _foam(66, 42, 14, [(126, 58, 12), (150, 52, 15), (174, 58, 12)], foam)
        + _bubbles(bubble, [(136, 128, 5, 0.7), (162, 170, 4, 0.6), (146, 204, 6, 0.6),
                            (156, 100, 3, 0.7)])
    )


def shaker_3(foam: str, bubble: str) -> str:
    """Very slightly concave sides - the optical curve thick glass really has."""
    return (
        _pour("M106 70 Q112 150 118 228 q1 10 11 10 h42 q10 0 11 -10 Q188 150 194 70 Z")
        + _foam(70, 44, 15, [(124, 61, 13), (150, 55, 16), (176, 61, 13)], foam)
        + _bubbles(bubble, [(138, 130, 5, 0.7), (160, 172, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 106, 3, 0.7)])
    )


# --- Nonic pint: straight sides broken by the bulge near the top. ---
def nonic_1(foam: str, bubble: str) -> str:
    """Bulge a third from the top, slim taper below."""
    return (
        _pour("M106 68 L106 100 Q99 112 108 124 L116 228 q0 10 10 10 h48 "
              "q10 0 10 -10 L192 124 Q201 112 194 100 L194 68 Z")
        + _foam(68, 44, 13, [(126, 60, 11), (150, 55, 14), (174, 60, 11)], foam)
        + _bubbles(bubble, [(130, 158, 5, 0.6), (162, 192, 4, 0.6), (144, 212, 6, 0.55)])
    )


def nonic_2(foam: str, bubble: str) -> str:
    """Ring set higher and softer; longer body below it."""
    return (
        _pour("M107 66 L107 96 Q102 106 110 118 L118 228 q1 10 11 10 h42 "
              "q10 0 11 -10 L190 118 Q198 106 193 96 L193 66 Z")
        + _foam(66, 43, 13, [(126, 58, 11), (150, 53, 14), (174, 58, 11)], foam)
        + _bubbles(bubble, [(132, 156, 5, 0.6), (162, 192, 4, 0.6), (144, 212, 6, 0.55)])
    )


def nonic_3(foam: str, bubble: str) -> str:
    """Wider, more pronounced bulge - the shape you can actually grip."""
    return (
        _pour("M108 70 L108 102 Q98 114 110 126 L116 230 q1 8 9 8 h50 "
              "q8 0 9 -8 L190 126 Q202 114 192 102 L192 70 Z")
        + _foam(70, 42, 13, [(128, 62, 11), (150, 57, 14), (172, 62, 11)], foam)
        + _bubbles(bubble, [(130, 160, 5, 0.6), (162, 194, 4, 0.6), (144, 214, 6, 0.55)])
    )


# --- Conical schooner: the Australian bell, per the reference photo. ---
def schooner_1(foam: str, bubble: str) -> str:
    """The reference bell: wide rim, curve in to a low waist, slight base flare."""
    return (
        _pour("M110 76 Q104 130 122 196 Q126 216 120 232 q3 8 11 8 h38 "
              "q8 0 11 -8 Q174 216 178 196 Q196 130 190 76 Z")
        + _foam(76, 40, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 140, 5, 0.6), (162, 180, 4, 0.6), (148, 208, 5, 0.55)])
    )


def schooner_2(foam: str, bubble: str) -> str:
    """Deeper waist and a fuller shoulder - the bell pushed further."""
    return (
        _pour("M108 74 Q98 132 120 200 Q124 218 118 232 q3 8 11 8 h42 "
              "q8 0 11 -8 Q176 218 180 200 Q202 132 192 74 Z")
        + _foam(74, 41, 13, [(128, 65, 12), (150, 60, 15), (172, 65, 12)], foam)
        + _bubbles(bubble, [(134, 142, 5, 0.6), (162, 184, 4, 0.6), (148, 210, 5, 0.55)])
    )


def schooner_3(foam: str, bubble: str) -> str:
    """The straight cone, flare moderated - what the old shape was reaching for."""
    return (
        _pour("M98 80 L124 228 q1 10 11 10 h30 q10 0 11 -10 L202 80 Z")
        + _foam(80, 52, 15, [(122, 71, 12), (150, 66, 15), (178, 71, 12)], foam)
        + _bubbles(bubble, [(136, 150, 5, 0.6), (160, 182, 4, 0.6), (150, 206, 5, 0.55)])
    )


# --- Tulip: bulbous bowl, pinched waist, flared lip, on a stem. ---
def tulip_1(foam: str, bubble: str) -> str:
    """Rounded bowl, clear waist, modest lip flare."""
    return (
        _pour("M110 86 C99 110 100 146 128 172 C136 180 138 190 138 200 "
              "L162 200 C162 190 164 180 172 172 C200 146 201 110 190 86 "
              "C166 96 134 96 110 86 Z")
        + _foam(88, 40, 12, [(130, 80, 10), (152, 76, 13), (172, 81, 9)], foam)
        + _bubbles(bubble, [(138, 132, 5, 0.6), (158, 154, 4, 0.55)])
        + _stem(200)
    )


def tulip_2(foam: str, bubble: str) -> str:
    """Fuller, rounder bowl with a tighter pinch - the Belgian shape."""
    return (
        _pour("M112 84 C96 112 100 152 130 176 C138 184 140 192 140 200 "
              "L160 200 C160 192 162 184 170 176 C200 152 204 112 188 84 "
              "C166 94 134 94 112 84 Z")
        + _foam(86, 38, 12, [(131, 78, 10), (152, 74, 13), (171, 79, 9)], foam)
        + _bubbles(bubble, [(138, 136, 5, 0.6), (159, 158, 4, 0.55)])
        + _stem(200)
    )


def tulip_3(foam: str, bubble: str) -> str:
    """Taller and narrower - reads better where cards are short and wide."""
    return (
        _pour("M116 82 C104 110 108 150 132 176 C139 183 141 192 141 200 "
              "L159 200 C159 192 161 183 168 176 C192 150 196 110 184 82 "
              "C164 92 136 92 116 82 Z")
        + _foam(84, 34, 11, [(133, 77, 9), (152, 73, 12), (169, 78, 9)], foam)
        + _bubbles(bubble, [(140, 134, 5, 0.6), (158, 156, 4, 0.55)])
        + _stem(200)
    )


# --- Teku: angular, long upper cone to a waist, small flare to the stem. ---
def teku_1(foam: str, bubble: str) -> str:
    """Long upper cone, soft waist, small flare."""
    return (
        _pour("M116 80 Q126 122 134 164 L131 202 L169 202 L166 164 Q174 122 184 80 Z")
        + _foam(80, 34, 11, [(131, 73, 9), (152, 70, 12), (171, 74, 9)], foam)
        + _bubbles(bubble, [(142, 118, 5, 0.6), (156, 152, 4, 0.55)])
        + _stem(202)
    )


def teku_2(foam: str, bubble: str) -> str:
    """Longer cone still, almost no flare - the severe version."""
    return (
        _pour("M114 78 Q126 124 136 168 L134 204 L166 204 L164 168 Q174 124 186 78 Z")
        + _foam(78, 36, 11, [(130, 71, 9), (152, 68, 12), (172, 72, 9)], foam)
        + _bubbles(bubble, [(142, 120, 5, 0.6), (157, 156, 4, 0.55)])
        + _stem(204)
    )


def teku_3(foam: str, bubble: str) -> str:
    """Dead-straight lines, no curve at all - maximum angularity."""
    return (
        _pour("M116 80 L136 166 L132 204 L168 204 L164 166 L184 80 Z")
        + _foam(80, 34, 11, [(131, 73, 9), (152, 70, 12), (171, 74, 9)], foam)
        + _bubbles(bubble, [(142, 118, 5, 0.6), (156, 154, 4, 0.55)])
        + _stem(204)
    )


CANDIDATES = {
    "default": [("1 - reference taper", shaker_1), ("2 - softer, taller", shaker_2),
                ("3 - concave sides", shaker_3)],
    "nonicpint": [("1 - bulge a third down", nonic_1), ("2 - higher, softer ring", nonic_2),
                  ("3 - wider bulge", nonic_3)],
    "schooner": [("1 - reference bell", schooner_1), ("2 - deeper waist", schooner_2),
                 ("3 - moderated cone", schooner_3)],
    "tulip": [("1 - rounded bowl", tulip_1), ("2 - fuller, tighter pinch", tulip_2),
              ("3 - tall and narrow", tulip_3)],
    "teku": [("1 - long cone, soft waist", teku_1), ("2 - severe, no flare", teku_2),
             ("3 - dead-straight lines", teku_3)],
}

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
SIZES = [(64, "64px - dense 8-up"), (120, "120px - typical card"), (230, "230px - full")]
VARIANTS = ["1", "2", "3"]


def render(glass: str, variant: str, base: str, uid: str) -> str:
    """Full SVG for one candidate, mirroring the production wrapper exactly."""
    if variant == "current":
        svg = beer_glass_svg(base, glass)
    else:
        top = _mix(base, "#ffffff", 0.30)
        bottom = _mix(base, "#000000", 0.28)
        foam = _mix(base, "#ffffff", 0.80)
        bubble = _mix(base, "#ffffff", 0.55)
        body = CANDIDATES[glass][int(variant) - 1][1](foam, bubble)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" '
            'width="300" height="300" role="img" aria-label="Beer">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{top}"/>'
            f'<stop offset="55%" stop-color="{base}"/>'
            f'<stop offset="100%" stop-color="{bottom}"/>'
            '</linearGradient></defs>' + body + '</svg>'
        )
    # Every SVG here ships a gradient with id="g". Inline in ONE document those
    # ids collide and every pour borrows the first gradient on the page - which
    # is why the stout first rendered straw. Production never hits this (each
    # glass is its own /img response), so this rename is a harness fix.
    return svg.replace('id="g"', f'id="{uid}"').replace("url(#g)", f"url(#{uid})")


def build_html() -> str:
    scenes = []
    for ck, _clabel, chex in COLOURS:
        for v in VARIANTS:
            rows = []
            for gk, glabel in GLASSES:
                cells = []
                for col in ("current", v):
                    uid = f"g-{ck}-{gk}-{col}-{v}"
                    svg = render(gk, col, chex, uid)
                    sizes = "".join(
                        f'<div class="s"><div class="box" style="width:{px}px;'
                        f'height:{px}px">{svg}</div><span>{note}</span></div>'
                        for px, note in SIZES
                    )
                    cells.append(f'<td><div class="sizes">{sizes}</div></td>')
                label = CANDIDATES[gk][int(v) - 1][0]
                rows.append(
                    f'<tr><th>{glabel}<br><span class="vlabel">{label}</span></th>'
                    f'{"".join(cells)}</tr>'
                )
            scenes.append(
                f'<section class="scene" data-colour="{ck}" data-variant="{v}">'
                f'<table><thead><tr><th>Candidate set {v}</th><th>Current (main)</th>'
                f'<th>Candidate {v}</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></section>'
            )
    bg_buttons = "".join(f'<button data-bg="{k}">{lbl}</button>' for k, lbl, _, _ in BACKGROUNDS)
    colour_buttons = "".join(f'<button data-colour="{k}">{k}</button>' for k, _, _ in COLOURS)
    bg_css = "".join(
        f'body[data-bg="{k}"]{{background:{bg};color:{fg}}}' for k, _, bg, fg in BACKGROUNDS
    )
    variants_js = "[" + ",".join(f'"{v}"' for v in VARIANTS) + "]"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>PROTOTYPE - beer glass silhouettes (issue #6)</title>
<style>
  body {{ margin:0; padding:24px 24px 120px; font:14px/1.4 system-ui,sans-serif;
         background:#131a22; color:#e8ecf2; }}
  {bg_css}
  h1 {{ font-size:16px; font-weight:600; margin:0 0 4px; }}
  p.sub {{ margin:0 0 20px; opacity:.7; max-width:78ch; }}
  .scene {{ display:none; }} .scene.on {{ display:block; }}
  table {{ border-collapse:collapse; width:100%; table-layout:fixed; }}
  th, td {{ border:1px solid rgba(128,144,160,.35); padding:10px; vertical-align:top; }}
  thead th {{ text-align:left; font-weight:600; }}
  tbody th {{ text-align:left; width:150px; font-weight:500; }}
  .vlabel {{ font-weight:400; font-size:11px; opacity:.6; }}
  .sizes {{ display:flex; gap:14px; align-items:flex-end; flex-wrap:wrap; }}
  .s {{ display:flex; flex-direction:column; align-items:center; gap:6px; }}
  .s span {{ font-size:10px; opacity:.55; }}
  .box svg {{ width:100%; height:100%; display:block; }}
  #bar {{ position:fixed; left:50%; bottom:16px; transform:translateX(-50%);
          display:flex; gap:10px; align-items:center; background:#ff3d7f; color:#fff;
          padding:10px 14px; border-radius:999px; box-shadow:0 6px 24px rgba(0,0,0,.45);
          font-weight:600; z-index:99; }}
  #bar button {{ font:inherit; font-size:12px; border:0; border-radius:999px;
                 padding:6px 10px; cursor:pointer; background:rgba(255,255,255,.22);
                 color:#fff; }}
  #bar button.on {{ background:#fff; color:#ff3d7f; }}
  #state {{ min-width:150px; text-align:center; font-size:13px; }}
  .sep {{ opacity:.55; }}
</style>
<body data-bg="dark">
<h1>PROTOTYPE - beer glass silhouettes (issue #6)</h1>
<p class="sub">The arrows flip between three candidate shapes per glass, in place, beside
what main draws today - flipping the same spot back and forth is a sharper comparison than
reading them side by side. The chips switch beer Colour and theme background. The vessel
approach was rejected in round 1 and is gone. Nothing here is production code.</p>
{"".join(scenes)}
<div id="bar">
  <button id="prev">&#8592;</button>
  <span id="state"></span>
  <button id="next">&#8594;</button>
  <span class="sep">|</span>
  {colour_buttons}
  <span class="sep">|</span>
  {bg_buttons}
</div>
<script>
  const variants = {variants_js};
  const q = new URLSearchParams(location.search);
  let vi = Math.max(0, variants.indexOf(q.get('variant')));
  let colour = q.get('colour') || 'amber';
  if (q.get('bg')) document.body.dataset.bg = q.get('bg');
  function draw() {{
    document.querySelectorAll('.scene').forEach(s => s.classList.toggle(
      'on', s.dataset.variant === variants[vi] && s.dataset.colour === colour));
    document.getElementById('state').textContent =
      'Candidate ' + variants[vi] + ' of ' + variants.length;
    document.querySelectorAll('#bar button[data-colour]').forEach(
      b => b.classList.toggle('on', b.dataset.colour === colour));
    document.querySelectorAll('#bar button[data-bg]').forEach(
      b => b.classList.toggle('on', b.dataset.bg === document.body.dataset.bg));
    history.replaceState(null, '', '?variant=' + variants[vi] + '&colour=' + colour
      + '&bg=' + document.body.dataset.bg);
  }}
  document.getElementById('prev').onclick = () => {{ vi = (vi + variants.length - 1) % variants.length; draw(); }};
  document.getElementById('next').onclick = () => {{ vi = (vi + 1) % variants.length; draw(); }};
  document.querySelectorAll('#bar button[data-colour]').forEach(b => b.onclick = () => {{
    colour = b.dataset.colour; draw();
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
