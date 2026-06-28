"""Demo / mock mode: seed a few sample taps so the display can be built,
demoed and screenshotted fully offline with no Brewfather credentials.

Enabled with DEMO_MODE=true. Seeding only happens when /data/taps has no tap
files yet, so it never clobbers real data on a configured box. Sample images
reuse the bundled placeholder (copied locally), keeping everything offline.
"""
from __future__ import annotations

import logging
import os
import shutil

from . import markdown_store as md
from .config_store import load_config, save_config
from .paths import TAPS_DIR, ensure_dirs, placeholder_path
from .timezone import iso_now

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
    return any(TAPS_DIR.glob("*_tap_*.md"))


def maybe_seed_demo() -> None:
    """Seed demo taps if DEMO_MODE is on and no taps exist yet."""
    if not _demo_enabled():
        return
    ensure_dirs()
    if _has_existing_taps():
        log.info("DEMO_MODE on but taps already exist; not seeding")
        return

    log.info("seeding %d demo taps", len(SAMPLE_TAPS))
    src_placeholder = placeholder_path()

    for tap, name, abv, ibu, ebc, source, desc in SAMPLE_TAPS:
        stem = f"{'custom' if source == 'custom' else 'bf'}_tap_{tap}"
        image_name = None
        if src_placeholder is not None:
            # Copy the placeholder locally as this tap's image (offline-safe).
            ext = src_placeholder.suffix
            dest = TAPS_DIR / f"{stem}{ext}"
            try:
                shutil.copyfile(src_placeholder, dest)
                image_name = dest.name
            except OSError as exc:
                log.warning("could not copy demo image for %s: %s", stem, exc)

        front_matter = {
            "name": name,
            "abv": abv,
            "ibu": ibu,
            "ebc": ebc,
            "source": source,
            "image": image_name,
            "updated": iso_now(),
        }
        path = md.custom_md_path(tap) if source == "custom" else md.bf_md_path(tap)
        md.write_tap_file(path, front_matter, desc)

    # Set a tap count and an announcement so the display looks intentional.
    cfg = load_config()
    cfg["num_taps"] = max(cfg.get("num_taps", 0), len(SAMPLE_TAPS))
    if not cfg.get("announcement_text"):
        cfg["announcement_text"] = "Now pouring — ask staff for samples!  •  Demo mode"
    save_config(cfg)
    log.info("demo seed complete: num_taps=%d", cfg["num_taps"])
