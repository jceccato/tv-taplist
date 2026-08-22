"""Demo / mock mode: seed a few sample taps so the display can be built,
demoed and screenshotted fully offline with no Brewfather credentials.

Enabled with DEMO_MODE=true. Seeding only happens when /data/taps has no tap
files yet, so it never clobbers real data on a configured box. Beer-glass
images are generated on-the-fly from EBC values (fully offline).
"""
from __future__ import annotations

import logging
import os

from . import tap_store as taps
from .beer import Beer
from .config_store import load_config, save_config
from .paths import ensure_dirs

log = logging.getLogger("taplist.demo")

# (tap, name, abv, ibu, ebc, source, description)
SAMPLE_TAPS = [
    (1, "West Coast IPA", 6.8, 65, 18, "brewfather", "Bright citrus and pine, crisp dry finish."),
    (2, "Hazy Pale Ale", 5.2, 35, 12, "brewfather", "Juicy stone fruit, soft bitterness, low haze."),
    (3, "Munich Helles", 4.9, 18, 7, "custom", "Clean malt, gentle noble hop, classic lager."),
    (4, "Irish Dry Stout", 4.4, 40, 79, "brewfather", "Roasty coffee, dry, creamy nitro pour."),
    (5, "Saison du Tap", 6.1, 28, 9, "brewfather", "Peppery phenols, lemony tartness, sparkling."),
    (6, "Vienna Lager", 5.0, 24, 26, "custom", "Toasty amber malt, balanced, smooth."),
]


def _demo_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _has_existing_taps() -> bool:
    """Whether any Source already holds a Tap file, for any Slot.

    Asked through the store rather than by globbing the taps directory: the
    filename convention is the store's alone, and this check used to be a
    fourth, subtly different spelling of it. Deliberately unbounded by the
    configured tap count - a box with Taps parked above it is still configured,
    and seeding demo data over them would clobber real data.
    """
    return any(taps.occupied_slots(source) for source in taps.SOURCE_PRECEDENCE)


def maybe_seed_demo() -> None:
    """Seed demo taps if DEMO_MODE is on and no taps exist yet."""
    if not _demo_enabled():
        return
    ensure_dirs()
    if _has_existing_taps():
        log.info("DEMO_MODE on but taps already exist; not seeding")
        return

    log.info("seeding %d demo taps", len(SAMPLE_TAPS))

    for tap, name, abv, ibu, ebc, source, desc in SAMPLE_TAPS:
        # The sample Beers set four fields and leave the rest at the type's
        # defaults. That used to be a hand-built dict of seven keys out of
        # roughly eighteen, and the gap was invisible because every reader
        # defended itself with `.get()` - which is the drift issue #32 closed.
        beer = Beer(name=name, abv=abv, ibu=ibu, ebc=ebc)
        # The SAMPLE_TAPS `source` strings are the Source enum's on-disk values
        # ("custom" / "brewfather"), so they convert straight across. Seeding
        # writes both Sources on purpose, so the demo board shows a mix of
        # Manual and Brewfather Slots (and the source badge has something to
        # say).
        taps.write(tap, taps.Source(source), beer, desc)

    # Set a tap count and an announcement so the display looks intentional.
    cfg = load_config()
    cfg["num_taps"] = max(cfg.get("num_taps", 0), len(SAMPLE_TAPS))
    if not cfg.get("announcement_text"):
        cfg["announcement_text"] = "Now pouring - ask staff for samples!  •  Demo mode"
    save_config(cfg)
    log.info("demo seed complete: num_taps=%d", cfg["num_taps"])
