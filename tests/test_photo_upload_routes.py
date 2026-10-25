"""The Photos tab's routes (issue #57): login, caps, streaming, status, thumbnails.

Over HTTP like the other route suites. What matters here is the route's own job:
nothing without the Admin login, the library's rejections turned into the right
status codes, and no upload ever touching the system temp directory.
"""
import io
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import paths, photo_library as lib
from app.main import app


@pytest.fixture(autouse=True)
def no_background_processing(monkeypatch):
    # Tests drive processing themselves so states are deterministic.
    monkeypatch.setattr(lib, "kick_processing", lambda: None)


def _jpeg(color=(10, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buf, "JPEG")
    return buf.getvalue()


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/admin/login", data={"password": "testpw"},
                  follow_redirects=False).status_code == 303
    return c


def test_every_photo_route_needs_the_admin_login():
    anon = TestClient(app)
    assert anon.post("/admin/photos/upload?name=a.jpg", content=_jpeg()).status_code == 401
    assert anon.get("/admin/photos/status").status_code == 401
    assert anon.get("/admin/photos/thumb/" + "a" * 32 + ".webp").status_code == 401
    assert list(paths.PHOTOS_DIR.iterdir()) == []


def test_an_upload_is_stored_and_a_repeat_is_a_duplicate_with_a_note():
    c = _admin()
    r = c.post("/admin/photos/upload?name=IMG_1.jpg", content=_jpeg())
    assert r.status_code == 200
    assert r.json() == {"ok": True, "outcome": "stored", "album": "Unsorted",
                        "name": "IMG_1.jpg", "renamed": False}
    again = c.post("/admin/photos/upload?name=copy.jpg", content=_jpeg()).json()
    assert (again["outcome"], again["name"]) == ("duplicate", "IMG_1.jpg")
    clash = c.post("/admin/photos/upload?name=IMG_1.jpg", content=_jpeg((1, 2, 3))).json()
    assert (clash["name"], clash["renamed"]) == ("IMG_1 (2).jpg", True)


def test_the_cap_and_the_free_space_floor_answer_413_and_507(monkeypatch):
    c = _admin()
    monkeypatch.setattr(lib, "MAX_UPLOAD_BYTES", 100)
    r = c.post("/admin/photos/upload?name=big.jpg", content=b"x" * 500)
    assert r.status_code == 413 and "MB" in r.json()["detail"]
    monkeypatch.undo()
    monkeypatch.setattr(lib, "kick_processing", lambda: None)
    monkeypatch.setattr(lib, "_free_bytes", lambda: 10)
    r = c.post("/admin/photos/upload?name=a.jpg", content=_jpeg())
    assert r.status_code == 507 and "free" in r.json()["detail"]
    assert list(paths.PHOTOS_DIR.iterdir()) == []
    assert not paths.PHOTO_UPLOAD_STAGING_DIR.exists() or not list(paths.PHOTO_UPLOAD_STAGING_DIR.iterdir())


def test_a_rejected_upload_answers_with_the_reason_and_leaves_nothing_staged():
    c = _admin()
    r = c.post("/admin/photos/upload?name=doc.pdf", content=b"%PDF")
    assert r.status_code == 422 and "not a photo format" in r.json()["detail"]
    r = c.post("/admin/photos/upload?name=a.jpg&album=Nope", content=_jpeg())
    assert r.status_code == 404
    assert not list(paths.PHOTO_UPLOAD_STAGING_DIR.iterdir())


def test_an_upload_never_touches_the_system_temp_directory(monkeypatch):
    spooled = []
    real = tempfile.SpooledTemporaryFile
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile",
                        lambda *a, **k: spooled.append(1) or real(*a, **k))
    real_mkstemp = tempfile.mkstemp

    def mkstemp(*a, **k):      # the library's own atomic index write is inside the data directory
        assert str(k.get("dir", "")).startswith(str(paths.DATA_DIR)), "temp file outside the data directory"
        return real_mkstemp(*a, **k)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda *a, **k: pytest.fail("temp file used"))
    r = _admin().post("/admin/photos/upload?name=a.jpg", content=_jpeg())
    assert r.status_code == 200 and spooled == []


def test_status_lists_counts_albums_and_each_photos_state_and_thumbnail_is_admin_only():
    (paths.PHOTOS_DIR / "Events").mkdir()
    c = _admin()
    c.post("/admin/photos/upload?name=ok.jpg&album=Events", content=_jpeg())
    c.post("/admin/photos/upload?name=bad.jpg&album=Events", content=b"\xff\xd8\xff garbage")
    st = c.get("/admin/photos/status?album=Events").json()
    assert (st["total"], st["pending"], st["ready"]) == (2, 2, 0)
    assert st["albums"] == ["Unsorted", "Events"]
    assert {p["name"]: p["state"] for p in st["photos"]} == {"ok.jpg": "waiting", "bad.jpg": "waiting"}
    lib.process_pending()
    st = c.get("/admin/photos/status?album=Events").json()
    assert (st["ready"], st["failed"], st["pending"]) == (1, 1, 0)
    by = {p["name"]: p for p in st["photos"]}
    assert by["ok.jpg"]["state"] == "ready" and by["ok.jpg"]["thumb"]
    assert by["bad.jpg"]["state"] == "failed" and by["bad.jpg"]["reason"]
    thumb = c.get(by["ok.jpg"]["thumb"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    assert TestClient(app).get(by["ok.jpg"]["thumb"]).status_code == 401
    assert c.get("/admin/photos/thumb/ok.jpg").status_code == 404


def test_the_photos_tab_and_its_status_have_left_the_settings_tab():
    html = _admin().get("/admin").text
    assert 'id="tab-photos"' in html and 'data-tab="photos"' in html
    assert "could not be read" not in html.split('id="tab-photos"')[0]


def test_a_small_photo_carries_the_low_resolution_note_in_the_status():
    c = _admin()
    small, big = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (640, 480), (200, 20, 20)).save(small, "JPEG")
    Image.new("RGB", (1600, 1200), (20, 20, 200)).save(big, "JPEG")
    c.post("/admin/photos/upload?name=small.jpg", content=small.getvalue())
    c.post("/admin/photos/upload?name=big.jpg", content=big.getvalue())
    lib.process_pending()
    by = {p["name"]: p for p in c.get("/admin/photos/status").json()["photos"]}
    assert by["small.jpg"]["low_res"] is True and by["small.jpg"]["state"] == "ready"
    assert by["big.jpg"]["low_res"] is False


def test_the_file_picker_offers_every_format_the_library_reads():
    html = _admin().get("/admin").text
    tag = html.split('id="photos-file"')[1].split(">")[0]
    accept = tag.split('accept="')[1].split('"')[0].split(",")
    assert accept[0] == "image/*"          # the phone's own photo picker
    assert set(accept[1:]) == set(lib.PHOTO_EXTENSIONS)
    assert set(tag.split('data-extensions="')[1].split('"')[0].split()) == set(lib.PHOTO_EXTENSIONS)
    for ext in (".heic", ".cr3", ".nef", ".arw", ".raf", ".orf", ".dng", ".cr2", ".avif"):
        assert ext in accept
