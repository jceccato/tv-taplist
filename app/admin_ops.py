"""The domain operations behind the Admin's Manual-override writes.

`app/main.py` owns HTTP - it parses a form, calls one of these, and turns the
answer (or a rejection) into a response. What a save *means* lives here: which
files move, what the front matter says, when a Slot is released. That split is
the point of the module: saving an override, clearing one, keeping an existing
photo and refusing a bad number were all previously reachable only by posting a
form and reading a file back, which made them expensive to assert and easy to
change by accident.

Nothing here knows about HTTP. A submitted value that cannot be used raises
`OverrideRejected`, and the route decides that means 422; the Settings side has
no equivalent because Settings clamp rather than reject (see CONTEXT.md).
"""
from __future__ import annotations

import logging
from typing import Any

from . import tap_store as taps
from .archive import archive_tap
from .atomic import JOB_LOCK
from .beer_glass import GLASS_KEYS
from .colors import display_color_to_ebc, parse_hex_color, parse_saturation
from .timezone import iso_now

log = logging.getLogger("taplist.admin")

# The Source these operations act on, named once. Admin writes Manual Taps and
# only Manual Taps - the same structural guard `brewfather.py` uses in the other
# direction, so neither module can reach into the other's Source by forgetting a
# check rather than by changing a constant.
ADMIN_SOURCE = taps.Source.MANUAL


class OverrideRejected(ValueError):
    """A submitted Manual override value cannot be used.

    Carries a message written for the operator, because the Admin form shows it
    verbatim. Raised before any filesystem side effect, so a rejected save
    leaves the Slot exactly as it was.
    """


def _number(value: Any) -> float | int | None:
    """Parse an optional numeric field: blank -> None, junk -> OverrideRejected.

    Integral values come back as `int` so the Tap file reads `abv: 5` rather
    than `abv: 5.0` for someone editing it by hand (ADR-0001).
    """
    text = str(value if value is not None else "").strip()
    if text == "":
        return None
    try:
        parsed = float(text)
    except ValueError:
        raise OverrideRejected(f"'{text}' is not a number") from None
    return int(parsed) if parsed.is_integer() else parsed


def clear_override(slot: int) -> bool:
    """Release a Slot back to Brewfather. Returns True if a Manual Tap moved.

    Archiving the Manual Tap is the only way to drop Manual precedence, and it
    is deliberately all that happens: the Brewfather Tap for this Slot is left
    where it is. Sync keeps it warm underneath the override, so releasing the
    Slot reveals a current Beer at once instead of leaving it Vacant until the
    next sync cycle - see ADR-0003. Do not "optimise" that into a skip; the skip
    was the original bug.
    """
    with JOB_LOCK:
        archived = archive_tap(slot, ADMIN_SOURCE)
    log.info("override cleared for tap %d (archived=%s)", slot, archived)
    return archived


def save_override(
    slot: int,
    *,
    name: str = "",
    abv: Any = "",
    ibu: Any = "",
    og: Any = "",
    fg: Any = "",
    color: Any = "",
    saturation: Any = "",
    color_override: Any = "",
    glass: str = "",
    show_og: bool | None = None,
    show_fg: bool | None = None,
    description: str = "",
    image: tuple[bytes, str] | None = None,
    unit: str = "ebc",
) -> dict[str, Any]:
    """Write a Manual Tap into a Slot. Returns the front matter as stored.

    `color` arrives in the operator's display `unit` and is stored as EBC, the
    only stored form. `image` is bytes plus an extension the caller has already
    vouched for (the extension allow-list and the size cap are HTTP concerns and
    stay in the route); passing None keeps whatever photo the Beer already had,
    so editing a description does not silently drop the picture.

    Every field that can reject the submission is parsed before the lock is
    taken and before anything is written, so a bad number can never leave an
    uploaded image orphaned with no md file beside it.

    Like `clear_override`, this leaves the Brewfather Tap for the Slot alone -
    overriding a Slot for one night is not a destructive act (ADR-0003).
    """
    glass_key = str(glass or "").strip()
    front_matter: dict[str, Any] = {
        "name": str(name or "").strip() or f"Tap {slot}",
        "abv": _number(abv),
        "ibu": _number(ibu),
        "ebc": display_color_to_ebc(_number(color), unit),
        "og": _number(og),
        "fg": _number(fg),
        "saturation": parse_saturation(saturation),
        "color_override": parse_hex_color(color_override),
        # An unrecognised glassware key inherits the global default rather than
        # rejecting: it is a picker value, not something an operator types.
        "glass": glass_key if glass_key in GLASS_KEYS else None,
        "show_og": show_og,
        "show_fg": show_fg,
        # Written for a human reading the file. The filename is what actually
        # decides the Source; this key is never read back as truth.
        "source": "custom",
        "updated": iso_now(),
    }

    with JOB_LOCK:
        if image is not None:
            data, ext = image
            image_name = taps.save_image(slot, ADMIN_SOURCE, data, ext)
        else:
            existing = taps.image_for(slot, ADMIN_SOURCE)
            image_name = existing.name if existing else None
        front_matter["image"] = image_name
        taps.write(slot, ADMIN_SOURCE, front_matter, description)

    log.info("override saved for tap %d (name=%r image=%s)",
             slot, front_matter["name"], image_name)
    return front_matter
