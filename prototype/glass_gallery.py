"""PROTOTYPE - THROWAWAY. Beer glass silhouette gallery for issue #6.

Question this answers: **what should the five glass silhouettes look like**, and
does drawing an actual glass vessel around the pour read better than refining
the liquid outline alone?

Run it, open the HTML it writes:

    python prototype/glass_gallery.py

Rows are glass types, columns are the three candidates side by side:

    CURRENT   - whatever `app.beer_glass` draws today (imported, not copied)
    A LIQUID  - refined liquid-only outline; no vessel, same drawing model
    B VESSEL  - translucent glass vessel (wall, rim, base ring) + inset liquid

Each cell renders at three sizes because a shape that only works at 300px is a
fail: 72px (a dense 8-up page), 140px (a typical card), 300px (full size).

The floating bar cycles the scene - Colour (pale straw / mid amber / near-black
stout / Unknown-amber fallback) and background (OLED black / default dark /
daylight light). Left and right arrow keys work too.

NOT production code: no tests, no error handling, coordinates hand-tuned by eye.
The winning path data gets rewritten properly into `app/beer_glass.py`; this
file stays on the prototype branch.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.beer_glass import (  # noqa: E402
    _GLASS_FILL,
    _GLASS_STROKE,
    _bubbles,
    _mix,
    _stem,
    beer_glass_svg,
)

# A heavier tint than the wall, for the thick base a shaker/schooner sits on.
_BASE_FILL = "rgba(214,226,240,0.30)"

LIQUID = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'


def _vessel(d: str) -> str:
    return f'<path d="{d}" fill="{_GLASS_FILL}" stroke="{_GLASS_STROKE}" stroke-width="2.5"/>'


def _base_ring(d: str) -> str:
    return f'<path d="{d}" fill="{_BASE_FILL}" stroke="{_GLASS_STROKE}" stroke-width="2"/>'


def _foam(cx: int, cy: int, rx: int, ry: int, blobs, foam: str) -> str:
    out = f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{foam}"/>'
    return out + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{foam}"/>' for x, y, r in blobs
    )


# --------------------------------------------------------------------------
# Approach A - liquid only. Same drawing model as today: the pour IS the glass.
# --------------------------------------------------------------------------
def body_a(glass: str, foam: str, bubble: str) -> str:
    if glass == "nonicpint":
        return (
            f'<path d="M106 68 L106 100 Q99 112 108 124 L116 228 q0 10 10 10 h48 '
            f'q10 0 10 -10 L192 124 Q201 112 194 100 L194 68 Z" {LIQUID}/>'
            + _foam(150, 68, 44, 13, [(126, 60, 11), (150, 55, 14), (174, 60, 11)], foam)
            + _bubbles(bubble, [(130, 160, 5, 0.6), (162, 192, 4, 0.6), (144, 212, 6, 0.55)])
        )
    if glass == "schooner":
        return (
            f'<path d="M98 80 L124 228 q1 10 11 10 h30 q10 0 11 -10 L202 80 Z" {LIQUID}/>'
            + _foam(150, 80, 52, 15, [(122, 71, 12), (150, 66, 15), (178, 71, 12)], foam)
            + _bubbles(bubble, [(136, 150, 5, 0.6), (160, 182, 4, 0.6), (150, 206, 5, 0.55)])
        )
    if glass == "tulip":
        return (
            f'<path d="M110 86 C99 110 100 146 128 172 C136 180 138 190 138 200 '
            f'L162 200 C162 190 164 180 172 172 C200 146 201 110 190 86 '
            f'C166 96 134 96 110 86 Z" {LIQUID}/>'
            + _foam(150, 88, 40, 12, [(130, 80, 10), (152, 76, 13), (172, 81, 9)], foam)
            + _bubbles(bubble, [(138, 132, 5, 0.6), (158, 154, 4, 0.55)])
            + _stem(200)
        )
    if glass == "teku":
        return (
            f'<path d="M116 80 Q126 122 134 164 L131 202 L169 202 L166 164 '
            f'Q174 122 184 80 Z" {LIQUID}/>'
            + _foam(150, 80, 34, 11, [(131, 73, 9), (152, 70, 12), (171, 74, 9)], foam)
            + _bubbles(bubble, [(142, 118, 5, 0.6), (156, 152, 4, 0.55)])
            + _stem(202)
        )
    # default: shaker pint - dead-straight sides, gentle taper, flat floor.
    return (
        f'<path d="M104 76 L113 226 q1 10 11 10 h52 q10 0 10 -10 L196 76 Z" {LIQUID}/>'
        + _foam(150, 76, 46, 15, [(124, 67, 13), (150, 61, 16), (176, 67, 13)], foam)
        + _bubbles(bubble, [(138, 140, 5, 0.7), (160, 176, 4, 0.6), (146, 204, 6, 0.6),
                            (158, 118, 3, 0.7)])
    )


# --------------------------------------------------------------------------
# Approach B - translucent vessel drawn behind an inset pour, with headspace.
# --------------------------------------------------------------------------
def body_b(glass: str, foam: str, bubble: str) -> str:
    if glass == "nonicpint":
        return (
            _vessel("M98 58 L98 92 Q92 104 100 116 L110 242 q1 6 7 6 h66 q6 0 7 -6 "
                    "L200 116 Q208 104 202 92 L202 58")
            + _base_ring("M109 228 L110 242 q1 6 7 6 h66 q6 0 7 -6 L191 228 Z")
            + f'<path d="M105 80 L105 94 Q100 104 107 114 L117 236 q1 6 7 6 h52 '
              f'q6 0 7 -6 L193 114 Q200 104 195 94 L195 80 Z" {LIQUID}/>'
            + _foam(150, 80, 44, 12, [(128, 72, 10), (150, 67, 13), (172, 72, 10)], foam)
            + _bubbles(bubble, [(128, 160, 5, 0.6), (162, 196, 4, 0.6), (144, 218, 6, 0.55)])
        )
    if glass == "schooner":
        return (
            _vessel("M92 70 L122 242 q1 8 9 8 h38 q8 0 9 -8 L208 70")
            + _base_ring("M120 230 L122 242 q1 8 9 8 h38 q8 0 9 -8 L180 230 Z")
            + f'<path d="M103 92 L128 236 q1 6 7 6 h30 q6 0 7 -6 L197 92 Z" {LIQUID}/>'
            + _foam(150, 92, 45, 13, [(124, 83, 12), (150, 78, 15), (176, 83, 12)], foam)
            + _bubbles(bubble, [(136, 158, 5, 0.6), (160, 190, 4, 0.6), (150, 214, 5, 0.55)])
        )
    if glass == "tulip":
        return (
            _vessel("M104 78 C92 108 94 148 124 176 C132 184 134 192 134 200 L166 200 "
                    "C166 192 168 184 176 176 C206 148 208 108 196 78")
            + f'<path d="M104 98 C97 122 102 152 128 176 C135 183 137 191 137 199 '
              f'L163 199 C163 191 165 183 172 176 C198 152 203 122 196 98 '
              f'C168 108 132 108 104 98 Z" {LIQUID}/>'
            + _foam(150, 100, 42, 12, [(130, 92, 10), (152, 88, 13), (172, 93, 9)], foam)
            + _bubbles(bubble, [(138, 140, 5, 0.6), (158, 160, 4, 0.55)])
            + _stem(200)
        )
    if glass == "teku":
        return (
            _vessel("M108 72 Q120 118 128 166 L124 204 L176 204 L172 166 Q180 118 192 72")
            + f'<path d="M119 92 Q129 120 134 164 L131 200 L169 200 L166 164 '
              f'Q171 120 181 92 Z" {LIQUID}/>'
            + _foam(150, 92, 31, 10, [(132, 85, 9), (152, 82, 11), (169, 86, 8)], foam)
            + _bubbles(bubble, [(142, 128, 5, 0.6), (156, 158, 4, 0.55)])
            + _stem(204)
        )
    # default: shaker pint with the thick base ring it is known for.
    return (
        _vessel("M96 62 L106 240 q1 6 7 6 h74 q6 0 7 -6 L204 62")
        + _base_ring("M105 226 L106 240 q1 6 7 6 h74 q6 0 7 -6 L195 226 Z")
        + f'<path d="M104 84 L113 232 q1 7 8 7 h58 q7 0 8 -7 L196 84 Z" {LIQUID}/>'
        + _foam(150, 84, 46, 13, [(126, 74, 11), (150, 69, 14), (174, 74, 11)], foam)
        + _bubbles(bubble, [(138, 150, 5, 0.7), (162, 186, 4, 0.6), (146, 212, 6, 0.6),
                            (158, 126, 3, 0.7)])
    )


def render(glass: str, approach: str, base: str) -> str:
    """Full SVG for one candidate, mirroring the production wrapper exactly."""
    if approach == "current":
        return beer_glass_svg(base, glass)
    top = _mix(base, "#ffffff", 0.30)
    bottom = _mix(base, "#000000", 0.28)
    foam = _mix(base, "#ffffff", 0.80)
    bubble = _mix(base, "#ffffff", 0.55)
    body = body_a(glass, foam, bubble) if approach == "a" else body_b(glass, foam, bubble)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" '
        'width="300" height="300" role="img" aria-label="Beer">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{top}"/>'
        f'<stop offset="55%" stop-color="{base}"/>'
        f'<stop offset="100%" stop-color="{bottom}"/>'
        '</linearGradient></defs>' + body + '</svg>'
    )


GLASSES = [
    ("default", "Shaker pint (default)"),
    ("nonicpint", "Nonic pint"),
    ("schooner", "Conical schooner"),
    ("tulip", "Tulip"),
    ("teku", "Teku"),
]
COLUMNS = [("current", "Current (main)"), ("a", "A - liquid only"), ("b", "B - with vessel")]
# Pale straw, mid amber, near-black stout, and the Unknown-amber fallback.
COLOURS = [
    ("straw", "Pale straw (EBC ~5)", "#f5d97a"),
    ("amber", "Mid amber (EBC ~25)", "#c3641a"),
    ("stout", "Near-black stout (EBC ~80)", "#1a0d06"),
    ("unknown", "Unknown Colour - this surface's amber fallback", "#e8a020"),
]
BACKGROUNDS = [
    ("oled", "OLED black", "#000000", "#e8ecf2"),
    ("dark", "Default dark", "#131a22", "#e8ecf2"),
    ("light", "Daylight", "#f4f6f8", "#1a2230"),
]
SIZES = [(64, "64px - dense 8-up"), (120, "120px - typical card"), (230, "230px - full")]


def build_html() -> str:
    scenes = []
    for ck, clabel, chex in COLOURS:
        rows = []
        for gk, glabel in GLASSES:
            cells = []
            for ak, _alabel in COLUMNS:
                # Every SVG on this page ships its own gradient with id="g". Inline
                # in ONE document those ids collide and every pour borrows the first
                # gradient on the page - which is why the stout first rendered straw.
                # Production never hits this (each glass is its own /img response),
                # so this rename is a harness fix, not a finding about the renderer.
                uid = f"g-{ck}-{gk}-{ak}"
                svg = render(gk, ak, chex).replace('id="g"', f'id="{uid}"')
                svg = svg.replace("url(#g)", f"url(#{uid})")
                sizes = "".join(
                    f'<div class="s"><div class="box" style="width:{px}px;height:{px}px">{svg}</div>'
                    f'<span>{note}</span></div>'
                    for px, note in SIZES
                )
                cells.append(f'<td><div class="sizes">{sizes}</div></td>')
            rows.append(f'<tr><th>{glabel}</th>{"".join(cells)}</tr>')
        head = "".join(f"<th>{lbl}</th>" for _, lbl in COLUMNS)
        scenes.append(
            f'<section class="scene" data-colour="{ck}">'
            f'<table><thead><tr><th>{clabel}</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>'
        )
    bg_buttons = "".join(
        f'<button data-bg="{k}">{lbl}</button>' for k, lbl, _, _ in BACKGROUNDS
    )
    bg_css = "".join(
        f'body[data-bg="{k}"]{{background:{bg};color:{fg}}}' for k, _, bg, fg in BACKGROUNDS
    )
    colour_keys = ",".join(f'"{k}"' for k, _, _ in COLOURS)
    colour_labels = ",".join(f'{k}:"{lbl}"' for k, lbl, _ in COLOURS)
    return f"""<!doctype html>
<meta charset="utf-8">
<title>PROTOTYPE - beer glass silhouettes (issue #6)</title>
<style>
  body {{ margin:0; padding:24px 24px 110px; font:14px/1.4 system-ui,sans-serif;
         background:#131a22; color:#e8ecf2; }}
  {bg_css}
  h1 {{ font-size:16px; font-weight:600; margin:0 0 4px; }}
  p.sub {{ margin:0 0 20px; opacity:.7; max-width:70ch; }}
  .scene {{ display:none; }} .scene.on {{ display:block; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ border:1px solid rgba(128,144,160,.35); padding:10px; vertical-align:top; }}
  thead th {{ text-align:left; font-weight:600; }}
  tbody th {{ text-align:left; width:110px; font-weight:500; }}
  table {{ table-layout:fixed; }}
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
  #state {{ min-width:300px; text-align:center; font-size:13px; }}
</style>
<body data-bg="dark">
<h1>PROTOTYPE - beer glass silhouettes (issue #6)</h1>
<p class="sub">Rows are glass types, columns are candidates. Left/right arrows (or the
buttons) cycle the beer Colour; the background buttons switch theme. Nothing here is
production code - the winner gets rewritten into <code>app/beer_glass.py</code>.</p>
{"".join(scenes)}
<div id="bar">
  <button id="prev">&#8592;</button>
  <span id="state"></span>
  <button id="next">&#8594;</button>
  <span style="opacity:.6">|</span>
  {bg_buttons}
</div>
<script>
  const colours = [{colour_keys}];
  const labels = {{{colour_labels}}};
  const q = new URLSearchParams(location.search);
  let i = Math.max(0, colours.indexOf(q.get('colour')));
  if (q.get('bg')) document.body.dataset.bg = q.get('bg');
  function draw() {{
    history.replaceState(null, '', '?colour=' + colours[i] + '&bg=' + document.body.dataset.bg);
    document.querySelectorAll('.scene').forEach(s =>
      s.classList.toggle('on', s.dataset.colour === colours[i]));
    document.getElementById('state').textContent =
      (i + 1) + '/' + colours.length + '  -  ' + labels[colours[i]]
      + '  -  ' + document.body.dataset.bg;
  }}
  document.getElementById('prev').onclick = () => {{ i = (i + colours.length - 1) % colours.length; draw(); }};
  document.getElementById('next').onclick = () => {{ i = (i + 1) % colours.length; draw(); }};
  document.querySelectorAll('#bar button[data-bg]').forEach(b => b.onclick = () => {{
    document.body.dataset.bg = b.dataset.bg;
    document.querySelectorAll('#bar button[data-bg]').forEach(x => x.classList.toggle('on', x === b));
    draw();
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
