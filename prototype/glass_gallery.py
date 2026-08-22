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

# --- Shaker pint: concave sides (round 2 pick), mouth widened. ---
def shaker_1(foam: str, bubble: str) -> str:
    """Wide mouth over the concave taper."""
    return (
        _pour("M100 70 Q112 150 118 228 q1 10 11 10 h42 q10 0 11 -10 Q188 150 200 70 Z")
        + _foam(70, 50, 16, [(122, 60, 14), (150, 54, 17), (178, 60, 14)], foam)
        + _bubbles(bubble, [(138, 130, 5, 0.7), (162, 172, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 106, 3, 0.7)])
    )


def shaker_2(foam: str, bubble: str) -> str:
    """Wider still - the mouth pushed as far as the shape takes."""
    return (
        _pour("M96 70 Q110 150 118 228 q1 10 11 10 h42 q10 0 11 -10 Q190 150 204 70 Z")
        + _foam(70, 54, 17, [(120, 60, 15), (150, 53, 18), (180, 60, 15)], foam)
        + _bubbles(bubble, [(136, 130, 5, 0.7), (162, 172, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 104, 3, 0.7)])
    )


def shaker_3(foam: str, bubble: str) -> str:
    """Wide mouth, but the sides nearly straight - less optical curve."""
    return (
        _pour("M100 70 Q116 148 118 228 q1 10 11 10 h42 q10 0 11 -10 Q184 148 200 70 Z")
        + _foam(70, 50, 16, [(122, 60, 14), (150, 54, 17), (178, 60, 14)], foam)
        + _bubbles(bubble, [(138, 132, 5, 0.7), (160, 174, 4, 0.6), (147, 204, 6, 0.6),
                            (158, 108, 3, 0.7)])
    )


# --- Nonic pint: round 2 pick, stubbier (height pulled back toward width). ---
def nonic_1(foam: str, bubble: str) -> str:
    """Stubby: shorter body, wider barrel, bulge a third down."""
    return (
        _pour("M102 84 L102 112 Q95 124 104 136 L112 228 q0 10 10 10 h56 "
              "q10 0 10 -10 L196 136 Q205 124 198 112 L198 84 Z")
        + _foam(84, 48, 14, [(124, 75, 12), (150, 70, 15), (176, 75, 12)], foam)
        + _bubbles(bubble, [(130, 166, 5, 0.6), (164, 196, 4, 0.6), (144, 214, 6, 0.55)])
    )


def nonic_2(foam: str, bubble: str) -> str:
    """Halfway back to the taller original."""
    return (
        _pour("M104 78 L104 108 Q97 120 106 132 L114 228 q0 10 10 10 h52 "
              "q10 0 10 -10 L194 132 Q203 120 196 108 L196 78 Z")
        + _foam(78, 46, 14, [(126, 69, 12), (150, 64, 15), (174, 69, 12)], foam)
        + _bubbles(bubble, [(130, 162, 5, 0.6), (163, 194, 4, 0.6), (144, 213, 6, 0.55)])
    )


def nonic_3(foam: str, bubble: str) -> str:
    """Stubbiest - nearly as wide as it is tall."""
    return (
        _pour("M100 90 L100 116 Q93 128 102 140 L110 228 q0 10 10 10 h60 "
              "q10 0 10 -10 L198 140 Q207 128 200 116 L200 90 Z")
        + _foam(90, 50, 15, [(122, 81, 13), (150, 75, 16), (178, 81, 13)], foam)
        + _bubbles(bubble, [(130, 170, 5, 0.6), (165, 198, 4, 0.6), (144, 215, 6, 0.55)])
    )


# --- Conical schooner: vertical rim, one smooth taper, NO flare at the base. ---
def schooner_1(foam: str, bubble: str) -> str:
    """Rim dead vertical, then one continuous narrowing - waist mid-height."""
    return (
        _pour("M108 76 L108 112 C108 150 120 190 126 232 q2 8 10 8 h28 "
              "q8 0 10 -8 C180 190 192 150 192 112 L192 76 Z")
        + _foam(76, 42, 13, [(128, 67, 12), (150, 62, 15), (172, 67, 12)], foam)
        + _bubbles(bubble, [(136, 146, 5, 0.6), (162, 184, 4, 0.6), (148, 212, 5, 0.55)])
    )


def schooner_2(foam: str, bubble: str) -> str:
    """Longer vertical rim, narrower foot - the taper carries lower."""
    return (
        _pour("M108 74 L108 124 C108 158 122 196 130 232 q2 8 10 8 h20 "
              "q8 0 10 -8 C178 196 192 158 192 124 L192 74 Z")
        + _foam(74, 42, 13, [(128, 65, 12), (150, 60, 15), (172, 65, 12)], foam)
        + _bubbles(bubble, [(138, 150, 5, 0.6), (162, 188, 4, 0.6), (150, 214, 5, 0.55)])
    )


def schooner_3(foam: str, bubble: str) -> str:
    """Waist set higher, gentler curve - closer to a soft cone."""
    return (
        _pour("M108 78 L108 104 C108 148 118 192 124 232 q2 8 10 8 h32 "
              "q8 0 10 -8 C182 192 192 148 192 104 L192 78 Z")
        + _foam(78, 42, 13, [(128, 69, 12), (150, 64, 15), (172, 69, 12)], foam)
        + _bubbles(bubble, [(136, 144, 5, 0.6), (162, 182, 4, 0.6), (148, 210, 5, 0.55)])
    )


# --- Tulip: round 2 pick, plus the straight rim collar from the reference. ---
def tulip_1(foam: str, bubble: str) -> str:
    """Fuller bowl under a short, straight rim collar."""
    return (
        _pour("M112 78 L112 96 C100 120 102 154 130 178 C138 186 140 194 140 202 "
              "L160 202 C160 194 162 186 170 178 C198 154 200 120 188 96 "
              "L188 78 Z")
        + _foam(78, 38, 12, [(130, 70, 10), (152, 66, 13), (172, 71, 9)], foam)
        + _bubbles(bubble, [(138, 140, 5, 0.6), (158, 162, 4, 0.55)])
        + _stem(202)
    )


def tulip_2(foam: str, bubble: str) -> str:
    """Taller collar - more of the straight rim showing above the bowl."""
    return (
        _pour("M112 72 L112 98 C99 122 101 156 130 180 C138 188 140 195 140 202 "
              "L160 202 C160 195 162 188 170 180 C199 156 201 122 188 98 "
              "L188 72 Z")
        + _foam(72, 38, 12, [(130, 64, 10), (152, 60, 13), (172, 65, 9)], foam)
        + _bubbles(bubble, [(138, 142, 5, 0.6), (158, 164, 4, 0.55)])
        + _stem(202)
    )


def tulip_3(foam: str, bubble: str) -> str:
    """Collar with a slight outward lean, as most tulips actually have."""
    return (
        _pour("M108 76 L113 98 C100 122 102 156 130 180 C138 188 140 195 140 202 "
              "L160 202 C160 195 162 188 170 180 C198 156 200 122 187 98 "
              "L192 76 Z")
        + _foam(76, 42, 12, [(129, 68, 10), (152, 64, 13), (173, 69, 9)], foam)
        + _bubbles(bubble, [(138, 142, 5, 0.6), (158, 164, 4, 0.55)])
        + _stem(202)
    )


# --- Teku: reworked as a wine bowl - flared aroma rim, waist, bowl, hip, stem.
def teku_1(foam: str, bubble: str) -> str:
    """Wine bowl: rim flares out for aroma, tight waist, bowl, hip, long stem."""
    return (
        _pour("M106 58 C112 74 120 84 126 92 C112 108 108 126 110 140 "
              "C112 158 114 166 117 172 C122 182 130 188 138 192 L162 192 "
              "C170 188 178 182 183 172 C186 166 188 158 190 140 "
              "C192 126 188 108 174 92 C180 84 188 74 194 58 Z")
        + _foam(58, 44, 13, [(126, 49, 11), (150, 44, 14), (174, 49, 11)], foam)
        + _bubbles(bubble, [(136, 132, 5, 0.6), (160, 162, 4, 0.55)])
        + _stem(192)
    )


def teku_2(foam: str, bubble: str) -> str:
    """Bigger flare, tighter waist, fuller bowl - the aroma rim exaggerated."""
    return (
        _pour("M100 56 C108 74 122 86 130 94 C112 112 104 128 106 142 "
              "C108 162 112 170 116 176 C122 186 130 190 138 194 L162 194 "
              "C170 190 178 186 184 176 C188 170 192 162 194 142 "
              "C196 128 188 112 170 94 C178 86 192 74 200 56 Z")
        + _foam(56, 48, 14, [(124, 47, 12), (150, 42, 15), (176, 47, 12)], foam)
        + _bubbles(bubble, [(134, 134, 5, 0.6), (162, 166, 4, 0.55)])
        + _stem(194)
    )


def teku_3(foam: str, bubble: str) -> str:
    """Rounder bowl, softer waist, subtler hip - nearest a plain wine glass."""
    return (
        _pour("M110 60 C116 76 122 86 128 94 C116 110 112 128 114 142 "
              "C116 160 118 168 122 174 C126 184 132 189 140 192 L160 192 "
              "C168 189 174 184 178 174 C182 168 184 160 186 142 "
              "C188 128 184 110 172 94 C178 86 184 76 190 60 Z")
        + _foam(60, 40, 12, [(128, 51, 10), (150, 46, 13), (172, 51, 10)], foam)
        + _bubbles(bubble, [(138, 134, 5, 0.6), (160, 164, 4, 0.55)])
        + _stem(192)
    )


CANDIDATES = {
    "default": [("1 - wide mouth", shaker_1), ("2 - widest mouth", shaker_2),
                ("3 - wide mouth, straighter sides", shaker_3)],
    "nonicpint": [("1 - stubby", nonic_1), ("2 - halfway back", nonic_2),
                  ("3 - stubbiest", nonic_3)],
    "schooner": [("1 - vertical rim, waist mid-height", schooner_1),
                 ("2 - longer rim, narrower foot", schooner_2),
                 ("3 - higher waist, gentler", schooner_3)],
    "tulip": [("1 - short straight collar", tulip_1), ("2 - taller collar", tulip_2),
              ("3 - collar leaning out", tulip_3)],
    "teku": [("1 - wine bowl, flared rim", teku_1), ("2 - bigger flare, tighter waist", teku_2),
             ("3 - rounder bowl, subtle hip", teku_3)],
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
