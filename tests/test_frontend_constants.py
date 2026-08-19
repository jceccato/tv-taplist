"""Guard the intentional server<->client duplication of shared constants.

display.js cannot import from the Python app (no build step, offline-first), so a
few values are mirrored by hand. That duplication is deliberate, but silent drift
would make the display disagree with the board. These tests fail loudly if a
mirrored constant changes on only one side.
"""
import re
from pathlib import Path

from app.colors import EBC_PER_SRM
from app.config_store import TAP_PHOTO_PRESETS, TAP_TEXT_PRESETS
from app.theme import THEME_KEYS

_DISPLAY_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "display.js"


def _display_js() -> str:
    return _DISPLAY_JS.read_text(encoding="utf-8")


def test_display_js_ebc_per_srm_matches_server():
    m = re.search(r"EBC_PER_SRM\s*=\s*([0-9.]+)", _display_js())
    assert m, "EBC_PER_SRM not found in display.js"
    assert float(m.group(1)) == EBC_PER_SRM


def test_display_js_theme_vars_match_server_keys():
    # The THEME_VARS object in display.js must cover exactly the server THEME_KEYS,
    # or a themed board would leave some CSS variables unset (or set stray ones).
    block = re.search(r"THEME_VARS\s*=\s*\{(.*?)\}", _display_js(), re.DOTALL)
    assert block, "THEME_VARS not found in display.js"
    js_keys = set(re.findall(r"(\w+)\s*:", block.group(1)))
    assert js_keys == set(THEME_KEYS), (js_keys, set(THEME_KEYS))


_ADMIN_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "admin.js"
_DISPLAY_CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "display.css"


def _admin_js_presets(name: str) -> dict[str, float]:
    block = re.search(name + r"\s*=\s*\{(.*?)\n  \};", _ADMIN_JS.read_text(encoding="utf-8"),
                      re.DOTALL)
    assert block, name + " not found in admin.js"
    return {key: float(value) for key, value in re.findall(r"(\w+)\s*:\s*([0-9.]+)", block.group(1))}


def test_admin_js_photo_presets_match_server():
    # admin.js repaints the slider when a preset is picked, so it mirrors the
    # server maps. The server re-resolves on save, so drift here would not corrupt
    # config - it would quietly show the operator the wrong number.
    assert _admin_js_presets("TAP_PHOTO_PRESETS") == TAP_PHOTO_PRESETS


def test_admin_js_text_presets_match_server():
    assert _admin_js_presets("TAP_TEXT_PRESETS") == TAP_TEXT_PRESETS


def test_card_text_scale_css_variable_is_set_and_consumed():
    # display.js writes this property; display.css is the only reader. A rename on
    # one side alone would silently stop scaling anything.
    css = _DISPLAY_CSS.read_text(encoding="utf-8")
    js_vars = set(re.findall(r'"(--tap-(?:image|text)-scale)"', _display_js()))
    css_vars = set(re.findall(r"(--tap-(?:image|text)-scale)", css))
    assert js_vars == {"--tap-text-scale"}
    assert js_vars == css_vars


def test_text_scale_scales_the_clamp_ceiling_but_not_the_floor():
    # The floor is a legibility guarantee and must stay absolute; the ceiling has
    # to follow the scale or every step above Default collapses onto it at 4K,
    # where the preferred vmin size already exceeds it. Every scaled font-size
    # site must have exactly this shape.
    css = _DISPLAY_CSS.read_text(encoding="utf-8")
    sites = re.findall(r"font-size: clamp\([^;]*--tap-text-scale[^;]*\);", css)
    assert len(sites) == 8, sites
    for site in sites:
        m = re.fullmatch(
            r"font-size: clamp\(\d+px, calc\([0-9.]+vmin \* var\(--tap-text-scale, 1\)\), "
            r"calc\(\d+px \* var\(--tap-text-scale, 1\)\)\);", site)
        assert m, site


def test_photo_cap_is_measured_from_the_painted_height():
    """The photo cap must be taken from the painted image, not its box.

    `object-fit: contain` letterboxes a photo whose width is the binding
    constraint - a 16:9 Brewfather shot in the wide-card layout, where
    `max-width: 46%` decides the width. Measuring the box there leaves the top
    of the scale inert: capping a 177px box does nothing visible until the cap
    falls below the 159px the photo is actually painted at, so every scale above
    about 0.85 looked broken on landscape photos while working on square ones.

    This pins the aspect-ratio correction rather than the arithmetic, which has
    no JS test harness in this project - it is covered by browser checks.
    """
    js = _display_js()
    assert "naturalWidth" in js and "naturalHeight" in js, (
        "applyPhotoScale no longer consults the photo's intrinsic size, so a "
        "width-bound photo will be capped against its letterboxed box again"
    )
    assert "Math.min(box.height" in js, (
        "the measured height should be the smaller of the box and the "
        "aspect-fitted height"
    )
