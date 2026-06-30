"""Board resolution: custom > brewfather > vacant, hide-vacant flags, colours."""
from app import config_store
from app.board import build_board, resolve_tap


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
    # Same EBC, different saturation -> a greyer swatch and a sat-tagged glass URL
    # so the no-photo placeholder matches the muted swatch.
    write_tap("custom", 1, name="Vivid", ebc=20)
    write_tap("custom", 2, name="Muted", ebc=20, saturation=0.3)
    vivid, muted = resolve_tap(1), resolve_tap(2)
    assert vivid["color_hex"] != muted["color_hex"]
    assert vivid["image_url"] == "/img/beer-glass?ebc=20"
    assert muted["image_url"] == "/img/beer-glass?ebc=20&sat=0.3"


def test_color_override_wins_over_ebc_everywhere(write_tap):
    # An exact colour override drives the swatch AND the placeholder glass (hex=),
    # ignoring the EBC-derived colour.
    write_tap("custom", 1, name="Forced Red", ebc=20, color_override="#780606")
    r = resolve_tap(1)
    assert r["color_hex"] == "#780606"
    assert r["image_url"] == "/img/beer-glass?hex=780606"


def test_glass_override_tags_placeholder_url(write_tap):
    # A per-tap glass selection is encoded in the glass URL; the default is omitted.
    write_tap("custom", 1, name="Tulip Beer", ebc=20, glass="tulip")
    assert resolve_tap(1)["image_url"] == "/img/beer-glass?ebc=20&glass=tulip"
    # A global default glass applies when the tap has none of its own.
    write_tap("custom", 2, name="Plain", ebc=20)
    assert resolve_tap(2, default_glass="teku")["image_url"] == "/img/beer-glass?ebc=20&glass=teku"


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
