"""Portraits side by side, captions and the slow zoom on the real display page (issue #62).

DOM state and geometry only, never pixels. Photos are ordered Name A-Z, so each test
knows exactly which photo comes next. Screenshots are kept when `ALBUM_SHOTS` names a
directory, as for the framed print.
"""
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import paths, photo_library as lib

from album_support import RECORDER, album_profile
from photo_fixtures import exif_bytes, write_image

SHOTS = os.environ.get("ALBUM_SHOTS")
PORTRAIT, LANDSCAPE = (900, 1200), (1600, 1000)


def make(names, **exif):
    """Photos named like 'a1' (portrait) or 'b2l' (a trailing l: landscape), processed.

    Each gets its own colour so the Display copies differ. `exif` maps a name to a
    date taken ('YYYY:MM:DD HH:MM:SS').
    """
    for i, name in enumerate(names):
        size = LANDSCAPE if name.endswith("l") else PORTRAIT
        im = Image.new("RGB", size, (40 + 30 * (i % 6), 90 + 20 * (i % 5), 200 - 25 * (i % 7)))
        ImageDraw.Draw(im).ellipse([size[0] * .3, size[1] * .3, size[0] * .7, size[1] * .6],
                                   fill=(250, 230, 160))
        kwargs = {"exif": exif_bytes(taken=exif[name])} if name in exif else {}
        write_image(paths.PHOTOS_DIR / "Events" / f"{name}.jpg", im, **kwargs)
    lib.scan()
    lib.process_pending()
    assert len(lib.ready_photos()) == len(names)


def photo_ids():
    """Photo id by name, in the board's (Name A-Z) order."""
    return {p.name.rsplit(".", 1)[0]: p.photo_id for p in lib.ready_photos()}


def visit(**settings):
    """A visit of K photos that starts at once (photos only), one spread at a time."""
    plan = {"album_every_turns": 0, "album_photos_per_visit": 6, "album_seconds_per_photo": 600,
            "album_fade_seconds": 0.2, "album_order": "name", **settings}
    album_profile(**plan)


def open_visit(page, base):
    page.goto(f"{base}/?display=lounge")
    page.wait_for_function("document.getElementById('album').dataset.state === 'visiting'")


PRINTS = """() => {
  const a = document.getElementById('album');
  const layer = Array.from(a.querySelectorAll('.album-layer')).find((l) => l.classList.contains('shown'));
  if (!layer || getComputedStyle(layer).opacity !== '1' || getComputedStyle(a).opacity !== '1') return null;
  const hosts = layer.querySelectorAll(':scope > .album-cell');
  const list = hosts.length ? Array.from(hosts) : [layer];
  const box = (e) => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const prints = list.map((h) => {
    const img = h.querySelector('.album-photo');
    const cap = h.querySelector('.print-caption');
    return {
      loaded: !!img && img.complete && img.naturalWidth > 0,
      cell: hosts.length ? box(h) : null, window: box(h.querySelector('.print-window')),
      mount: box(h.querySelector('.print-mount')), frame: h.dataset.frame, style: h.dataset.style,
      art: box(h.querySelector('.frame-art')), caption: cap ? cap.textContent : null,
      captionBox: box(cap), captionShown: cap ? getComputedStyle(cap).display !== 'none' : false,
      src: img && img.getAttribute('src'), transform: img ? getComputedStyle(img).transform : null,
      windowOverflow: getComputedStyle(h.querySelector('.print-window')).overflow,
    };
  });
  if (!prints.every((p) => p.loaded)) return null;
  return {ids: (a.dataset.photo || '').split(','), count: layer.dataset.count, prints,
          vw: innerWidth, vh: innerHeight};
}"""


def on_screen(page, previous=None):
    """The prints on screen once faded in (a new set when `previous` ids are given)."""
    page.wait_for_function(
        f"(prev) => {{ const i = ({PRINTS})(); return i && (!prev || i.ids.join() !== prev); }}",
        arg=previous)
    return page.evaluate(PRINTS)


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"spread-{name}.png"))


# ---- two-up and three-up --------------------------------------------------------------

def test_two_portraits_show_two_up_each_in_its_own_mount_and_frame(venue, serve, page):
    make(["a1", "a2", "a3"])
    visit(album_frame="oak")
    ids = photo_ids()
    open_visit(page, serve())
    info = on_screen(page)
    shot(page, "two-up")
    assert info["ids"] == [ids["a1"], ids["a2"]] and info["count"] == "2"
    left, right = info["prints"]
    for p in info["prints"]:
        assert p["mount"] and p["window"] and p["frame"] == "oak" and p["style"] == "flat"
        # The photo sits inside its own mount, whole and in its own shape.
        assert p["mount"]["x"] <= p["window"]["x"] and p["window"]["x"] + p["window"]["w"] <= p["mount"]["x"] + p["mount"]["w"]
        assert p["window"]["w"] / p["window"]["h"] == pytest.approx(0.75, abs=0.01)
        assert p["cell"]["h"] == info["vh"]
    # Side by side with no overlap, filling the screen's width between them.
    assert left["cell"]["x"] == 0 and right["cell"]["x"] >= left["cell"]["x"] + left["cell"]["w"]
    assert right["cell"]["x"] + right["cell"]["w"] == pytest.approx(info["vw"], abs=1)
    assert left["src"] != right["src"]


def test_two_up_counts_each_photo_towards_the_visit(venue, serve, page):
    """K = 3 over a1 a2 / b1l / c1: the spread is two photos, the landscape the third."""
    make(["a1", "a2", "b1l", "c1"])
    visit(album_every_turns=1, album_photos_per_visit=3, album_seconds_per_photo=2)
    ids = photo_ids()
    base = serve()
    page.goto(f"{base}/?display=lounge")
    page.evaluate(RECORDER)
    page.wait_for_function("document.querySelectorAll('.page').length > 0")
    page.wait_for_function("() => window.__log && window.__log.some((e) => e.visiting)", timeout=30_000)
    page.wait_for_function("() => window.__log.some((e) => e.visiting) && "
                           "!document.body.classList.contains('album-visit')", timeout=30_000)
    seen = []
    for entry in page.evaluate("window.__log"):
        if entry["photo"] and entry["photo"] not in seen:
            seen.append(entry["photo"])
    assert seen == [f"{ids['a1']},{ids['a2']}", ids["b1l"]]      # c1 waits for the next visit


def test_a_visit_with_one_photo_left_shows_a_lone_portrait(venue, serve, page):
    make(["a1", "a2"])
    visit(album_every_turns=1, album_photos_per_visit=1, album_seconds_per_photo=600)
    open_visit(page, serve())
    info = on_screen(page)
    assert info["count"] == "1" and len(info["prints"]) == 1


def test_three_up_shows_three_portraits(venue, serve, page):
    make(["a1", "a2", "a3", "a4"])
    visit(album_three_up=True)
    ids = photo_ids()
    open_visit(page, serve())
    info = on_screen(page)
    shot(page, "three-up")
    assert info["ids"] == [ids["a1"], ids["a2"], ids["a3"]] and info["count"] == "3"
    xs = [p["cell"]["x"] for p in info["prints"]]
    assert xs == sorted(xs) and len(set(xs)) == 3
    for a, b in zip(info["prints"], info["prints"][1:]):
        assert a["cell"]["x"] + a["cell"]["w"] <= b["cell"]["x"]


def test_three_up_with_two_left_shows_two(venue, serve, page):
    make(["a1", "a2"])
    visit(album_three_up=True)
    open_visit(page, serve())
    assert on_screen(page)["count"] == "2"


def test_pairing_off_shows_one_photo_at_a_time(venue, serve, page):
    make(["a1", "a2", "a3"])
    visit(album_pair_portraits=False, album_three_up=True)
    ids = photo_ids()
    base = serve()
    open_visit(page, base)
    first = on_screen(page)
    assert first["ids"] == [ids["a1"]] and first["count"] == "1"
    page.keyboard.press("Enter")
    assert on_screen(page, ids["a1"])["ids"] == [ids["a2"]]


def test_a_lone_portrait_gets_wider_sides_than_top_and_bottom(venue, serve, page):
    make(["a1"])
    visit()
    open_visit(page, serve())
    info = on_screen(page)
    shot(page, "lone-portrait")
    p = info["prints"][0]
    side = p["window"]["x"] - p["mount"]["x"]
    top = p["window"]["y"] - p["mount"]["y"]
    assert side > 2 * top and top >= 0.05 * min(info["vw"], info["vh"])


def test_landscape_photos_and_a_lack_of_partners_stay_single(venue, serve, page):
    """a1 then a landscape: no neighbour partner in Name order, so a1 is alone."""
    make(["a1", "b1l", "c1", "d1l"])
    visit()
    ids = photo_ids()
    open_visit(page, serve())
    first = on_screen(page)
    assert first["ids"] == [ids["a1"]]
    page.keyboard.press("Enter")
    second = on_screen(page, ids["a1"])
    assert second["ids"] == [ids["b1l"]]
    page.keyboard.press("Enter")
    assert on_screen(page, ids["b1l"])["ids"] == [ids["c1"]]


def test_an_advance_moves_on_by_the_whole_spread(venue, serve, page):
    make(["a1", "a2", "a3", "a4", "a5"])
    visit()
    ids = photo_ids()
    open_visit(page, serve())
    first = on_screen(page)
    assert first["ids"] == [ids["a1"], ids["a2"]]
    page.keyboard.press("Enter")
    second = on_screen(page, ",".join(first["ids"]))
    assert second["ids"] == [ids["a3"], ids["a4"]]
    page.mouse.click(640, 360)
    third = on_screen(page, ",".join(second["ids"]))
    assert third["ids"] == [ids["a5"], ids["a1"]]       # the list starts over, as it does for one at a time


def test_a_portrait_screen_does_not_pair(venue, serve, page):
    make(["a1", "a2"])
    visit()
    page.set_viewport_size({"width": 720, "height": 1280})
    open_visit(page, serve())
    assert on_screen(page)["count"] == "1"


def test_shuffle_pairs_portraits_it_finds_later_in_the_pass(venue, serve, page):
    """Landscapes between the portraits do not stop shuffle pairing them, and no photo
    comes round twice in one pass."""
    make(["a1", "b1l", "c1", "d1l", "e1", "f1l"])
    visit(album_order="shuffle", album_seconds_per_photo=2, album_fade_seconds=0.2,
          album_photos_per_visit=50)
    ids = photo_ids()
    portraits = {ids["a1"], ids["c1"], ids["e1"]}
    base = serve()
    page.goto(f"{base}/?display=lounge")
    page.evaluate(RECORDER)
    page.wait_for_function("window.__log && window.__log.some((e) => e.visiting)")
    shown = []
    for _ in range(4):                       # advance by hand until the pass is spent
        info = on_screen(page, shown[-1] if shown else None)
        shown.append(",".join(info["ids"]))
        page.keyboard.press("Enter")
    flat = [i for group in shown for i in group.split(",")]
    first_pass = flat[:6]
    assert len(set(first_pass)) == len(first_pass)            # no photo twice in a pass
    pairs = [g.split(",") for g in shown if "," in g]
    assert pairs and all(set(g) <= portraits for g in pairs)


def test_a_kept_ticker_sits_in_a_band_under_a_spread(venue, serve, page):
    make(["a1", "a2"])
    visit(album_keep_ticker=True, album_frame="oak")
    open_visit(page, serve())
    info = on_screen(page)
    page.wait_for_function("() => { const t = document.querySelector('.album-ticker');"
                           " return t && t.getBoundingClientRect().width >= innerWidth - 1; }")
    ticker = page.evaluate("""() => { const t = document.querySelector('.album-ticker');
        const r = t.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height,
        text: t.innerText}; }""")
    assert "Ticker text" in ticker["text"]
    assert ticker["x"] == 0 and ticker["w"] == pytest.approx(info["vw"], abs=1)
    assert ticker["y"] + ticker["h"] == pytest.approx(info["vh"], abs=1)
    for p in info["prints"]:
        assert p["cell"]["y"] + p["cell"]["h"] <= ticker["y"] + 1     # the prints stop above it


# ---- captions ------------------------------------------------------------------------------

def test_captions_are_off_by_default(venue, serve, page):
    make(["a1"], a1="2025:03:04 05:06:07")
    visit()
    open_visit(page, serve())
    assert on_screen(page)["prints"][0]["caption"] is None


@pytest.mark.parametrize("settings, text", [
    ({"album_caption_date": True}, "4 Mar 2025"),
    ({"album_caption_album": True}, "Events"),
    ({"album_caption_date": True, "album_caption_album": True}, "4 Mar 2025  ·  Events"),
])
def test_captions_render_only_when_switched_on(venue, serve, page, settings, text):
    make(["a1"], a1="2025:03:04 05:06:07")
    visit(**settings)
    open_visit(page, serve())
    info = on_screen(page)
    shot(page, "caption-" + str(len(settings)) + text[:3].strip())
    p = info["prints"][0]
    assert p["caption"] == text and p["captionShown"]
    assert "a1" not in p["caption"] and ".jpg" not in p["caption"]        # never a file name
    # On the mount's bottom edge: under the photo, inside the mount.
    c, w, m = p["captionBox"], p["window"], p["mount"]
    assert c["y"] >= w["y"] + w["h"] and c["y"] + c["h"] <= m["y"] + m["h"] + 1
    assert c["x"] >= m["x"] and c["x"] + c["w"] <= m["x"] + m["w"]


def test_a_photo_with_no_exif_date_is_captioned_with_its_file_date(venue, serve, page):
    make(["a1"])
    visit(album_caption_date=True)
    open_visit(page, serve())
    text = on_screen(page)["prints"][0]["caption"]
    assert text and text[0].isdigit() and text[-4:].isdigit()


def test_every_photo_of_a_spread_is_captioned_on_its_own_mount(venue, serve, page):
    make(["a1", "a2"], a1="2024:01:09 10:00:00", a2="2023:12:25 10:00:00")
    visit(album_caption_date=True)
    open_visit(page, serve())
    info = on_screen(page)
    shot(page, "two-up-captions")
    assert [p["caption"] for p in info["prints"]] == ["9 Jan 2024", "25 Dec 2023"]
    for p in info["prints"]:
        assert p["captionBox"]["x"] >= p["cell"]["x"] and \
            p["captionBox"]["x"] + p["captionBox"]["w"] <= p["cell"]["x"] + p["cell"]["w"]


def test_a_no_mount_photo_has_no_caption(venue, serve, page):
    make(["a1"], a1="2025:03:04 05:06:07")
    visit(album_mount_style="none", album_caption_date=True, album_caption_album=True)
    open_visit(page, serve())
    assert on_screen(page)["prints"][0]["caption"] is None


# ---- the slow zoom -------------------------------------------------------------------------

def test_the_photo_stays_still_with_the_slow_zoom_off(venue, serve, page):
    make(["a1"])
    visit()
    open_visit(page, serve())
    first = on_screen(page)["prints"][0]
    page.wait_for_timeout(1200)
    assert page.evaluate(PRINTS)["prints"][0]["transform"] == first["transform"] == "none"


def test_the_slow_zoom_moves_the_photo_inside_its_mount_only(venue, serve, page):
    make(["a1", "a2"])
    visit(album_ken_burns=True, album_frame="real-oak-limed", album_seconds_per_photo=40)
    open_visit(page, serve())
    before = on_screen(page)
    page.wait_for_function(f"() => ({PRINTS})().prints.every((p) => p.transform !== 'none')")
    after = page.evaluate(PRINTS)
    shot(page, "slow-zoom")
    for b, a in zip(before["prints"], after["prints"]):
        assert a["transform"] != b["transform"] and a["transform"] != "none"   # the photo moves
        assert a["windowOverflow"] == "hidden"                                 # inside its window
        # The mount, window, frame and cell never move.
        for key in ("mount", "window", "art", "cell"):
            assert a[key] == b[key], key
        assert a["frame"] == b["frame"] and a["style"] == b["style"]


def test_the_slow_zoom_keeps_the_photo_covering_its_window(venue, serve, page):
    make(["a1"])
    visit(album_ken_burns=True, album_seconds_per_photo=10)
    open_visit(page, serve())
    on_screen(page)
    page.wait_for_function("() => getComputedStyle(document.querySelector("
                           "'.album-layer.shown .album-photo')).transform !== 'none'")
    ok = page.evaluate("""() => {
        const layer = document.querySelector('.album-layer.shown');
        const win = layer.querySelector('.print-window').getBoundingClientRect();
        const img = layer.querySelector('.album-photo').getBoundingClientRect();
        return img.left <= win.left + 0.5 && img.top <= win.top + 0.5 &&
               img.right >= win.right - 0.5 && img.bottom >= win.bottom - 0.5; }""")
    assert ok


def test_three_up_captions_still_fit_on_each_mount(venue, serve, page):
    make(["a1", "a2", "a3"], a1="2025:03:04 05:06:07", a2="2025:03:05 05:06:07",
         a3="2025:03:06 05:06:07")
    visit(album_three_up=True, album_caption_date=True, album_caption_album=True)
    open_visit(page, serve())
    info = on_screen(page)
    assert info["count"] == "3"
    assert all(p["caption"] and p["captionShown"] for p in info["prints"])
