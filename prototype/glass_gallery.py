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

from app.beer_glass import (  # noqa: E402
    _GLASS_FILL,
    _GLASS_STROKE,
    _bubbles,
    _mix,
    beer_glass_svg,
)

LIQUID = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'


def _foam(cy: int, rx: int, ry: int, blobs, foam: str) -> str:
    """The head: a surface ellipse plus three blobs mounding over the rim."""
    out = f'<ellipse cx="150" cy="{cy}" rx="{rx}" ry="{ry}" fill="{foam}"/>'
    return out + "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{foam}"/>' for x, y, r in blobs
    )


def _stem(top_y: int, width: int = 12, foot_y: int = 254, foot_half: int = 34) -> str:
    """Stem + foot, drawn JOINED.

    Production's version ends the stem rect at y=238 while the foot crescent
    peaks around y=243, so the foot floats a few pixels clear of the stem - the
    "disconnected glass on the table" the maintainer spotted. Here the rect runs
    past the crescent's apex so the two overlap and read as one piece of glass.
    Whichever bowl wins, this fix has to travel back into `app/beer_glass.py`.
    """
    x = 150 - width // 2
    apex = foot_y - 16
    return (
        f'<rect x="{x}" y="{top_y}" width="{width}" height="{foot_y - 4 - top_y}" '
        f'rx="3" fill="{_GLASS_FILL}" stroke="{_GLASS_STROKE}" stroke-width="2"/>'
        f'<path d="M{150 - foot_half} {foot_y} Q150 {apex} {150 + foot_half} {foot_y} z" '
        f'fill="{_GLASS_FILL}" stroke="{_GLASS_STROKE}" stroke-width="2"/>'
    )


def _pour(d: str) -> str:
    return f'<path d="{d}" {LIQUID}/>'


# ---------------------------------------------------------------------------
# Candidates. Three per glass; each is (label, builder). Builders take the
# already-tinted foam and bubble colours, exactly like the production module.
# ---------------------------------------------------------------------------

# --- Shaker pint: widest mouth (round 3 pick), sides straightened. ---
def shaker_1(foam: str, bubble: str) -> str:
    """Dead-straight sides under the wide mouth."""
    return (
        _pour("M96 70 L118 228 q1 10 11 10 h42 q10 0 11 -10 L204 70 Z")
        + _foam(70, 54, 17, [(120, 60, 15), (150, 53, 18), (180, 60, 15)], foam)
        + _bubbles(bubble, [(136, 130, 5, 0.7), (162, 172, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 104, 3, 0.7)])
    )


def shaker_2(foam: str, bubble: str) -> str:
    """Straight, but the taper eased - a wider foot under the same mouth."""
    return (
        _pour("M96 70 L124 228 q1 10 11 10 h30 q10 0 11 -10 L204 70 Z")
        + _foam(70, 54, 17, [(120, 60, 15), (150, 53, 18), (180, 60, 15)], foam)
        + _bubbles(bubble, [(136, 130, 5, 0.7), (162, 172, 4, 0.6), (149, 204, 6, 0.6),
                            (158, 104, 3, 0.7)])
    )


def shaker_3(foam: str, bubble: str) -> str:
    """The last trace of curve - straighter than round 3's set, not quite flat."""
    return (
        _pour("M96 70 Q104 150 118 228 q1 10 11 10 h42 q10 0 11 -10 Q196 150 204 70 Z")
        + _foam(70, 54, 17, [(120, 60, 15), (150, 53, 18), (180, 60, 15)], foam)
        + _bubbles(bubble, [(136, 130, 5, 0.7), (162, 172, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 104, 3, 0.7)])
    )


# --- Nonic pint: stubby (round 3 pick), wider gentler bump, wider mouth. ---
def nonic_1(foam: str, bubble: str) -> str:
    """Wide mouth, and the bump spread over a much longer run."""
    return (
        _pour("M98 84 L98 106 Q92 126 104 146 L112 228 q0 10 10 10 h56 "
              "q10 0 10 -10 L196 146 Q208 126 202 106 L202 84 Z")
        + _foam(84, 50, 15, [(122, 74, 13), (150, 68, 16), (178, 74, 13)], foam)
        + _bubbles(bubble, [(130, 172, 5, 0.6), (164, 198, 4, 0.6), (144, 215, 6, 0.55)])
    )


def nonic_2(foam: str, bubble: str) -> str:
    """Gentler still - the bump barely more than a swell."""
    return (
        _pour("M98 84 L98 110 Q94 128 104 148 L112 228 q0 10 10 10 h56 "
              "q10 0 10 -10 L196 148 Q206 128 202 110 L202 84 Z")
        + _foam(84, 50, 15, [(122, 74, 13), (150, 68, 16), (178, 74, 13)], foam)
        + _bubbles(bubble, [(130, 172, 5, 0.6), (164, 198, 4, 0.6), (144, 215, 6, 0.55)])
    )


def nonic_3(foam: str, bubble: str) -> str:
    """Widest mouth of the three, bump wide and low."""
    return (
        _pour("M94 84 L94 108 Q90 130 102 150 L110 228 q0 10 10 10 h60 "
              "q10 0 10 -10 L198 150 Q210 130 206 108 L206 84 Z")
        + _foam(84, 54, 16, [(120, 73, 14), (150, 67, 17), (180, 73, 14)], foam)
        + _bubbles(bubble, [(128, 174, 5, 0.6), (166, 200, 4, 0.6), (144, 216, 6, 0.55)])
    )


# --- Conical schooner: LOCKED shape family. The rim's straight run now flows
# --- into the taper with a vertical tangent (no corner), and the taper resolves
# --- into the vertical base about a third of the way up the glass.
def schooner_1(foam: str, bubble: str) -> str:
    """Base starts a third up; smooth tangents at both ends of the curve."""
    return (
        _pour("M108 76 L108 100 C108 130 126 150 126 185 L126 240 L174 240 "
              "L174 185 C174 150 192 130 192 100 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 140, 5, 0.6), (162, 172, 4, 0.6), (148, 214, 5, 0.55)])
    )


def schooner_2(foam: str, bubble: str) -> str:
    """Same flow, narrower foot - the taper bites a little deeper."""
    return (
        _pour("M108 76 L108 100 C108 132 128 152 128 182 L128 240 L172 240 "
              "L172 182 C172 152 192 132 192 100 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 140, 5, 0.6), (162, 172, 4, 0.6), (148, 212, 5, 0.55)])
    )


def schooner_3(foam: str, bubble: str) -> str:
    """Curve starts higher and eases longer - the gentlest of the three."""
    return (
        _pour("M108 76 L108 94 C108 128 125 152 125 188 L125 240 L175 240 "
              "L175 188 C175 152 192 128 192 94 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 138, 5, 0.6), (162, 170, 4, 0.6), (148, 216, 5, 0.55)])
    )


# --- Tulip: bowl close to INVERTED - widest just under the collar, tapering
# --- down to the stem. The collar meets the bowl on a vertical tangent so
# --- there is no corner between them.
def tulip_1(foam: str, bubble: str) -> str:
    """Inverted bowl: mass at the top, tapering to the stem. Stubby stem."""
    return (
        _pour("M112 72 L112 92 C112 104 96 108 95 124 C94 150 116 182 140 206 "
              "L160 206 C184 182 206 150 205 124 C204 108 188 104 188 92 "
              "L188 72 Z")
        + _foam(72, 38, 12, [(130, 64, 10), (152, 60, 13), (172, 65, 9)], foam)
        + _bubbles(bubble, [(126, 130, 5, 0.6), (168, 158, 4, 0.55)])
        + _stem(206, width=18, foot_y=254, foot_half=36)
    )


def tulip_2(foam: str, bubble: str) -> str:
    """More inverted still - widest higher and wider, a sharper run to the stem."""
    return (
        _pour("M112 72 L112 90 C112 100 94 104 92 118 C90 146 114 182 140 208 "
              "L160 208 C186 182 210 146 208 118 C206 104 188 100 188 90 "
              "L188 72 Z")
        + _foam(72, 38, 12, [(130, 64, 10), (152, 60, 13), (172, 65, 9)], foam)
        + _bubbles(bubble, [(124, 128, 5, 0.6), (170, 158, 4, 0.55)])
        + _stem(208, width=20, foot_y=254, foot_half=38)
    )


def tulip_3(foam: str, bubble: str) -> str:
    """Inverted, but the shoulder rounder - less of a hard flare off the collar."""
    return (
        _pour("M112 72 L112 94 C112 108 99 114 98 132 C97 158 118 184 140 206 "
              "L160 206 C182 184 203 158 202 132 C201 114 188 108 188 94 "
              "L188 72 Z")
        + _foam(72, 38, 12, [(130, 64, 10), (152, 60, 13), (172, 65, 9)], foam)
        + _bubbles(bubble, [(128, 136, 5, 0.6), (166, 162, 4, 0.55)])
        + _stem(206, width=18, foot_y=254, foot_half=36)
    )


# --- Teku: round 4's shape 1, REPROPORTIONED - the stem is now about as tall
# --- as the bowl, as in the reference photo. That meant shrinking the bowl
# --- rather than lengthening the stem: 300 units of height is the whole budget,
# --- so an equal split is roughly 105 each once the foot is allowed for.
def teku_1(foam: str, bubble: str) -> str:
    """Stem height ~= bowl height. Bowl 52-167, stem 167-258."""
    return (
        _pour("M118 52 C119 56 121 58 120 63 C112 85 102 110 102 133 "
              "C102 150 118 162 140 167 L160 167 C182 162 198 150 198 133 "
              "C198 110 188 85 180 63 C179 58 181 56 182 52 Z")
        + _foam(52, 32, 9, [(132, 45, 8), (150, 41, 11), (168, 45, 8)], foam)
        + _bubbles(bubble, [(126, 108, 4, 0.6), (172, 138, 4, 0.55), (148, 152, 4, 0.5)])
        + _stem(167, width=11, foot_y=258, foot_half=32)
    )


def teku_2(foam: str, bubble: str) -> str:
    """Bowl a touch bigger, stem a touch shorter - stem ~0.85x the bowl."""
    return (
        _pour("M116 50 C117 54 119 56 118 62 C109 86 98 112 98 137 "
              "C98 155 116 168 139 174 L161 174 C184 168 202 155 202 137 "
              "C202 112 191 86 182 62 C181 56 183 54 184 50 Z")
        + _foam(50, 34, 10, [(131, 43, 8), (150, 39, 11), (169, 43, 8)], foam)
        + _bubbles(bubble, [(124, 110, 4, 0.6), (174, 142, 4, 0.55), (148, 158, 4, 0.5)])
        + _stem(174, width=11, foot_y=258, foot_half=32)
    )


def teku_3(foam: str, bubble: str) -> str:
    """Smaller bowl, longer and more slender stem - stem ~1.1x the bowl."""
    return (
        _pour("M120 54 C121 58 123 60 122 65 C115 85 106 108 106 128 "
              "C106 144 120 155 140 160 L160 160 C180 155 194 144 194 128 "
              "C194 108 185 85 178 65 C177 60 179 58 180 54 Z")
        + _foam(54, 30, 9, [(133, 47, 8), (150, 43, 10), (167, 47, 8)], foam)
        + _bubbles(bubble, [(128, 104, 4, 0.6), (170, 132, 4, 0.55), (148, 146, 4, 0.5)])
        + _stem(160, width=10, foot_y=258, foot_half=32)
    )


CANDIDATES = {
    # Shaker and nonic are SETTLED (round 4, shape 1). Both entries point at the
    # chosen builder so flipping the arrows cannot un-decide them by accident.
    "default": [("LOCKED - straight sides", shaker_1), ("LOCKED - straight sides", shaker_1),
                ("LOCKED - straight sides", shaker_1)],
    "nonicpint": [("LOCKED - wide mouth, spread bump", nonic_1),
                  ("LOCKED - wide mouth, spread bump", nonic_1),
                  ("LOCKED - wide mouth, spread bump", nonic_1)],
    "schooner": [("1 - base a third up", schooner_1), ("2 - narrower foot", schooner_2),
                 ("3 - gentlest easing", schooner_3)],
    "tulip": [("1 - inverted bowl", tulip_1), ("2 - most inverted", tulip_2),
              ("3 - inverted, rounder shoulder", tulip_3)],
    "teku": [("1 - stem = bowl", teku_1), ("2 - stem 0.85x bowl", teku_2),
             ("3 - stem 1.1x bowl", teku_3)],
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
