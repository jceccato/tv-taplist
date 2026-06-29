"""EBC->hex colour (the ebc2hex polynomial), saturation, and the contrast rule."""
from app.colors import (
    DEFAULT_SATURATION,
    ebc_to_hex,
    parse_saturation,
    relative_luminance,
    text_color_for,
)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_ebc_none_and_invalid_return_neutral():
    assert ebc_to_hex(None) == "#cccccc"
    assert ebc_to_hex("not-a-number") == "#cccccc"


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
