"""Data and the page recorder shared by the browser tests (kept out of conftest.py so
tests import it by a name that cannot collide with the other conftest module)."""
from __future__ import annotations

from PIL import Image

from app import admin_ops, config_store, paths, photo_library as lib

from photo_fixtures import write_image


def make_photos():
    """Three distinct photos (landscape JPEG, portrait PNG, square WebP), processed."""
    write_image(paths.PHOTOS_DIR / "Events" / "landscape.jpg",
                Image.new("RGB", (1600, 1000), (200, 120, 40)))
    write_image(paths.PHOTOS_DIR / "Events" / "portrait.png",
                Image.new("RGB", (900, 1200), (40, 120, 200)))
    write_image(paths.PHOTOS_DIR / "square.webp", Image.new("RGB", (1000, 1000), (60, 160, 90)))
    lib.scan()
    lib.process_pending()
    assert len(lib.ready_photos()) == 3


def album_profile(name="lounge", **settings):
    """A profile with the album on, short enough for a test (3 s pages, 2 s photos)."""
    plan = {"album_enabled": True, "album_every_turns": 1, "album_photos_per_visit": 2,
            "album_seconds_per_photo": 2, "album_fade_seconds": 0.3, "album_order": "name",
            **settings}
    admin_ops.create_profile(name)
    admin_ops.save_profile(
        name, customise={u for u, keys in config_store.CUSTOMISABLE_UNITS.items()
                         if any(k in plan for k in keys)},
        values={**config_store.DEFAULT_CONFIG, **plan})


# Records what the page does, sampled every 100 ms, so a test can assert on the
# whole timeline ("never an overlay during a visit") rather than a lucky instant.
RECORDER = """
(() => {
  window.__log = [];
  const t0 = performance.now();
  setInterval(() => { try {
    const q = (s) => document.querySelector(s);
    const visible = (el) => !!el && !el.hidden && getComputedStyle(el).visibility !== "hidden" &&
      getComputedStyle(el).display !== "none";
    window.__log.push({
      t: Math.round(performance.now() - t0),
      visiting: document.body.classList.contains("album-visit"),
      photo: (q("#album") || {}).dataset ? q("#album").dataset.photo || null : null,
      header: visible(q("#venue-header")),
      ticker: visible(q("#ticker")),
      dots: visible(q("#dots")),
      overlays: document.querySelectorAll(".cross-fade-overlay").length,
      panel: !!document.querySelector(".upcoming-panel.show"),
      page: Array.from(document.querySelectorAll(".page")).findIndex((p) => p.classList.contains("active")),
    });
  } catch (e) { /* the page is still loading */ } }, 100);
})();
"""
