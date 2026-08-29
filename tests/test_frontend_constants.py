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
    # The six that legitimately survive: the colour unit, the source badge, the
    # three pagination/rotation values, and (issue #40) the one cadence the
    # cross-fade scheduler needs running before the first board arrives.
    # `upcoming_rotate_occupied` must never join this set - it is consumed
    # entirely into the per-teaser `cross_fade` answer server-side.
    assert mirrored == {"color_unit", "show_source_badge", "paginate",
                        "page_size", "rotation_seconds",
                        "upcoming_interval_seconds"}, mirrored


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


def test_display_js_reads_on_surfaces_as_a_resolved_answer():
    """The on-deck page reads `on_surfaces`; it never re-derives it (issue #41).

    board.py resolves `on_surfaces` from `upcoming_surface_scope` (among
    pinned/cross_fade); the assertion above already pins that Setting off the
    file entirely, so this pins the other half - that the resolved boolean is
    actually read, not silently unused while some other filter (e.g. plain
    `!pinned`) stands in for it.
    """
    js = _display_js()
    assert "on_surfaces" in js, "display.js never reads on_surfaces"
    assert re.search(r"\bu\.on_surfaces\b", js), (
        "display.js does not read on_surfaces off a teaser entry")


def test_display_js_panel_scheduling_runs_off_the_shared_interval():
    """The half-board panel must not spin up a second timer (CLAUDE.md, #42).

    `setUpcomingInterval` owns the ONE interval; a surface joins it and the
    shared `upcomingBusy` interlock rather than building a private
    `setInterval`. This greps for exactly one `setInterval` call feeding the
    cross-fade/panel scheduling family, which is what would break if a
    second, independent timer were introduced for the panel's own cadence.
    """
    js = _display_js()
    scheduling_intervals = re.findall(r"setInterval\(\s*(\w+)\s*,", js)
    # carouselTick's own setInterval is a separate, pre-existing timer
    # (the page-rotation clock); the upcoming family must contribute exactly
    # one more, driving both the cross-fade and the panel's own tick.
    assert scheduling_intervals.count("upcomingTick") == 1, scheduling_intervals
    assert "crossFadeTick" not in scheduling_intervals, (
        "the panel must not have its own timer, and crossFadeTick must be "
        "invoked through the shared upcomingTick dispatcher rather than "
        "registered as its own setInterval callback")


def test_display_js_deck_page_has_no_scheduler_of_its_own():
    """The on-deck page rides the ordinary carousel rotation (#4 close-out).

    Its scheduled turn (jump to the page, hold, jump back) flicked over
    instead of flowing on a real display, so the whole hold-and-return
    machinery was removed: no deck tick, no hold or return timer, no
    per-page multiple read off the wire. `deckPageIndex` alone survives, so
    the panel can refuse to stack on top of the page.
    """
    js = _display_js()
    for name in ("deckPageTick", "deckHoldTimer", "deckReturnTimer",
                 "deckReturnPage", "deckMultiple", "deckTickCounter",
                 "upcoming_deck_multiple"):
        assert name not in js, f"display.js still references {name}"
    assert "deckPageIndex" in js, (
        "deckPageIndex is gone - the panel's no-stack guard has nothing to read")


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


def test_display_js_never_reads_the_conditioning_status_setting():
    """The on-tap marker is a resolved answer too (issue #45).

    `show_conditioning_status` decides whether a pouring Conditioning beer is
    marked, and board.py consumes it entirely into the Tap's own `status_label`.
    The display must never see the Setting - reading it here would be the
    Visibility chain reappearing in the one language this project cannot test.
    """
    assert "show_conditioning_status" not in _display_js()


def test_display_js_renders_the_status_marker_on_a_tap_card_not_only_a_teaser():
    """The `.status` line is shared, not teaser-only (issue #45).

    #39 emitted the meta block that hosts `.status` only for a teaser card. A
    Tap card carrying a marker needs the identical block, so the gate has to
    read `status_label` rather than `teaser` alone - otherwise a conditioning
    beer on tap resolves a label server-side that nothing ever draws.
    """
    js = _display_js()
    gate = re.search(r"function hasMeta\((\w+)\)\s*\{([^}]*)\}", js)
    assert gate, "display.js has no hasMeta() gate for the card meta block"
    body = gate.group(2)
    assert "status_label" in body, (
        "hasMeta() does not consult status_label, so a Tap card's marker would "
        "never be emitted")
    assert re.search(r"hasMeta\(t\)", js), "hasMeta() is never applied to a card"


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


def test_display_js_escapes_the_tap_badge_interpolation():
    """The tap-num badge is the one card field that once skipped esc() (#4 review).

    Deck and panel cards carry a synthetic string tap id built from the raw
    Batch id, and a hand-written batch_id in /data/upcoming/ is an editable
    surface - an unescaped interpolation there executed markup on the display
    page. The badge must go through esc() and render only for a real integer
    Slot, so the synthetic ids never reach the DOM at all.
    """
    js = _display_js()
    assert "${esc(t.tap)}" in js
    assert "${t.tap}" not in js, "a raw t.tap interpolation reached innerHTML"


def test_display_js_dispatches_the_panel_before_the_cross_fade():
    """The panel's turn comes before the cross-fade's in upcomingTick (#4 review).

    The busy window is shorter than every legal interval, so the interlock is
    always free at a tick boundary and the first consumer dispatched wins the
    tick. The cross-fade wants every tick; the panel wants one in N. With the
    cross-fade first, any board where it has a candidate starves the panel
    forever - it never appears and its multiple is inert.
    """
    js = _display_js()
    body = js.split("function upcomingTick()", 1)[1].split("}", 1)[0]
    assert body.index("panelTick()") < body.index("crossFadeTick()")


def test_display_js_cross_fade_covers_every_bound_slot_in_one_turn():
    """The cross-fade fades ALL bound Slots on the active page together.

    The baseline shipped as one teaser per tick, cycling - which on a board
    with several upcoming beers read as a teaser always coming or going
    somewhere, one at a time. A turn now groups the candidates by Slot,
    mounts one overlay per Slot on the active page, and fades the whole
    group in and out on shared timers. Teasers SHARING a Slot still
    alternate across turns (two Batches may claim one occupied Slot - the
    FAQ's "both tease" contract - and they cannot stack on one cell), which
    is the one place a turn counter survives. A grep-shaped guard, like the
    rest of this file: it pins the group machinery present and the
    single-teaser cycler absent.
    """
    js = _display_js()
    assert "crossFadeIndex" not in js, (
        "the one-teaser-per-tick cycler is back - a turn must cover every "
        "bound Slot on the active page, not one candidate")
    assert "crossFadeOverlays" in js, (
        "the in-flight overlay is singular again - the group of per-Slot "
        "overlays is gone")
    tick = js.split("function crossFadeTick()", 1)[1].split("\n  }", 1)[0]
    assert "bySlot.forEach" in tick, (
        "crossFadeTick no longer fans out one overlay per grouped Slot")
    assert re.search(r"bySlot\.(get|set|has)\(u\.slot\)", tick), (
        "crossFadeTick no longer groups the candidates into bySlot - a "
        "leftover fan-out over an empty map covers nothing")
    assert re.search(r"%\s*slotTeasers\.length", tick), (
        "teasers sharing a Slot no longer alternate across turns")


def test_display_js_panel_never_stacks_on_the_deck_page():
    """The panel and the deck page carry the same teasers (#4 close-out).

    Two directions, two guards, and both must exist because navigation does
    not go through the scheduler: panelTick() skips a turn that would START
    over the deck page, and showPage() pulls an in-flight panel when the
    carousel or a dot click LANDS on the deck page mid-hold. A grep-shaped
    guard, like the rest of this file, because there is no JS test harness.
    """
    js = _display_js()
    tick = js.split("function panelTick()", 1)[1].split("\n  }", 1)[0]
    assert re.search(r"state\.currentPage === deckPageIndex.*return", tick), (
        "panelTick() no longer skips its turn while the deck page is active")
    show = js.split("function showPage(", 1)[1].split("\n  }", 1)[0]
    assert re.search(r"deckPageIndex\b.*panelHide\(\)", show), (
        "showPage() no longer pulls the panel when landing on the deck page")
