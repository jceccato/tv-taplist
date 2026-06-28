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
