"""Guard the intentional server<->client duplication of shared constants.

display.js cannot import from the Python app (no build step, offline-first), so a
few values are mirrored by hand. That duplication is deliberate, but silent drift
would make the display disagree with the board. These tests fail loudly if a
mirrored constant changes on only one side.
"""
import re
from pathlib import Path

from app.colors import EBC_PER_SRM, UNKNOWN_SWATCH_HEX
from app.config_store import DEFAULT_CONFIG, TAP_PHOTO_PRESETS, TAP_TEXT_PRESETS
from app.theme import THEME_KEYS

_DISPLAY_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "display.js"


def _display_js() -> str:
    return _DISPLAY_JS.read_text(encoding="utf-8")


def test_display_js_ebc_per_srm_matches_server():
    m = re.search(r"EBC_PER_SRM\s*=\s*([0-9.]+)", _display_js())
    assert m, "EBC_PER_SRM not found in display.js"
    assert float(m.group(1)) == EBC_PER_SRM


def test_display_js_unknown_swatch_fallback_matches_server():
    """The swatch's Unknown fallback is declared on both sides of the wire.

    The board sends a null colour when Colour is Unknown, so display.js supplies
    the grey itself. That is the swatch surface declaring its own fallback
    (ADR-0004), not a stale copy of a server value - but it is still the same
    grey `/api/preview-color` paints in the admin, and the two must not drift
    apart into a board that looks different from its own preview.
    """
    found = set(re.findall(r't\.(?:color_hex|text_color)\s*\|\|\s*"(#[0-9a-f]{6})"',
                           _display_js()))
    assert UNKNOWN_SWATCH_HEX in found, (found, UNKNOWN_SWATCH_HEX)


def _display_js_default_settings() -> dict[str, object]:
    """Parse display.js's DEFAULT_SETTINGS into Python values."""
    block = re.search(r"DEFAULT_SETTINGS\s*=\s*\{(.*?)\n  \};", _display_js(), re.DOTALL)
    assert block, "DEFAULT_SETTINGS not found in display.js"
    out: dict[str, object] = {}
    for key, raw in re.findall(r"(\w+)\s*:\s*(true|false|\"[^\"]*\"|[0-9.]+)",
                               block.group(1)):
        if raw in ("true", "false"):
            out[key] = raw == "true"
        elif raw.startswith('"'):
            out[key] = raw.strip('"')
        else:
            out[key] = float(raw) if "." in raw else int(raw)
    assert out, "DEFAULT_SETTINGS parsed as empty - the literal shape changed"
    return out


def test_display_js_default_settings_match_the_server_defaults():
    """The surviving settings mirror must not drift from the config schema.

    display.js seeds `state.settings` before the first board arrives, and every
    key in it is a hand-copy of a `DEFAULT_CONFIG` entry. The values are inert in
    practice (the first board replaces the object wholesale), which is precisely
    why drift here would go unnoticed until someone read the file and believed
    it. Pin both the key's existence and its value.
    """
    for key, value in _display_js_default_settings().items():
        assert key in DEFAULT_CONFIG, f"{key} is not a server setting"
        assert DEFAULT_CONFIG[key] == value, (key, DEFAULT_CONFIG[key], value)


def test_display_js_default_settings_carry_no_visibility_flags():
    """Visibility must not creep back into the display's settings mirror.

    The board resolves the whole chain (per-Tap override, global toggle, Empty
    suppression) and sends six booleans per tap. A `show_abv` or a
    `hide_ibu_when_empty` reappearing here is the first symptom of the chain
    being reimplemented in JavaScript, where nothing tests it.
    """
    mirrored = set(_display_js_default_settings())
    visibility = {"show_abv", "show_ibu", "show_color", "show_og", "show_fg",
                  "hide_abv_when_empty", "hide_ibu_when_empty",
                  "hide_color_when_empty", "hide_og_when_empty",
                  "hide_fg_when_empty"}
    assert mirrored & visibility == set(), mirrored & visibility
    # The five that legitimately survive: the colour unit, the source badge and
    # the three pagination/rotation values. Nothing else belongs here.
    assert mirrored == {"color_unit", "show_source_badge", "paginate",
                        "page_size", "rotation_seconds"}, mirrored


def test_display_js_does_not_reimplement_the_visibility_chain():
    """The display renders the answers; it never recomputes them.

    A grep-shaped guard rather than a behavioural one, because this project has
    no JS test harness. It pins the two failure modes that put the chain back in
    the browser: reading a raw toggle off the board payload, or writing the
    swatch's `Colour is known` special case out by hand again.
    """
    js = _display_js()
    for flag in ("show_abv", "show_ibu", "show_color", "show_og", "show_fg",
                 "hide_abv_when_empty", "hide_ibu_when_empty",
                 "hide_color_when_empty", "hide_og_when_empty",
                 "hide_fg_when_empty", "color_known"):
        assert flag not in js, f"display.js still references {flag}"
    for helper in ("statHidden", "effShow"):
        assert helper not in js, f"display.js still defines/uses {helper}"
    # Every stat's hidden state must be a plain read of a resolved boolean.
    assert set(re.findall(r"t\.(\w+_visible)", js)) == {
        "abv_visible", "ibu_visible", "ebc_visible", "og_visible", "fg_visible",
        "swatch_visible"}


def test_display_js_never_reads_the_upcoming_settings():
    """The display draws a pinned teaser; it never decides whether to (issue #38).

    board.py resolves the whole Upcoming composition - `pinned`, `cross_fade`,
    `on_surfaces` - from `show_upcoming_previews`, `upcoming_rotate_occupied` and
    `upcoming_surface_scope`, among other Settings. display.js is handed those
    three resolved booleans per teaser and must never see the Settings that
    produced them: reading one here would be the chain from Visibility (this
    file's other drift guards) reappearing for a different feature. This is the
    test that fails if a later ticket (#39/#40/#41) reaches for a
    `show_upcoming_*` toggle instead of the wire answer.
    """
    js = _display_js()
    assert re.search(r"show_upcoming_\w*", js) is None, (
        "display.js references a show_upcoming_* Setting"
    )
    for setting in ("upcoming_rotate_occupied", "upcoming_surface_scope"):
        assert setting not in js, f"display.js still references {setting}"


def test_display_js_renders_teaser_words_from_resolved_answers_not_derived():
    """The teaser card's words are resolved answers (issue #39), not JS logic.

    status_label, subtitle and abv_estimated already carry the customer word,
    the boundness-aware subtitle text and the "is this shown ABV an estimate"
    answer. display.js must read them as plain values, never hardcode the
    customer-facing status vocabulary (STATUS_DISPLAY_LABELS in board.py) or
    derive the '~' marker from anything about the beer itself - both of which
    would put a second implementation of board.py's resolution in the one
    language this project has no test harness for.
    """
    js = _display_js()
    for field in ("status_label", "subtitle", "abv_estimated", "teaser_label"):
        assert f"t.{field}" in js, f"display.js never reads t.{field}"
    for word in ("Ready", "Conditioning", "Fermenting", "Brewing", "Planned"):
        assert word not in js, (
            f"display.js hardcodes the customer status word {word!r} instead "
            "of reading t.status_label"
        )


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
    # 8 pre-existing sites, plus 3 for the teaser's own words (issue #39): the
    # ribbon, the subtitle and the status line.
    assert len(sites) == 11, sites
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


def test_admin_js_update_states_match_server():
    """The four update states are named in both admin.js and update_check.py.

    admin.js only chooses wording from the state the server resolved - it must
    never re-derive it, because that would mean reimplementing
    _looks_like_release's regex in JS. This pins the strings so a rename cannot
    silently leave the admin matching nothing and falling through to the
    "untagged build" wording on a healthy container (issue #26).
    """
    from app import update_check
    js = _ADMIN_JS.read_text(encoding="utf-8")
    server = {update_check.STATE_DISABLED, update_check.STATE_UNKNOWN,
              update_check.STATE_BEHIND, update_check.STATE_CURRENT}
    found = set(re.findall(r'UPDATE_STATE_\w+\s*=\s*"([a-z]+)"', js))
    assert found == server, (found, server)
