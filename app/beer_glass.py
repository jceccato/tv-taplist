"""Generate a beer-glass SVG tinted to match a beer's colour.

Used as the image for taps that have no uploaded photo, so the placeholder beer
in the glass matches the beer's SRM/EBC colour instead of a fixed gold. The base
liquid colour reuses the same EBC->hex mapping as the colour swatch, so the two
always agree.
"""
from __future__ import annotations

from .colors import ebc_to_hex

# Fallback liquid colour when a beer's colour is unknown (a neutral amber).
_DEFAULT_HEX = "#e8a020"


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


def beer_glass_svg(ebc: float | int | None = None) -> str:
    """Return an SVG of a beer glass whose liquid matches the beer's colour."""
    base = ebc_to_hex(ebc) if ebc is not None else _DEFAULT_HEX
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
        '<path d="M104 72 h92 l-10 150 a14 14 0 0 1 -14 12 h-44 a14 14 0 0 1 -14 -12 z" '
        f'fill="url(#g)" stroke="rgba(255,255,255,0.18)" stroke-width="3"/>'
        f'<ellipse cx="150" cy="72" rx="48" ry="17" fill="{foam}"/>'
        f'<circle cx="124" cy="64" r="13" fill="{foam}"/>'
        f'<circle cx="150" cy="58" r="16" fill="{foam}"/>'
        f'<circle cx="176" cy="64" r="13" fill="{foam}"/>'
        f'<circle cx="140" cy="150" r="5" fill="{bubble}" opacity="0.7"/>'
        f'<circle cx="158" cy="180" r="4" fill="{bubble}" opacity="0.6"/>'
        f'<circle cx="148" cy="200" r="6" fill="{bubble}" opacity="0.6"/>'
        f'<circle cx="160" cy="130" r="3" fill="{bubble}" opacity="0.7"/>'
        '</svg>'
    )
