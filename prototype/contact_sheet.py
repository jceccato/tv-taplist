"""PROTOTYPE - THROWAWAY. Every glass as production renders it (issue #6).

    python prototype/contact_sheet.py   ->  prototype/contact_sheet.html

The replacement for the hand-generated `glassware.html`. That file was pasted
together from a snippet typed into a session, so it could not be regenerated
after a shape changed and it quietly went stale. This reads `_SILHOUETTES`
directly: whatever the app draws today is what the page shows, and a new glass
appears the moment its row exists.

Two jobs:

1. **Judge a shape against the set.** Every glass at 220/120/64/40px, on any
   theme background, in any beer Colour. A shape that only works at 220 is a
   fail, and the Daylight theme is where clear glass goes to die - both are one
   click away rather than a rebuild away.
2. **Hand the path data back.** Each glass carries its production row - the
   pour, the head, the foam band, the bubbles and any stem - in a block that
   copies to the clipboard whole, so a shape being edited by hand can be taken
   out, worked on, and put back without retyping coordinates.

Every id is renamed per cell. Inline SVGs on one page share `id="g"` and
`id="p"`, so without that every glass borrows the first one's gradient and the
stout renders straw - it looks exactly like a colour bug. Production never hits
this: each glass is its own `/img/beer-glass` response.

NOT production code, and not covered by tests.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.beer_glass import (  # noqa: E402
    _SILHOUETTES, DEFAULT_GLASS, GLASS_TYPES, beer_glass_svg,
)

SIZES = (220, 120, 64, 40)


def _row_source(key: str) -> str:
    """The glass's `_SILHOUETTES` row, as it would be pasted into the module.

    Rendered from the values themselves rather than sliced out of the file, so
    it cannot drift from what is actually drawn - and so a shape edited in a
    lab can be round-tripped through here without hand-formatting.
    """
    s = _SILHOUETTES[key]
    cy, rx, ry = s.head
    out = [f'    "{key}": _Silhouette(', f'        pour="{s.pour}",',
           f"        head=({cy:g}, {rx:g}, {ry:g}),", f'        foam="{s.foam}",']
    if s.bubbles:
        inner = ", ".join(f"({b[0]:g}, {b[1]:g}, {b[2]:g}, {b[3]:g})" for b in s.bubbles)
        out.append(f"        bubbles=({inner}),")
    else:
        out.append("        bubbles=(),")
    for name in ("stem", "etch", "sheen"):
        val = getattr(s, name)
        if val:
            out.append(f'        {name}="{val}",')
    out.append("    ),")
    return "\n".join(out)


def build() -> str:
    glasses = []
    for key, label in GLASS_TYPES:
        glasses.append({"key": key, "label": label, "source": _row_source(key)})
    data = {"glasses": glasses, "sizes": list(SIZES), "default": DEFAULT_GLASS,
            "svg": {c: {g["key"]: beer_glass_svg(h, glass=g["key"]) for g in glasses}
                    for c, h in (("pale", "#f0c14b"), ("amber", "#c07f1a"),
                                 ("stout", "#2c1608"), ("unknown", None))}}
    return _TEMPLATE.replace("__DATA__", json.dumps(data))


_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8"><title>Glassware - contact sheet</title>
<style>
  :root { --bg:#131a22; --fg:#c9d3de; --panel:#1b242e; --line:#2c3846; }
  body.light  { --bg:#f4f1ec; --fg:#333;    --panel:#e7e2da; --line:#cfc8bd; }
  body.oled   { --bg:#000;    --fg:#c9d3de; --panel:#0e0e0e; --line:#242424; }
  body.dimmed { --bg:#1c2430; --fg:#c9d3de; --panel:#242e3b; --line:#38455a; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 system-ui,sans-serif; }
  header { position:sticky; top:0; z-index:2; padding:12px 20px;
           background:var(--panel); border-bottom:1px solid var(--line);
           display:flex; gap:22px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:14px; margin:0; font-weight:600; }
  .seg { display:flex; gap:4px; }
  .seg span { align-self:center; opacity:.55; font-size:12px; margin-right:2px; }
  .seg button { padding:4px 9px; font:12px system-ui,sans-serif; cursor:pointer;
                background:transparent; color:var(--fg);
                border:1px solid var(--line); border-radius:5px; }
  .seg button[aria-pressed=true] { background:var(--fg); color:var(--panel); }
  main { padding:8px 20px 60px; }
  .glass { border-bottom:1px solid var(--line); padding:14px 0; }
  .head { display:flex; align-items:baseline; gap:12px; }
  .head h2 { font-size:13px; margin:0; font-weight:600; }
  .head code { opacity:.5; font-size:11px; }
  .head button { margin-left:auto; padding:3px 10px; font:12px system-ui,sans-serif;
                 background:transparent; color:var(--fg); cursor:pointer;
                 border:1px solid var(--line); border-radius:5px; }
  .sizes { display:flex; gap:28px; align-items:flex-end; margin-top:10px;
           flex-wrap:wrap; }
  figure { margin:0; text-align:center; }
  figcaption { opacity:.5; font-size:11px; margin-top:2px; }
  details { margin-top:10px; }
  summary { cursor:pointer; opacity:.6; font-size:12px; }
  textarea { width:100%; box-sizing:border-box; height:150px; margin-top:8px;
             font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;
             background:var(--panel); color:var(--fg); padding:8px;
             border:1px solid var(--line); border-radius:5px; }
  p.note { opacity:.6; margin:6px 20px 0; max-width:70ch; }
</style>
<body>
<header>
  <h1>Glassware</h1>
  <div class="seg" id="bg"><span>background</span></div>
  <div class="seg" id="colour"><span>beer</span></div>
</header>
<p class="note">Exactly what <code>app/beer_glass.py</code> draws today - the page
reads <code>_SILHOUETTES</code>, so it cannot go stale. <b>Check every shape at
40px and on Daylight</b> before believing it. Each glass's block below is its
production row, ready to paste back.</p>
<main id="stage"></main>
<script>
const DATA = __DATA__;
const BGS = [["dark", "Dark"], ["dimmed", "Local dimming"], ["oled", "OLED"],
             ["light", "Daylight"]];
const COLOURS = [["pale", "Pale"], ["amber", "Amber"], ["stout", "Stout"],
                 ["unknown", "Unknown"]];
let bg = "dark", colour = "amber", uid = 0;

/* Every inline SVG on this page ships the same gradient ids. Renaming them per
   cell is what stops all nine borrowing the first one's colour. */
function cell(key, size) {
  uid++;
  let svg = DATA.svg[colour][key];
  ["g", "p"].forEach(function (i) {
    svg = svg.split('id="' + i + '"').join('id="' + i + uid + '"')
             .split("url(#" + i + ")").join("url(#" + i + uid + ")");
  });
  return svg.replace('width="300" height="300"',
                     'width="' + size + '" height="' + size + '"');
}

function seg(host, items, get, set) {
  Array.prototype.slice.call(host.querySelectorAll("button")).forEach(b => b.remove());
  items.forEach(function (it) {
    const b = document.createElement("button");
    b.textContent = it[1];
    b.setAttribute("aria-pressed", get() === it[0]);
    b.onclick = function () { set(it[0]); render(); };
    host.appendChild(b);
  });
}

function render() {
  document.body.className = bg;
  seg(document.getElementById("bg"), BGS, () => bg, v => { bg = v; });
  seg(document.getElementById("colour"), COLOURS, () => colour, v => { colour = v; });

  uid = 0;
  document.getElementById("stage").innerHTML = DATA.glasses.map(function (g) {
    return '<section class="glass">'
      + '<div class="head"><h2>' + g.label + '</h2><code>' + g.key + '</code>'
      + '<button data-copy="' + g.key + '">Copy row</button></div>'
      + '<div class="sizes">' + DATA.sizes.map(s =>
          '<figure>' + cell(g.key, s) + '<figcaption>' + s + 'px</figcaption></figure>'
        ).join("") + '</div>'
      + '<details><summary>Path data</summary><textarea readonly spellcheck="false">'
      + g.source.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      + '</textarea></details></section>';
  }).join("");

  Array.prototype.forEach.call(document.querySelectorAll("[data-copy]"), function (b) {
    b.onclick = function () {
      const src = DATA.glasses.filter(g => g.key === b.dataset.copy)[0].source;
      const done = function () {
        b.textContent = "Copied";
        setTimeout(function () { b.textContent = "Copy row"; }, 1200);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(src).then(done, fallback);
      else fallback();
      function fallback() {
        const t = document.createElement("textarea");
        t.value = src; document.body.appendChild(t); t.select();
        document.execCommand("copy"); t.remove(); done();
      }
    };
  });

  history.replaceState(null, "", "?bg=" + bg + "&colour=" + colour);
}

const q = new URLSearchParams(location.search);
if (q.get("bg")) bg = q.get("bg");
if (q.get("colour")) colour = q.get("colour");
render();
</script>
"""


if __name__ == "__main__":
    dest = pathlib.Path(__file__).with_name("contact_sheet.html")
    io.open(dest, "w", encoding="utf-8").write(build())
    print("wrote", dest)
