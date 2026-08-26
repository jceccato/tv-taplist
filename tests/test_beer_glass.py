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
    from app.beer_glass import DEFAULT_GLASS

    assert normalize_glass("tulip") == "tulip"
    assert normalize_glass("default") == "default"   # the shaker's key still resolves
    # Unknown and absent both land on whatever the default glass currently is,
    # which is the point of the helper - the specific key is a product choice.
    assert normalize_glass("nope") == DEFAULT_GLASS
    assert normalize_glass(None) == DEFAULT_GLASS


def test_every_glass_type_renders_valid_svg():
    for key in GLASS_KEYS:
        svg = beer_glass_svg("#c07f1a", glass=key)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert 'fill="url(#g)"' in svg   # the liquid uses the shared gradient


def test_every_glass_key_has_a_silhouette_and_nothing_extra():
    """The selectable list and the drawing table are one set, not two.

    A new glass is added by writing a row and listing it; forgetting either half
    is a KeyError on a live board, so the two are pinned to agree here.
    """
    from app.beer_glass import _SILHOUETTES

    assert set(_SILHOUETTES) == GLASS_KEYS
    from app.beer_glass import DEFAULT_GLASS

    assert DEFAULT_GLASS in GLASS_KEYS


def test_every_silhouette_is_centred_on_the_canvas():
    """The head sits at x=150, so every pour has to be drawn symmetrically there.

    Hand-modelled shapes arrive off-centre and are corrected before they land
    here (issue #6). This is the guard that a future one was actually corrected:
    the head is drawn at 150 unconditionally, so a pour centred anywhere else
    wears its foam off to one side.
    """
    for key in GLASS_KEYS:
        svg = beer_glass_svg("#c07f1a", glass=key)
        assert svg.count('<ellipse cx="150"') == 1, key


def test_stemmed_glasses_draw_the_stem_behind_the_pour():
    """Layering, and the one tint. The stem is drawn first so the liquid sits in
    front of it, and it uses the shared glass tint rather than one of its own -
    a near-white stem vanishes on the Daylight theme."""
    from app.beer_glass import _GLASS_FILL, _SILHOUETTES

    for key in ("tulip", "teku", "dimpledmug"):
        assert _SILHOUETTES[key].stem, key
        svg = beer_glass_svg("#c07f1a", glass=key)
        assert svg.index(_GLASS_FILL) < svg.index('fill="url(#g)"'), key


def test_etched_glasses_clip_their_detail_to_the_pour():
    """The facets are etched INTO the liquid, not laid on top of it.

    The clip is what lets a course of dimples run past the profile and be cut
    in half by it, the way a real mug's are. Without it the overhang draws
    outside the glass and the shape reads as broken.
    """
    from app.beer_glass import _SILHOUETTES

    for key, shape in _SILHOUETTES.items():
        if not shape.etch:
            continue
        svg = beer_glass_svg("#c07f1a", glass=key)
        assert '<clipPath id="p">' in svg, key
        assert 'clip-path="url(#p)"' in svg, key
        # Over the liquid, under the foam: a facet crossing the head would be
        # drawn on the surface of the beer rather than through the glass.
        assert svg.index('fill="url(#g)"') < svg.index('clip-path="url(#p)"') < \
            svg.index("<ellipse"), key
