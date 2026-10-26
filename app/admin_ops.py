"""The domain operations behind the Admin's Manual-override writes.

`app/main.py` owns HTTP - it parses a form, calls one of these, and turns the
answer (or a rejection) into a response. What a save *means* lives here: which
files move, what Beer the Slot now holds, when a Slot is released. That split is
the point of the module: saving an override, clearing one, keeping an existing
photo and refusing a bad number were all previously reachable only by posting a
form and reading a file back, which made them expensive to assert and easy to
change by accident.

Nothing here knows about HTTP. A submitted value that cannot be used raises
`OverrideRejected` (or `ProfileRejected` for a Display profile operation), and the route decides that means 422; the Settings side has
no equivalent because Settings clamp rather than reject (see GLOSSARY.md).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from . import config_store, photo_library
from . import tap_store as taps
from .archive import archive_tap
from .atomic import JOB_LOCK
from .beer import Beer, TapPresentation
from .beer_glass import GLASS_KEYS
from .colors import display_color_to_ebc

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
) -> Beer:
    """Write a Manual Tap into a Slot. Returns the Beer as stored.

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
    beer = Beer(
        name=str(name or "").strip() or f"Tap {slot}",
        # The five Attributes are parsed here, not left to the Beer's own
        # coercion, because this is the one caller with somebody to tell: a
        # typo in the Admin form gets a 422 and the operator fixes it. The
        # type's coerce-to-None disposition is for a hand-edited file, which has
        # no such audience (docs/adr/0005).
        abv=_number(abv),
        ibu=_number(ibu),
        ebc=display_color_to_ebc(_number(color), unit),
        og=_number(og),
        fg=_number(fg),
        # Saturation and the Colour override are handed over raw: both are
        # normalised by the Beer, using the same colors.py parsers this module
        # used to call, and letting the type do it means a value it cannot use
        # is logged at the write instead of vanishing silently.
        saturation=saturation,
        color_override=color_override,
        # An unrecognised glassware key inherits the global default rather than
        # rejecting: it is a picker value, not something an operator types. The
        # Beer keeps a glass key verbatim, so the check stays here.
        glass=glass_key if glass_key in GLASS_KEYS else None,
    )
    presentation = TapPresentation(show_og=show_og, show_fg=show_fg)

    with JOB_LOCK:
        if image is not None:
            data, ext = image
            taps.save_image(slot, ADMIN_SOURCE, data, ext)
        # The photo is stored before the write so the store's `image:` key names
        # what is on disk; passing no image keeps whatever was already there.
        taps.write(slot, ADMIN_SOURCE, beer, description,
                   presentation=presentation)
        stored_image = taps.image_for(slot, ADMIN_SOURCE)

    log.info("override saved for tap %d (name=%r image=%s)",
             slot, beer.name, stored_image.name if stored_image else None)
    return beer


# ---- Display profiles (ADR-0007) -------------------------------------------
#
# A profile is a name mapped to its Customised settings and nothing else. These
# operations are the only writers of `profiles` from the Admin: create, rename,
# delete, and save one profile's customised units. Each one goes through
# `config_store.update_config`, so every value is clamped and every name
# validated by the same coercion the Default profile gets - nothing here
# restates a bound. The Default profile is the top-level Settings and is never a
# stored profile, so none of these can touch it.


class ProfileRejected(ValueError):
    """A Display profile operation cannot be done (bad, missing or clashing name).

    Carries a message written for the operator; the route turns it into a 4xx.
    """


# Every write of the config's profiles or its album selection takes this lock: a
# profile save, a Settings Save and an album rename are all read-modify-write on
# the same file, so two at once would lose one (and a Save could write an album's
# old name back over a rename; issue #69).
_PROFILES_LOCK = threading.Lock()

# Album renames made since the app started, old name to new. A Settings page that
# was open across a rename still posts the old name; `_follow_renames` maps it on.
# Held under `_PROFILES_LOCK`, bounded so it cannot grow without end.
_RENAMED_ALBUMS: dict[str, str] = {}
_RENAMED_ALBUMS_MAX = 200


class AlbumRenameFailed(RuntimeError):
    """An album rename could not finish; the message says what state it left."""


def _follow_renames(names: Iterable[str]) -> list[str]:
    """Ticked album names with any album renamed since start-up followed to its new name.

    Only a name that is no longer a folder is followed, so an album made again
    under an old name keeps meaning itself. The caller holds `_PROFILES_LOCK`.
    """
    if not _RENAMED_ALBUMS:
        return list(names)
    existing = set(photo_library.list_albums())
    out: list[str] = []
    for name in names:
        seen = {name}
        while name not in existing and name in _RENAMED_ALBUMS:
            name = _RENAMED_ALBUMS[name]
            if name in seen:
                break
            seen.add(name)
        if name not in out:
            out.append(name)
    return out


def _name(raw: Any) -> str:
    """The canonical profile name, or a rejection saying why not."""
    name = config_store.normalize_profile_name(raw)
    if name is None:
        raise ProfileRejected(
            "A profile name is 1-%d lowercase letters, digits or hyphens, and "
            "cannot be \"default\"." % config_store.MAX_PROFILE_NAME_LEN)
    return name


def _existing(raw: Any, profiles: Mapping[str, Any]) -> str:
    name = config_store.normalize_profile_name(raw)
    if name is None or name not in profiles:
        raise ProfileRejected("There is no profile called %r." % str(raw)[:64])
    return name


def _write_profiles(change) -> dict[str, Any]:
    """Apply `change(profiles)` to a copy of the stored profiles and save it.

    Returns the Settings as saved. The write goes through `apply_settings`,
    which clears the upcoming cache when the change leaves no profile (and not
    the Default) wanting Upcoming Beers (ADR-0006).
    """
    with _PROFILES_LOCK:
        profiles = {name: dict(values)
                    for name, values in config_store.load_config()["profiles"].items()}
        return config_store.apply_settings(profiles=change(profiles))


def save_settings(**fields: Any) -> dict[str, Any]:
    """The Settings Save for the Default profile, serialised with profile saves and renames.

    Waits for an album rename in progress and then follows it: the page that
    posted this may still carry an album's old name, and writing it back would
    leave a screen quietly skipping the album. Returns the Settings as saved.
    """
    with _PROFILES_LOCK:
        if "album_selection" in fields:
            fields["album_selection"] = _follow_renames(fields["album_selection"])
        return config_store.apply_settings(**fields)


def customised_units(values: Mapping[str, Any]) -> list[str]:
    """The units a profile customises, in declaration order.

    A unit counts only when all of its keys are present: the store never holds
    half a unit, and this reads the same declaration the save path does.
    """
    return [unit for unit, keys in config_store.CUSTOMISABLE_UNITS.items()
            if all(key in values for key in keys)]


def create_profile(name: Any) -> str:
    """Add an empty profile (it follows the Default profile in everything)."""
    clean = _name(name)

    def add(profiles):
        if clean in profiles:
            raise ProfileRejected("There is already a profile called %r." % clean)
        profiles[clean] = {}
        return profiles

    _write_profiles(add)
    log.info("profile created: %s", clean)
    return clean


def rename_profile(old: Any, new: Any) -> str:
    """Rename a profile, keeping its values and its place in the list.

    There is no redirect: a screen still asking for the old name gets the
    Default profile and the name is listed as unknown. The Admin warns of this
    before it calls here.
    """
    new_name = _name(new)

    def rename(profiles):
        old_name = _existing(old, profiles)
        if new_name != old_name and new_name in profiles:
            raise ProfileRejected("There is already a profile called %r." % new_name)
        return {(new_name if name == old_name else name): values
                for name, values in profiles.items()}

    _write_profiles(rename)
    log.info("profile renamed: %s -> %s", str(old)[:64], new_name)
    return new_name


def delete_profile(name: Any) -> None:
    """Delete a profile. Its screens get the Default profile from then on."""
    def drop(profiles):
        del profiles[_existing(name, profiles)]
        return profiles

    _write_profiles(drop)
    log.info("profile deleted: %s", str(name)[:64])


def save_profile(name: Any, *, customise: Iterable[str],
                 values: Mapping[str, Any],
                 user_agent: str | None = None) -> dict[str, Any]:
    """Write one profile: exactly the units named in `customise`, from `values`.

    `values` is a full Settings submission; only the keys of the customised units
    are read from it, so a followed unit's value, whatever the form carried, is
    dropped - that is what Follow means. Units that are not declared customisable
    are ignored. A named card-size preset owns its scale, as it does in a
    Settings Save. Returns the profile as stored (after clamping).

    `user_agent` is the profile's "also use for screens whose User-Agent
    contains" text: None leaves the stored text alone, blank clears it.
    """
    wanted = set(customise)
    key = _existing(name, config_store.load_config()["profiles"])

    def save(profiles):
        _existing(key, profiles)
        mine: dict[str, Any] = {}
        submitted = dict(values)
        if "album_selection" in submitted:
            submitted["album_selection"] = _follow_renames(submitted["album_selection"])
        for unit, keys in config_store.CUSTOMISABLE_UNITS.items():
            if unit in wanted and all(k in submitted for k in keys):
                mine.update({k: submitted[k] for k in keys})
        if "tap_photo_preset" in mine:
            mine["tap_photo_preset"], mine["tap_image_scale"] = (
                config_store.resolve_tap_photo_preset(
                    mine["tap_photo_preset"], mine["tap_image_scale"]))
        if "tap_text_preset" in mine:
            mine["tap_text_preset"], mine["tap_text_scale"] = (
                config_store.resolve_tap_text_preset(
                    mine["tap_text_preset"], mine["tap_text_scale"]))
        ua_key = config_store.PROFILE_UA_KEY
        text = (profiles[key].get(ua_key, "") if user_agent is None
                else config_store.normalize_user_agent_match(user_agent))
        if text:
            mine[ua_key] = text
        profiles[key] = mine
        return profiles

    saved = _write_profiles(save)
    stored = saved["profiles"][key]
    log.info("profile saved: %s (%d customised unit(s))",
             key, len(customised_units(stored)))
    return stored


# ---- photo albums ---------------------------------------------------------------

def rename_album(old: str, new: str) -> str:
    """Rename an album and every tick of it, in the Default profile and in each profile.

    The folder moves first; if that is refused nothing in the config changes. If
    the config write then fails, the folder is moved back, so the profiles never
    keep a name that no folder has, and `AlbumRenameFailed` says so. A folder
    renamed by hand never comes through here, so its old name just stops
    matching anything (the Admin lists it as "no such folder now").
    """
    with _PROFILES_LOCK:
        clean = photo_library.rename_album(old, new)
        if clean == old:
            return clean

        def swap(names: list[str]) -> list[str]:
            out: list[str] = []
            for name in names:
                name = clean if name == old else name
                if name not in out:
                    out.append(name)
            return out

        try:
            cfg = config_store.load_config()
            profiles = {name: dict(values) for name, values in cfg["profiles"].items()}
            for values in profiles.values():
                if "album_selection" in values:
                    values["album_selection"] = swap(values["album_selection"])
            config_store.apply_settings(album_selection=swap(cfg["album_selection"]),
                                        profiles=profiles)
        except Exception as exc:
            log.error("album rename: the config write failed (%s); moving the folder back", exc)
            try:
                photo_library.rename_album(clean, old)
            except Exception as back:
                log.error("album rename: could not move the folder back (%s)", back)
                raise AlbumRenameFailed(
                    f"The album folder was renamed to “{clean}” but the profiles could not be "
                    f"updated, and the folder could not be moved back. Rename it to “{old}” "
                    "again, or tick it afresh in the profiles.") from exc
            raise AlbumRenameFailed(
                f"The settings could not be saved, so “{old}” was not renamed.") from exc
        _RENAMED_ALBUMS[old] = clean
        while len(_RENAMED_ALBUMS) > _RENAMED_ALBUMS_MAX:
            del _RENAMED_ALBUMS[next(iter(_RENAMED_ALBUMS))]
    log.info("album renamed in the config: profiles that ticked it follow")
    return clean
