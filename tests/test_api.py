"""HTTP surface: display, board API, image serving, admin auth + mutations."""
import re

import pytest
from fastapi.testclient import TestClient

from app import config_store, main, paths, status_store, tap_store as taps
from app.main import _safe_tap_image, app


def _base_stop(svg: str) -> str:
    """The liquid's base colour in a beer-glass SVG (the gradient's 55% stop)."""
    return re.search(r'offset="55%" stop-color="(#[0-9a-fA-F]{6})"', svg).group(1)

# Plain TestClient (no context manager) so the lifespan scheduler/initial-sync
# threads are not started during unit tests.
client = TestClient(app)


def _login(c: TestClient) -> TestClient:
    r = c.post("/admin/login", data={"password": "testpw"}, follow_redirects=False)
    assert r.status_code == 303
    return c


# ---- public endpoints --------------------------------------------------

def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_display_page_has_no_external_origins():
    html = client.get("/").text
    assert "http://" not in html
    assert "https://" not in html


def test_api_board_shape(write_tap):
    config_store.update_config(num_taps=2)
    write_tap("custom", 1, name="Board Beer", abv=5, ibu=20, ebc=14)
    board = client.get("/api/board").json()
    assert board["num_taps"] == 2
    assert board["taps"][0]["name"] == "Board Beer"
    assert board["taps"][1]["vacant"] is True


def test_image_missing_falls_back_to_placeholder():
    r = client.get("/img/does_not_exist.png")
    assert r.status_code == 200
    assert "image" in r.headers["content-type"]


def test_beer_glass_route_tints_by_colour():
    from app.beer_glass import _hex_to_rgb
    from app.colors import ebc_to_hex

    # The route is handed a *resolved* colour, not an EBC: the board resolves
    # once and puts the answer in the URL.
    pale = client.get("/img/beer-glass", params={"hex": ebc_to_hex(8).lstrip("#")})
    dark = client.get("/img/beer-glass", params={"hex": ebc_to_hex(80).lstrip("#")})
    assert pale.status_code == 200 and "svg" in pale.headers["content-type"]
    assert dark.status_code == 200

    # A dark beer's liquid must be markedly darker than a pale one's.
    assert sum(_hex_to_rgb(_base_stop(dark.text))) < sum(_hex_to_rgb(_base_stop(pale.text)))


def test_beer_glass_route_no_longer_derives_colour_from_ebc():
    # EBC and saturation are inputs to resolution, which has already happened by
    # the time this URL is built. A stale cached URL still carrying them renders
    # the Unknown amber rather than re-running the precedence chain here.
    from app.beer_glass import _DEFAULT_HEX

    r = client.get("/img/beer-glass", params={"ebc": 80, "sat": 30})
    assert r.status_code == 200
    assert _base_stop(r.text).lower() == _DEFAULT_HEX


def test_board_uses_colour_glass_when_no_photo(write_tap):
    from app.colors import ebc_to_hex

    config_store.update_config(num_taps=1)
    write_tap("custom", 1, name="Glassy", abv=5, ebc=20)
    board = client.get("/api/board").json()
    assert board["taps"][0]["image_url"] == "/img/beer-glass?hex=" + ebc_to_hex(20).lstrip("#")


def test_safe_tap_image_rejects_traversal():
    # Direct unit check of the sanitiser.
    assert _safe_tap_image("../config.json") is None
    assert _safe_tap_image("..\\config.json") is None


# ---- auth --------------------------------------------------------------

def test_admin_requires_login():
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_admin_page_renders_all_tabs():
    config_store.update_config(num_taps=2)
    c = _login(TestClient(app))
    html = c.get("/admin").text
    # The tabbed layout and the new theme / glass / pagination controls render.
    for needle in ("data-tab=\"settings\"", "data-tab=\"theme\"", "data-tab=\"overrides\"",
                   "name=\"theme\"", "name=\"glass_type\"", "name=\"paginate\"",
                   "name=\"color_override\"", "OLED true black"):
        assert needle in html, needle


def test_admin_assets_are_cache_busted():
    # The admin JS/CSS carry a ?v=<mtime> token so a rebuild/edit is picked up
    # without a manual hard-refresh (the admin browser caches them aggressively).
    c = _login(TestClient(app))
    html = c.get("/admin").text
    assert "/static/js/admin.js?v=" in html
    assert "/static/css/admin.css?v=" in html


def test_wrong_password_401():
    r = client.post("/admin/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401


def test_login_sets_httponly_cookie():
    c = TestClient(app)
    r = c.post("/admin/login", data={"password": "testpw"}, follow_redirects=False)
    assert r.status_code == 303
    set_cookie = r.headers.get("set-cookie", "")
    assert "taplist_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=strict" in set_cookie.lower()


def test_rate_limit_locks_out_after_five_failures():
    c = TestClient(app)
    for _ in range(5):
        c.post("/admin/login", data={"password": "wrong"}, follow_redirects=False)
    r = c.post("/admin/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 429  # locked out


def test_settings_save_requires_auth():
    r = client.post("/admin/settings", data={"num_taps": 5, "max_archive_age_days": 1, "max_archive_storage_mb": 1})
    assert r.status_code == 401


# ---- admin mutations ---------------------------------------------------

def test_save_settings_persists():
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "brewfather_user_id": "u", "brewfather_api_key": "k",
        "num_taps": "6", "hide_vacant_taps": "true", "announcement_text": "Hi",
        "max_archive_age_days": "90", "max_archive_storage_mb": "1000",
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    cfg = config_store.load_config()
    assert cfg["num_taps"] == 6
    assert cfg["hide_vacant_taps"] is True
    assert cfg["announcement_text"] == "Hi"


def test_save_settings_sync_status_toggles_are_independent():
    """Both Brewfather status toggles save in all four combinations.

    An unchecked HTML checkbox is simply absent from the post, so this also pins
    that a missing field turns the toggle off rather than leaving it stuck on.
    """
    c = _login(TestClient(app))
    base = {"num_taps": "6", "max_archive_age_days": "90",
            "max_archive_storage_mb": "1000"}
    for conditioning, fermenting in [(True, True), (False, True),
                                     (True, False), (False, False)]:
        data = dict(base)
        if conditioning:
            data["include_conditioning"] = "true"
        if fermenting:
            data["include_fermenting"] = "true"
        r = c.post("/admin/settings", data=data)
        assert r.status_code == 200 and r.json()["ok"] is True
        cfg = config_store.load_config()
        assert cfg["include_conditioning"] is conditioning
        assert cfg["include_fermenting"] is fermenting


def test_admin_page_offers_both_sync_status_checkboxes():
    c = _login(TestClient(app))
    body = c.get("/admin").text
    assert 'name="include_conditioning"' in body
    assert 'name="include_fermenting"' in body


def test_save_settings_rejects_negative_taps():
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "-1", "max_archive_age_days": "1", "max_archive_storage_mb": "1"})
    assert r.status_code == 422


def test_override_save_then_clear_with_image():
    c = _login(TestClient(app))
    # Save an override on tap 2 with an uploaded image.
    r = c.post("/admin/override/2",
               data={"enabled": "true", "name": "Hand Pour", "abv": "4.5",
                     "ibu": "18", "color": "9", "description": "Cask ale."},
               files={"image": ("beer.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200 and r.json()["override"] is True
    assert taps.exists(2, taps.Source.MANUAL)
    assert (paths.TAPS_DIR / "custom_tap_2.png").exists()
    data = taps.read(2, taps.Source.MANUAL).front_matter
    assert data["name"] == "Hand Pour"
    assert data["abv"] == 4.5
    assert data["ebc"] == 9  # EBC unit by default

    # Clearing the override archives the custom files.
    r2 = c.post("/admin/override/2", data={"enabled": "false"})
    assert r2.status_code == 200 and r2.json()["override"] is False
    assert not taps.exists(2, taps.Source.MANUAL)
    assert list(paths.OLD_BEERS_DIR.glob("custom_tap_2_*.md"))


def test_override_save_leaves_the_existing_brewfather_tap_in_place(write_tap):
    # Inverted deliberately: saving an override used to archive the Brewfather
    # Tap underneath it, which is what made clearing the override leave the Slot
    # Vacant until the next sync. It now stays warm - both files exist for the
    # Slot, and the Manual one wins.
    c = _login(TestClient(app))
    write_tap("bf", 3, name="BF Three", abv=5, ebc=10, image_ext=".jpg")
    r = c.post("/admin/override/3", data={"enabled": "true", "name": "Now Custom", "abv": "5", "color": "10"})
    assert r.status_code == 200
    assert taps.exists(3, taps.Source.MANUAL)
    assert taps.exists(3, taps.Source.BREWFATHER)
    assert (paths.TAPS_DIR / "bf_tap_3.jpg").exists()
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_3_*")) == []
    assert taps.resolve(3).front_matter["name"] == "Now Custom"


def test_clearing_an_override_reveals_the_brewfather_beer_with_no_sync(write_tap):
    # The story that motivated the change: no sync run happens anywhere in this
    # test, and the board shows the Brewfather Beer the instant the override is
    # cleared - not up to fifteen minutes later.
    config_store.update_config(num_taps=1)
    c = _login(TestClient(app))
    write_tap("bf", 1, name="BF One", abv=5, ebc=10)

    c.post("/admin/override/1", data={"enabled": "true", "name": "Hand Pour", "abv": "5", "color": "10"})
    assert client.get("/api/board").json()["taps"][0]["name"] == "Hand Pour"

    r = c.post("/admin/override/1", data={"enabled": "false"})
    assert r.status_code == 200 and r.json()["override"] is False
    tap = client.get("/api/board").json()["taps"][0]
    assert tap["name"] == "BF One"
    assert tap["source"] == "brewfather"


def test_shadow_hint_names_the_waiting_beer_only_under_an_override(write_tap):
    # The row tells the operator what clearing the override will reveal. Both
    # files existing for one Slot is the normal case now, so it is labelled
    # rather than left to be discovered in the data directory.
    config_store.update_config(num_taps=3)
    write_tap("custom", 1, name="Hand Pour", ebc=12)
    write_tap("bf", 1, name="Shadowed Stout", ebc=40)
    write_tap("custom", 2, name="Lonely Pour", ebc=12)  # override, no shadow
    write_tap("bf", 3, name="Plain BF", ebc=20)         # shadow, no override

    rows = main._build_admin_tap_rows(config_store.load_config())
    assert [r["shadow_name"] for r in rows] == ["Shadowed Stout", None, None]

    html = _login(TestClient(app)).get("/admin").text
    assert html.count("data-shadow-hint") == 1
    assert "Shadowed Stout" in html
    # Text only: no thumbnail, no toggle, no clear-and-show shortcut.
    assert "override-thumb" not in html


def test_admin_row_photo_is_the_photo_the_display_shows(write_tap):
    # Admin is only useful as a preview if it agrees with the TV, so the row
    # resolves the Slot exactly as the board does - same Source, same photo.
    config_store.update_config(num_taps=2)
    write_tap("bf", 1, name="BF One", ebc=12, image_ext=".jpg")
    write_tap("custom", 2, name="Hand Pour", ebc=12, image_ext=".png")
    rows = main._build_admin_tap_rows(config_store.load_config())
    board = client.get("/api/board").json()["taps"]
    assert [r["image_url"] for r in rows] == [t["image_url"] for t in board]
    assert [r["source"] for r in rows] == [t["source"] for t in board]


def test_manual_tap_without_photo_shows_placeholder_not_the_brewfather_one(write_tap):
    # The live bug this ticket closes: the row used to fall back to the other
    # Source's image, so this Slot showed the Brewfather photo in Admin and a
    # glass placeholder on the TV. A Tap comes entirely from one Source.
    config_store.update_config(num_taps=1)
    write_tap("bf", 1, name="BF One", ebc=12, image_ext=".jpg")
    write_tap("custom", 1, name="Hand Pour", ebc=12)  # no photo of its own

    row = main._build_admin_tap_rows(config_store.load_config())[0]
    assert row["override"] is True
    assert row["image_url"] is None
    from app.colors import ebc_to_hex
    assert (client.get("/api/board").json()["taps"][0]["image_url"]
            == "/img/beer-glass?hex=" + ebc_to_hex(12).lstrip("#"))

    # And what the operator actually sees: the row renders no thumbnail at all,
    # certainly not the Brewfather beer's.
    html = _login(TestClient(app)).get("/admin").text
    assert "bf_tap_1.jpg" not in html
    assert "override-thumb" not in html


def test_override_image_upload_sweeps_the_previous_extension():
    c = _login(TestClient(app))
    c.post("/admin/override/1", data={"enabled": "true", "name": "Pour"},
           files={"image": ("beer.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert (paths.TAPS_DIR / "custom_tap_1.png").exists()
    # A second upload with a different extension leaves exactly one image, so
    # the Slot can never hold two photos with no way to say which is current.
    r = c.post("/admin/override/1", data={"enabled": "true", "name": "Pour"},
               files={"image": ("beer.webp", b"RIFF----WEBP", "image/webp")})
    assert r.status_code == 200
    assert (paths.TAPS_DIR / "custom_tap_1.webp").exists()
    assert not (paths.TAPS_DIR / "custom_tap_1.png").exists()
    assert taps.read(1, taps.Source.MANUAL).front_matter["image"] == "custom_tap_1.webp"


def test_admin_row_source_comes_from_the_filename(write_tap):
    # The front-matter `source:` key is written for a human reading the file and
    # is never read back as truth - the filename decides, here as on the board.
    config_store.update_config(num_taps=1)
    write_tap("bf", 1, name="Mislabelled", ebc=12, source="custom")
    row = main._build_admin_tap_rows(config_store.load_config())[0]
    assert row["override"] is False
    assert row["source"] == "brewfather"


def test_override_saves_saturation_as_fraction():
    c = _login(TestClient(app))
    # The admin enters a percentage; it is stored as a 0..1 fraction.
    r = c.post("/admin/override/1",
               data={"enabled": "true", "name": "Muted", "color": "20", "saturation": "60"})
    assert r.status_code == 200
    assert taps.read(1, taps.Source.MANUAL).front_matter["saturation"] == 0.6


def test_override_saves_colour_glass_gravity_and_visibility():
    c = _login(TestClient(app))
    r = c.post("/admin/override/1", data={
        "enabled": "true", "name": "Loaded", "color": "20",
        "color_override": "780606", "glass": "teku",
        "og": "1.052", "fg": "1.011", "show_og": "true", "show_fg": "false",
    })
    assert r.status_code == 200
    data = taps.read(1, taps.Source.MANUAL).front_matter
    assert data["color_override"] == "#780606"   # normalised with leading #
    assert data["glass"] == "teku"
    assert data["og"] == 1.052 and data["fg"] == 1.011
    assert data["show_og"] is True and data["show_fg"] is False


def test_override_ignores_unknown_glass():
    c = _login(TestClient(app))
    r = c.post("/admin/override/1",
               data={"enabled": "true", "name": "X", "glass": "notaglass"})
    assert r.status_code == 200
    assert taps.read(1, taps.Source.MANUAL).front_matter["glass"] is None


def test_save_settings_theme_pagination_and_gravity():
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "4", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "theme": "oled", "glass_type": "tulip",
        "paginate": "true", "page_size": "4", "rotation_seconds": "15",
        "show_og": "true", "show_fg": "true", "show_source_badge": "true",
        "theme_bg": "#010203",
    })
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert cfg["theme"] == "oled"
    assert cfg["glass_type"] == "tulip"
    assert cfg["paginate"] is True and cfg["page_size"] == 4 and cfg["rotation_seconds"] == 15
    assert cfg["show_og"] is True and cfg["show_source_badge"] is True
    # The custom-theme colour is captured even when another preset is active.
    assert cfg["theme_custom"]["bg"] == "#010203"


def test_board_includes_theme_and_pagination():
    config_store.update_config(num_taps=1, theme="oled", paginate=True, page_size=3)
    board = client.get("/api/board").json()
    assert board["theme"]["bg"] == "#000000"        # OLED true black
    assert board["paginate"] is True and board["page_size"] == 3
    assert "show_source_badge" in board


def test_beer_glass_route_accepts_glass_and_hex():
    # The resolved colour and the silhouette are the route's only two inputs.
    r = client.get("/img/beer-glass", params={"hex": "780606", "glass": "tulip"})
    assert r.status_code == 200 and "svg" in r.headers["content-type"]
    assert "#780606" in r.text


def test_override_color_input_converts_from_srm():
    config_store.update_config(color_unit="srm")
    c = _login(TestClient(app))
    # 10 SRM should be stored as ~19.7 EBC.
    r = c.post("/admin/override/1", data={"enabled": "true", "name": "Dark", "color": "10"})
    assert r.status_code == 200
    assert taps.read(1, taps.Source.MANUAL).front_matter["ebc"] == pytest.approx(19.7, abs=0.05)


def test_save_settings_display_options():
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "4", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "color_unit": "srm", "show_abv": "true", "show_ibu": "false", "show_color": "true",
        "hide_abv_when_empty": "true", "hide_ibu_when_empty": "false", "hide_color_when_empty": "true",
        "venue_logo_height_vh": "20",
    })
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert cfg["color_unit"] == "srm"
    assert cfg["show_ibu"] is False
    assert cfg["hide_ibu_when_empty"] is False
    assert cfg["venue_logo_height_vh"] == 20


def test_settings_does_not_overwrite_env_credentials(monkeypatch):
    monkeypatch.setenv("BREWFATHER_API_KEY", "env-secret")
    c = _login(TestClient(app))
    c.post("/admin/settings", data={
        "num_taps": "2", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "brewfather_api_key": "should-be-ignored",
    })
    # The env-managed key is never written to config.json.
    assert config_store.load_config()["brewfather_api_key"] != "should-be-ignored"


def test_board_includes_display_settings(write_tap):
    # The colour unit stays raw on the wire (it is a unit conversion, not
    # Visibility). The Visibility toggles do not: the board applies them and
    # sends the answer per tap, so a global "off" arrives as a false boolean on
    # the card rather than as a flag the TV has to interpret.
    config_store.update_config(num_taps=1, color_unit="srm", show_ibu=False)
    write_tap("custom", 1, name="Beer", abv=5, ibu=30, ebc=10)
    board = client.get("/api/board").json()
    assert board["color_unit"] == "srm"
    assert "show_ibu" not in board
    assert "hide_color_when_empty" not in board
    assert board["taps"][0]["ibu_visible"] is False
    assert board["taps"][0]["abv_visible"] is True
    assert board["venue_logo_url"] is None  # none uploaded


def test_venue_logo_upload_serve_and_remove():
    c = _login(TestClient(app))
    # No logo yet.
    assert client.get("/img/venue-logo").status_code == 404
    # Upload.
    r = c.post("/admin/venue-logo",
               files={"image": ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200
    assert client.get("/img/venue-logo").status_code == 200
    config_store.update_config(venue_logo_height_vh=20)
    assert client.get("/api/board").json()["venue_logo_url"].startswith("/img/venue-logo")
    # Remove.
    r2 = c.post("/admin/venue-logo", data={"remove": "true"})
    assert r2.status_code == 200
    assert client.get("/img/venue-logo").status_code == 404


def test_override_rejects_non_numeric_field():
    c = _login(TestClient(app))
    r = c.post("/admin/override/1", data={"enabled": "true", "name": "X", "abv": "not-a-number"})
    assert r.status_code == 422


def test_manual_sync_skips_without_credentials():
    c = _login(TestClient(app))
    r = c.post("/admin/sync")
    assert r.status_code == 200
    assert r.json().get("skipped") is True


# ---- live colour preview endpoint (Feature 3) --------------------------

def test_preview_color_override_wins():
    from app.colors import ebc_to_hex
    r = client.get("/api/preview-color", params={"ebc": "40", "hex": "#780606"})
    assert r.status_code == 200
    body = r.json()
    # The exact hex override beats the EBC colour, exactly as the board resolves it.
    assert body["color_hex"] == "#780606"
    assert body["color_hex"] != ebc_to_hex(40)
    assert body["text_color"] in ("#f5f5f5", "#161616")


def test_preview_color_ebc_matches_colours_module():
    from app.colors import ebc_to_hex
    # sat is a percentage (30 -> 0.3); the result must match the server colour model.
    r = client.get("/api/preview-color", params={"ebc": "40", "sat": "30"})
    assert r.json()["color_hex"] == ebc_to_hex(40, 0.3)


def test_preview_color_converts_srm_unit():
    from app.colors import ebc_to_hex
    config_store.update_config(color_unit="srm")
    r = client.get("/api/preview-color", params={"ebc": "10"})
    # 10 SRM -> ~19.7 EBC, matching _color_to_ebc in save_override.
    assert r.json()["color_hex"] == ebc_to_hex(10 * 1.97)


def test_preview_color_blank_is_the_swatch_fallback():
    # Unknown Colour. The preview paints a swatch, so it draws the swatch's
    # declared fallback - the grey - rather than the Placeholder's amber.
    from app.colors import UNKNOWN_SWATCH_HEX, text_color_for
    r = client.get("/api/preview-color")
    assert r.json()["color_hex"] == UNKNOWN_SWATCH_HEX == "#cccccc"
    assert r.json()["text_color"] == text_color_for(UNKNOWN_SWATCH_HEX)


@pytest.mark.parametrize("fields", [
    {"ebc": 20},                                        # EBC only
    {"color_override": "#780606"},                      # override only
    {"ebc": 20, "color_override": "#780606"},           # both: the override wins
    {"ebc": 4, "saturation": 0.3},                       # a muted computed colour
    {"ebc": 79},                                         # a near-black stout
])
def test_preview_endpoint_returns_what_the_board_would_resolve(write_tap, fields):
    """The admin preview and the board must agree, asserted rather than claimed.

    Both delegate to colors.resolve_color, so this fails the moment either grows
    a precedence chain of its own - which is what the prose comment the endpoint
    used to carry could not do.
    """
    from app.board import resolve_tap

    write_tap("custom", 1, name="Same Beer", **fields)
    board = resolve_tap(1)
    r = client.get("/api/preview-color", params={
        "ebc": str(fields.get("ebc", "")),
        # The endpoint takes saturation as a percentage, front matter as a fraction.
        "sat": str(fields["saturation"] * 100) if "saturation" in fields else "",
        "hex": fields.get("color_override", ""),
    })
    assert r.json()["color_hex"] == board["color_hex"]
    assert r.json()["text_color"] == board["text_color"]


@pytest.mark.parametrize("fields", [
    {"ebc": 20},                                        # EBC only
    {"color_override": "#780606"},                      # override only
    {"ebc": 20, "color_override": "#780606"},           # both: the override wins
    {"ebc": 4, "saturation": 0.3},                       # a muted computed colour
    {"ebc": 79},                                         # a near-black stout
])
def test_swatch_and_placeholder_agree_on_a_known_colour(write_tap, fields):
    """A *known* Colour is byte-identical on the swatch and in the pour.

    This is the guarantee resolving once buys, and it is checked end to end: the
    board's color_hex is the swatch, and the SVG actually served from the
    image_url it built is the Placeholder.
    """
    config_store.update_config(num_taps=1)
    write_tap("custom", 1, name="Same Beer", **fields)
    tap = client.get("/api/board").json()["taps"][0]
    svg = client.get(tap["image_url"])
    assert svg.status_code == 200
    assert _base_stop(svg.text).lower() == tap["color_hex"]


def test_unknown_colour_lets_each_surface_use_its_own_fallback(write_tap):
    """Unknown is not a shared colour: the swatch greys, the Placeholder ambers.

    Deliberate, and the one case where the two surfaces differ - see ADR-0004.
    """
    from app.beer_glass import _DEFAULT_HEX
    from app.colors import UNKNOWN_SWATCH_HEX

    config_store.update_config(num_taps=1)
    write_tap("custom", 1, name="Colourless")           # no EBC, no override
    tap = client.get("/api/board").json()["taps"][0]

    # The board sends no colour at all rather than picking one of the two.
    assert tap["color_hex"] is None and tap["text_color"] is None
    assert tap["image_url"] == "/img/beer-glass"

    # The Placeholder draws its amber; the swatch (here via the admin preview,
    # which paints the same surface as display.js) draws its grey.
    assert _base_stop(client.get(tap["image_url"]).text).lower() == _DEFAULT_HEX
    assert client.get("/api/preview-color").json()["color_hex"] == UNKNOWN_SWATCH_HEX
    assert _DEFAULT_HEX != UNKNOWN_SWATCH_HEX


# ---- passwordless demo admin (Feature 6) -------------------------------

def test_demo_open_admin_without_password(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    r = TestClient(app).get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert "Demo mode" in r.text  # the open-admin banner renders


def test_demo_with_password_still_requires_login(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    r = TestClient(app).get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


def test_no_demo_no_password_admin_denied(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    r = TestClient(app).get("/admin", follow_redirects=False)
    assert r.status_code == 303  # fail-closed, unchanged
    assert r.headers["location"] == "/admin/login"


# ---- review-fix regressions -------------------------------------------

def test_display_assets_are_cache_busted():
    # The TV display CSS/JS carry a ?v=<mtime> token too - the display is the
    # hardest surface to hard-refresh, so it must pick up a rebuild automatically.
    html = client.get("/").text
    assert "/static/css/display.css?v=" in html
    assert "/static/js/display.js?v=" in html


def test_api_board_omits_sync_status():
    # /api/board is public and unauthenticated; sync status/error (which can carry
    # upstream API error text) must NOT leak there. Status lives in its own file
    # now, so it is seeded through the Status store - writing it via the config
    # store would be dropped as an unknown key and prove nothing.
    config_store.update_config(num_taps=1)
    status_store.update_status(last_sync_error="boom: upstream 500 body",
                               last_sync_success="2026-01-01T00:00:00")
    board = client.get("/api/board").json()
    for key in status_store.STATUS_KEYS:
        assert key not in board


def test_admin_status_panel_renders_status_from_status_json():
    # The panel used to read these off `cfg`; they come from the Status store
    # now, so a rename in the template would otherwise fail silently to "never".
    status_store.update_status(last_sync_success="2026-04-04T00:00:00",
                               last_sync_attempt="2026-04-04T00:00:01",
                               last_sync_error="upstream 500")
    html = _login(TestClient(app)).get("/admin").text
    assert "2026-04-04T00:00:00" in html
    assert "2026-04-04T00:00:01" in html
    assert "upstream 500" in html


def test_admin_status_panel_says_never_on_a_fresh_box():
    html = _login(TestClient(app)).get("/admin").text
    assert "never" in html


def test_img_responses_carry_svg_csp():
    # Every /img response neutralises script in a directly-opened SVG.
    for path in ("/img/beer-glass", "/img/placeholder"):
        r = client.get(path)
        assert r.status_code == 200
        assert "script-src 'none'" in r.headers.get("content-security-policy", "")
        assert r.headers.get("x-content-type-options") == "nosniff"


def test_oversized_upload_is_rejected(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)  # tiny cap for the test
    c = _login(TestClient(app))
    r = c.post("/admin/venue-logo",
               files={"image": ("logo.png", b"\x89PNG\r\n\x1a\n-oversized", "image/png")})
    assert r.status_code == 413


def test_bad_number_does_not_orphan_uploaded_image():
    # Validation runs before any filesystem write, so a rejected override never
    # leaves an orphaned image with no md file.
    c = _login(TestClient(app))
    r = c.post("/admin/override/1",
               data={"enabled": "true", "name": "Bad", "abv": "not-a-number"},
               files={"image": ("beer.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 422
    assert taps.image_for(1, taps.Source.MANUAL) is None
    assert not taps.exists(1, taps.Source.MANUAL)


def test_save_settings_preset_overrides_the_posted_scales():
    # The sliders may be showing anything (a stale tab, a scripted post); a named
    # preset owns its scale, so the stored Settings can never say "small" beside
    # Default's number.
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "4", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "tap_photo_preset": "small", "tap_text_preset": "small",
        "tap_image_scale": "2.5", "tap_text_scale": "1.9",
    })
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert cfg["tap_photo_preset"] == "small"
    assert cfg["tap_text_preset"] == "small"
    assert cfg["tap_image_scale"] == 0.6
    assert cfg["tap_text_scale"] == 0.75


def test_save_settings_custom_keeps_the_posted_scales():
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "4", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "tap_photo_preset": "custom", "tap_text_preset": "custom",
        "tap_image_scale": "0.4", "tap_text_scale": "1.8",
    })
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert cfg["tap_photo_preset"] == "custom"
    assert cfg["tap_text_preset"] == "custom"
    assert cfg["tap_image_scale"] == 0.4
    assert cfg["tap_text_scale"] == 1.8


def test_save_settings_axes_are_independent():
    # Picking a photo preset must leave the text axis exactly as posted, and the
    # other way round: the two controls do not share a preset any more.
    c = _login(TestClient(app))
    r = c.post("/admin/settings", data={
        "num_taps": "4", "max_archive_age_days": "1", "max_archive_storage_mb": "1",
        "tap_photo_preset": "tiny", "tap_text_preset": "custom",
        "tap_image_scale": "0.9", "tap_text_scale": "1.8",
    })
    assert r.status_code == 200
    cfg = config_store.load_config()
    assert cfg["tap_image_scale"] == 0.4      # the photo preset owns its number
    assert cfg["tap_text_scale"] == 1.8       # the text axis kept what was posted
