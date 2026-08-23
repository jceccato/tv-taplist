"""Generate a beer-glass SVG tinted to an already-resolved beer Colour.

Used as the image for taps that have no uploaded photo, so the placeholder beer
in the glass matches the beer's Colour instead of a fixed gold. This module
resolves nothing: it is handed the colour `colors.resolve_color` produced for
the beer - the same value the swatch is painted with - which is what guarantees
the two agree.

Several glass silhouettes are available (`GLASS_TYPES`); the shape is chosen by
the global default or a per-beer override, the tint by the beer's colour.

**A silhouette is data, not code.** Every glass is one row of `_SILHOUETTES`:
the pour's path, an optional stem path, where the head sits, and where the
bubbles sit. Adding a glass is adding a row - there is no branching to extend,
and nothing else in the module needs to know how many glasses exist. The paths
themselves were modelled by hand and then made symmetrical about x=150 by rule
(issue #6); the throwaway harness that produced them is not shipped, so treat
the path data here as the source and re-run that harness if a shape changes.
"""
from __future__ import annotations

from typing import NamedTuple

from .colors import parse_hex_color

# What this surface draws when Colour is Unknown: a neutral amber. Deliberately
# NOT the swatch's grey - a Placeholder is an illustration of a glass of beer,
# and a grey pour reads as a broken image rather than as "colour unknown", while
# amber reads as "a beer", the most that can honestly be said. Resolution
# answers Unknown and each surface declares its own fallback - see ADR-0004
# before unifying this with colors.UNKNOWN_SWATCH_HEX.
_DEFAULT_HEX = "#e8a020"

# Selectable glassware, in admin display order: (key, label). The nonic pint is
# the default: it reads as a beer glass at every size the board renders, where
# the shaker's straight sides can pass for a tumbler once the card is small.
# The `default` KEY still means the shaker - it is written into operators'
# config.json, their per-tap override files and their Brewfather notes, so
# renaming it would silently repoint every board that uses it.
GLASS_TYPES: list[tuple[str, str]] = [
    ("nonicpint", "Nonic pint (default)"),
    ("default", "Shaker pint"),
    ("schooner", "Conical schooner"),
    ("tulip", "Tulip"),
    ("teku", "Teku"),
]
GLASS_KEYS = {k for k, _ in GLASS_TYPES}
DEFAULT_GLASS = "nonicpint"

# Tints for the (clear) glass stem and foot on stemmed glasses. A mid-grey, not
# the near-white translucent this used to be: the stemmed glasses now carry a
# full stem and a wide foot, and a near-white one is invisible against the
# Daylight theme, leaving the bowl floating. This reads against both ends of the
# theme range. Any new stemmed glass should use these rather than its own.
_GLASS_FILL = "rgba(146,160,180,0.30)"
_GLASS_STROKE = "rgba(108,124,146,0.75)"

# How the three blobs of the head are placed, as fractions of the head ellipse:
# (dx * rx, dy * ry, r * rx). Shared by every glass so a new silhouette only has
# to say where its mouth is and how wide it is.
_HEAD_BLOBS = ((-0.55, -0.60, 0.30), (0.0, -0.95, 0.36), (0.55, -0.60, 0.30))


class _Silhouette(NamedTuple):
    """One glass: the pour, its head, its bubbles, and an optional stem.

    `pour` and `stem` are SVG path data in the 300x300 viewBox, symmetrical
    about x=150. `head` is (cy, rx, ry) for the ellipse of foam sitting in the
    mouth; `bubbles` are (cx, cy, r, opacity) inside the pour.
    """

    pour: str
    head: tuple[float, float, float]
    bubbles: tuple[tuple[float, float, float, float], ...]
    stem: str | None = None


_SILHOUETTES: dict[str, _Silhouette] = {
    # Nonic pint: straight sides broken by the bulge a third from the top.
    "nonicpint": _Silhouette(
        pour=(
            "M 106.5 88 L 107.5 110 Q 103.87 125.02 109.49 136.23 "
            "C 110.24 138.3 110.59 142.02 110.82 145.01 L 117.5 229.5 "
            "A 1 0.4 0 0 0 182.5 229.5 L 189.18 145.01 "
            "C 189.41 142.02 189.76 138.3 190.51 136.23 "
            "Q 196.13 125.02 192.5 110 L 193.5 88 Z"
        ),
        head=(88, 40.5, 13.0),
        bubbles=((135.2, 147.4, 4.5, 0.6), (164.8, 172.9, 4.0, 0.55),
                 (146.3, 198.4, 5.0, 0.5)),
    ),
    # Shaker pint: dead-straight sides, a wide mouth, a flat floor.
    "default": _Silhouette(
        pour="M 100.5 70 L 116 228 Q 117 238 127 238 L 173 238 Q 183 238 184 228 L 199.5 70 Z",
        head=(70, 46.5, 14.9),
        bubbles=((134.2, 140.6, 4.5, 0.6), (165.8, 170.8, 4.0, 0.55),
                 (146.0, 201.0, 5.0, 0.5)),
    ),
    # Conical schooner: vertical rim, one continuous taper, and a vertical base
    # meeting the table square - no flare back out at the foot.
    "schooner": _Silhouette(
        pour=(
            "M 194 76 C 195.05 85.83 195.11 92.14 194.9 100.14 "
            "C 193.25 137.29 185.84 161.82 183.77 184.46 "
            "C 182.33 204.09 181.69 220.08 181.5 236 "
            "A 1 0.11 0 0 1 118.5 236 "
            "C 118.31 220.08 117.67 204.09 116.23 184.46 "
            "C 114.16 161.82 106.75 137.29 105.1 100.14 "
            "C 104.89 92.14 104.95 85.83 106 76 Z"
        ),
        head=(76, 41.0, 13.1),
        bubbles=((135.6, 143.2, 4.5, 0.6), (164.4, 172.0, 4.0, 0.55),
                 (146.4, 200.8, 5.0, 0.5)),
    ),
    # Tulip: straight collar easing into a bowl whose mass sits high, pinching
    # in above a short, thick stem.
    "tulip": _Silhouette(
        pour=(
            "M 106 71 C 102 100 97 110 93.5 122.5 C 82.5 175.5 116.42 196.37 135 203 "
            "Q 150 208 165 203 C 183.58 196.37 217.5 175.5 206.5 122.5 "
            "C 203 110 198 100 194 71 Z"
        ),
        head=(71, 41.0, 13.1),
        bubbles=((128.4, 128.5, 4.5, 0.6), (171.6, 153.2, 4.0, 0.55),
                 (144.6, 177.9, 5.0, 0.5)),
        stem=(
            "M 197.5 255 C 165 251.5 159 240 159.14 225.02 "
            "C 159.38 216.31 159.15 207.5 165 203 Q 150 208 135 203 "
            "C 140.85 207.5 140.62 216.31 140.86 225.02 C 141 240 135 251.5 102.5 255 "
            "A 47 18 0 0 0 197.5 255 Z "
            "M 102.5 255 A 47 18 0 1 0 197.5 255 A 47 13 0 1 0 102.5 255 Z"
        ),
    ),
    # Teku: lipped rim, walls widening downward to a low widest point, then a
    # turn under to a stem about as tall as the bowl.
    "teku": _Silhouette(
        pour=(
            "M 114.68 51.99 C 117.77 57.55 118.16 64.42 117.05 67.63 "
            "C 110.36 84.47 102.68 118.18 99.31 134.36 "
            "C 97.06 145.81 117 157.5 138.5 167 C 146 170 154 170 161.5 167 "
            "C 183 157.5 202.94 145.81 200.69 134.36 "
            "C 197.32 118.18 189.64 84.47 182.95 67.63 "
            "C 181.84 64.42 182.23 57.55 185.32 51.99 Z"
        ),
        head=(51.99, 32.3, 10.3),
        bubbles=((133.1, 101.6, 4.5, 0.6), (166.9, 122.8, 4.0, 0.55),
                 (145.8, 144.0, 5.0, 0.5)),
        stem=(
            "M 192 264 C 166.5 255 154.5 259 154 223 C 154 210.5 153.5 187.5 162.34 166.44 "
            "C 154.56 169.7 145.44 169.7 137.66 166.44 "
            "C 146.5 187.5 146 210.5 146 223 C 145.5 259 133.5 255 108 264 "
            "A 42 8 0 0 0 192 264 Z "
            "M 108 264 A 42 8 0 1 0 192 264 A 42 8 0 1 0 108 264 Z"
        ),
    ),
}


def normalize_glass(value: object) -> str:
    """Coerce a glass key to a known type, falling back to the default."""
    return value if isinstance(value, str) and value in GLASS_KEYS else DEFAULT_GLASS


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    clamp = lambda v: max(0, min(255, round(v)))
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    """Blend two hex colours; t=0 -> a, t=1 -> b."""
    ar, ag, ab = _hex_to_rgb(hex_a)
    br, bg, bb = _hex_to_rgb(hex_b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


def _head(cy: float, rx: float, ry: float, foam: str) -> str:
    """The head: a surface ellipse in the mouth, three blobs mounding over it."""
    out = f'<ellipse cx="150" cy="{cy:g}" rx="{rx:g}" ry="{ry:g}" fill="{foam}"/>'
    return out + "".join(
        f'<circle cx="{150 + rx * dx:.1f}" cy="{cy + ry * dy:.1f}" '
        f'r="{rx * r:.1f}" fill="{foam}"/>'
        for dx, dy, r in _HEAD_BLOBS
    )


def _bubbles(c: str, pts) -> str:
    return "".join(
        f'<circle cx="{x:g}" cy="{y:g}" r="{r:g}" fill="{c}" opacity="{o:g}"/>'
        for x, y, r, o in pts
    )


def _glass_body(glass: str, foam: str, bubble: str) -> str:
    """One silhouette: its stem (if any) behind the pour, then head and bubbles."""
    shape = _SILHOUETTES[glass]
    liquid = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'
    out = ""
    if shape.stem:
        out += (f'<path d="{shape.stem}" fill="{_GLASS_FILL}" '
                f'stroke="{_GLASS_STROKE}" stroke-width="2"/>')
    out += f'<path d="{shape.pour}" {liquid}/>'
    return out + _head(*shape.head, foam) + _bubbles(bubble, shape.bubbles)


def beer_glass_svg(color: str | None = None, glass: str | None = None) -> str:
    """Return an SVG beer glass whose liquid is tinted to a resolved Colour.

    `color` is the beer's **already-resolved** Colour as a hex string (with or
    without the leading ``#``); `None` - or anything that will not parse as a
    hex colour - means Unknown and selects this renderer's amber fallback. EBC
    and saturation are deliberately absent: they are inputs to resolution, which
    happens once in `colors.resolve_color` before this is ever called, so the
    pour cannot drift from the swatch.

    `glass` selects the silhouette (see `GLASS_TYPES`).
    """
    base = parse_hex_color(color) or _DEFAULT_HEX

    top = _mix(base, "#ffffff", 0.30)     # lighter towards the top of the pour
    bottom = _mix(base, "#000000", 0.28)  # darker at the base
    foam = _mix(base, "#ffffff", 0.80)    # creamy head, tinted by the beer
    bubble = _mix(base, "#ffffff", 0.55)

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" '
        'width="300" height="300" role="img" aria-label="Beer">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{top}"/>'
        f'<stop offset="55%" stop-color="{base}"/>'
        f'<stop offset="100%" stop-color="{bottom}"/>'
        '</linearGradient></defs>'
        + _glass_body(normalize_glass(glass), foam, bubble)
        + '</svg>'
    )
