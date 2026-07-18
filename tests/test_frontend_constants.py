"""Guard the intentional server<->client duplication of shared constants.

display.js cannot import from the Python app (no build step, offline-first), so a
few values are mirrored by hand. That duplication is deliberate, but silent drift
would make the display disagree with the board. These tests fail loudly if a
mirrored constant changes on only one side.
"""
import re
from pathlib import Path

from app.colors import EBC_PER_SRM
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
