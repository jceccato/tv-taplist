"""The Album visit on the real display page (issue #56, ADR-0008).

Each test prepares a venue and fixture photos, serves them, opens the display
the way a kiosk would (`/?display=<profile>`) and asserts DOM state and
geometry from a recorded timeline. Screenshots are kept as artefacts when
`ALBUM_SHOTS` names a directory; nothing is pixel-diffed.
"""
import os
import time
from pathlib import Path

import pytest

from app import config_store

from album_support import RECORDER, album_profile, make_photos

SHOTS = os.environ.get("ALBUM_SHOTS")


def _open(page, base, display="lounge"):
    page.add_init_script(RECORDER)
    page.goto(f"{base}/?display={display}" if display else f"{base}/")
    page.wait_for_selector(".card")
    page.wait_for_function("window.__log && window.__log.length")


def _log(page):
    return page.evaluate("window.__log")


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _wait_state(page, state, timeout=30_000):
    page.wait_for_function(
        "s => document.getElementById('album').dataset.state === s", arg=state, timeout=timeout)


def test_a_visit_shows_k_photos_then_returns_to_the_taps_with_the_header_hidden(venue, serve, page):
    make_photos()
    album_profile(album_photos_per_visit=2, album_seconds_per_photo=2)
    _open(page, serve())

    # Before the visit the venue chrome is there.
    first = _log(page)[-1]
    assert first["header"] and first["ticker"] and first["dots"] and not first["visiting"]

    _wait_state(page, "visiting")
    page.wait_for_function("document.getElementById('album').dataset.photo")
    _shot(page, "visit")
    assert page.is_hidden("#venue-header") and page.is_hidden("#dots") and page.is_hidden("#ticker")
    assert not page.query_selector(".album-ticker")       # not kept: no copy either
    _wait_state(page, "idle")
    page.wait_for_function("document.getElementById('album').hidden")
    _shot(page, "taps-again")

    log = _log(page)
    visiting = [e for e in log if e["visiting"]]
    assert visiting, "a visit never started"
    # Header, ticker and dots were hidden for the whole visit, and back after it.
    assert not any(e["header"] or e["ticker"] or e["dots"] for e in visiting)
    after = [e for e in log if not e["visiting"] and e["t"] > visiting[-1]["t"]]
    assert after and after[-1]["header"] and after[-1]["ticker"] and after[-1]["dots"]
    # Exactly K photos, each different (the order is Name A-Z), then the taps.
    shown = []
    for e in visiting:
        if e["photo"] and (not shown or shown[-1] != e["photo"]):
            shown.append(e["photo"])
    assert len(shown) == 2 and len(set(shown)) == 2


def test_a_screen_without_the_album_never_has_a_visit(venue, serve, page):
    make_photos()
    album_profile()
    from app import admin_ops
    admin_ops.create_profile("tablet")
    _open(page, serve(), display="tablet")
    page.wait_for_timeout(8000)       # well past the first page turns and any visit
    log = _log(page)
    assert not any(e["visiting"] for e in log)
    assert page.evaluate("document.getElementById('album').hidden")
    assert max(e["page"] for e in log) == 1       # the taps carried on rotating


def test_no_ready_photos_means_taps_only(venue, serve, page):
    album_profile()                     # the album is on, but the folder is empty
    _open(page, serve())
    page.wait_for_timeout(7500)
    assert not any(e["visiting"] for e in _log(page))


def test_with_no_mount_and_no_frame_photos_fill_the_screen_whatever_their_shape(venue, serve, page):
    make_photos()
    album_profile(album_photos_per_visit=3, album_seconds_per_photo=3, album_fade_seconds=0.2,
                  album_mount_style="none", album_frame="none")
    _open(page, serve())
    seen = {}
    deadline = time.time() + 40
    while time.time() < deadline and len(seen) < 3:
        page.wait_for_function("document.getElementById('album').dataset.photo", timeout=30_000)
        info = page.evaluate("""() => {
            const a = document.getElementById('album');
            const layer = Array.from(a.querySelectorAll('.album-layer')).find((l) => l.classList.contains('shown'));
            const img = layer && layer.querySelector('.album-photo');
            if (!img || !img.naturalWidth) return null;
            const r = img.getBoundingClientRect();
            return {id: a.dataset.photo, w: r.width, h: r.height, x: r.x, y: r.y,
                    nw: img.naturalWidth, nh: img.naturalHeight, vw: innerWidth, vh: innerHeight};
        }""")
        if info:
            seen[info["id"]] = info
        page.wait_for_timeout(300)
    assert len(seen) == 3
    for info in seen.values():
        # Fitted whole (never cropped), touching two opposite edges, centred.
        assert info["w"] / info["h"] == pytest.approx(info["nw"] / info["nh"], abs=0.01)
        assert info["w"] == info["vw"] or info["h"] == info["vh"]
        assert info["x"] + info["w"] / 2 == pytest.approx(info["vw"] / 2, abs=1)
        assert info["y"] + info["h"] / 2 == pytest.approx(info["vh"] / 2, abs=1)
        # The Display copy is fitted to 4K, so no photo is left to the browser to upscale.
        assert info["nw"] <= 3840 and info["nh"] <= 2160
        assert info["nw"] == 3840 or info["nh"] == 2160
    shapes = sorted(round(i["nw"] / i["nh"], 2) for i in seen.values())
    assert shapes == [0.75, 1.0, 1.6]


def test_enter_space_and_click_advance_a_photo_and_the_last_one_returns_to_the_taps(venue, serve, page):
    make_photos()
    album_profile(album_photos_per_visit=3, album_seconds_per_photo=120, album_fade_seconds=0.2)
    _open(page, serve())
    _wait_state(page, "visiting")
    page.wait_for_function("document.getElementById('album').dataset.photo")
    ids = [page.evaluate("document.getElementById('album').dataset.photo")]

    for key in ("Enter", "Space"):
        page.keyboard.press(key if key == "Enter" else " ")
        page.wait_for_function("old => document.getElementById('album').dataset.photo !== old", arg=ids[-1])
        ids.append(page.evaluate("document.getElementById('album').dataset.photo"))
    assert len(set(ids)) == 3

    page.mouse.click(640, 360)               # a click on the third photo ends the visit
    _wait_state(page, "idle", timeout=5000)
    assert page.is_visible("#venue-header")


def test_a_kept_ticker_shows_inside_the_frame_not_over_it(venue, serve, page):
    make_photos()
    album_profile(album_keep_ticker=True, album_seconds_per_photo=3, album_frame="oak")
    _open(page, serve())
    _wait_state(page, "visiting")
    page.wait_for_function("document.querySelector('.album-layer.shown .print-mount')")
    page.wait_for_timeout(500)
    assert page.is_hidden("#venue-header") and page.is_hidden("#dots")
    # The real ticker is hidden like the header; a copy is inside the frame.
    assert page.is_hidden("#ticker") and page.is_visible(".album-ticker")
    geo = page.evaluate("""() => {
        const r = (el) => { const b = el.getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height}; };
        const layer = document.querySelector('.album-layer.shown');
        const t = document.querySelector('.album-ticker');
        const c = {x: t.getBoundingClientRect().x + 20, y: t.getBoundingClientRect().y + t.getBoundingClientRect().height / 2};
        const top = document.elementFromPoint(c.x, c.y);
        return {ticker: r(t), mount: r(layer.querySelector('.print-mount')),
                bottom: r(layer.querySelector('.frame-bottom')), left: r(layer.querySelector('.frame-left')),
                text: t.innerText, textBox: r(t.querySelector('.ticker-text')), onTop: !!top && !!top.closest('.album-ticker'),
                realH: document.getElementById('ticker').offsetHeight, vw: innerWidth};
    }""")
    t, m, b, l = geo["ticker"], geo["mount"], geo["bottom"], geo["left"]
    assert "Ticker text" in geo["text"] and geo["onTop"]
    assert t["h"] > geo["realH"]                       # the real height, plus black below the text
    # Between the frame's sides, resting on its bottom edge, and below the mount.
    assert t["x"] == l["w"] and t["x"] + t["w"] == geo["vw"] - l["w"]
    assert t["y"] + t["h"] == b["y"]
    assert m["y"] + m["h"] <= t["y"] + 1
    # Clear black between the text and the frame.
    tb = geo["textBox"]
    assert b["y"] - (tb["y"] + tb["h"]) >= 0.012 * 720


def test_the_upcoming_surfaces_pause_for_the_visit_and_carry_on_after_it(venue, serve, page, write_tap):
    from app import config_store, upcoming_store
    from app.beer import Beer
    make_photos()
    config_store.update_config(paginate=False)      # one page, so the teaser's Slot is always on show
    upcoming_store.write("batch-1", Beer(name="Next Lager"), "coming soon", slot=1,
                         status="conditioning", revision=1)
    album_profile(album_every_turns=2, album_photos_per_visit=1, album_seconds_per_photo=3,
                  show_upcoming_previews=True, upcoming_interval_seconds=5,
                  upcoming_rotate_occupied=True, show_upcoming_panel=True, upcoming_panel_multiple=1)
    _open(page, serve())
    page.wait_for_function("document.querySelector('.cross-fade-overlay, .upcoming-panel.show')")
    _wait_state(page, "visiting")
    _wait_state(page, "idle")
    page.wait_for_function(
        "document.querySelector('.cross-fade-overlay, .upcoming-panel.show')", timeout=30_000)
    page.wait_for_timeout(400)            # let the 100 ms recorder sample it
    log = _log(page)
    during = [e for e in log if e["visiting"]]
    assert during
    assert not any(e["overlays"] or e["panel"] for e in during)     # nothing over a photo
    before = [e for e in log if not e["visiting"] and e["t"] < during[0]["t"]]
    after = [e for e in log if not e["visiting"] and e["t"] > during[-1]["t"]]
    assert any(e["overlays"] or e["panel"] for e in before)         # it was live before...
    assert any(e["overlays"] or e["panel"] for e in after)          # ...and picks up again


def test_a_board_update_during_a_visit_shows_when_the_taps_return(venue, serve, page, write_tap):
    make_photos()
    config_store.update_config(paginate=False, num_taps=1)
    album_profile(album_every_turns=1, album_seconds_per_photo=600, album_photos_per_visit=1)
    page.clock.install()                 # the page polls every 30 s: jump there instead of waiting
    _open(page, serve())
    page.clock.run_for(3000)             # the first tap-page turn starts the visit
    _wait_state(page, "visiting")
    page.wait_for_function("document.getElementById('album').dataset.photo")
    assert "Hazy IPA" in page.inner_text("#stage")

    write_tap("custom", 1, name="Keg Changed", abv=4.0)
    page.clock.run_for(31000)            # the poll fires mid-visit and fetches the new board
    page.wait_for_timeout(800)
    assert "Hazy IPA" in page.inner_text("#stage")        # not applied while the photos are up

    page.keyboard.press("Enter")         # K = 1, so this ends the visit
    _wait_state(page, "idle", timeout=5000)
    page.wait_for_function("document.getElementById('stage').innerText.includes('Keg Changed')")


def test_photos_only_is_one_endless_visit(venue, serve, page):
    make_photos()
    album_profile(album_every_turns=0, album_seconds_per_photo=2, album_photos_per_visit=1)
    _open(page, serve())
    _wait_state(page, "visiting")
    page.wait_for_timeout(7000)           # far longer than "K photos" would last
    log = _log(page)
    assert log[-1]["visiting"] and all(e["visiting"] for e in log if e["t"] > 1500)
    assert not any(e["header"] or e["dots"] for e in log if e["visiting"])
    distinct = {e["photo"] for e in log if e["photo"]}
    assert len(distinct) >= 3             # it kept moving through the album (order: Name A-Z)


def test_swiping_or_enter_through_the_taps_reaches_the_photos(venue, serve, page):
    make_photos()
    # Pages that would take a minute to turn by themselves.
    config_store.update_config(rotation_seconds=60)
    album_profile(album_every_turns=2, album_photos_per_visit=1, album_seconds_per_photo=120)
    _open(page, serve())
    page.keyboard.press("Enter")                      # turn 1 -> page 2
    page.wait_for_timeout(300)
    assert not _log(page)[-1]["visiting"]
    # Two tap pages have now been on screen: the next forward move, a swipe to
    # the left, starts the visit instead of turning the page.
    box = page.query_selector("#board").bounding_box()
    y = box["y"] + box["height"] / 2
    page.evaluate("""([x1, x2, y]) => {
        const b = document.getElementById('board');
        const t = (x) => new Touch({identifier: 1, target: b, clientX: x, clientY: y});
        b.dispatchEvent(new TouchEvent('touchstart', {touches: [t(x1)], changedTouches: [t(x1)], bubbles: true}));
        b.dispatchEvent(new TouchEvent('touchend', {touches: [], changedTouches: [t(x2)], bubbles: true}));
    }""", [box["width"] * 0.8, box["width"] * 0.2, y])
    _wait_state(page, "visiting", timeout=3000)
    # And by hand from the start: Enter twice from fresh taps also gets there.
    page.keyboard.press("Enter")                      # K = 1: back to the taps
    _wait_state(page, "idle", timeout=3000)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert not _log(page)[-1]["visiting"]
    page.keyboard.press("Enter")
    _wait_state(page, "visiting", timeout=3000)
