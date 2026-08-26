"""PROTOTYPE - THROWAWAY. Build the dimpled-mug tuning page (issue #6, Phase 2).

    python prototype/mug_lab.py   ->  prototype/mug_lab.html

The mug is the one glass whose silhouette is not enough on its own: without the
dimples the pour is a stange with a handle. The dimples therefore have knobs,
and this page is where they get turned. Everything it draws is DERIVED from the
corrected pour - the grid asks the profile for its width at each row - so the
whole thing re-fits if the pour is redrawn, the same discipline the foam and the
bubbles already follow.

The pour is flattened to a polyline here, in Python, and baked into the page;
the page's JS only does the placement maths. That keeps one path parser in the
prototype (symmetry.py) rather than a second one in JavaScript.
"""
from __future__ import annotations

import io
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import symmetry as sy  # noqa: E402

# Hand-drawn by the maintainer; see prototype/mug-dimples.md.
POUR = "M 83 80 C 80 117 77 208 100 247 A 1 0.16 0 0 0 199 247 C 217 190 223 129 213 80 Z"
# One-sided by nature - it hangs off the right - so the symmetriser cannot fold
# it and it ships exactly as drawn.
HANDLE = ("M 216 99 C 245 97 273 97 266 160 C 259 227 211 217 205 222 L 211 201 "
          "C 211 207 244 210 252 169 C 269 97 219 112 217 116 Z")

MODES = ("as-is", "left", "right", "average")


def flatten(d: str, steps: int = 40) -> list[tuple[float, float]]:
    """The path as a polyline, so the page can ask its width at any height."""
    pts: list[tuple[float, float]] = []
    cur = start = (0.0, 0.0)
    for cmd, p in sy.parse(d):
        if cmd == "M":
            cur = start = (p[0], p[1])
            pts.append(cur)
        elif cmd == "L":
            cur = (p[0], p[1])
            pts.append(cur)
        elif cmd == "C":
            x0, y0 = cur
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                pts.append((u**3 * x0 + 3 * u * u * t * p[0] + 3 * u * t * t * p[2] + t**3 * p[4],
                            u**3 * y0 + 3 * u * u * t * p[1] + 3 * u * t * t * p[3] + t**3 * p[5]))
            cur = (p[4], p[5])
        elif cmd == "A":
            # The degenerate-radius idiom (A 1 0.16 ...): SVG scales the radii
            # up until they span the endpoints, so this is a half ellipse.
            x0, y0 = cur
            x1, y1 = p[5], p[6]
            cx, rx = (x0 + x1) / 2, abs(x1 - x0) / 2
            ry = rx * (p[1] / p[0]) if p[0] else 0.0
            sign = 1 if p[4] else -1
            for i in range(1, steps + 1):
                a = math.pi * (i / steps)
                pts.append((cx + rx * math.cos(math.pi - a) * (1 if x1 > x0 else -1),
                            y0 + ry * math.sin(a) * sign))
            cur = (x1, y1)
        elif cmd == "Z":
            pts.append(start)
            cur = start
    return pts


def build() -> str:
    data = {
        "handle": HANDLE,
        "pours": {m: sy.symmetrise(POUR, m) for m in MODES},
        "outlines": {m: flatten(sy.symmetrise(POUR, m)) for m in MODES},
    }
    return _TEMPLATE.replace("__DATA__", json.dumps(data))


_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8"><title>Dimpled mug - shape lab</title>
<style>
  :root { --bg:#131a22; --fg:#c9d3de; --panel:#1b242e; --line:#2c3846; }
  body.light { --bg:#f4f1ec; --fg:#333; --panel:#e7e2da; --line:#cfc8bd; }
  body.oled  { --bg:#000;    --fg:#c9d3de; --panel:#0e0e0e; --line:#242424; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 system-ui,sans-serif; display:flex; min-height:100vh; }
  #panel { width:300px; flex:none; padding:16px; background:var(--panel);
           border-right:1px solid var(--line); overflow:auto; max-height:100vh; }
  #stage { flex:1; padding:24px; }
  h1 { font-size:15px; margin:0 0 4px; }
  p.note { opacity:.65; margin:0 0 16px; }
  label { display:block; margin:10px 0 2px; font-size:12px; }
  label b { float:right; font-weight:600; opacity:.8; }
  input[type=range] { width:100%; }
  .seg { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; }
  .seg button { flex:1 1 auto; padding:5px 8px; font:12px system-ui,sans-serif;
                background:transparent; color:var(--fg); border:1px solid var(--line);
                border-radius:5px; cursor:pointer; }
  .seg button[aria-pressed=true] { background:var(--fg); color:var(--panel); }
  .row { display:flex; gap:28px; align-items:flex-end; margin-bottom:20px; }
  .row span { display:block; opacity:.6; font-size:12px; }
  textarea { width:100%; height:120px; margin-top:8px; font:11px/1.4 ui-monospace,monospace;
             background:var(--bg); color:var(--fg); border:1px solid var(--line);
             border-radius:5px; padding:6px; }
  hr { border:0; border-top:1px solid var(--line); margin:16px 0; }
</style>
<body>
<div id="panel">
  <h1>Dimpled mug</h1>
  <p class="note">Everything is derived from the corrected pour. Redraw the pour
  in mug_lab.py and the grid re-fits itself.</p>

  <label>Symmetry correction</label>
  <div class="seg" id="mode"></div>
  <label>Background</label>
  <div class="seg" id="bg"></div>
  <label>Beer colour</label>
  <div class="seg" id="colour"></div>

  <hr>
  <label>Warp round the barrel <b id="v-theta"></b></label>
  <input type="range" id="theta" min="0" max="80" step="1">
  <label>Row bow at the front <b id="v-bow"></b></label>
  <input type="range" id="bow" min="-16" max="16" step="0.5">
  <label>Column spread <b id="v-inset"></b></label>
  <input type="range" id="inset" min="0.3" max="1" step="0.01">
  <label>Dimple width, of its column <b id="v-fill"></b></label>
  <input type="range" id="fill" min="0.4" max="1.3" step="0.01">
  <label>Dimple height, of its row <b id="v-hfrac"></b></label>
  <input type="range" id="hfrac" min="0.3" max="1" step="0.01">
  <label>Bottom row stretch <b id="v-bottom"></b></label>
  <input type="range" id="bottom" min="1" max="3" step="0.05">
  <label>Bottom row nudge <b id="v-boff"></b></label>
  <input type="range" id="boff" min="-30" max="30" step="1">
  <label>Stagger, every other row <b id="v-stagger"></b></label>
  <input type="range" id="stagger" min="0" max="1" step="0.05">
  <label>Row spacing <b id="v-vgap"></b></label>
  <input type="range" id="vgap" min="0.4" max="2" step="0.01">
  <label>Move the block up or down <b id="v-vshift"></b></label>
  <input type="range" id="vshift" min="-80" max="80" step="1">
  <label>Corner radius <b id="v-round"></b></label>
  <input type="range" id="round" min="0" max="0.5" step="0.01">
  <label>Stroke weight <b id="v-weight"></b></label>
  <input type="range" id="weight" min="1" max="6" step="0.25">
  <label>Stroke opacity <b id="v-alpha"></b></label>
  <input type="range" id="alpha" min="0.05" max="0.6" step="0.01">

  <hr>
  <label>Inner reflection, opacity <b id="v-refl"></b></label>
  <input type="range" id="refl" min="0" max="0.7" step="0.01">
  <label>Reflection weight <b id="v-reflw"></b></label>
  <input type="range" id="reflw" min="0.5" max="8" step="0.25">
  <label>Reflection length <b id="v-reflext"></b></label>
  <input type="range" id="reflext" min="0" max="1" step="0.02">
  <label>Rows <b id="v-rows"></b></label>
  <input type="range" id="rows" min="2" max="7" step="1">
  <label>Columns <b id="v-cols"></b></label>
  <input type="range" id="cols" min="2" max="6" step="1">

  <hr>
  <div class="seg">
    <button id="reset">Reset</button>
    <button id="copy">Copy paths</button>
  </div>
  <textarea id="out" readonly></textarea>
  <p class="note">Deep-linkable: the URL carries every knob, so a setting worth
  keeping is worth pasting back to me.</p>
</div>
<div id="stage">
  <div class="row" id="sizes"></div>
  <div class="row" id="sizes2"></div>
</div>
<script>
const DATA = __DATA__;
const FOAM_RY = 0.32;
/* The maintainer's settled baseline, round 4. */
const D = {theta:0, bow:3, inset:0.66, fill:1.20, hfrac:1, bottom:1.3, boff:0,
           stagger:1, vgap:1.28, vshift:-8, round:0.34, weight:3.75, alpha:0.09,
           refl:0.16, reflw:2.5, reflext:0.55, rows:3, cols:3};
const KNOBS = Object.keys(D);
let mode = "average", bg = "dark", colour = "amber";

const COLOURS = {
  pale:  ["#f6d488", "#e8bd5a", "#d8a63c"],
  amber: ["#e08a2e", "#c3641a", "#a4500f"],
  stout: ["#4a2a18", "#2a1509", "#170b04"],
  unknown: ["#f0b048", "#e8a020", "#c98413"]
};

function span(outline, y) {
  const xs = [];
  for (let i = 0; i < outline.length - 1; i++) {
    const a = outline[i], b = outline[i + 1];
    if ((a[1] - y) * (b[1] - y) <= 0 && a[1] !== b[1])
      xs.push(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]));
  }
  return xs.length ? [Math.min.apply(null, xs), Math.max.apply(null, xs)] : [150, 150];
}
const g = n => +document.getElementById(n).value;
const f = v => Math.round(v * 100) / 100;

function rr(x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  return "M " + f(x+r) + " " + f(y) + " L " + f(x+w-r) + " " + f(y)
    + " A " + f(r) + " " + f(r) + " 0 0 1 " + f(x+w) + " " + f(y+r)
    + " L " + f(x+w) + " " + f(y+h-r)
    + " A " + f(r) + " " + f(r) + " 0 0 1 " + f(x+w-r) + " " + f(y+h)
    + " L " + f(x+r) + " " + f(y+h)
    + " A " + f(r) + " " + f(r) + " 0 0 1 " + f(x) + " " + f(y+h-r)
    + " L " + f(x) + " " + f(y+r)
    + " A " + f(r) + " " + f(r) + " 0 0 1 " + f(x+r) + " " + f(y) + " Z";
}

/* The inner reflection: the left edge and the bottom-left corner of a dimple,
   as its own open path. It reuses rr()'s corner arc exactly, so the highlight
   sits on the outline rather than near it. Length runs the same fraction up
   the side and along the base, which keeps the corner reading as a corner. */
function reflection(x, y, w, h, r, ext) {
  r = Math.min(r, w / 2, h / 2);
  if (ext <= 0) return "";
  const along = x + r + (w - 2 * r) * ext;
  const up = y + h - r - (h - 2 * r) * ext;
  return "M " + f(along) + " " + f(y + h) + " L " + f(x + r) + " " + f(y + h)
    + " A " + f(r) + " " + f(r) + " 0 0 1 " + f(x) + " " + f(y + h - r)
    + " L " + f(x) + " " + f(up);
}

/* A mug is a cylinder: a column's offset round the barrel goes as sin(theta)
   and its apparent width as cos(theta), so the outer columns crowd toward the
   edge and squash. Each row bows down at the front, because the mug sits below
   eye level. That is the whole of the distortion. */
function dimples(outline, top, bot) {
  const p = {}; KNOBS.forEach(k => p[k] = g(k));
  const rim = span(outline, top + 1);
  const first = top + (rim[1] - rim[0]) / 2 * FOAM_RY * 2 + 8;
  const last = bot - 16;
  // The band between the foam and the base is what the grid gets to use. Its
  // natural row height sizes the dimples; spacing then stretches the PITCH
  // about the band's centre, so opening the rows up never fattens them, and
  // the block stays centred instead of growing downward.
  const base = (last - first) / p.rows;
  const pitch = base * p.vgap;
  const h = base * p.hfrac;
  const middle = (first + last) / 2 + p.vshift;
  const th = p.theta * Math.PI / 180;
  // Bow is CURVATURE ONLY, and DECOUPLED from the wrap.
  //
  // Two corrections, both deliberate. Raw cos() is positive everywhere, so
  // bowing a row also pushed the whole grid down - bow and position fought
  // each other. Removing the mean leaves the shape of the curve and none of
  // the displacement, so vshift is the only knob that moves the block.
  //
  // And the bow is driven by the column's POSITION, not by its angle round the
  // barrel. Angle would be the honest reading - how far round you are decides
  // both how squashed you look and how much lower you sit - but it makes bow
  // do nothing at low wrap, which is exactly where the mug settled. The row
  // now curves the same amount whatever the wrap is doing.
  const curve = t => Math.cos(t * Math.PI / 2);
  let meanCurve = 0;
  for (let c = 0; c < p.cols; c++) {
    const t = p.cols === 1 ? 0 : (c - (p.cols - 1) / 2) / ((p.cols - 1) / 2);
    meanCurve += curve(t) / p.cols;
  }
  // Columns live on a -1..1 axis, so one column's pitch is 2/(cols-1). A
  // staggered row is that axis shifted by half a pitch and given one extra
  // dimple, which is what puts a half dimple at each edge: the outer pair runs
  // past the profile and the pour clips it. Real mugs are laid out this way -
  // the rows interlock rather than stacking.
  const pitchT = p.cols > 1 ? 2 / (p.cols - 1) : 0;
  const out = [], hi = [];
  for (let r = 0; r < p.rows; r++) {
    const last = r === p.rows - 1;
    // The bottom row is taller on a real mug - the dimpling runs out before the
    // base does, so the last course stretches to meet it. It grows DOWNWARD
    // from its own top edge: half the extra height is added back to its centre
    // so the gap above it is the same gap every other row has. Stretching about
    // the centre is what drove it into the row above.
    const rh = h * (last ? p.bottom : 1);
    const grow = last ? (rh - h) / 2 + p.boff : 0;
    const cy = middle + pitch * (r - (p.rows - 1) / 2) + grow;
    const s = span(outline, cy);
    const half = (s[1] - s[0]) / 2 * p.inset, mid = (s[0] + s[1]) / 2;
    const stag = p.stagger > 0 && r % 2 === 0;
    const n = stag ? p.cols + 1 : p.cols;
    for (let c = 0; c < n; c++) {
      const t = p.cols === 1 ? 0
        : stag ? -1 - pitchT + c * pitchT + p.stagger * pitchT / 2
               : -1 + c * pitchT;
      const a = th * t;
      const cx = th ? mid + half * Math.sin(a) / Math.sin(th) : mid + t * half;
      const w = 2 * half / p.cols * p.fill * Math.cos(a);
      const dy = p.bow * (curve(t) - meanCurve);
      if (w > 1) {
        const x = cx - w / 2, y = cy + dy - rh / 2, rad = Math.min(w, rh) * p.round;
        out.push(rr(x, y, w, rh, rad));
        if (p.refl > 0) hi.push(reflection(x, y, w, rh, rad, p.reflext));
      }
    }
  }
  return {main: out.join(" "), hi: hi.join(" ")};
}

function svg(size, uid) {
  const outline = DATA.outlines[mode];
  const ys = outline.map(p => p[1]);
  const top = Math.min.apply(null, ys), bot = Math.max.apply(null, ys);
  const rim = span(outline, top + 1);
  const c = COLOURS[colour];
  const dd = dimples(outline, top, bot);
  return '<svg viewBox="0 0 300 300" width="' + size + '" height="' + size + '">'
    + '<defs><linearGradient id="' + uid + '" x1="0" y1="0" x2="0" y2="1">'
    + '<stop offset="0%" stop-color="' + c[0] + '"/>'
    + '<stop offset="55%" stop-color="' + c[1] + '"/>'
    + '<stop offset="100%" stop-color="' + c[2] + '"/></linearGradient>'
    // The dimples are clipped to the pour, so a staggered row's outer pair is
    // cut vertically by the profile instead of hanging off it.
    + '<clipPath id="clip-' + uid + '"><path d="' + DATA.pours[mode] + '"/></clipPath></defs>'
    + '<path d="' + DATA.handle + '" fill="rgba(146,160,180,0.30)"'
    + ' stroke="rgba(108,124,146,0.75)" stroke-width="2"/>'
    + '<path d="' + DATA.pours[mode] + '" fill="url(#' + uid + ')"/>'
    + '<g clip-path="url(#clip-' + uid + ')">'
    + '<path d="' + dd.main + '" fill="none"'
    + ' stroke="rgba(255,255,255,' + g("alpha") + ')" stroke-width="' + g("weight") + '"/>'
    + (g("refl") > 0 && dd.hi
        ? '<path d="' + dd.hi + '" fill="none" stroke-linecap="round"'
          + ' stroke="rgba(255,255,255,' + g("refl") + ')"'
          + ' stroke-width="' + g("reflw") + '"/>'
        : "")
    + '</g>'
    + '<ellipse cx="' + f((rim[0] + rim[1]) / 2) + '" cy="' + f(top) + '"'
    + ' rx="' + f((rim[1] - rim[0]) / 2) + '"'
    + ' ry="' + f((rim[1] - rim[0]) / 2 * FOAM_RY) + '" fill="#f7f1e4"/>'
    + '</svg>';
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

function render() {
  document.body.className = bg === "dark" ? "" : bg;
  seg(document.getElementById("mode"),
      [["as-is", "as drawn"], ["left", "mirror L"], ["right", "mirror R"], ["average", "averaged"]],
      function () { return mode; }, function (v) { mode = v; });
  seg(document.getElementById("bg"),
      [["dark", "dark"], ["light", "daylight"], ["oled", "oled"]],
      function () { return bg; }, function (v) { bg = v; });
  seg(document.getElementById("colour"),
      [["pale", "pale"], ["amber", "amber"], ["stout", "stout"], ["unknown", "unknown"]],
      function () { return colour; }, function (v) { colour = v; });
  KNOBS.forEach(k => document.getElementById("v-" + k).textContent = g(k));

  document.getElementById("sizes").innerHTML =
    [250, 160, 120].map((s, i) => "<div>" + svg(s, "a" + i) + "<span>" + s + "px</span></div>").join("");
  document.getElementById("sizes2").innerHTML =
    [96, 64, 40].map((s, i) => "<div>" + svg(s, "b" + i) + "<span>" + s + "px</span></div>").join("");

  const outline = DATA.outlines[mode];
  const ys = outline.map(p => p[1]);
  const dd = dimples(outline, Math.min.apply(null, ys), Math.max.apply(null, ys));
  document.getElementById("out").value =
    "POUR (" + mode + "):\n" + DATA.pours[mode]
    + "\n\nDIMPLES:\n" + dd.main
    + (dd.hi ? "\n\nREFLECTIONS:\n" + dd.hi : "")
    + "\n\nHANDLE (as drawn):\n" + DATA.handle;

  const q = new URLSearchParams({mode: mode, bg: bg, colour: colour});
  KNOBS.forEach(k => q.set(k, g(k)));
  history.replaceState(null, "", "?" + q);
}

function load() {
  const q = new URLSearchParams(location.search);
  mode = q.get("mode") || mode;
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
    out = pathlib.Path(__file__).with_name("mug_lab.html")
    io.open(out, "w", encoding="utf-8").write(build())
    print("wrote", out)
