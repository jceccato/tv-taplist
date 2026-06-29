"""EBC/SRM -> hex colour mapping, server-side twin of the JS ebcToHex().

We map EBC to a beer colour using the well-known SRM reference colour chart
(the values popularised by Bru'n Water / the SRM Wikipedia chart), converting
EBC to SRM first (SRM = EBC / 1.97). Colours are interpolated in RGB between
the two nearest integer SRM reference points and clamped to the 1..40 range.

The JS implementation in static/js/display.js uses the *same* table so the
display swatch matches what the API reports. Keep the two tables in sync.
"""
from __future__ import annotations

# SRM integer -> (R, G, B). Reference SRM colour chart, 1..40.
SRM_RGB: dict[int, tuple[int, int, int]] = {
    1: (0xFF, 0xE6, 0x99),
    2: (0xFF, 0xD8, 0x78),
    3: (0xFF, 0xCA, 0x5A),
    4: (0xFF, 0xBF, 0x42),
    5: (0xFB, 0xB1, 0x23),
    6: (0xF8, 0xA6, 0x00),
    7: (0xF3, 0x9C, 0x00),
    8: (0xEA, 0x8F, 0x00),
    9: (0xE5, 0x85, 0x00),
    10: (0xDE, 0x7C, 0x00),
    11: (0xD7, 0x72, 0x00),
    12: (0xCF, 0x69, 0x00),
    13: (0xCB, 0x62, 0x00),
    14: (0xC3, 0x59, 0x00),
    15: (0xBB, 0x51, 0x00),
    16: (0xB5, 0x4C, 0x00),
    17: (0xB0, 0x45, 0x00),
    18: (0xA6, 0x3E, 0x00),
    19: (0xA1, 0x37, 0x00),
    20: (0x9B, 0x32, 0x00),
    21: (0x95, 0x2D, 0x00),
    22: (0x8E, 0x29, 0x00),
    23: (0x88, 0x23, 0x00),
    24: (0x82, 0x1E, 0x00),
    25: (0x7B, 0x1A, 0x00),
    26: (0x77, 0x19, 0x00),
    27: (0x70, 0x14, 0x00),
    28: (0x6A, 0x0E, 0x00),
    29: (0x66, 0x0D, 0x00),
    30: (0x5E, 0x0B, 0x00),
    31: (0x5A, 0x0A, 0x02),
    32: (0x60, 0x09, 0x03),
    33: (0x52, 0x09, 0x07),
    34: (0x4C, 0x05, 0x05),
    35: (0x47, 0x06, 0x06),
    36: (0x44, 0x06, 0x07),
    37: (0x3F, 0x07, 0x08),
    38: (0x3B, 0x06, 0x07),
    39: (0x3A, 0x07, 0x0B),
    40: (0x36, 0x08, 0x0A),
}

_SRM_MIN = 1
_SRM_MAX = 40

EBC_PER_SRM = 1.97  # EBC = SRM * 1.97; SRM = EBC / 1.97


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


def srm_to_rgb(srm: float) -> tuple[int, int, int]:
    """Interpolate the SRM chart between integer reference points, clamped 1..40."""
    if srm <= _SRM_MIN:
        return SRM_RGB[_SRM_MIN]
    if srm >= _SRM_MAX:
        return SRM_RGB[_SRM_MAX]
    lo = int(srm)
    hi = lo + 1
    frac = srm - lo
    r0, g0, b0 = SRM_RGB[lo]
    r1, g1, b1 = SRM_RGB[hi]
    r = round(r0 + (r1 - r0) * frac)
    g = round(g0 + (g1 - g0) * frac)
    b = round(b0 + (b1 - b0) * frac)
    return r, g, b


def ebc_to_hex(ebc: float | int | None) -> str:
    """Map an EBC value to a #rrggbb beer colour. None/invalid -> a neutral grey."""
    if ebc is None:
        return "#cccccc"
    try:
        ebc_f = float(ebc)
    except (TypeError, ValueError):
        return "#cccccc"
    srm = ebc_f / EBC_PER_SRM
    r, g, b = srm_to_rgb(srm)
    return f"#{r:02x}{g:02x}{b:02x}"


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
