"""PROTOTYPE - THROWAWAY. Tune the head on every shipped glass (issue #6).

    python prototype/foam_lab.py   ->  prototype/foam_lab.html

Two questions this exists to answer:

1. **The head sits short of the lip.** Production sizes the foam ellipse from a
   hand-entered `rx` per silhouette, and on most glasses that number is a few
   units narrower than the mouth actually is - so the beer stops before the
   glass does. The mug is the exception only because its `rx` happened to be
   measured off its own rim. Here the rim is MEASURED from the pour and the
   width is a multiplier on it, so 1.0 means "exactly the mouth" and anything
   above overhangs.

2. **A head has depth.** Foam is not a lid: it is the top inch of what is in
   the glass. A band of foam is drawn below the surface and clipped to the
   pour, with a curved underside where it meets the beer.

Everything is derived from the shipped `_SILHOUETTES` paths, so what is tuned
here can be read straight back into them.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.beer_glass import (  # noqa: E402
    _GLASS_FILL, _GLASS_STROKE, _SILHOUETTES, DEFAULT_GLASS, GLASS_TYPES,
)
from mug_lab import flatten  # noqa: E402


def build() -> str:
    glasses = {}
    for key, label in GLASS_TYPES:
        s = _SILHOUETTES[key]
        glasses[key] = {
            "label": label,
            "pour": s.pour,
            "outline": flatten(s.pour),
            "stem": s.stem or "",
            "etch": s.etch or "",
            "sheen": s.sheen or "",
            # What production draws today, so the lab can show the shortfall
            # rather than assert it.
            "shipped": list(s.head),
        }
    data = {"glasses": glasses, "default": DEFAULT_GLASS,
            "glassFill": _GLASS_FILL, "glassStroke": _GLASS_STROKE}
    return _TEMPLATE.replace("__DATA__", json.dumps(data))


_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8"><title>Foam - head shape lab</title>
<style>
  :root { --bg:#131a22; --fg:#c9d3de; --panel:#1b242e; --line:#2c3846; }
  body.light { --bg:#f4f1ec; --fg:#333; --panel:#e7e2da; --line:#cfc8bd; }
  body.oled  { --bg:#000;    --fg:#c9d3de; --panel:#0e0e0e; --line:#242424; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 system-ui,sans-serif; display:flex; min-height:100vh; }
  #panel { width:300px; flex:none; padding:16px; background:var(--panel);
           border-right:1px solid var(--line); overflow:auto; max-height:100vh; }
  #stage { flex:1; padding:20px; overflow:auto; }
  h1 { font-size:15px; margin:0 0 4px; }
  p.note { opacity:.65; margin:0 0 14px; }
  label { display:block; margin:10px 0 2px; font-size:12px; }
  label b { float:right; font-weight:600; opacity:.8; }
  input[type=range] { width:100%; }
  .seg { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; }
  .seg button { flex:1 1 auto; padding:5px 8px; font:12px system-ui,sans-serif;
                background:transparent; color:var(--fg); border:1px solid var(--line);
                border-radius:5px; cursor:pointer; }
  .seg button[aria-pressed=true] { background:var(--fg); color:var(--panel); }
  .row { display:flex; gap:24px; align-items:flex-end; flex-wrap:wrap; margin-bottom:16px; }
  .row figure { margin:0; text-align:center; }
  .row figcaption { opacity:.6; font-size:11px; }
  textarea { width:100%; height:120px; margin-top:8px; font:11px/1.4 ui-monospace,monospace;
             background:var(--bg); color:var(--fg); border:1px solid var(--line);
             border-radius:5px; padding:6px; }
  hr { border:0; border-top:1px solid var(--line); margin:16px 0; }
</style>
<body>
<div id="panel">
  <h1>Head shape</h1>
  <p class="note">The rim is measured from each pour, not typed in. Width 1.00
  means the foam is exactly as wide as the mouth.</p>

  <label>Glass</label>
  <div class="seg" id="glass"></div>
  <label>Background</label>
  <div class="seg" id="bg"></div>
  <label>Beer colour</label>
  <div class="seg" id="colour"></div>

  <hr>
  <label>Width, of the mouth <b id="v-width"></b></label>
  <input type="range" id="width" min="0.8" max="1.25" step="0.01">
  <label>Surface depth (ry, of rx) <b id="v-ry"></b></label>
  <input type="range" id="ry" min="0.15" max="0.55" step="0.01">
  <label>Sit, up or down the glass <b id="v-sit"></b></label>
  <input type="range" id="sit" min="-14" max="14" step="0.5">

  <hr>
  <label>Head depth, into the beer <b id="v-depth"></b></label>
  <input type="range" id="depth" min="0" max="90" step="1">
  <label>Underside curve <b id="v-curve"></b></label>
  <input type="range" id="curve" min="-30" max="30" step="0.5">
  <label>Underside softness <b id="v-fade"></b></label>
  <input type="range" id="fade" min="0" max="1" step="0.02">

  <hr>
  <label>Mound blob size <b id="v-blob"></b></label>
  <input type="range" id="blob" min="0.15" max="0.55" step="0.01">
  <label>Mound spread <b id="v-spread"></b></label>
  <input type="range" id="spread" min="0.2" max="0.9" step="0.01">
  <label>Mound lift <b id="v-lift"></b></label>
  <input type="range" id="lift" min="0.3" max="1.6" step="0.02">

  <hr>
  <div class="seg">
    <button id="reset">Reset</button>
    <button id="shipped">Show shipped</button>
    <button id="copy">Copy values</button>
  </div>
  <textarea id="out" readonly></textarea>
  <p class="note">Every knob is in the URL - paste it back to keep a setting.</p>
</div>
<div id="stage">
  <div class="row" id="sizes"></div>
  <div class="row" id="all"></div>
</div>
<script>
const DATA = __DATA__;
/* Width 1.0 and no depth is TODAY's geometry made honest: production's
   hand-entered rx is narrower than the mouth on every glass but the mug. */
const D = {width:1.0, ry:0.32, sit:0, depth:0, curve:0, fade:0,
           blob:0.30, spread:0.55, lift:0.60};
const KNOBS = Object.keys(D);
let glass = DATA.default, bg = "dark", colour = "amber", showShipped = false;

const COLOURS = {
  pale:  ["#f6d488", "#e8bd5a", "#d8a63c"],
  amber: ["#e08a2e", "#c3641a", "#a4500f"],
  stout: ["#4a2a18", "#2a1509", "#170b04"],
  unknown: ["#f0b048", "#e8a020", "#c98413"]
};
const FOAM = {pale:"#fbf3dd", amber:"#f7f1e4", stout:"#d9cfc4", unknown:"#faf0d9"};
const BUBBLE = {pale:"#f2dda6", amber:"#eab77a", stout:"#7d6553", unknown:"#f3cd82"};

const g = n => +document.getElementById(n).value;
const f = v => Math.round(v * 100) / 100;

function span(outline, y) {
  const xs = [];
  for (let i = 0; i < outline.length - 1; i++) {
    const a = outline[i], b = outline[i + 1];
    if ((a[1] - y) * (b[1] - y) <= 0 && a[1] !== b[1])
      xs.push(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]));
  }
  return xs.length ? [Math.min.apply(null, xs), Math.max.apply(null, xs)] : [150, 150];
}

/* The mouth as the path actually draws it. This is the whole fix: production
   asks a stored number how wide the glass is, and the stored number is wrong. */
function mouth(outline) {
  const ys = outline.map(p => p[1]);
  const top = Math.min.apply(null, ys);
  const s = span(outline, top + 1.5);
  return {top: top, left: s[0], right: s[1], half: (s[1] - s[0]) / 2};
}

function head(outline, foam, uid) {
  const m = mouth(outline);
  const cy = m.top + g("sit");
  const rx = m.half * g("width");
  const ry = rx * g("ry");
  let out = "";

  // The body of the head: foam filling the top of the glass, clipped to the
  // pour, with a curved underside where it meets the beer.
  const depth = g("depth");
  if (depth > 0) {
    const c = g("curve"), bot = cy + depth;
    /* The underside curves ACROSS THE GLASS, not across the canvas, and about
       the depth line rather than below it. Spanning 0..300 put the glass in
       the flat middle of the arc, so the knob did almost nothing until the far
       end of its range; and hanging the arc below `bot` meant every turn of it
       also pushed the head deeper. Edges sit c/2 above the line, the centre
       c/2 below it, so the mean depth is exactly `depth` at any curve. */
    const s = span(outline, bot);
    const edge = bot - c / 2;
    const ctrl = bot + 1.5 * c;
    const band = "M -30 " + f(cy) + " L 330 " + f(cy) + " L 330 " + f(edge)
      + " L " + f(s[1]) + " " + f(edge)
      + " Q 150 " + f(ctrl) + " " + f(s[0]) + " " + f(edge)
      + " L -30 " + f(edge) + " Z";
    const fade = g("fade");
    if (fade > 0) {
      // userSpaceOnUse so the fade spans the band itself. Setting x1/y1 twice
      // is silently ignored by the parser, which collapses the gradient to one
      // user unit and makes the whole band invisible.
      out += '<linearGradient id="fade-' + uid + '" gradientUnits="userSpaceOnUse"'
        + ' x1="0" x2="0" y1="' + f(cy) + '" y2="' + f(bot + c / 2) + '">'
        + '<stop offset="' + f(Math.max(0, 1 - fade)) + '" stop-color="' + foam
        + '" stop-opacity="1"/>'
        + '<stop offset="1" stop-color="' + foam + '" stop-opacity="0"/></linearGradient>';
    }
    out += '<g clip-path="url(#clip-' + uid + ')"><path d="' + band + '" fill="'
      + (fade > 0 ? "url(#fade-" + uid + ")" : foam) + '"/></g>';
  }

  // The surface, and the mound of bubbles standing over the rim.
  out += '<ellipse cx="150" cy="' + f(cy) + '" rx="' + f(rx) + '" ry="' + f(ry)
    + '" fill="' + foam + '"/>';
  const sp = g("spread"), lift = g("lift"), br = g("blob");
  [[-sp, -lift, br], [0, -lift * 1.58, br * 1.2], [sp, -lift, br]].forEach(function (b) {
    out += '<circle cx="' + f(150 + rx * b[0]) + '" cy="' + f(cy + ry * b[1])
      + '" r="' + f(rx * b[2]) + '" fill="' + foam + '"/>';
  });
  return out;
}

function svg(key, size, uid) {
  const spec = DATA.glasses[key];
  const c = COLOURS[colour];
  const foam = FOAM[colour];
  let out = '<svg viewBox="0 0 300 300" width="' + size + '" height="' + size + '">'
    + '<defs><linearGradient id="' + uid + '" x1="0" y1="0" x2="0" y2="1">'
    + '<stop offset="0%" stop-color="' + c[0] + '"/>'
    + '<stop offset="55%" stop-color="' + c[1] + '"/>'
    + '<stop offset="100%" stop-color="' + c[2] + '"/></linearGradient>'
    + '<clipPath id="clip-' + uid + '"><path d="' + spec.pour + '"/></clipPath></defs>';
  if (spec.stem)
    out += '<path d="' + spec.stem + '" fill="' + DATA.glassFill + '" stroke="'
      + DATA.glassStroke + '" stroke-width="2"/>';
  out += '<path d="' + spec.pour + '" fill="url(#' + uid + ')"'
    + ' stroke="rgba(255,255,255,0.16)" stroke-width="3"/>';
  if (spec.etch) {
    out += '<g clip-path="url(#clip-' + uid + ')">'
      + '<path d="' + spec.etch + '" fill="none" stroke="rgba(255,255,255,0.09)"'
      + ' stroke-width="3.75"/>'
      + (spec.sheen ? '<path d="' + spec.sheen + '" fill="none" stroke-linecap="round"'
          + ' stroke="rgba(255,255,255,0.09)" stroke-width="5.25"/>' : "")
      + '</g>';
  }
  if (showShipped) {
    // What production draws today, in outline, to see the shortfall directly.
    const h = spec.shipped;
    out += '<ellipse cx="150" cy="' + h[0] + '" rx="' + h[1] + '" ry="' + h[2]
      + '" fill="none" stroke="#ff5f6d" stroke-width="2" stroke-dasharray="5 4"/>';
  }
  return out + head(spec.outline, foam, uid) + "</svg>";
}

function seg(host, items, get, set) {
  host.innerHTML = "";
  items.forEach(function (it) {
    const b = document.createElement("button");
    b.textContent = it[1];
    b.setAttribute("aria-pressed", get() === it[0]);
    b.onclick = function () { set(it[0]); render(); };
    host.appendChild(b);
  });
}

let uid = 0;
function render() {
  document.body.className = bg === "dark" ? "" : bg;
  seg(document.getElementById("glass"),
      Object.keys(DATA.glasses).map(k => [k, DATA.glasses[k].label.replace(" (default)", "")]),
      function () { return glass; }, function (v) { glass = v; });
  seg(document.getElementById("bg"),
      [["dark", "dark"], ["light", "daylight"], ["oled", "oled"]],
      function () { return bg; }, function (v) { bg = v; });
  seg(document.getElementById("colour"),
      [["pale", "pale"], ["amber", "amber"], ["stout", "stout"], ["unknown", "unknown"]],
      function () { return colour; }, function (v) { colour = v; });
  KNOBS.forEach(k => document.getElementById("v-" + k).textContent = g(k));
  document.getElementById("shipped").setAttribute("aria-pressed", showShipped);

  document.getElementById("sizes").innerHTML = [260, 150, 96, 64, 40]
    .map(s => '<figure>' + svg(glass, s, "u" + (++uid))
      + '<figcaption>' + s + 'px</figcaption></figure>').join("");
  document.getElementById("all").innerHTML = Object.keys(DATA.glasses)
    .map(k => '<figure>' + svg(k, 120, "u" + (++uid))
      + '<figcaption>' + DATA.glasses[k].label.replace(" (default)", "")
      + '</figcaption></figure>').join("");

  const lines = Object.keys(DATA.glasses).map(function (k) {
    const m = mouth(DATA.glasses[k].outline);
    const rx = m.half * g("width");
    return k + ": head=(" + f(m.top + g("sit")) + ", " + f(rx) + ", "
      + f(rx * g("ry")) + ")   shipped rx " + DATA.glasses[k].shipped[1]
      + "  mouth " + f(m.half);
  });
  document.getElementById("out").value = lines.join("\n");

  const q = new URLSearchParams({glass: glass, bg: bg, colour: colour});
  KNOBS.forEach(k => q.set(k, g(k)));
  history.replaceState(null, "", "?" + q);
}

function load() {
  const q = new URLSearchParams(location.search);
  glass = q.get("glass") || glass;
  bg = q.get("bg") || bg;
  colour = q.get("colour") || colour;
  KNOBS.forEach(function (k) {
    const v = q.get(k);
    document.getElementById(k).value = v === null ? D[k] : v;
  });
}

KNOBS.forEach(k => document.getElementById(k).addEventListener("input", render));
document.getElementById("reset").onclick = function () {
  KNOBS.forEach(k => document.getElementById(k).value = D[k]);
  render();
};
document.getElementById("shipped").onclick = function () {
  showShipped = !showShipped;
  render();
};
document.getElementById("copy").onclick = function () {
  const t = document.getElementById("out");
  t.select();
  if (navigator.clipboard) navigator.clipboard.writeText(t.value);
  else document.execCommand("copy");
};
load();
render();
</script>
"""

if __name__ == "__main__":
    out = pathlib.Path(__file__).with_name("foam_lab.html")
    io.open(out, "w", encoding="utf-8").write(build())
    print("wrote", out)
