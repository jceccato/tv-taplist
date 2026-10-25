"""The Photos tab's upload flow in a real browser (issue #57).

Several files go up in one selection, each in its own request with its own row;
one is forced to fail at the network and must leave the others stored, then
Retry puts it right. Then the tab shows the photos as ready thumbnails.
"""
import io

from PIL import Image

from app import paths


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


def _file(name, color, mime="image/jpeg"):
    return {"name": name, "mimeType": mime, "buffer": _jpeg(color)}


def _sign_in(page, base):
    r = page.context.request.post(f"{base}/admin/login", form={"password": "testpw"},
                                  max_redirects=0)
    assert r.status == 303


def test_many_photos_upload_separately_and_one_forced_failure_leaves_the_rest(serve, page):
    base = serve()
    _sign_in(page, base)
    page.goto(f"{base}/admin")
    page.click('.tab[data-tab="photos"]')

    sent = []
    def intercept(route):
        sent.append(route.request.url)
        if "name=bad.jpg" in route.request.url:
            route.abort("failed")
        else:
            route.continue_()
    page.route("**/admin/photos/upload*", intercept)

    page.set_input_files("#photos-file", files=[
        _file("a.jpg", (200, 40, 40)), _file("b.jpg", (40, 200, 40)),
        _file("bad.jpg", (40, 40, 200)), _file("c.jpg", (200, 200, 40)),
        _file("a-copy.jpg", (200, 40, 40)),      # same pixels and bytes as a.jpg
    ])
    page.wait_for_function(
        "document.querySelectorAll('#photos-uploads .upload-row[data-state=\"waiting\"],"
        "#photos-uploads .upload-row[data-state=\"uploading\"]').length === 0")

    rows = page.eval_on_selector_all(
        "#photos-uploads .upload-row",
        "rs => rs.map(r => ({name: r.querySelector('.upload-name').textContent, state: r.dataset.state,"
        " msg: r.querySelector('.upload-msg').textContent}))")
    by = {r["name"]: r for r in rows}
    assert len(sent) == 5                                   # one request per file
    assert [by[n]["state"] for n in ("a.jpg", "b.jpg", "c.jpg")] == ["done"] * 3
    assert by["bad.jpg"]["state"] == "failed"
    assert by["a-copy.jpg"]["state"] == "duplicate" and "a.jpg" in by["a-copy.jpg"]["msg"]
    assert page.is_visible("#photos-uploads .upload-row[data-state='failed'] .upload-retry")
    stored = sorted(p.name for p in paths.PHOTOS_DIR.iterdir())
    assert stored == ["a.jpg", "b.jpg", "c.jpg"]

    # Retry sends only the failed file, and this time it goes through.
    page.unroute("**/admin/photos/upload*")
    page.click("#photos-uploads .upload-row[data-state='failed'] .upload-retry")
    page.wait_for_selector("#photos-uploads .upload-row[data-state='done']:has-text('bad.jpg')")
    assert sorted(p.name for p in paths.PHOTOS_DIR.iterdir()) == ["a.jpg", "b.jpg", "bad.jpg", "c.jpg"]

    # Processing finishes in the background and the tab shows every photo ready.
    page.wait_for_function(
        "document.querySelectorAll('#photos-grid .photo-tile[data-state=\"ready\"]').length === 4",
        timeout=30_000)
    page.wait_for_function(
        "document.getElementById('photos-progress').textContent.includes('4 ready')")
    assert page.eval_on_selector_all(
        "#photos-grid img", "is => is.every(i => i.complete && i.naturalWidth > 0)")
    assert not list(paths.PHOTO_UPLOAD_STAGING_DIR.iterdir())


def test_a_file_that_cannot_be_decoded_shows_as_failed_with_its_reason(serve, page):
    base = serve()
    _sign_in(page, base)
    page.goto(f"{base}/admin")
    page.click('.tab[data-tab="photos"]')
    page.set_input_files("#photos-file", files=[
        {"name": "broken.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8\xff not really a jpeg"}])
    page.wait_for_selector("#photos-uploads .upload-row[data-state='done']")
    page.wait_for_selector("#photos-grid .photo-tile[data-state='failed']", timeout=30_000)
    assert "cannot be read" in page.inner_text("#photos-grid .photo-tile[data-state='failed']")
    assert "could not be read" in page.inner_text("#photos-progress")
