"""Brewfather field extraction and the sync orchestration / archive logic."""
import httpx
import pytest

from app import brewfather, config_store, markdown_store as md, paths


# ---- field extraction --------------------------------------------------

def test_find_tap_number_variants():
    assert brewfather._find_tap_number({"batchNotes": "pour on tap:3"}) == 3
    assert brewfather._find_tap_number({"batchNotes": "Tap: 12 please"}) == 12
    assert brewfather._find_tap_number({"batchNotes": "no token"}) is None
    # notes as a list of {note: ...} objects
    assert brewfather._find_tap_number({"notes": [{"note": "tap:7"}]}) == 7


def test_extract_abv_prefers_measured():
    batch = {"measuredAbv": 6.5, "recipe": {"abv": 6.0}}
    assert brewfather._extract_abv(batch) == 6.5
    assert brewfather._extract_abv({"recipe": {"abv": 6.0}}) == 6.0


def test_extract_ebc_treats_brewfather_color_as_ebc():
    assert brewfather._extract_ebc({"measuredEbc": 40}) == 40.0
    assert brewfather._extract_ebc({"recipe": {"color": 25}}) == 25.0


def test_extract_ebc_converts_explicit_srm():
    assert brewfather._extract_ebc({"srm": 10}) == pytest.approx(19.7, abs=0.05)


def test_extract_image_url_handles_null():
    assert brewfather._extract_image_url({"recipe": {"img_url": None}}) is None
    assert brewfather._extract_image_url({"recipe": {"img_url": "http://x/y.webp"}}) == "http://x/y.webp"


# ---- desired map / conflict resolution ---------------------------------

def test_conflict_newest_wins():
    batches = [
        {"_id": "a", "name": "Old", "status": "Completed", "batchNotes": "tap:3", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "Completed", "batchNotes": "tap:3", "updated": 200},
    ]
    desired = brewfather._build_desired_map(batches)
    assert desired[3]["batch"]["name"] == "New"


def test_no_tap_token_is_ignored():
    batches = [{"_id": "a", "name": "X", "status": "Completed", "batchNotes": "no token"}]
    assert brewfather._build_desired_map(batches) == {}


# ---- sync orchestration (network mocked) -------------------------------

def _detail(bid, tap, name, **extra):
    d = {"_id": bid, "name": name, "status": "Completed", "batchNotes": f"tap:{tap}",
         "measuredAbv": 5.0, "measuredEbc": 12, "recipe": {"ibu": 30}}
    d.update(extra)
    return d


@pytest.fixture
def mock_network(monkeypatch):
    """Patch the HTTP helpers so run_sync runs fully offline."""
    state = {"summaries": [], "details": {}, "downloads": {}}

    monkeypatch.setattr(brewfather, "_list_completed_batches", lambda c: state["summaries"])
    monkeypatch.setattr(brewfather, "_fetch_detail", lambda c, bid: state["details"].get(bid))

    def fake_download(client, url, stem):
        name = state["downloads"].get(stem)
        if name:
            (paths.TAPS_DIR / name).write_bytes(b"img")
        return name

    monkeypatch.setattr(brewfather, "_download_image", fake_download)
    return state


def test_sync_writes_bf_tap(mock_network):
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)
    mock_network["summaries"] = [{"_id": "b1"}]
    mock_network["details"] = {"b1": _detail("b1", 2, "Tap Two Ale")}

    result = brewfather.run_sync()
    assert result["ok"] is True
    assert result["written"] == 1
    data = md.read_tap_file(md.bf_md_path(2))
    assert data["name"] == "Tap Two Ale"
    assert data["source"] == "brewfather"


def test_sync_never_touches_manual_override(mock_network, write_tap):
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)
    write_tap("custom", 2, name="My Override", abv=4.2, ebc=8)
    mock_network["summaries"] = [{"_id": "b1"}]
    mock_network["details"] = {"b1": _detail("b1", 2, "Should Not Win")}

    result = brewfather.run_sync()
    assert result["skipped_overrides"] == 1
    # No bf_tap_2.md written, custom intact.
    assert not md.bf_md_path(2).exists()
    assert md.read_tap_file(md.custom_md_path(2))["name"] == "My Override"


def test_sync_archives_undesired_bf_tap(mock_network, write_tap):
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)
    # Pre-existing bf tap 1 that is no longer claimed by any batch.
    write_tap("brewfather", 1, name="Retiring Ale", abv=5, ebc=10, image_ext=".jpg")
    mock_network["summaries"] = [{"_id": "b1"}]
    mock_network["details"] = {"b1": _detail("b1", 2, "New Tap Two")}

    result = brewfather.run_sync()
    assert result["archived"] == 1
    assert not md.bf_md_path(1).exists()
    archived = list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.md"))
    assert len(archived) == 1
    # Paired image archived too.
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.jpg"))


def test_failed_sync_makes_no_destructive_changes(mock_network, write_tap, monkeypatch):
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)
    write_tap("brewfather", 1, name="Existing", abv=5, ebc=10)

    def boom(client):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(brewfather, "_list_completed_batches", boom)

    result = brewfather.run_sync()
    assert result["ok"] is False
    # Existing file untouched, nothing archived.
    assert md.bf_md_path(1).exists()
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []
    assert config_store.load_config()["last_sync_error"]


def test_sync_skipped_without_credentials(mock_network):
    config_store.update_config(num_taps=4)  # no credentials
    result = brewfather.run_sync()
    assert result.get("skipped") is True


def test_sync_keeps_cached_image_when_download_fails(mock_network, write_tap):
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)
    # Existing cached image for tap 3.
    (paths.TAPS_DIR / "bf_tap_3.webp").write_bytes(b"old-good-image")
    mock_network["summaries"] = [{"_id": "b3"}]
    mock_network["details"] = {"b3": _detail("b3", 3, "Tap Three", recipe={"img_url": "http://x/y.webp", "ibu": 20})}
    # download returns None (failure) -> must keep existing image.
    mock_network["downloads"] = {}

    brewfather.run_sync()
    data = md.read_tap_file(md.bf_md_path(3))
    assert data["image"] == "bf_tap_3.webp"
    assert (paths.TAPS_DIR / "bf_tap_3.webp").read_bytes() == b"old-good-image"


def test_download_image_preserves_source_extension(monkeypatch):
    """_download_image keeps the URL's extension rather than forcing .jpg."""
    class FakeResp:
        content = b"webp-bytes"
        headers = {"content-type": "image/webp"}
        def raise_for_status(self):
            pass

    class FakeClient:
        def get(self, url, **kw):
            return FakeResp()

    name = brewfather._download_image(FakeClient(), "http://x/pic.webp", "bf_tap_9")
    assert name == "bf_tap_9.webp"
    assert (paths.TAPS_DIR / "bf_tap_9.webp").exists()
