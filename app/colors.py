"""EBC/SRM -> hex beer-colour mapping, server-side twin of the JS ebcToHex().

Colour is produced by the ebc2hex polynomial model
(github.com/moussaclarke/ebc2hexjs): the EBC is clamped to the model's 0..80
range, converted to SRM, and each RGB channel is fitted with its own curve. An
optional `saturation` (0..1) then blends the colour towards its luminance grey,
so a per-beer override can mute a too-vivid swatch.

The display (static/js/display.js) no longer recomputes colour: the board API
sends the computed `color_hex` / `text_color` for every tap, so the swatch, the
glass placeholder and the API all agree and there is a single implementation to
keep correct.
"""
from __future__ import annotations

from typing import Any

EBC_PER_SRM = 1.97  # stat-unit conversion: EBC = SRM * 1.97; SRM = EBC / 1.97

# The colour model's own EBC->SRM factor (~1/1.97) and clamp range.
_EBC_TO_SRM = 0.508
_EBC_MAX = 80.0

# 1.0 keeps the model's full colour; lower values mute it towards grey. Used
# when a beer has no per-tap saturation override.
DEFAULT_SATURATION = 1.0


def ebc_to_srm(ebc: float | int | None) -> float | None:
    """Convert a stored EBC value to SRM (None passes through)."""
    if ebc is None:
        return None
    try:
        return float(ebc) / EBC_PER_SRM
    except (TypeError, ValueError):
        return None


def srm_to_ebc(srm: float | int | None) -> float | None:
    """Convert an SRM value to EBC for storage (None passes through)."""
    if srm is None:
        return None
    try:
        return float(srm) * EBC_PER_SRM
    except (TypeError, ValueError):
        return None


def parse_saturation(value: Any, default: float | None = None) -> float | None:
    """Normalise a saturation value to a 0..1 fraction (or `default` if blank).

    Accepts a fraction (``0.6``) or a percentage (``60`` -> ``0.6``): any value
    greater than 1 is read as a percentage. The result is clamped to [0, 1].
    Blank / non-numeric input returns `default`.
    """
    if value is None or value == "":
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f > 1:
        f /= 100.0
    return max(0.0, min(1.0, f))


def _clamp8(v: float) -> int:
    return max(0, min(255, round(v)))


def _desaturate(r: float, g: float, b: float, sat: float) -> tuple[float, float, float]:
    """Blend an RGB triple towards its luminance grey. sat=1 keeps the colour."""
    gray = (r * 0.3086 + g * 0.6094 + b * 0.0820) * (1.0 - sat)
    return (r * sat + gray, g * sat + gray, b * sat + gray)


def ebc_to_hex(ebc: float | int | None,
               saturation: float | None = DEFAULT_SATURATION) -> str:
    """Map an EBC value to a #rrggbb beer colour. None/invalid -> a neutral grey.

    `saturation` is a 0..1 fraction (None -> DEFAULT_SATURATION); below 1 it
    mutes the colour towards grey via `_desaturate`.
    """
    if ebc is None:
        return "#cccccc"
    try:
        ebc_f = float(ebc)
    except (TypeError, ValueError):
        return "#cccccc"

    srm = max(0.0, min(_EBC_MAX, ebc_f)) * _EBC_TO_SRM
    # Per-channel fits from the ebc2hex model (red capped high, blue floored low,
    # matching the reference implementation before desaturation).
    r = min(255.0, round(280 - srm * 5.65))
    g = round(0.188349 * srm**2 - 13.2676 * srm + 239.51)
    b = round(0.000933566 * srm**4 - 0.0894788 * srm**3
              + 3.00611 * srm**2 - 40.8883 * srm + 183.409)
    if b < 0:
        b = 0

    sat = DEFAULT_SATURATION if saturation is None else max(0.0, min(1.0, saturation))
    r, g, b = _desaturate(r, g, b, sat)
    return f"#{_clamp8(r):02x}{_clamp8(g):02x}{_clamp8(b):02x}"


def relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG relative luminance of an sRGB colour (0..1)."""
    def lin(c: int) -> float:
        cs = c / 255.0
        return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def text_color_for(hex_color: str) -> str:
    """Pick legible text/badge colour (light or dark) for a swatch background.

    High-EBC beers converge to near-black, so dark text on them would be
    illegible; this returns light text there and dark text on pale beers.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "#111111"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#f5f5f5" if relative_luminance(r, g, b) < 0.4 else "#161616"
