"""Guard the intentional server<->client duplication of shared constants.

display.js cannot import from the Python app (no build step, offline-first), so a
few values are mirrored by hand. That duplication is deliberate, but silent drift
would make the display disagree with the board. These tests fail loudly if a
mirrored constant changes on only one side.
"""
import re
from pathlib import Path

from app.colors import EBC_PER_SRM
from app.config_store import TAP_SIZE_PRESETS
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


def test_admin_js_size_presets_match_server():
    # admin.js repaints the sliders when a preset is picked, so it mirrors
    # TAP_SIZE_PRESETS. The server re-resolves on save, so drift here would not
    # corrupt config - it would quietly show the operator the wrong numbers.
    block = re.search(r"TAP_SIZE_PRESETS\s*=\s*\{(.*?)\n  \};", _ADMIN_JS.read_text(encoding="utf-8"),
                      re.DOTALL)
    assert block, "TAP_SIZE_PRESETS not found in admin.js"
    js = {
        name: (float(image), float(text))
        for name, image, text in re.findall(
            r"(\w+)\s*:\s*\{\s*image:\s*([0-9.]+)\s*,\s*text:\s*([0-9.]+)\s*\}", block.group(1))
    }
    assert js == TAP_SIZE_PRESETS


def test_card_scale_css_variables_are_set_and_consumed():
    # display.js writes these two properties; display.css is the only reader. A
    # rename on one side alone would silently stop scaling anything.
    js_vars = set(re.findall(r'"(--tap-(?:image|text)-scale)"', _display_js()))
    css_vars = set(re.findall(r"(--tap-(?:image|text)-scale)", _DISPLAY_CSS.read_text(encoding="utf-8")))
    assert js_vars == {"--tap-image-scale", "--tap-text-scale"}
    assert js_vars == css_vars
