"""Demo / mock mode: seed a few sample taps so the display can be built,
demoed and screenshotted fully offline with no Brewfather credentials.

Enabled with DEMO_MODE=true. Seeding only happens when /data/taps has no tap
files yet, so it never clobbers real data on a configured box. Beer-glass
images are generated on-the-fly from EBC values (fully offline).

Also seeds two Upcoming Beers (issue #43) behind the same freshness guard, so
an evaluator sees the teaser feature (issue #4) without configuring Brewfather
or waiting for a sync. One is bound to a Slot past the sample taps (kept
genuinely Vacant, so it renders `pinned` on the very first paint - no
animation to wait for); the other is unbound, so the overflow queue has
something in it the moment `show_upcoming_previews` is switched on. Two is
enough to show both shapes; see docs/adr/0006 for why the store is disposable
and separate from the Tap file store this module also seeds.
"""
from __future__ import annotations

import logging
import os

from . import tap_store as taps
from . import upcoming_store
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

# The Slot the pinned demo teaser binds to. One past the last sample tap, so
# raising num_taps to include it (below) creates a Slot with no Tap file at
# all - genuinely Vacant, which is what makes the teaser resolve `pinned` on
# the very first board build rather than depending on operator action.
_PINNED_UPCOMING_SLOT = len(SAMPLE_TAPS) + 1

# (batch_id, name, abv, ibu, ebc, status, slot, description)
SAMPLE_UPCOMING = [
    ("demo-upcoming-pinned", "Foggy Horizon NEIPA", 6.5, 42, 14, "conditioning",
     _PINNED_UPCOMING_SLOT,
     "Tropical hop bomb clearing in the tank - lined up for the next pour."),
    ("demo-upcoming-unbound", "Bourbon Barrel Stout", 9.2, 38, 140, "fermenting",
     None,
     "Rich vanilla and oak, still resting in the barrel."),
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

    log.info("seeding %d demo upcoming beers", len(SAMPLE_UPCOMING))
    for batch_id, name, abv, ibu, ebc, status, slot, desc in SAMPLE_UPCOMING:
        # Same reasoning as the Tap Beers above: build the typed Beer and let
        # the store serialise it, rather than hand-assembling front matter
        # (the drift issue #32 closed). The Upcoming store's writer produces
        # its own file shape from this Beer - see test_all_three_writers_produce_a_beer,
        # which this seeder's Beer now also exercises.
        beer = Beer(name=name, abv=abv, ibu=ibu, ebc=ebc)
        upcoming_store.write(batch_id, beer, desc, slot=slot, status=status, revision=1)

    # Set a tap count and an announcement so the display looks intentional.
    # num_taps is raised past _PINNED_UPCOMING_SLOT so that Slot exists on the
    # board and is Vacant (no Tap file was written for it above) - the
    # precondition board.resolve_upcoming checks before calling a teaser
    # pinned.
    cfg = load_config()
    cfg["num_taps"] = max(cfg.get("num_taps", 0), _PINNED_UPCOMING_SLOT)
    if not cfg.get("announcement_text"):
        cfg["announcement_text"] = "Now pouring - ask staff for samples!  •  Demo mode"
    # Turn the teaser feature on so a fresh demo box shows it without the
    # operator finding the toggle first - the whole point of issue #43.
    cfg["show_upcoming_previews"] = True
    save_config(cfg)
    log.info("demo seed complete: num_taps=%d", cfg["num_taps"])
