"""Board resolution: custom > brewfather > vacant, hide-vacant flags, colours."""
from pathlib import Path

from app import config_store
from app.board import build_board, resolve_tap
from app.colors import ebc_to_hex


def _glass_url(color_hex: str | None = None, glass: str | None = None) -> str:
    """The placeholder URL the board builds: a resolved colour, or none at all."""
    params = []
    if color_hex is not None:
        params.append("hex=" + color_hex.lstrip("#"))
    if glass is not None:
        params.append("glass=" + glass)
    return "/img/beer-glass" + ("?" + "&".join(params) if params else "")


def test_custom_overrides_brewfather(write_tap):
    write_tap("brewfather", 1, name="BF Beer", abv=5.0, ebc=10)
    write_tap("custom", 1, name="Custom Beer", abv=4.0, ebc=8)
    r = resolve_tap(1)
    assert r["source"] == "custom"
    assert r["name"] == "Custom Beer"


def test_brewfather_when_no_custom(write_tap):
    write_tap("brewfather", 2, name="BF Beer", abv=5.0, ibu=30, ebc=12)
    r = resolve_tap(2)
    assert r["source"] == "brewfather"
    assert r["abv"] == 5.0
    assert r["ibu"] == 30
    assert r["color_hex"].startswith("#")
    assert r["vacant"] is False


def test_filename_decides_source_not_front_matter(write_tap):
    # A hand-edited bf_tap_X.md claiming the Manual Source is still Brewfather:
    # the filename is authoritative and the front-matter key is never read back
    # as truth. Otherwise the display would badge a Tap as Manual while sync
    # kept rewriting it as Brewfather.
    write_tap("brewfather", 4, name="Mislabelled", source="custom")
    assert resolve_tap(4)["source"] == "brewfather"
    # And the mirror case, so this is about the filename rather than a
    # hard-coded default.
    write_tap("custom", 6, name="Also mislabelled", source="brewfather")
    assert resolve_tap(6)["source"] == "custom"


def test_photo_comes_only_from_the_winning_source(write_tap):
    # A Tap comes entirely from one Source. A Manual Tap with no photo shows the
    # placeholder glass rather than borrowing the shadowed Brewfather photo, so
    # a card's name and picture can never come from different Beers.
    write_tap("brewfather", 1, name="BF Beer", ebc=20, image_ext=".jpg")
    write_tap("custom", 1, name="Manual Beer", ebc=20)
    assert resolve_tap(1)["image_url"] == _glass_url(ebc_to_hex(20))
    # The Brewfather photo is still what a Slot with no Manual Tap shows.
    write_tap("brewfather", 2, name="BF Beer", ebc=20, image_ext=".jpg")
    assert resolve_tap(2)["image_url"] == "/img/bf_tap_2.jpg"


def test_unreadable_manual_tap_does_not_promote_brewfather(write_tap, monkeypatch):
    # An existing-but-unreadable Manual Tap file renders the Slot as an empty
    # card under the Manual Source. It must NOT fall through to Brewfather: a
    # transient read error on a bind mount would otherwise put another
    # brewery's beer on the TV, which is worse than a blank card.
    write_tap("brewfather", 3, name="BF Beer", abv=5.0, ebc=10)
    write_tap("custom", 3, name="Manual Beer", abv=4.0, ebc=8)

    real_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self.name == "custom_tap_3.md":
            raise PermissionError("simulated bind-mount hiccup")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    r = resolve_tap(3)
    assert r["source"] == "custom"
    assert r["name"] != "BF Beer"
    assert r["abv"] is None and r["ebc"] is None
    assert r["description"] == ""


def test_vacant_when_nothing():
    r = resolve_tap(5)
    assert r["vacant"] is True
    assert r["name"] is None


def test_build_board_marks_hidden_when_hide_vacant(write_tap):
    config_store.update_config(num_taps=3, hide_vacant_taps=True)
    write_tap("brewfather", 1, name="One", abv=5, ebc=10)
    # taps 2 and 3 vacant
    board = build_board()
    by_tap = {t["tap"]: t for t in board["taps"]}
    assert by_tap[1]["hidden"] is False
    assert by_tap[2]["vacant"] is True and by_tap[2]["hidden"] is True
    assert by_tap[3]["hidden"] is True


def test_build_board_vacant_visible_when_toggle_off(write_tap):
    config_store.update_config(num_taps=2, hide_vacant_taps=False)
    board = build_board()
    by_tap = {t["tap"]: t for t in board["taps"]}
    assert by_tap[1]["vacant"] is True and by_tap[1]["hidden"] is False


def test_board_numbers_coerced(write_tap):
    config_store.update_config(num_taps=1)
    write_tap("custom", 1, name="N", abv="6.8", ibu="65", ebc="18")
    t = build_board()["taps"][0]
    assert t["abv"] == 6.8
    assert t["ibu"] == 65
    assert t["ebc"] == 18


def test_saturation_override_mutes_colour_and_tags_glass(write_tap):
    # Same EBC, different saturation -> a greyer swatch, and the placeholder URL
    # carries that muted colour so the pour matches the swatch. Saturation is an
    # input to resolution and never reaches the URL.
    write_tap("custom", 1, name="Vivid", ebc=20)
    write_tap("custom", 2, name="Muted", ebc=20, saturation=0.3)
    vivid, muted = resolve_tap(1), resolve_tap(2)
    assert vivid["color_hex"] != muted["color_hex"]
    assert vivid["image_url"] == _glass_url(ebc_to_hex(20))
    assert muted["image_url"] == _glass_url(ebc_to_hex(20, 0.3))
    assert "sat=" not in muted["image_url"]


def test_color_override_wins_over_ebc_everywhere(write_tap):
    # An exact colour override drives the swatch AND the placeholder glass,
    # ignoring the EBC-derived colour.
    write_tap("custom", 1, name="Forced Red", ebc=20, color_override="#780606")
    r = resolve_tap(1)
    assert r["color_hex"] == "#780606"
    assert r["image_url"] == _glass_url("#780606")


def test_color_override_with_saturation_is_not_muted(write_tap):
    # A Colour override is an exact instruction. An operator who writes both
    # tokens gets the override untouched, on the swatch and in the pour alike.
    write_tap("custom", 1, name="Forced Red", ebc=20, color_override="#780606",
              saturation=0.3)
    r = resolve_tap(1)
    assert r["color_hex"] == "#780606"
    assert r["image_url"] == _glass_url("#780606")


def test_unknown_colour_sends_no_colour_at_all(write_tap):
    # Neither an EBC nor an override: resolution answers Unknown, so the board
    # sends null rather than inventing a colour, and the placeholder URL carries
    # no colour - which is what selects the glass renderer's own amber. The two
    # surfaces' fallbacks are deliberately different (ADR-0004).
    write_tap("custom", 1, name="Colourless")
    r = resolve_tap(1)
    assert r["color_hex"] is None
    assert r["text_color"] is None
    assert r["color_known"] is False
    assert r["image_url"] == _glass_url()


def test_vacant_tap_carries_no_colour_fields(write_tap):
    # A Vacant Slot has no Beer to resolve. The display styles those cards from a
    # CSS custom property and never read the colour fields, so they are not sent.
    r = resolve_tap(5)
    assert r["vacant"] is True
    assert "color_hex" not in r
    assert "text_color" not in r


def test_color_known_tracks_ebc_or_override(write_tap):
    # The swatch shows when the colour is known: via EBC, or an override alone.
    write_tap("custom", 1, name="Override only", color_override="#445566")  # no ebc
    write_tap("custom", 2, name="Ebc only", ebc=12)
    write_tap("custom", 3, name="Neither")
    assert resolve_tap(1)["color_known"] is True
    assert resolve_tap(2)["color_known"] is True
    assert resolve_tap(3)["color_known"] is False
    assert resolve_tap(5)["color_known"] is False   # vacant


def test_glass_override_tags_placeholder_url(write_tap):
    # A per-tap glass selection is encoded in the glass URL; the default is omitted.
    write_tap("custom", 1, name="Tulip Beer", ebc=20, glass="tulip")
    assert resolve_tap(1)["image_url"] == _glass_url(ebc_to_hex(20), "tulip")
    # A global default glass applies when the tap has none of its own.
    write_tap("custom", 2, name="Plain", ebc=20)
    assert resolve_tap(2, default_glass="teku")["image_url"] == _glass_url(ebc_to_hex(20), "teku")


def test_og_fg_and_per_tap_show_flags(write_tap):
    write_tap("custom", 1, name="Gravity Beer", abv=5, og=1.052, fg=1.010, show_og=True, show_fg=False)
    r = resolve_tap(1)
    assert r["og"] == 1.052
    assert r["fg"] == 1.010
    assert r["show_og"] is True
    assert r["show_fg"] is False
    # A tap without per-tap flags reports None (inherit the global toggle).
    write_tap("custom", 2, name="Plain", abv=5)
    r2 = resolve_tap(2)
    assert r2["show_og"] is None and r2["og"] is None


def test_board_carries_the_resolved_card_scales():
    # The board sends numbers, not the preset key: the display never needs to
    # know which button produced them.
    config_store.update_config(
        tap_photo_preset="small", tap_text_preset="small",
        tap_image_scale=0.6, tap_text_scale=0.75)
    b = build_board()
    assert b["tap_image_scale"] == 0.6
    assert b["tap_text_scale"] == 0.75
    assert "tap_photo_preset" not in b
    assert "tap_text_preset" not in b


def test_board_card_scales_default_to_one():
    b = build_board()
    assert b["tap_image_scale"] == 1.0
    assert b["tap_text_scale"] == 1.0
