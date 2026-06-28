"""EBC/SRM colour mapping and the luminance contrast rule."""
from app.colors import ebc_to_hex, relative_luminance, srm_to_rgb, text_color_for


def test_ebc_none_and_invalid_return_neutral():
    assert ebc_to_hex(None) == "#cccccc"
    assert ebc_to_hex("not-a-number") == "#cccccc"


def test_low_ebc_is_pale_high_ebc_is_near_black():
    pale = ebc_to_hex(4)     # ~SRM 2
    dark = ebc_to_hex(79)    # ~SRM 40, clamps to chart max
    # Pale beer should be much brighter than a stout.
    pr, pg, pb = int(pale[1:3], 16), int(pale[3:5], 16), int(pale[5:7], 16)
    dr, dg, db = int(dark[1:3], 16), int(dark[3:5], 16), int(dark[5:7], 16)
    assert relative_luminance(pr, pg, pb) > relative_luminance(dr, dg, db)


def test_clamping_below_and_above_range():
    # EBC 0 -> SRM 0 clamps to SRM 1; very high clamps to SRM 40.
    assert ebc_to_hex(0) == "#ffe699"            # SRM 1 reference
    assert ebc_to_hex(1000).lower() == "#36080a"  # SRM 40 reference


def test_srm_interpolation_between_points():
    # Halfway between SRM 1 and 2 should sit between the two reference RGBs.
    r, g, b = srm_to_rgb(1.5)
    assert 0xD8 <= r <= 0xFF
    assert 0x78 <= g <= 0xE6


def test_contrast_rule_picks_legible_text():
    # Pale swatch -> dark text; dark swatch -> light text.
    assert text_color_for(ebc_to_hex(4)) == "#161616"
    assert text_color_for(ebc_to_hex(79)) == "#f5f5f5"
