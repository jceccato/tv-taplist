"""Colour resolution, the ebc2hex polynomial, saturation, and the contrast rule."""
import pytest

from app.colors import (
    DEFAULT_SATURATION,
    EBC_PER_SRM,
    UNKNOWN_SWATCH_HEX,
    display_color_to_ebc,
    ebc_to_hex,
    parse_hex_color,
    parse_saturation,
    relative_luminance,
    resolve_color,
    text_color_for,
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_ebc_none_and_invalid_return_neutral():
    assert ebc_to_hex(None) == UNKNOWN_SWATCH_HEX == "#cccccc"
    assert ebc_to_hex("not-a-number") == UNKNOWN_SWATCH_HEX


def test_low_ebc_is_pale_high_ebc_is_near_black():
    pale = _rgb(ebc_to_hex(4))     # ~SRM 2, a pale lager
    dark = _rgb(ebc_to_hex(79))    # ~SRM 40, a stout
    assert relative_luminance(*pale) > relative_luminance(*dark)
    assert relative_luminance(*pale) > 0.5     # genuinely bright
    assert relative_luminance(*dark) < 0.05    # genuinely near-black


def test_ebc_clamped_to_model_range():
    # EBC is clamped to the model's 0..80 range, so out-of-range inputs collapse
    # onto the endpoints instead of producing nonsense colours.
    assert ebc_to_hex(1000) == ebc_to_hex(80)
    assert ebc_to_hex(-5) == ebc_to_hex(0)


def test_saturation_mutes_towards_grey():
    full = _rgb(ebc_to_hex(14, 1.0))
    half = _rgb(ebc_to_hex(14, 0.5))
    grey = _rgb(ebc_to_hex(14, 0.0))
    # Zero saturation is a pure grey (all channels equal).
    assert grey[0] == grey[1] == grey[2]
    # Lowering saturation reduces the spread between the channels.
    assert (max(full) - min(full)) > (max(half) - min(half)) > 0
    # None means "use the default saturation".
    assert ebc_to_hex(14, None) == ebc_to_hex(14, DEFAULT_SATURATION)


def test_parse_saturation_percent_or_fraction():
    assert parse_saturation(60) == 0.6        # percentage
    assert parse_saturation("60") == 0.6
    assert parse_saturation(0.6) == 0.6       # already a fraction
    assert parse_saturation(150) == 1.0       # clamped high
    assert parse_saturation(-10) == 0.0       # clamped low
    assert parse_saturation("") is None
    assert parse_saturation(None) is None
    assert parse_saturation("nope", default=1.0) == 1.0


def test_contrast_rule_picks_legible_text():
    assert text_color_for(ebc_to_hex(4)) == "#161616"   # dark text on a pale beer
    assert text_color_for(ebc_to_hex(79)) == "#f5f5f5"  # light text on a stout


def test_parse_hex_color_normalises_and_rejects():
    assert parse_hex_color("#780606") == "#780606"
    assert parse_hex_color("780606") == "#780606"          # leading # optional
    assert parse_hex_color("#ABC") == "#aabbcc"             # short form expanded
    assert parse_hex_color("  #FfFfFf  ") == "#ffffff"      # trimmed + lowercased
    assert parse_hex_color("nope") is None
    assert parse_hex_color("#12345") is None                # wrong length
    assert parse_hex_color(None) is None
    assert parse_hex_color(123456) is None                  # not a string


# ---- Colour resolution: override, then EBC, then Unknown ------------------

def test_resolve_color_override_wins_over_ebc():
    r = resolve_color(ebc=40, color_override="#780606")
    assert r is not None
    assert r.color_hex == "#780606"
    assert r.color_hex != ebc_to_hex(40)
    # The contrast rule travels with the colour, so no caller re-derives it.
    assert r.text_color == text_color_for("#780606")


def test_resolve_color_override_is_never_muted_by_saturation():
    # A Colour override is an exact instruction: saturation applies to the
    # *computed* branch only, or there would be no way to ask for one colour.
    assert resolve_color(ebc=40, saturation=0.3,
                         color_override="#780606").color_hex == "#780606"
    assert resolve_color(saturation=0.0,
                         color_override="#780606").color_hex == "#780606"


def test_resolve_color_computes_from_ebc_with_saturation():
    assert resolve_color(ebc=40).color_hex == ebc_to_hex(40)
    assert resolve_color(ebc=40, saturation=0.3).color_hex == ebc_to_hex(40, 0.3)


def test_resolve_color_answers_unknown_rather_than_a_fallback():
    # Unknown is a real answer with no colour attached: the surface drawing it
    # declares its own fallback (ADR-0004), so resolution must not pick one.
    assert resolve_color() is None
    assert resolve_color(ebc=None, saturation=0.5, color_override=None) is None
    assert resolve_color(ebc="") is None


def test_resolve_color_coerces_defensively():
    # Front matter and query strings arrive as strings, and a malformed override
    # is not an instruction - it falls through to the EBC branch.
    assert resolve_color(ebc="40").color_hex == ebc_to_hex(40)
    assert resolve_color(ebc=40, color_override="nope").color_hex == ebc_to_hex(40)
    assert resolve_color(ebc="not-a-number") is None
    assert resolve_color(ebc="not-a-number", color_override="#abc").color_hex == "#aabbcc"


# ---- the display unit -> stored EBC conversion -----------------------------

def test_display_color_to_ebc_passes_ebc_through():
    """EBC is the stored form, so entering EBC is a no-op beyond tidying."""
    assert display_color_to_ebc(20, "ebc") == 20
    assert display_color_to_ebc(9.5, "ebc") == 9.5


def test_display_color_to_ebc_converts_srm():
    assert display_color_to_ebc(10, "srm") == pytest.approx(10 * EBC_PER_SRM, abs=0.05)


def test_display_color_to_ebc_tidies_the_number_for_a_hand_edited_file():
    """Integral values store as ints and the rest round to a tenth.

    The result is written into a Tap file an operator may open in a text editor
    (ADR-0001), so `19.700000000000003` would be noise to read past - and a
    tenth of an EBC is far below the colour model's resolution anyway.
    """
    assert display_color_to_ebc(20.0, "ebc") == 20
    assert isinstance(display_color_to_ebc(20.0, "ebc"), int)
    assert display_color_to_ebc(10, "srm") == 19.7


def test_display_color_to_ebc_treats_blank_and_junk_as_unknown():
    # Unknown is a real answer; a Beer need not have a Colour at all.
    assert display_color_to_ebc(None, "ebc") is None
    assert display_color_to_ebc("", "srm") is None
    assert display_color_to_ebc("not-a-number", "ebc") is None


def test_display_color_to_ebc_treats_an_unknown_unit_as_ebc():
    """Only "srm" converts; anything else is the stored unit, as config coerces."""
    assert display_color_to_ebc(10, "bogus") == 10
    assert display_color_to_ebc(10, "SRM") == pytest.approx(19.7, abs=0.05)
