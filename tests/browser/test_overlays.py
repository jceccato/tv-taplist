"""The "old scanned photo" overlays on the real display page (issue #63).

Grain, burns and light leaks, dust and floaties age the photo: they are drawn
inside its window, beside the photo, so they never reach the mount and the slow
zoom never moves them. Creases are folds in the print and run across the photo
and the mount, inside the frame. Each kind has its own switch; one strength
scales them all; every photo gets fresh placement. These tests assert DOM state
and geometry, never pixels; `ALBUM_SHOTS` keeps screenshots to judge by eye.
"""
import os
import re
from pathlib import Path

import pytest

from app import paths, photo_library as lib

from album_support import album_profile
from photo_fixtures import write_image
from test_framed_print import _scene, make_prints

SHOTS = os.environ.get("ALBUM_SHOTS")
KINDS = {"grain": {"album_grain": True}, "burns": {"album_burns": True},
         "holes": {"album_burn_holes": True},
         "dust": {"album_dust": True}, "creases": {"album_creases": "photo"}}
ALL_ON = {"album_grain": True, "album_burns": True, "album_burn_holes": True, "album_dust": True,
          "album_creases": "mount"}


def _profile(name="lounge", **settings):
    """Photos only (the visit starts at once), one long photo at a time, Name A-Z."""
    album_profile(name, **{"album_every_turns": 0, "album_photos_per_visit": 1,
                           "album_seconds_per_photo": 600, "album_fade_seconds": 0.2, **settings})


def _open(page, base, name="lounge"):
    page.goto(f"{base}/?display={name}")
    page.wait_for_function("document.getElementById('album').dataset.state === 'visiting'")
    # Overlays never take a tap (pointer-events: none), which also hides them from
    # elementsFromPoint; here they must be found, so the hit test sees them.
    page.add_style_tag(content=".overlay, .overlay-crease, .overlay-crease * "
                               "{ pointer-events: auto !important; }")


# What the shown print's overlays are, where they sit, and what is on top at a
# point in the mount (so "the mount is untouched" is a fact about the screen).
INFO = """() => {
  const a = document.getElementById('album');
  const layer = Array.from(a.querySelectorAll('.album-layer')).find((l) => l.classList.contains('shown'));
  if (!layer || getComputedStyle(layer).opacity !== '1' || getComputedStyle(a).opacity !== '1') return null;
  const img = layer.querySelector('.album-photo');
  if (!img || !img.complete || !img.naturalWidth) return null;
  const rect = (e) => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const win = layer.querySelector('.print-window');
  const one = (sel) => layer.querySelector(sel);
  const crease = one('.overlay-crease');
  const tex = one('.overlay-crease-tex');
  const m = rect(one('.print-mount'));
  const w = rect(win);
  // A point in the mount, half-way between the frame and the photo's left edge.
  const mx = m ? (m.x + w.x) / 2 : null, my = m ? w.y + w.h / 2 : null;
  const stack = m ? document.elementsFromPoint(mx, my).map((e) => e.className && e.className.baseVal === undefined ? e.className : '') : [];
  return {
    id: a.dataset.photo, overlays: win.dataset.overlays || '',
    window: w, mount: m, crease: rect(crease), creaseKey: crease ? crease.dataset.crease : null,
    creaseInWindow: crease ? crease.parentElement === win : null,
    texTransform: tex ? tex.style.transform : null, texBg: tex ? tex.style.backgroundImage : null,
    texOpacity: tex ? Number(getComputedStyle(tex).opacity) : null,
    texBlend: tex ? getComputedStyle(tex).mixBlendMode : null,
    // every overlay part, and the window it is in
    parts: Array.from(layer.querySelectorAll('.overlay')).map((e) => ({
      cls: e.className, inWindow: e.parentElement === win, box: rect(e),
      opacity: Number(getComputedStyle(e).opacity), anim: getComputedStyle(e).animationName,
      transform: getComputedStyle(e).transform, bgPos: e.style.backgroundPosition,
      bg: e.style.backgroundImage, html: e.innerHTML })),
    windowOverflow: getComputedStyle(win).overflow,
    mountStack: stack,
    imgTransform: getComputedStyle(img).transform,
    vw: innerWidth, vh: innerHeight,
  };
}"""


def _settled(page, previous=None):
    page.wait_for_function(f"(prev) => {{ const i = ({INFO})(); return i && i.id !== prev; }}",
                           arg=previous)
    return page.evaluate(INFO)


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"overlay-{name}.png"))


def _kind_of(cls):
    return next(k for k in ("grain", "leak", "burn", "hole", "dust") if f"overlay-{k}" in cls)


def test_an_overlay_on_with_the_mount_untouched(venue, serve, page):
    make_prints()
    _profile(album_grain=True, album_burns=True, album_dust=True)
    base = serve()
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    _open(page, base)
    info = _settled(page)
    _shot(page, "on-mount-untouched")
    # Drawn by the page: nothing is fetched from outside the app.
    assert requests and all(u.startswith((base, "data:")) for u in requests), requests
    assert info["overlays"].split() == ["grain", "burns", "dust"]
    assert info["parts"], "no overlay was drawn"
    # Every overlay lives in the photo's window, which clips it ...
    assert info["windowOverflow"] == "hidden"
    assert all(p["inWindow"] for p in info["parts"]), info["parts"]
    w = info["window"]
    for p in info["parts"]:
        if "overlay-grain" in p["cls"]:
            continue                       # bigger than the window on purpose, clipped by it
        b = p["box"]
        assert (b["x"], b["y"], b["w"], b["h"]) == pytest.approx((w["x"], w["y"], w["w"], w["h"]), abs=1)
    # ... so the mount, beside the photo, has nothing of them on it.
    assert info["mountStack"] and "print-mount" in info["mountStack"]
    assert not any("overlay" in c for c in info["mountStack"]), info["mountStack"]
    assert info["crease"] is None


@pytest.mark.parametrize("kind", [k for k in KINDS if k != "holes"])   # holes: some photos only
def test_each_overlay_kind_switches_on_by_itself(venue, serve, page, kind):
    make_prints()
    _profile(**KINDS[kind])
    base = serve()
    _open(page, base)
    info = _settled(page)
    _shot(page, f"kind-{kind}")
    assert info["overlays"].split() == [kind]
    if kind == "creases":
        assert info["parts"] == [] and info["creaseKey"]
    else:
        assert info["parts"] and info["crease"] is None
    # Nothing moves: grain is a still texture (moving grain read as odd on a photo).
    assert all(p["anim"] == "none" for p in info["parts"])


def test_a_burn_hole_is_on_some_photos_not_all(venue, serve, page):
    # About one photo in three gets a hole (the maintainer), so step through
    # photos until both kinds of photo have been seen: the odds of 30 photos
    # all alike are about one in fifty thousand.
    make_prints()
    _profile(**KINDS["holes"])
    base = serve()
    _open(page, base)
    seen, info = set(), _settled(page)
    for _ in range(30):
        seen.add(info["overlays"])
        if {"", "holes"} <= seen:
            break
        page.keyboard.press("Enter")
        info = _settled(page, info["id"])
    assert seen == {"", "holes"}
    holed = info if info["overlays"] == "holes" else None
    while holed is None:
        page.keyboard.press("Enter")
        info = _settled(page, info["id"])
        holed = info if info["overlays"] == "holes" else None
    _shot(page, "kind-holes")
    # In the photo's window, and its shape is on the photo (not cut off by it).
    hole = page.evaluate("""() => {
      const layer = document.querySelector('.album-layer.shown');
      const win = layer.querySelector('.print-window').getBoundingClientRect();
      const core = layer.querySelector('.overlay-hole polygon:last-of-type').getBoundingClientRect();
      return {inWindow: layer.querySelector('.overlay-hole').parentElement.classList.contains('print-window'),
              core: [core.left, core.top, core.right, core.bottom],
              win: [win.left, win.top, win.right, win.bottom]};
    }""")
    assert hole["inWindow"]
    (l, t, r, b), (wl, wt, wr, wb) = hole["core"], hole["win"]
    assert wl <= l and r <= wr and wt <= t and b <= wb, hole
    # Small: the white-hot core is a few percent of the photo across.
    assert (r - l) < 0.08 * (wr - wl), hole


def test_all_off_draws_no_overlay(venue, serve, page):
    make_prints()
    _profile()
    base = serve()
    _open(page, base)
    info = _settled(page)
    assert (info["overlays"], info["parts"], info["crease"]) == ("", [], None)


@pytest.mark.parametrize("over", ["photo", "mount"])
def test_creases_cross_the_photo_or_the_mount_never_the_frame(venue, serve, page, over):
    make_prints()
    _profile(album_creases=over, album_overlay_strength=100)
    base = serve()
    _open(page, base)
    info = _settled(page)
    _shot(page, f"creases-{over}")
    # "photo": exactly the photo, in its window like the other overlays.
    # "mount": exactly the mount (the space inside the frame), photo included.
    c, m = info["crease"], info["window" if over == "photo" else "mount"]
    assert info["creaseInWindow"] is (over == "photo")
    assert (c["x"], c["y"], c["w"], c["h"]) == pytest.approx((m["x"], m["y"], m["w"], m["h"]), abs=1)
    # Either way it stays inside the mount, so the frame is never crossed.
    mt = info["mount"]
    assert c["x"] >= mt["x"] - 1 and c["y"] >= mt["y"] - 1
    assert c["x"] + c["w"] <= mt["x"] + mt["w"] + 1 and c["y"] + c["h"] <= mt["y"] + mt["h"] + 1
    assert info["texBlend"] == "overlay"
    assert info["creaseKey"] in info["texBg"] and "/static/creases/" in info["texBg"]
    # The turned texture still covers every corner of the box.
    covered = page.evaluate("""() => {
      const layer = Array.from(document.querySelectorAll('.album-layer')).find((l) => l.classList.contains('shown'));
      const r = layer.querySelector('.overlay-crease').getBoundingClientRect();
      return [[r.left + 2, r.top + 2], [r.right - 3, r.top + 2], [r.left + 2, r.bottom - 3],
              [r.right - 3, r.bottom - 3]].map(([x, y]) =>
        document.elementsFromPoint(x, y).some((e) => e.classList && e.classList.contains('overlay-crease-tex')));
    }""")
    assert covered == [True, True, True, True]
    # The texture is one of the recorded CC0 files, and it loads from the app.
    status = page.evaluate("(u) => fetch(u).then((r) => r.status)",
                           f"{base}/static/creases/{info['creaseKey']}.webp")
    assert status == 200


def test_placement_differs_between_photos(venue, serve, page):
    make_prints()
    _profile(**ALL_ON)
    base = serve()
    _open(page, base)
    first = _settled(page)
    page.keyboard.press("Enter")
    second = _settled(page, first["id"])

    def marks(info):
        by = {_kind_of(p["cls"]): p for p in info["parts"]}
        return {"grain": by["grain"]["bgPos"], "leak": by["leak"]["bg"],
                "dust": by["dust"]["html"], "crease": (info["creaseKey"], info["texTransform"]),
                # the hole's shape and place, less its page-unique filter ids
                "hole": re.findall(r'points="([^"]+)"', by["hole"]["html"]) if "hole" in by else None}

    a, b = marks(first), marks(second)
    for kind in a:
        if kind == "hole" and None in (a[kind], b[kind]):
            continue                       # only some photos have one
        assert a[kind] != b[kind], kind


def test_strength_scales_every_overlay(venue, serve, page):
    make_prints()
    _profile("faint", **ALL_ON, album_overlay_strength=10)
    _profile("heavy", **ALL_ON, album_overlay_strength=100)
    base = serve()
    seen = {}
    for name in ("faint", "heavy"):
        _open(page, base, name)
        info = _settled(page)
        _shot(page, f"strength-{name}")
        seen[name] = {**{_kind_of(p["cls"]): p["opacity"] for p in info["parts"]},
                      "crease": info["texOpacity"]}
    # A burnt corner (about two photos in three) and a burn hole (about one in
    # three) are compared only when both photos have one; the rest always show.
    assert {"grain", "leak", "dust", "crease"} <= set(seen["faint"]) & set(seen["heavy"])
    for kind in set(seen["faint"]) & set(seen["heavy"]):
        assert 0 < seen["faint"][kind] < seen["heavy"][kind], kind


def test_the_slow_zoom_moves_the_photo_under_still_overlays(venue, serve, page):
    make_prints()
    _profile(album_grain=True, album_burns=True, album_dust=True, album_creases="photo",
             album_ken_burns=True)
    base = serve()
    _open(page, base)
    start = _settled(page)["imgTransform"]
    page.wait_for_function(f"(prev) => {{ const i = ({INFO})(); return i && i.imgTransform !== 'none' "
                           f"&& i.imgTransform !== prev; }}", arg=start)
    info = page.evaluate(INFO)
    assert info["overlays"].split() == ["grain", "burns", "dust", "creases"]
    assert all(p["transform"] == "none" for p in info["parts"]), info["parts"]
    # A crease on the photo is in its window too, and still.
    still = page.evaluate("""() => getComputedStyle(document.querySelector(
      '.album-layer.shown .print-window > .overlay-crease')).transform""")
    assert still == "none"


def test_each_print_of_a_spread_gets_its_own_overlays(venue, serve, page):
    for name, sky in (("a-left", ((180, 90, 60), (240, 190, 120))),
                      ("b-right", ((60, 90, 180), (200, 220, 240)))):
        write_image(paths.PHOTOS_DIR / f"{name}.jpg", _scene((900, 1200), sky, (70, 60, 40), (255, 240, 200)))
    lib.scan()
    lib.process_pending()
    _profile(album_photos_per_visit=2, album_burns=True, album_dust=True, album_creases="mount")
    base = serve()
    _open(page, base)
    page.wait_for_function("""() => {
      const layer = document.querySelector('.album-layer.shown');
      return layer && layer.querySelectorAll('.album-cell').length === 2 &&
        getComputedStyle(layer).opacity === '1' &&
        getComputedStyle(document.getElementById('album')).opacity === '1';
    }""")
    _shot(page, "spread")
    cells = page.evaluate("""() => Array.from(document.querySelectorAll('.album-layer.shown .album-cell')).map((c) => ({
      overlays: c.querySelector('.print-window').dataset.overlays,
      leak: (c.querySelector('.overlay-leak') || {style: {}}).style.backgroundImage,
      crease: !!c.querySelector('.overlay-crease'),
      filters: Array.from(c.querySelectorAll('.overlay-dust filter')).map((f) => f.id),
      inCell: Array.from(c.querySelectorAll('.overlay')).every((e) => e.parentElement.classList.contains('print-window')),
    }))""")
    assert [c["overlays"] for c in cells] == ["burns dust creases"] * 2
    assert all(c["crease"] and c["inCell"] for c in cells)
    assert cells[0]["leak"] != cells[1]["leak"]
    # Each print's floaties blur through their own filter, never another print's.
    ids = [i for c in cells for i in c["filters"]]
    assert len(ids) == 2 and len(set(ids)) == 2
