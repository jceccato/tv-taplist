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
    ("dimpledmug", "Dimpled mug"),
]
GLASS_KEYS = {k for k, _ in GLASS_TYPES}
DEFAULT_GLASS = "nonicpint"

# Surface detail etched INTO the pour: the dimpled mug's facets, and the light
# caught along the inside of each one. Both are strokes over the liquid, clipped
# to it, so they read as the glass the beer is seen through rather than as marks
# floating on top. Deliberately faint - at full strength they stop looking like
# glass and start looking like a pattern.
_ETCH_STROKE = "rgba(255,255,255,0.09)"
_ETCH_WIDTH = 3.75
_SHEEN_STROKE = "rgba(255,255,255,0.09)"
_SHEEN_WIDTH = 5.25

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
    """One glass: the pour, its head, its bubbles, and optional glass detail.

    `pour`, `stem`, `etch` and `sheen` are SVG path data in the 300x300 viewBox,
    symmetrical about x=150. `head` is (cy, rx, ry) for the ellipse of foam
    sitting in the mouth; `bubbles` are (cx, cy, r, opacity) inside the pour.

    `stem` is any glass drawn BEHIND the pour in the glass tint - the stem and
    foot of a stemmed glass, or the mug's handle. The name is historical and
    describes where the path is drawn, not what it depicts.

    `etch` and `sheen` are drawn OVER the pour and clipped to it: the facets of
    a dimpled mug, and the light along the inside of each. A shape that runs
    past the profile is cut by it, which is what lets a row of dimples sit half
    off the edge of the glass the way it does on the real thing.
    """

    pour: str
    head: tuple[float, float, float]
    bubbles: tuple[tuple[float, float, float, float], ...]
    stem: str | None = None
    etch: str | None = None
    sheen: str | None = None


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
    # Dimpled mug: a squat barrel with a handle, its faces laid out in three
    # staggered courses so the outer pair of each odd course is cut in half by
    # the profile. The bottom course is taller - the dimpling runs out before
    # the base does. No bubbles: the facets already carry the surface, and a
    # bubble among them reads as a smudge.
    "dimpledmug": _Silhouette(
        pour=(
            "M 85 80 C 78.5 123 80 199 100.5 247 A 1 0.16 0 0 0 199.5 247 "
            "C 220 199 221.5 123 215 80 Z"
        ),
        head=(80, 65.0, 20.8),
        bubbles=(),
        stem=(
            "M 216 99 C 245 97 273 97 266 160 C 259 227 211 217 205 222 L 211 201 "
            "C 211 207 244 210 252 169 C 269 97 219 112 217 116 Z"
        ),
        etch=(
            "M 75.69 109.12 L 88.85 109.12 A 11.48 11.48 0 0 1 100.33 120.6 "
            "L 100.33 131.4 A 11.48 11.48 0 0 1 88.85 142.88 L 75.69 142.88 "
            "A 11.48 11.48 0 0 1 64.21 131.4 L 64.21 120.6 "
            "A 11.48 11.48 0 0 1 75.69 109.12 Z "
            "M 120.84 113.36 L 134 113.36 A 11.48 11.48 0 0 1 145.48 124.84 "
            "L 145.48 135.65 A 11.48 11.48 0 0 1 134 147.13 L 120.84 147.13 "
            "A 11.48 11.48 0 0 1 109.36 135.65 L 109.36 124.84 "
            "A 11.48 11.48 0 0 1 120.84 113.36 Z "
            "M 166 113.36 L 179.16 113.36 A 11.48 11.48 0 0 1 190.64 124.84 "
            "L 190.64 135.65 A 11.48 11.48 0 0 1 179.16 147.13 L 166 147.13 "
            "A 11.48 11.48 0 0 1 154.52 135.65 L 154.52 124.84 "
            "A 11.48 11.48 0 0 1 166 113.36 Z "
            "M 211.15 109.12 L 224.31 109.12 A 11.48 11.48 0 0 1 235.79 120.6 "
            "L 235.79 131.4 A 11.48 11.48 0 0 1 224.31 142.88 L 211.15 142.88 "
            "A 11.48 11.48 0 0 1 199.67 131.4 L 199.67 120.6 "
            "A 11.48 11.48 0 0 1 211.15 109.12 Z "
            "M 100.06 154.46 L 112.19 154.46 A 11.48 11.48 0 0 1 123.67 165.94 "
            "L 123.67 176.75 A 11.48 11.48 0 0 1 112.19 188.23 L 100.06 188.23 "
            "A 11.48 11.48 0 0 1 88.57 176.75 L 88.57 165.94 "
            "A 11.48 11.48 0 0 1 100.06 154.46 Z "
            "M 143.93 157.46 L 156.07 157.46 A 11.48 11.48 0 0 1 167.55 168.94 "
            "L 167.55 179.75 A 11.48 11.48 0 0 1 156.07 191.23 L 143.93 191.23 "
            "A 11.48 11.48 0 0 1 132.45 179.75 L 132.45 168.94 "
            "A 11.48 11.48 0 0 1 143.93 157.46 Z "
            "M 187.81 154.46 L 199.94 154.46 A 11.48 11.48 0 0 1 211.43 165.94 "
            "L 211.43 176.75 A 11.48 11.48 0 0 1 199.94 188.23 L 187.81 188.23 "
            "A 11.48 11.48 0 0 1 176.33 176.75 L 176.33 165.94 "
            "A 11.48 11.48 0 0 1 187.81 154.46 Z "
            "M 86.9 195.56 L 96.82 195.56 A 10.54 10.54 0 0 1 107.37 206.11 "
            "L 107.37 225.55 A 10.54 10.54 0 0 1 96.82 236.09 L 86.9 236.09 "
            "A 10.54 10.54 0 0 1 76.36 225.55 L 76.36 206.11 "
            "A 10.54 10.54 0 0 1 86.9 195.56 Z "
            "M 125.66 199.81 L 135.58 199.81 A 10.54 10.54 0 0 1 146.12 210.35 "
            "L 146.12 229.79 A 10.54 10.54 0 0 1 135.58 240.33 L 125.66 240.33 "
            "A 10.54 10.54 0 0 1 115.12 229.79 L 115.12 210.35 "
            "A 10.54 10.54 0 0 1 125.66 199.81 Z "
            "M 164.42 199.81 L 174.34 199.81 A 10.54 10.54 0 0 1 184.88 210.35 "
            "L 184.88 229.79 A 10.54 10.54 0 0 1 174.34 240.33 L 164.42 240.33 "
            "A 10.54 10.54 0 0 1 153.88 229.79 L 153.88 210.35 "
            "A 10.54 10.54 0 0 1 164.42 199.81 Z "
            "M 203.18 195.56 L 213.1 195.56 A 10.54 10.54 0 0 1 223.64 206.11 "
            "L 223.64 225.55 A 10.54 10.54 0 0 1 213.1 236.09 L 203.18 236.09 "
            "A 10.54 10.54 0 0 1 192.63 225.55 L 192.63 206.11 "
            "A 10.54 10.54 0 0 1 203.18 195.56 Z"
        ),
        sheen=(
            "M 80.43 142.88 L 75.69 142.88 A 11.48 11.48 0 0 1 64.21 131.4 "
            "L 64.21 127.51 "
            "M 125.58 147.13 L 120.84 147.13 A 11.48 11.48 0 0 1 109.36 135.65 "
            "L 109.36 131.76 "
            "M 170.73 147.13 L 166 147.13 A 11.48 11.48 0 0 1 154.52 135.65 "
            "L 154.52 131.76 "
            "M 215.89 142.88 L 211.15 142.88 A 11.48 11.48 0 0 1 199.67 131.4 "
            "L 199.67 127.51 "
            "M 104.43 188.23 L 100.06 188.23 A 11.48 11.48 0 0 1 88.57 176.75 "
            "L 88.57 172.86 "
            "M 148.3 191.23 L 143.93 191.23 A 11.48 11.48 0 0 1 132.45 179.75 "
            "L 132.45 175.86 "
            "M 192.18 188.23 L 187.81 188.23 A 11.48 11.48 0 0 1 176.33 176.75 "
            "L 176.33 172.86 "
            "M 90.47 236.09 L 86.9 236.09 A 10.54 10.54 0 0 1 76.36 225.55 "
            "L 76.36 218.55 "
            "M 129.23 240.33 L 125.66 240.33 A 10.54 10.54 0 0 1 115.12 229.79 "
            "L 115.12 222.79 "
            "M 167.99 240.33 L 164.42 240.33 A 10.54 10.54 0 0 1 153.88 229.79 "
            "L 153.88 222.79 "
            "M 206.75 236.09 L 203.18 236.09 A 10.54 10.54 0 0 1 192.63 225.55 "
            "L 192.63 218.55"
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


def _etch(shape: _Silhouette) -> str:
    """Facets and their highlights, clipped to the pour they are etched into."""
    if not shape.etch:
        return ""
    out = (f'<clipPath id="p"><path d="{shape.pour}"/></clipPath>'
           f'<g clip-path="url(#p)">'
           f'<path d="{shape.etch}" fill="none" stroke="{_ETCH_STROKE}" '
           f'stroke-width="{_ETCH_WIDTH:g}"/>')
    if shape.sheen:
        out += (f'<path d="{shape.sheen}" fill="none" stroke-linecap="round" '
                f'stroke="{_SHEEN_STROKE}" stroke-width="{_SHEEN_WIDTH:g}"/>')
    return out + "</g>"


def _glass_body(glass: str, foam: str, bubble: str) -> str:
    """One silhouette: glass behind the pour, then its etching, head and bubbles."""
    shape = _SILHOUETTES[glass]
    liquid = 'fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"'
    out = ""
    if shape.stem:
        out += (f'<path d="{shape.stem}" fill="{_GLASS_FILL}" '
                f'stroke="{_GLASS_STROKE}" stroke-width="2"/>')
    out += f'<path d="{shape.pour}" {liquid}/>' + _etch(shape)
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
