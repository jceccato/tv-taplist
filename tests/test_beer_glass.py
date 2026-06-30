"""Beer-glass SVG: colour tinting, hex override, and glass silhouettes."""
import re

from app.beer_glass import GLASS_KEYS, beer_glass_svg, normalize_glass


def _base_stop(svg: str) -> str:
    return re.search(r'offset="55%" stop-color="(#[0-9a-fA-F]{6})"', svg).group(1)


def test_hex_override_drives_liquid_colour():
    svg = beer_glass_svg(ebc=4, hex_override="#780606")
    # The override wins over the (pale) EBC colour for the base liquid stop.
    assert _base_stop(svg).lower() == "#780606"


def test_ebc_used_when_no_override():
    dark = beer_glass_svg(ebc=80)
    pale = beer_glass_svg(ebc=4)
    assert _base_stop(dark) != _base_stop(pale)


def test_normalize_glass_falls_back():
    assert normalize_glass("tulip") == "tulip"
    assert normalize_glass("nope") == "default"
    assert normalize_glass(None) == "default"


def test_every_glass_type_renders_valid_svg():
    for key in GLASS_KEYS:
        svg = beer_glass_svg(ebc=20, glass=key)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert 'fill="url(#g)"' in svg   # the liquid uses the shared gradient
