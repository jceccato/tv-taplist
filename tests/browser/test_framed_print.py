"""The framed print on the real display page (issue #61).

Each photo of an Album visit is drawn as a framed print: a frame at the screen's
edges, a mount inside it, and the photo fitted inside the mount. These tests open
the display as a kiosk would and assert DOM state and geometry - which parts are
drawn, where the photo window sits, which colour the mount is - never pixels.
Screenshots are kept as artefacts when `ALBUM_SHOTS` names a directory, so the
look can be judged by eye.
"""
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import config_store, paths, photo_library as lib

from album_support import album_profile
from photo_fixtures import write_image

SHOTS = os.environ.get("ALBUM_SHOTS")


def _scene(size, sky, ground, sun):
    """A small photo-like picture: a sky gradient, a hill and a sun."""
    w, h = size
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(*sky)))
    d.ellipse([w * 0.65, h * 0.15, w * 0.8, h * 0.15 + w * 0.15], fill=sun)
    d.polygon([(0, h * 0.7), (w * 0.3, h * 0.55), (w * 0.7, h * 0.68), (w, h * 0.6), (w, h), (0, h)],
              fill=ground)
    return im


def make_prints():
    """A landscape and a portrait (Name A-Z: the landscape comes first), processed."""
    write_image(paths.PHOTOS_DIR / "a-landscape.jpg",
                _scene((1600, 1000), ((70, 120, 190), (220, 200, 170)), (60, 110, 50), (250, 220, 120)))
    write_image(paths.PHOTOS_DIR / "b-portrait.jpg",
                _scene((900, 1200), ((180, 90, 60), (240, 190, 120)), (90, 60, 40), (255, 240, 200)))
    lib.scan()
    lib.process_pending()
    assert len(lib.ready_photos()) == 2


def _frame_profile(**settings):
    """Photos only (the visit starts at once), one long photo at a time, Name A-Z."""
    album_profile(album_every_turns=0, album_photos_per_visit=1, album_seconds_per_photo=600,
                  album_fade_seconds=0.2, **settings)


def _open(page, base):
    page.goto(f"{base}/?display=lounge")
    page.wait_for_function("document.getElementById('album').dataset.state === 'visiting'")


INFO = """() => {
  const a = document.getElementById('album');
  const layer = Array.from(a.querySelectorAll('.album-layer')).find((l) => l.classList.contains('shown'));
  if (!layer || getComputedStyle(layer).opacity !== '1' || getComputedStyle(a).opacity !== '1') return null;
  const img = layer.querySelector('.album-photo');
  if (!img || !img.complete || !img.naturalWidth) return null;
  const rect = (sel) => { const e = layer.querySelector(sel); if (!e) return null;
    const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const fill = layer.querySelector('.print-fill-img');
  const mount = layer.querySelector('.print-mount');
  return {
    id: a.dataset.photo, style: layer.dataset.style, color: layer.dataset.color,
    frame: layer.dataset.frame, mount: layer.dataset.mount || null,
    window: rect('.print-window'), photo: rect('.album-photo'), mountBox: rect('.print-mount'),
    mat: rect('.print-mat'), fillBox: rect('.print-fill'),
    fillSrc: fill ? fill.getAttribute('src') : null, src: img.getAttribute('src'),
    fillFilter: fill ? getComputedStyle(fill).filter : null,
    mountBg: mount ? getComputedStyle(mount).backgroundColor : null,
    mountShadow: mount ? getComputedStyle(mount).boxShadow : null,
    windowShadow: getComputedStyle(layer.querySelector('.print-window')).boxShadow,
    sides: Array.from(layer.querySelectorAll('.frame-side')).map((s) => {
      const r = s.getBoundingClientRect();
      return {cls: s.className, x: r.x, y: r.y, w: r.width, h: r.height,
              bg: getComputedStyle(s).backgroundImage};
    }),
    vw: innerWidth, vh: innerHeight,
  };
}"""


def _settled(page, previous=None):
    """The print on screen once its fade is over (a new photo when `previous` is given)."""
    page.wait_for_function(f"(prev) => {{ const i = ({INFO})(); return i && i.id !== prev; }}",
                           arg=previous)
    return page.evaluate(INFO)


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"print-{name}.png"))


def _rgb(hex_colour):
    return "rgb({}, {}, {})".format(*(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)))


def _board_photo(base, page, name):
    board = page.evaluate("(u) => fetch(u).then((r) => r.json())", f"{base}/api/board?display=lounge")
    return next(p for p in board["album"]["photos"] if p["id"] == name)


def test_a_flat_mounted_landscape_photo(venue, serve, page):
    make_prints()
    _frame_profile()                       # the defaults: Flat, From photo, thin black
    base = serve()
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    _open(page, base)
    info = _settled(page)
    # Drawn by the page itself: every request is to the app (textures are data: URIs).
    assert requests and all(u.startswith((base, "data:")) for u in requests), requests
    _shot(page, "flat-landscape")
    S = min(info["vw"], info["vh"])

    assert (info["style"], info["color"], info["frame"]) == ("flat", "photo", "thin-black")
    # The mount is the photo's own sampled colour, as the board carries it.
    photo = _board_photo(base, page, info["id"])
    assert info["mount"] == photo["mount"] and info["mountBg"] == _rgb(photo["mount"])
    assert photo["aspect"] == pytest.approx(1.6, abs=0.01)

    # A frame on all four sides at the screen's edges; the mount fills inside it.
    assert len(info["sides"]) == 4
    frame = round(0.014 * S)
    m = info["mountBox"]
    assert (m["x"], m["y"]) == (frame, frame)
    assert (m["w"], m["h"]) == (info["vw"] - 2 * frame, info["vh"] - 2 * frame)
    # The photo keeps its shape, is centred, and has at least ~10 % of the short
    # side of mount all round (the binding side gets exactly that, plus the bevel).
    w = info["window"]
    assert w["w"] / w["h"] == pytest.approx(1.6, abs=0.01)
    assert w["x"] + w["w"] / 2 == pytest.approx(info["vw"] / 2, abs=1)
    assert w["y"] + w["h"] / 2 == pytest.approx(info["vh"] / 2, abs=1)
    margins = [w["x"] - m["x"], w["y"] - m["y"],
               m["x"] + m["w"] - (w["x"] + w["w"]), m["y"] + m["h"] - (w["y"] + w["h"])]
    # ~6 % of the short side (the spec's 10 % was too much on a real TV).
    assert min(margins) >= 0.06 * S
    assert min(margins) <= 0.06 * S + 0.005 * S + 2
    # The photo fills its window; there is a bevel; nothing is blurred behind it.
    assert info["photo"] == w
    assert info["windowShadow"] != "none"
    assert info["fillBox"] is None


def test_a_no_mount_photo_fills_the_frame_with_a_blurred_copy_behind_it(venue, serve, page):
    make_prints()
    _frame_profile(album_mount_style="none", album_frame="none")
    _open(page, serve())
    info = _settled(page)                  # Name A-Z: the landscape, then the portrait
    page.keyboard.press("Enter")
    info = _settled(page, info["id"])
    assert info["id"] == next(p.photo_id for p in lib.ready_photos() if p.name == "b-portrait.jpg")
    _shot(page, "no-mount-portrait")

    assert (info["style"], info["frame"], info["mount"]) == ("none", "none", None)
    assert info["sides"] == [] and info["mountBox"] is None
    # The portrait fills the screen's height, centred, keeping its shape.
    w = info["window"]
    assert (w["y"], w["h"]) == (0, info["vh"])
    assert w["w"] / w["h"] == pytest.approx(0.75, abs=0.01)
    assert w["x"] + w["w"] / 2 == pytest.approx(info["vw"] / 2, abs=1)
    # The edges it leaves are the same photo, blurred and darkened, over the whole screen.
    f = info["fillBox"]
    assert (f["x"], f["y"], f["w"], f["h"]) == (0, 0, info["vw"], info["vh"])
    assert info["fillSrc"] == info["src"]
    assert "blur(" in info["fillFilter"] and "brightness(" in info["fillFilter"]


@pytest.mark.parametrize("style", ["none", "flat", "double", "shadow", "float"])
def test_every_mount_style_renders(venue, serve, page, style):
    make_prints()
    _frame_profile(album_mount_style=style)
    _open(page, serve())
    info = _settled(page)
    _shot(page, f"style-{style}")
    assert info["style"] == style and len(info["sides"]) == 4
    assert (info["mountBox"] is None) == (style == "none")
    assert (info["fillBox"] is None) == (style != "none")
    assert (info["mat"] is not None) == (style == "double")
    if style == "double":                  # the second mat shows a reveal round the window
        assert info["mat"]["x"] < info["window"]["x"] and info["mat"]["w"] > info["window"]["w"]
    if style == "float":                   # a wider mount than Flat, and a cast shadow
        S = min(info["vw"], info["vh"])
        assert info["window"]["y"] - info["mountBox"]["y"] >= 0.075 * S - 1
    if style != "none":
        assert info["windowShadow"] != "none"
    if style == "shadow":                  # a deep box, unlike Flat: no bevel ring, heavy walls
        assert "rgb(247, 245, 239)" not in info["windowShadow"]
        assert info["mountShadow"].count("inset") >= 3
    if style == "flat":
        assert "rgb(247, 245, 239)" in info["windowShadow"]


@pytest.mark.parametrize("color, expected", [
    ("white", "#f3f2ee"), ("cream", "#ebe3cf"), ("black", "#1c1c1c"), ("custom", "#336699")])
def test_every_mount_colour_renders(venue, serve, page, color, expected):
    make_prints()
    _frame_profile(album_mount_color=color, album_mount_custom="#336699")
    _open(page, serve())
    info = _settled(page)
    _shot(page, f"colour-{color}")
    assert info["color"] == color and info["mount"] == expected
    assert info["mountBg"] == _rgb(expected)


@pytest.mark.parametrize("frame", ["none", "thin-black", "thin-white", "oak", "walnut",
                                   "gilded", "polaroid"])
def test_every_frame_renders(venue, serve, page, frame):
    make_prints()
    _frame_profile(album_frame=frame)
    _open(page, serve())
    info = _settled(page)
    _shot(page, f"frame-{frame}")
    assert info["frame"] == frame
    if frame == "none":
        assert info["sides"] == [] and info["mountBox"]["x"] == 0
        return
    assert len(info["sides"]) == 4
    assert all(f"frame-{frame}" in s["cls"] and s["bg"] != "none" for s in info["sides"])
    top, right, bottom, left = info["sides"]
    if frame == "polaroid":                # the wide bottom edge
        assert bottom["h"] > 3 * top["h"]
    else:
        assert bottom["h"] == top["h"] == left["w"] == right["w"]
    if frame in ("oak", "walnut"):         # wood grain, drawn inline (no fetch)
        assert all("data:image/svg+xml" in s["bg"] for s in info["sides"])


def test_random_picks_per_photo_from_the_real_choices(venue, serve, page):
    make_prints()
    _frame_profile(album_mount_style="random", album_mount_color="random", album_frame="random")
    _open(page, serve())
    seen = []
    info = _settled(page)
    for i in range(12):
        seen.append((info["style"], info["color"], info["frame"]))
        if i == 0:
            _shot(page, "random")
        page.keyboard.press("Enter")
        info = _settled(page, info["id"])
    styles, colors, frames = (set(x) for x in zip(*seen))
    assert styles <= set(config_store.MOUNT_STYLES) - {"random"} and len(styles) > 1
    assert colors <= (set(config_store.MOUNT_COLORS) - {"random"}) | {"none"} and len(colors) > 1
    assert frames <= set(config_store.FRAMES) - {"random"} and len(frames) > 1


def test_a_resize_lays_the_print_out_again(venue, serve, page):
    make_prints()
    _frame_profile()
    _open(page, serve())
    before = _settled(page)
    page.set_viewport_size({"width": 800, "height": 800})
    page.wait_for_function("() => document.querySelector('.album-layer.shown .print-mount')"
                           ".getBoundingClientRect().width < 800")
    after = page.evaluate(INFO)
    assert after["id"] == before["id"] and after["style"] == before["style"]
    assert after["window"]["w"] / after["window"]["h"] == pytest.approx(1.6, abs=0.01)
    assert after["window"]["x"] - after["mountBox"]["x"] >= 0.06 * 800
