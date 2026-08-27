# THIS IS A THROWAWAY HARNESS, KEPT BECAUSE IT WAS USEFUL - NOT MAINTAINED.
#
# It reads from `app/` and it is NOT covered by the test suite, so nothing fails
# when production moves underneath it. Assume it is stale until you have run it.
# Before trusting anything it draws, check the notes at the top of
# prototype/glasses/README.md, and run it: an ImportError or a KeyError is the
# cheap failure. The expensive one is a page that still renders while quietly
# disagreeing with what the app ships.
#
# Reads every knob back out of `_SILHOUETTES`, including the shape of the
# foam band, which it decodes by position (`nums[5]` and `nums[9]`). Change
# how `foam` is written and this reads the wrong two numbers and opens on a
# head nobody chose, without erroring. That decode is the first thing to
# check if the sliders disagree with the page.

"""PROTOTYPE - THROWAWAY. Tune the head on every shipped glass (issue #6).

    python prototype/glasses/foam_lab.py   ->  prototype/glasses/foam_lab.html

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
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.beer_glass import (  # noqa: E402
    _GLASS_FILL, _GLASS_STROKE, _HEAD_BLOBS, _SILHOUETTES, DEFAULT_GLASS,
    GLASS_TYPES,
)
from mug_lab import flatten  # noqa: E402


def _span(outline, y: float) -> tuple[float, float]:
    """The pour's left and right edge at one height, off the flattened outline."""
    xs = []
    for a, b in zip(outline, outline[1:]):
        if (a[1] - y) * (b[1] - y) <= 0 and a[1] != b[1]:
            xs.append(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]))
    return (min(xs), max(xs)) if xs else (150.0, 150.0)


def _settled(key: str, outline) -> dict[str, float]:
    """Every knob, read back out of what the glass actually ships.

    The lab used to open on a shared default with the depth seeded from each
    glass's height, which was the right start when nothing had been decided.
    It is the wrong start now: the head is settled, so opening anywhere else
    means the first thing seen is a shape nobody chose, and a knob nudged from
    there is being judged against the wrong neighbour.

    Reading production back rather than pasting a table of numbers in is what
    keeps this honest - edit a row in `app/beer_glass.py`, regenerate, and the
    lab opens on the new value with nothing here to update. The head's own
    parameters come out of `_SILHOUETTES`; the mound comes out of the shared
    `_HEAD_BLOBS`, which is where production keeps it.
    """
    s = _SILHOUETTES[key]
    top = min(p[1] for p in outline)
    left, right = _span(outline, top + 1.5)
    half = (right - left) / 2
    cy, rx, ry = s.head
    # The foam band, written back to front: its straight edge sits curve/2 above
    # the depth line and its control point 1.5 * curve below, which is the pair
    # this reads to recover both. See `head()` in the page for the forward form.
    nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", s.foam)]
    edge, ctrl = nums[5], nums[9]
    curve = (ctrl - edge) / 2
    return {
        "width": round(rx / half, 3),
        "ry": round(ry / rx, 3),
        "sit": round(cy - top, 2),
        "depth": round(edge + curve / 2 - cy, 2),
        "curve": round(curve, 2),
        # Not implemented in production at all: the underside settled on a hard
        # edge everywhere, so there is nothing to read back. The knob stays -
        # it is the first thing to try if a head ever reads as painted on.
        "fade": 0,
        "blob": _HEAD_BLOBS[0][2],
        "spread": _HEAD_BLOBS[2][0],
        "lift": -_HEAD_BLOBS[0][1],
    }


def build() -> str:
    glasses = {}
    for key, label in GLASS_TYPES:
        s = _SILHOUETTES[key]
        outline = flatten(s.pour)
        glasses[key] = {
            "label": label,
            "pour": s.pour,
            "outline": outline,
            # Where this glass ships, so the lab opens on it rather than on a
            # seed - which also makes "Reset" mean "back to what production
            # draws" rather than "back to the day nothing was decided".
            "settled": _settled(key, outline),
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
  .seg button i { font-style:italic; }
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
  means the foam is exactly as wide as the mouth. <b>Every knob below applies
  to <i data-for></i> alone</b> - the row at the bottom shows where every other
  glass is set.</p>

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
  <input type="range" id="depth" min="0" max="120" step="1">
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
    <button id="apply-all">Copy <i data-for></i>'s settings to every glass</button>
  </div>
  <div class="seg">
    <button id="reset-one">Reset <i data-for></i></button>
    <button id="reset">Reset all</button>
  </div>
  <div class="seg">
    <button id="shipped">Show shipped</button>
    <button id="copy">Copy values</button>
  </div>
  <textarea id="out" readonly></textarea>
  <p class="note">Every glass's settings are in the URL, one parameter each -
  paste it back to keep them.</p>
</div>
<div id="stage">
  <div class="row" id="sizes"></div>
  <div class="row" id="all"></div>
</div>
<script>
const DATA = __DATA__;
/* The shared baseline. Every glass overrides all of it from its own shipped
   row on load - this is only what a knob falls back to if production ever
   stops carrying one. */
const D = {width:1.0, ry:0.32, sit:0, depth:0, curve:0, fade:0,
           blob:0.30, spread:0.55, lift:0.30};
/* EVERY knob is per glass while the shapes are being tuned. Some of these will
   turn out to hold the same value on every glass - width and the mound almost
   certainly will, being ratios of each glass's own mouth - and those collapse
   back to one shared default when they fold into production. Until then it is
   cheaper to let each glass disagree than to argue about which ones may. */
const KNOBS = Object.keys(D);
const S = {};
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

function head(outline, foam, uid, p) {
  const m = mouth(outline);
  const cy = m.top + p.sit;
  const rx = m.half * p.width;
  const ry = rx * p.ry;
  const depth = p.depth;
  let out = "";

  // The body of the head: foam filling the top of the glass, clipped to the
  // pour, with a curved underside where it meets the beer.
  if (depth > 0) {
    const c = p.curve, bot = cy + depth;
    // The fade needs GEOMETRY to happen in. Measured as a fraction of the whole
    // band, it ran past the bottom of the shape and was cut off mid-fade, which
    // is why a soft setting looked like a hard line in the wrong place. It is
    // now a distance either side of the boundary, and the shape is extended by
    // that distance so the gradient has somewhere to finish.
    const feather = p.fade * 40;
    /* The underside curves ACROSS THE GLASS, not across the canvas, and about
       the depth line rather than below it. Spanning 0..300 put the glass in
       the flat middle of the arc, so the knob did almost nothing until the far
       end of its range; and hanging the arc below `bot` meant every turn of it
       also pushed the head deeper. Edges sit c/2 above the line, the centre
       c/2 below it, so the mean depth is exactly `depth` at any curve. */
    const s = span(outline, bot);
    const edge = bot - c / 2 + feather;
    const ctrl = bot + 1.5 * c + feather;
    const band = "M -30 " + f(cy) + " L 330 " + f(cy) + " L 330 " + f(edge)
      + " L " + f(s[1]) + " " + f(edge)
      + " Q 150 " + f(ctrl) + " " + f(s[0]) + " " + f(edge)
      + " L -30 " + f(edge) + " Z";
    if (feather > 0) {
      // userSpaceOnUse so the fade spans the band itself. Setting x1/y1 twice
      // is silently ignored by the parser, which collapses the gradient to one
      // user unit and makes the whole band invisible.
      out += '<linearGradient id="fade-' + uid + '" gradientUnits="userSpaceOnUse"'
        + ' x1="0" x2="0" y1="' + f(bot - feather) + '" y2="' + f(bot + feather) + '">'
        + '<stop offset="0" stop-color="' + foam + '" stop-opacity="1"/>'
        + '<stop offset="1" stop-color="' + foam + '" stop-opacity="0"/></linearGradient>';
    }
    out += '<g clip-path="url(#clip-' + uid + ')"><path d="' + band + '" fill="'
      + (feather > 0 ? "url(#fade-" + uid + ")" : foam) + '"/></g>';
  }

  // The surface, and the mound of bubbles standing over the rim.
  out += '<ellipse cx="150" cy="' + f(cy) + '" rx="' + f(rx) + '" ry="' + f(ry)
    + '" fill="' + foam + '"/>';
  const sp = p.spread, lift = p.lift, br = p.blob;
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
  return out + head(spec.outline, foam, uid, S[key]) + "</svg>";
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
  const cur = S[glass];
  KNOBS.forEach(function (k) {
    document.getElementById(k).value = cur[k];
    document.getElementById("v-" + k).textContent = cur[k];
  });
  document.querySelectorAll("[data-for]").forEach(el =>
    el.textContent = DATA.glasses[glass].label.replace(" (default)", ""));
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
    return k + ": head=(" + f(m.top + S[k].sit) + ", " + f(rx) + ", "
      + f(rx * S[k].ry) + ")  "
      + KNOBS.map(n => n + " " + S[k][n]).join("  ");
  });
  document.getElementById("out").value = lines.join("\n");

  // One param per glass, values in KNOBS order - short enough to paste back.
  const q = new URLSearchParams({glass: glass, bg: bg, colour: colour});
  Object.keys(S).forEach(k => q.set(k, KNOBS.map(n => S[k][n]).join(",")));
  history.replaceState(null, "", "?" + q);
}

function load() {
  const q = new URLSearchParams(location.search);
  glass = q.get("glass") || glass;
  bg = q.get("bg") || bg;
  colour = q.get("colour") || colour;
  Object.keys(DATA.glasses).forEach(function (k) {
    // Open on what the glass ships, so the first thing on screen is the
    // decision that was made rather than a starting point nobody chose.
    S[k] = Object.assign({}, D, DATA.glasses[k].settled);
    const packed = q.get(k);
    if (packed) packed.split(",").forEach(function (v, i) {
      if (KNOBS[i] && v !== "") S[k][KNOBS[i]] = +v;
    });
  });
}

KNOBS.forEach(k => document.getElementById(k).addEventListener("input", function () {
  S[glass][k] = +this.value;
  render();
}));
document.getElementById("apply-all").onclick = function () {
  const v = Object.assign({}, S[glass]);
  Object.keys(S).forEach(k => S[k] = Object.assign({}, v));
  render();
};
document.getElementById("reset-one").onclick = function () {
  S[glass] = Object.assign({}, D, DATA.glasses[glass].settled);
  render();
};
document.getElementById("reset").onclick = function () {
  Object.keys(DATA.glasses).forEach(k =>
    S[k] = Object.assign({}, D, DATA.glasses[k].settled));
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
