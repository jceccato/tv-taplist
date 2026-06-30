"""Generate a beer-glass SVG tinted to match a beer's colour.

Used as the image for taps that have no uploaded photo, so the placeholder beer
in the glass matches the beer's SRM/EBC colour instead of a fixed gold. The base
liquid colour reuses the same EBC->hex mapping as the colour swatch (or a per-beer
hex override), so the two always agree.

Several glass silhouettes are available (`GLASS_TYPES`); the shape is chosen by
the global default or a per-beer override, the tint by the beer's colour.
"""
from __future__ import annotations

from .colors import ebc_to_hex, parse_hex_color

# Fallback liquid colour when a beer's colour is unknown (a neutral amber).
_DEFAULT_HEX = "#e8a020"

# Selectable glassware, in admin display order: (key, label).
GLASS_TYPES: list[tuple[str, str]] = [
    ("default", "Shaker pint (default)"),
    ("nonicpint", "Nonic pint"),
    ("schooner", "Conical schooner"),
    ("tulip", "Tulip"),
    ("teku", "Teku"),
]
GLASS_KEYS = {k for k, _ in GLASS_TYPES}
DEFAULT_GLASS = "default"

# Tints for the (clear) glass stem and foot on stemmed glasses.
_GLASS_FILL = "rgba(214,226,240,0.16)"
_GLASS_STROKE = "rgba(255,255,255,0.28)"


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


def _stem(top_y: int) -> str:
    """A clear-glass stem + foot below a stemmed bowl (tulip / teku)."""
    return (
        f'<rect x="144" y="{top_y}" width="12" height="{238 - top_y}" rx="3" '
        f'fill="{_GLASS_FILL}" stroke="{_GLASS_STROKE}" stroke-width="2"/>'
        f'<path d="M118 250 q32 -14 64 0 z" fill="{_GLASS_FILL}" '
        f'stroke="{_GLASS_STROKE}" stroke-width="2"/>'
    )


def _bubbles(c: str, pts: list[tuple[int, int, int, float]]) -> str:
    return "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}" opacity="{o}"/>'
        for x, y, r, o in pts
    )


def _glass_body(glass: str, base: str, foam: str, bubble: str) -> str:
    """Per-glass silhouette: liquid path (filled with the shared gradient), foam, stem."""
    liquid = ('fill="url(#g)" stroke="rgba(255,255,255,0.16)" stroke-width="3"')

    if glass == "nonicpint":
        # Tall straight sides with the characteristic nonic bulge near the top.
        return (
            f'<path d="M102 80 L102 112 L96 122 L104 132 L108 244 a9 9 0 0 0 9 8 '
            f'h58 a9 9 0 0 0 9 -8 L196 132 L204 122 L198 112 L198 80 Z" {liquid}/>'
            f'<ellipse cx="150" cy="80" rx="48" ry="14" fill="{foam}"/>'
            f'<circle cx="126" cy="72" r="11" fill="{foam}"/>'
            f'<circle cx="150" cy="67" r="14" fill="{foam}"/>'
            f'<circle cx="174" cy="72" r="11" fill="{foam}"/>'
            + _bubbles(bubble, [(132, 170, 5, 0.6), (160, 198, 4, 0.6), (146, 215, 6, 0.55)])
        )
    if glass == "schooner":
        # Conical / flared straight sides: wide rim, narrow base.
        return (
            f'<path d="M88 84 L212 84 L186 248 a7 7 0 0 1 -7 6 h-58 a7 7 0 0 1 -7 -6 Z" {liquid}/>'
            f'<ellipse cx="150" cy="84" rx="60" ry="16" fill="{foam}"/>'
            f'<circle cx="120" cy="76" r="13" fill="{foam}"/>'
            f'<circle cx="150" cy="70" r="16" fill="{foam}"/>'
            f'<circle cx="182" cy="76" r="13" fill="{foam}"/>'
            + _bubbles(bubble, [(138, 168, 5, 0.6), (162, 196, 4, 0.6), (150, 214, 6, 0.55)])
        )
    if glass == "tulip":
        # Rounded bowl that narrows to a stem, with a flared lip.
        return (
            f'<path d="M110 90 Q98 132 130 168 Q146 186 142 200 L158 200 '
            f'Q154 186 170 168 Q202 132 190 90 Q150 104 110 90 Z" {liquid}/>'
            f'<ellipse cx="150" cy="92" rx="40" ry="12" fill="{foam}"/>'
            f'<circle cx="132" cy="86" r="10" fill="{foam}"/>'
            f'<circle cx="154" cy="82" r="13" fill="{foam}"/>'
            f'<circle cx="174" cy="87" r="9" fill="{foam}"/>'
            + _bubbles(bubble, [(140, 150, 5, 0.6), (158, 168, 4, 0.55)])
            + _stem(200)
        )
    if glass == "teku":
        # Angular stemmed tulip: flared lip, sharp waist, short flare to the stem.
        return (
            f'<path d="M114 86 L186 86 L168 150 L172 200 L128 200 L132 150 Z" {liquid}/>'
            f'<ellipse cx="150" cy="86" rx="36" ry="11" fill="{foam}"/>'
            f'<circle cx="132" cy="80" r="9" fill="{foam}"/>'
            f'<circle cx="152" cy="77" r="12" fill="{foam}"/>'
            f'<circle cx="170" cy="81" r="9" fill="{foam}"/>'
            + _bubbles(bubble, [(142, 138, 5, 0.6), (158, 162, 4, 0.55)])
            + _stem(200)
        )

    # default: shaker / straight pint.
    return (
        f'<path d="M104 72 h92 l-10 150 a14 14 0 0 1 -14 12 h-44 a14 14 0 0 1 -14 -12 z" {liquid}/>'
        f'<ellipse cx="150" cy="72" rx="48" ry="17" fill="{foam}"/>'
        f'<circle cx="124" cy="64" r="13" fill="{foam}"/>'
        f'<circle cx="150" cy="58" r="16" fill="{foam}"/>'
        f'<circle cx="176" cy="64" r="13" fill="{foam}"/>'
        + _bubbles(bubble, [(140, 150, 5, 0.7), (158, 180, 4, 0.6), (148, 200, 6, 0.6), (160, 130, 3, 0.7)])
    )


def beer_glass_svg(ebc: float | int | None = None,
                   saturation: float | None = None,
                   glass: str | None = None,
                   hex_override: str | None = None) -> str:
    """Return an SVG beer glass whose liquid matches the beer's colour.

    `hex_override` (a ``#rrggbb`` string) wins over the EBC mapping when present,
    so a per-beer colour override is reflected in the placeholder pour. `glass`
    selects the silhouette (see `GLASS_TYPES`).
    """
    override = parse_hex_color(hex_override)
    if override:
        base = override
    elif ebc is not None:
        base = ebc_to_hex(ebc, saturation)
    else:
        base = _DEFAULT_HEX
    if not (isinstance(base, str) and base.startswith("#") and len(base) == 7):
        base = _DEFAULT_HEX

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
        + _glass_body(normalize_glass(glass), base, foam, bubble)
        + '</svg>'
    )
