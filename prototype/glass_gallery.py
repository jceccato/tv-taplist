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


# --- Conical schooner: vertical rim, taper, then a vertical foot with square
# --- corners meeting the table - the rim's geometry mirrored at the bottom.
def schooner_1(foam: str, bubble: str) -> str:
    """Taper resolves into a short vertical base, square-cornered on the table."""
    return (
        _pour("M108 76 L108 112 C108 150 120 190 126 212 L126 240 L174 240 "
              "L174 212 C180 190 192 150 192 112 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 146, 5, 0.6), (162, 184, 4, 0.6), (148, 216, 5, 0.55)])
    )


def schooner_2(foam: str, bubble: str) -> str:
    """Longer vertical base - more of a plinth under the taper."""
    return (
        _pour("M108 76 L108 112 C108 148 118 184 124 202 L124 240 L176 240 "
              "L176 202 C182 184 192 148 192 112 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 146, 5, 0.6), (162, 182, 4, 0.6), (148, 218, 5, 0.55)])
    )


def schooner_3(foam: str, bubble: str) -> str:
    """Shortest base, narrowest foot - taper carries almost to the table."""
    return (
        _pour("M108 76 L108 112 C108 152 122 196 130 222 L130 240 L170 240 "
              "L170 222 C178 196 192 152 192 112 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 148, 5, 0.6), (162, 186, 4, 0.6), (150, 216, 5, 0.55)])
    )


# --- Tulip: gradual collar-to-bowl transition, fatter bowl, stubby stem.
# --- Widest point sits HIGH and the bowl pinches in above the stem, which is
# --- what keeps a tulip reading as a tulip beside the teku (whose bowl is
# --- widest low down). Without that the two silhouettes converge.
def tulip_1(foam: str, bubble: str) -> str:
    """Collar eases into a fat, high-shouldered bowl; short thick stem."""
    return (
        _pour("M112 78 L112 92 C101 102 97 118 99 138 C101 164 118 186 138 204 "
              "L162 204 C182 186 199 164 201 138 C203 118 199 102 188 92 "
              "L188 78 Z")
        + _foam(78, 38, 12, [(130, 70, 10), (152, 66, 13), (172, 71, 9)], foam)
        + _bubbles(bubble, [(132, 132, 5, 0.6), (166, 160, 4, 0.55)])
        + _stem(204, width=18, foot_y=254, foot_half=36)
    )


def tulip_2(foam: str, bubble: str) -> str:
    """Fatter again, transition longer still - almost no corner at the collar."""
    return (
        _pour("M112 78 L112 90 C99 100 93 118 95 140 C97 168 116 190 138 206 "
              "L162 206 C184 190 203 168 205 140 C207 118 201 100 188 90 "
              "L188 78 Z")
        + _foam(78, 38, 12, [(130, 70, 10), (152, 66, 13), (172, 71, 9)], foam)
        + _bubbles(bubble, [(130, 134, 5, 0.6), (168, 162, 4, 0.55)])
        + _stem(206, width=20, foot_y=254, foot_half=38)
    )


def tulip_3(foam: str, bubble: str) -> str:
    """Fat bowl, but a taller collar showing above it."""
    return (
        _pour("M112 70 L112 92 C101 102 97 118 99 138 C101 164 118 186 138 204 "
              "L162 204 C182 186 199 164 201 138 C203 118 199 102 188 92 "
              "L188 70 Z")
        + _foam(70, 38, 12, [(130, 62, 10), (152, 58, 13), (172, 63, 9)], foam)
        + _bubbles(bubble, [(132, 132, 5, 0.6), (166, 160, 4, 0.55)])
        + _stem(204, width=18, foot_y=254, foot_half=36)
    )


# --- Teku: reworked again from the second reference photo. That glass is NOT a
# --- funnel on an egg: it is a small lipped rim, then walls that widen steadily
# --- all the way DOWN to the widest point low in the bowl, then turn under
# --- sharply into a long slender stem.
def teku_1(foam: str, bubble: str) -> str:
    """Lipped rim, walls widening downward, widest low, long stem."""
    return (
        _pour("M106 62 C107 68 109 72 108 80 C100 106 90 132 90 158 "
              "C90 182 112 204 137 212 L163 212 C188 204 210 182 210 158 "
              "C210 132 200 106 192 80 C191 72 193 68 194 62 Z")
        + _foam(62, 44, 12, [(126, 53, 11), (150, 48, 14), (174, 53, 11)], foam)
        + _bubbles(bubble, [(128, 130, 5, 0.6), (166, 168, 4, 0.55), (146, 188, 5, 0.5)])
        + _stem(212)
    )


def teku_2(foam: str, bubble: str) -> str:
    """Wider bowl, widest point lower - the fuller version of the same glass."""
    return (
        _pour("M108 62 C109 68 111 72 110 82 C100 110 88 138 88 166 "
              "C88 188 110 208 138 216 L162 216 C190 208 212 188 212 166 "
              "C212 138 200 110 190 82 C189 72 191 68 192 62 Z")
        + _foam(62, 42, 12, [(127, 53, 11), (150, 48, 14), (173, 53, 11)], foam)
        + _bubbles(bubble, [(126, 134, 5, 0.6), (168, 174, 4, 0.55), (146, 194, 5, 0.5)])
        + _stem(216)
    )


def teku_3(foam: str, bubble: str) -> str:
    """Straighter walls - nearer a cone, with only a small turn under."""
    return (
        _pour("M108 62 C109 68 111 72 110 80 C104 108 96 136 96 160 "
              "C96 184 116 202 138 210 L162 210 C184 202 204 184 204 160 "
              "C204 136 196 108 190 80 C189 72 191 68 192 62 Z")
        + _foam(62, 42, 12, [(127, 53, 11), (150, 48, 14), (173, 53, 11)], foam)
        + _bubbles(bubble, [(130, 132, 5, 0.6), (164, 170, 4, 0.55), (146, 190, 5, 0.5)])
        + _stem(210)
    )


CANDIDATES = {
    "default": [("1 - straight sides", shaker_1), ("2 - straight, eased taper", shaker_2),
                ("3 - the last trace of curve", shaker_3)],
    "nonicpint": [("1 - wide mouth, spread bump", nonic_1), ("2 - gentler bump", nonic_2),
                  ("3 - widest mouth, bump low", nonic_3)],
    "schooner": [("1 - short vertical base", schooner_1), ("2 - longer plinth", schooner_2),
                 ("3 - shortest base, narrow foot", schooner_3)],
    "tulip": [("1 - fat bowl, stubby stem", tulip_1), ("2 - fattest, longest transition", tulip_2),
              ("3 - fat bowl, taller collar", tulip_3)],
    "teku": [("1 - lipped rim, widest low", teku_1), ("2 - wider bowl, widest lower", teku_2),
             ("3 - straighter walls", teku_3)],
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
