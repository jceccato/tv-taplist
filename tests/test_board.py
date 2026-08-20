"""Board resolution: custom > brewfather > vacant, hide-vacant flags, colours."""
from pathlib import Path

from app import config_store
from app.board import build_board, resolve_tap, resolve_visibility
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
    assert r["image_url"] == _glass_url()


def test_vacant_tap_carries_no_colour_fields(write_tap):
    # A Vacant Slot has no Beer to resolve. The display styles those cards from a
    # CSS custom property and never read the colour fields, so they are not sent.
    r = resolve_tap(5)
    assert r["vacant"] is True
    assert "color_hex" not in r
    assert "text_color" not in r


def test_swatch_and_ebc_ask_different_emptiness_questions(write_tap):
    """One operator toggle, two answers - the swatch is not the EBC Attribute.

    The swatch asks whether *Colour* is known (an EBC or a Colour override); the
    EBC Attribute asks whether *EBC* is present. A Beer with only an override is
    the case that separates them, and it is why `hide_color_when_empty` cannot be
    applied once and reused. See ADR-0004 and CONTEXT.md (Attribute).
    """
    write_tap("custom", 1, name="Override only", color_override="#445566")  # no ebc
    write_tap("custom", 2, name="Ebc only", ebc=12)
    write_tap("custom", 3, name="Neither")

    override_only = resolve_tap(1)
    assert override_only["swatch_visible"] is True
    assert override_only["ebc_visible"] is False

    both_visible = resolve_tap(2)
    assert both_visible["swatch_visible"] is True
    assert both_visible["ebc_visible"] is True

    neither = resolve_tap(3)
    assert neither["swatch_visible"] is False
    assert neither["ebc_visible"] is False


def test_vacant_tap_carries_no_visibility_answers():
    # A Vacant Slot has no Beer and renders no stats block, so there is no
    # Attribute to answer for. Sending six booleans anyway would be a claim
    # rather than an answer.
    r = resolve_tap(5)
    assert r["vacant"] is True
    for key in ("abv_visible", "ibu_visible", "ebc_visible", "og_visible",
                "fg_visible", "swatch_visible"):
        assert key not in r


def test_board_payload_carries_no_visibility_inputs(write_tap):
    """The raw toggles and the per-Tap tri-states stay off the wire.

    Sending both the inputs and the answer would leave two implementations of
    the chain in play, which is exactly the state this replaced. `color_known`
    goes with them: the display has no reason to know *why* the swatch shows.
    """
    config_store.update_config(num_taps=2)
    write_tap("custom", 1, name="Beer", abv=5, ebc=10, show_og=True)
    board = build_board()

    for flag in ("show_abv", "show_ibu", "show_color", "show_og", "show_fg",
                 "hide_abv_when_empty", "hide_ibu_when_empty",
                 "hide_color_when_empty", "hide_og_when_empty",
                 "hide_fg_when_empty"):
        assert flag not in board, flag
    # The two settings that are not Visibility stay raw, on purpose.
    assert board["show_source_badge"] is False
    assert board["color_unit"] == "ebc"

    for tap in board["taps"]:
        assert "color_known" not in tap
        assert "show_og" not in tap
        assert "show_fg" not in tap


# ---- Visibility, resolved (CONTEXT.md's three-step chain) ----------------

def test_visibility_global_toggle_applies_without_an_override():
    # Step 2: with no per-Tap override the global toggle decides.
    assert resolve_visibility(5.0, True, False) is True
    assert resolve_visibility(5.0, False, False) is False


def test_visibility_per_tap_override_beats_the_global_toggle():
    # Step 1: an explicit per-Tap value wins in both directions; only None and
    # "" mean "inherit". A hand-edited front matter can produce either.
    assert resolve_visibility(1.052, False, False, per_tap=True) is True
    assert resolve_visibility(1.052, True, False, per_tap=False) is False
    assert resolve_visibility(1.052, True, False, per_tap=None) is True
    assert resolve_visibility(1.052, True, False, per_tap="") is True


def test_visibility_empty_suppression_hides_an_enabled_attribute():
    # Step 3, and only step 3: a missing value hides an *enabled* Attribute...
    assert resolve_visibility(None, True, True) is False
    assert resolve_visibility("", True, True) is False
    # ...and does nothing when the operator asked to keep the empty stat.
    assert resolve_visibility(None, True, False) is True


def test_visibility_empty_suppression_cannot_reveal_a_disabled_attribute():
    # Order matters: suppression refines an enabled Attribute, it is not a
    # toggle of its own, so a present value can never override "switched off".
    assert resolve_visibility(42, False, True) is False
    assert resolve_visibility(42, False, False) is False


def test_visibility_treats_zero_as_a_real_reading():
    # 0 IBU is a fact about a lager, not an absent value - falsiness is the wrong
    # emptiness test here and hiding it would lose real data.
    assert resolve_visibility(0, True, True) is True
    assert resolve_visibility(0.0, True, True) is True


def test_per_tap_override_survives_into_the_resolved_answer(write_tap):
    # The tri-state stays an editable value in the Tap file; what changed is that
    # the board now applies it instead of forwarding it.
    config_store.update_config(show_og=False, show_fg=True)
    write_tap("custom", 1, name="Shown", og=1.052, fg=1.010, show_og=True, show_fg=False)
    r = resolve_tap(1)
    assert r["og_visible"] is True     # per-tap True beats the global False
    assert r["fg_visible"] is False    # per-tap False beats the global True

    # A Tap with no override inherits the globals.
    write_tap("custom", 2, name="Plain", og=1.052, fg=1.010)
    r2 = resolve_tap(2)
    assert r2["og_visible"] is False
    assert r2["fg_visible"] is True


def test_glass_override_tags_placeholder_url(write_tap):
    # A per-tap glass selection is encoded in the glass URL; the default is omitted.
    write_tap("custom", 1, name="Tulip Beer", ebc=20, glass="tulip")
    assert resolve_tap(1)["image_url"] == _glass_url(ebc_to_hex(20), "tulip")
    # A global default glass applies when the tap has none of its own.
    write_tap("custom", 2, name="Plain", ebc=20)
    assert resolve_tap(2, default_glass="teku")["image_url"] == _glass_url(ebc_to_hex(20), "teku")


def test_og_fg_values_and_their_resolved_visibility(write_tap):
    write_tap("custom", 1, name="Gravity Beer", abv=5, og=1.052, fg=1.010, show_og=True, show_fg=False)
    r = resolve_tap(1)
    assert r["og"] == 1.052
    assert r["fg"] == 1.010
    assert r["og_visible"] is True
    assert r["fg_visible"] is False
    # A tap with no value and no override: the globals are off by default, so
    # both stats resolve hidden.
    write_tap("custom", 2, name="Plain", abv=5)
    r2 = resolve_tap(2)
    assert r2["og"] is None
    assert r2["og_visible"] is False


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
