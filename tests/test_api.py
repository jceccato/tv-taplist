"""HTTP surface: display, board API, image serving, admin auth + mutations."""
import pytest
from fastapi.testclient import TestClient

from app import config_store, markdown_store as md, paths
from app.main import _safe_tap_image, app

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
    import re

    from app.beer_glass import _hex_to_rgb

    pale = client.get("/img/beer-glass", params={"ebc": 8})
    dark = client.get("/img/beer-glass", params={"ebc": 80})
    assert pale.status_code == 200 and "svg" in pale.headers["content-type"]
    assert dark.status_code == 200

    def base_stop(svg: str) -> str:
        return re.search(r'offset="55%" stop-color="(#[0-9a-fA-F]{6})"', svg).group(1)

    # A dark beer's liquid must be markedly darker than a pale one's.
    assert sum(_hex_to_rgb(base_stop(dark.text))) < sum(_hex_to_rgb(base_stop(pale.text)))


def test_board_uses_colour_glass_when_no_photo(write_tap):
    config_store.update_config(num_taps=1)
    write_tap("custom", 1, name="Glassy", abv=5, ebc=20)
    board = client.get("/api/board").json()
    assert board["taps"][0]["image_url"] == "/img/beer-glass?ebc=20"


def test_safe_tap_image_rejects_traversal():
    # Direct unit check of the sanitiser.
    assert _safe_tap_image("../config.json") is None
    assert _safe_tap_image("..\\config.json") is None


# ---- auth --------------------------------------------------------------

def test_admin_requires_login():
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


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
    assert md.custom_md_path(2).exists()
    assert (paths.TAPS_DIR / "custom_tap_2.png").exists()
    data = md.read_tap_file(md.custom_md_path(2))
    assert data["name"] == "Hand Pour"
    assert data["abv"] == 4.5
    assert data["ebc"] == 9  # EBC unit by default

    # Clearing the override archives the custom files.
    r2 = c.post("/admin/override/2", data={"enabled": "false"})
    assert r2.status_code == 200 and r2.json()["override"] is False
    assert not md.custom_md_path(2).exists()
    assert list(paths.OLD_BEERS_DIR.glob("custom_tap_2_*.md"))


def test_override_save_archives_existing_brewfather(write_tap):
    c = _login(TestClient(app))
    write_tap("bf", 3, name="BF Three", abv=5, ebc=10, image_ext=".jpg")
    r = c.post("/admin/override/3", data={"enabled": "true", "name": "Now Custom", "abv": "5", "color": "10"})
    assert r.status_code == 200
    assert md.custom_md_path(3).exists()
    assert not md.bf_md_path(3).exists()
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_3_*.md"))


def test_override_saves_saturation_as_fraction():
    c = _login(TestClient(app))
    # The admin enters a percentage; it is stored as a 0..1 fraction.
    r = c.post("/admin/override/1",
               data={"enabled": "true", "name": "Muted", "color": "20", "saturation": "60"})
    assert r.status_code == 200
    assert md.read_tap_file(md.custom_md_path(1))["saturation"] == 0.6


def test_override_color_input_converts_from_srm():
    config_store.update_config(color_unit="srm")
    c = _login(TestClient(app))
    # 10 SRM should be stored as ~19.7 EBC.
    r = c.post("/admin/override/1", data={"enabled": "true", "name": "Dark", "color": "10"})
    assert r.status_code == 200
    assert md.read_tap_file(md.custom_md_path(1))["ebc"] == pytest.approx(19.7, abs=0.05)


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


def test_board_includes_display_settings():
    config_store.update_config(num_taps=1, color_unit="srm", show_ibu=False)
    board = client.get("/api/board").json()
    assert board["color_unit"] == "srm"
    assert board["show_ibu"] is False
    assert "hide_color_when_empty" in board
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
