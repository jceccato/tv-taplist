"""Colour resolution plus the EBC/SRM -> hex mapping it computes with.

`resolve_color` is the **only** place Colour precedence is expressed: a Colour
override, then the EBC-derived colour, then _Unknown_. It answers with a
resolved colour or with Unknown and stops there - it never substitutes a
fallback, because the colour drawn for Unknown belongs to the surface doing the
drawing (a grey swatch reads as "no data"; a grey pour reads as a broken image).
See ADR-0004.

The computed colour comes from the ebc2hex polynomial model
(github.com/moussaclarke/ebc2hexjs): the EBC is clamped to the model's 0..80
range, converted to SRM, and each RGB channel is fitted with its own curve. An
optional `saturation` (0..1) then blends the colour towards its luminance grey,
so a per-beer override can mute a too-vivid swatch.

The display (static/js/display.js) never recomputes colour: the board API sends
the resolved `color_hex` / `text_color` for every tap, and the placeholder-glass
URL carries the resolved colour too, so the swatch, the glass placeholder and
the admin preview cannot disagree about a *known* Colour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

EBC_PER_SRM = 1.97  # stat-unit conversion: EBC = SRM * 1.97; SRM = EBC / 1.97

# #rrggbb or #rgb (with or without the leading #).
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

# The colour model's own EBC->SRM factor (~1/1.97) and clamp range.
_EBC_TO_SRM = 0.508
_EBC_MAX = 80.0

# 1.0 keeps the model's full colour; lower values mute it towards grey. Used
# when a beer has no per-tap saturation override.
DEFAULT_SATURATION = 1.0

# What a *swatch* draws when Colour is Unknown. This is the swatch surface's
# declared fallback, not a resolved Colour: `resolve_color` answers Unknown and
# each surface decides what that looks like. The glassware Placeholder declares
# a different one (`beer_glass._DEFAULT_HEX`, an amber) on purpose - ADR-0004.
# static/js/display.js mirrors this literal for the swatch it paints, guarded by
# tests/test_frontend_constants.py.
UNKNOWN_SWATCH_HEX = "#cccccc"


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


def display_color_to_ebc(value: float | int | None, unit: str) -> float | int | None:
    """Convert a Colour typed in the operator's display unit into stored EBC.

    EBC is the only stored form of a Beer's Colour; SRM is a display unit that
    exists at the Admin form and nowhere else (see CONTEXT.md's Colour entry).
    This function is where that sentence is enforced, so the override save and
    the Admin's live preview cannot drift into converting differently - they
    call this, rather than each repeating the multiply.

    Rounded on the way in because the number is about to be written into a
    hand-editable Tap file: `19.700000000000003` in front matter is noise an
    operator would have to read past, and a tenth of an EBC is far below the
    colour model's resolution. `None` passes through - Unknown is a real answer.
    """
    if value is None or value == "":
        return None
    if str(unit).lower() == "srm":
        ebc = srm_to_ebc(value)
    else:
        try:
            ebc = float(value)
        except (TypeError, ValueError):
            ebc = None
    if ebc is None:
        return None
    return int(ebc) if ebc.is_integer() else round(ebc, 1)


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


def parse_hex_color(value: Any) -> str | None:
    """Normalise a colour string to ``#rrggbb`` (lowercase), or None if invalid.

    Accepts ``#780606``, ``780606``, ``#abc`` (expanded to ``#aabbcc``). Used for
    the per-beer colour override and for validating custom theme colours.
    """
    if not isinstance(value, str):
        return None
    m = _HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.lower()


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

    This is the *computed* branch of Colour, not resolution: it always returns a
    colour, so a caller that needs to know whether the beer has one at all must
    ask `resolve_color` instead. The no-EBC grey here is the swatch's fallback
    (`UNKNOWN_SWATCH_HEX`) because this function predates resolution and the
    swatch was its only caller with nothing to draw; nothing in the app reaches
    it with a missing EBC any more.
    """
    if ebc is None:
        return UNKNOWN_SWATCH_HEX
    try:
        ebc_f = float(ebc)
    except (TypeError, ValueError):
        return UNKNOWN_SWATCH_HEX

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


@dataclass(frozen=True)
class ResolvedColor:
    """A Colour that resolved: the hex and the text colour legible on it.

    The pair travels together so no caller can pick one up without the other and
    re-derive the contrast rule for itself.
    """

    color_hex: str
    text_color: str


def resolve_color(ebc: Any = None,
                  saturation: float | None = None,
                  color_override: Any = None) -> ResolvedColor | None:
    """Resolve a Beer's Colour: override, then EBC, then Unknown (None).

    The single expression of Colour's Value precedence - the board, the
    placeholder-glass URL and the admin's live preview all read this answer
    rather than repeating the chain, which is how a *known* Colour is guaranteed
    to be byte-identical on the swatch and the Placeholder.

    `None` means **Unknown**, a real answer rather than an error: the Beer has
    neither an EBC nor a Colour override. No fallback colour is substituted here
    on purpose - the surface doing the drawing declares its own (ADR-0004).

    `saturation` mutes the *computed* colour only. A Colour override is an exact
    instruction, so an override plus a saturation yields the override untouched;
    otherwise there would be no way to ask for exactly one colour.

    Inputs are coerced defensively because they arrive from front matter and
    query strings: a malformed override falls through to the EBC branch (an
    unparseable hex is not an instruction), and an EBC that is not a number is
    the same as no EBC at all.
    """
    override = parse_hex_color(color_override)
    if override:
        return ResolvedColor(override, text_color_for(override))
    if ebc is None or ebc == "":
        return None
    try:
        ebc_f = float(ebc)
    except (TypeError, ValueError):
        return None
    computed = ebc_to_hex(ebc_f, saturation)
    return ResolvedColor(computed, text_color_for(computed))
