"""Beer-glass SVG: tinting to a resolved Colour, the Unknown fallback, silhouettes."""
import re

from app.beer_glass import _DEFAULT_HEX, GLASS_KEYS, beer_glass_svg, normalize_glass
from app.colors import UNKNOWN_SWATCH_HEX


def _base_stop(svg: str) -> str:
    return re.search(r'offset="55%" stop-color="(#[0-9a-fA-F]{6})"', svg).group(1)


def test_tints_to_the_colour_it_is_given():
    # The renderer resolves nothing: it paints the colour it is handed, whether
    # that came from a Colour override or from the EBC model.
    assert _base_stop(beer_glass_svg("#780606")).lower() == "#780606"
    assert _base_stop(beer_glass_svg("780606")).lower() == "#780606"  # bare hex too


def test_different_colours_produce_different_pours():
    assert _base_stop(beer_glass_svg("#1a0d00")) != _base_stop(beer_glass_svg("#f8e08a"))


def test_unknown_colour_falls_back_to_this_surfaces_amber():
    # Unknown is expressed by sending no colour at all. This surface declares
    # amber for it - deliberately NOT the swatch's grey (ADR-0004).
    assert _base_stop(beer_glass_svg()).lower() == _DEFAULT_HEX
    assert _base_stop(beer_glass_svg(None)).lower() == _DEFAULT_HEX
    assert _DEFAULT_HEX != UNKNOWN_SWATCH_HEX


def test_unparseable_colour_is_treated_as_unknown():
    # A malformed value in an old cached URL must not produce a broken SVG.
    assert _base_stop(beer_glass_svg("not-a-colour")).lower() == _DEFAULT_HEX


def test_normalize_glass_falls_back():
    assert normalize_glass("tulip") == "tulip"
    assert normalize_glass("nope") == "default"
    assert normalize_glass(None) == "default"


def test_every_glass_type_renders_valid_svg():
    for key in GLASS_KEYS:
        svg = beer_glass_svg("#c07f1a", glass=key)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert 'fill="url(#g)"' in svg   # the liquid uses the shared gradient
