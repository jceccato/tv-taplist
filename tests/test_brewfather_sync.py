"""Brewfather field extraction, the efficient fetch, and sync/archive logic."""
import httpx
import pytest

from app import brewfather, config_store, markdown_store as md, paths


# ---- field extraction --------------------------------------------------

def test_find_tap_number_variants():
    assert brewfather._find_tap_number({"batchNotes": "pour on tap:3"}) == 3
    assert brewfather._find_tap_number({"batchNotes": "Tap: 12 please"}) == 12
    assert brewfather._find_tap_number({"batchNotes": "no token"}) is None
    assert brewfather._find_tap_number({"notes": [{"note": "tap:7"}]}) == 7


def test_extract_abv_prefers_measured():
    assert brewfather._extract_abv({"measuredAbv": 6.5, "recipe": {"abv": 6.0}}) == 6.5
    assert brewfather._extract_abv({"recipe": {"abv": 6.0}}) == 6.0


def test_extract_name_prefers_recipe_over_generic_batch():
    # Brewfather's default batch name is generic; the recipe holds the beer name.
    assert brewfather._extract_name({"name": "Batch", "recipe": {"name": "Hazy IPA"}}) == "Hazy IPA"
    assert brewfather._extract_name({"name": "Batch #12", "recipe": {"name": "Stout"}}) == "Stout"
    # A user-customised batch name is respected over the recipe name.
    assert brewfather._extract_name(
        {"name": "Festbier 2026", "recipe": {"name": "Festbier"}}) == "Festbier 2026"
    # No recipe name -> fall back to the batch number.
    assert brewfather._extract_name({"name": "Batch", "batchNo": 7}) == "Batch 7"


def test_zero_stats_are_treated_as_missing():
    # Brewfather sends 0 (not null) for unset values; we store None so the
    # display hides the stat instead of showing a "0".
    assert brewfather._extract_abv({"measuredAbv": 0, "recipe": {"abv": 0}}) is None
    assert brewfather._extract_ibu({"measuredIbu": 0}) is None
    assert brewfather._extract_ebc({"measuredEbc": 0, "estimatedColor": 0}) is None
    # A real value still comes through even when a measured field is 0.
    assert brewfather._extract_abv({"measuredAbv": 0, "recipe": {"abv": 5.2}}) == 5.2


def test_description_uses_taste_notes_then_style():
    # A dedicated tasting-note field wins (and any tap token in it is stripped).
    assert brewfather._extract_description(
        {"tasteNotes": "Crisp and clean", "batchNotes": "tap:4"}) == "Crisp and clean"
    # No tasting notes -> fall back to the recipe style name.
    assert brewfather._extract_description(
        {"batchNotes": "tap:4", "recipe": {"style": {"name": "English Porter"}}}) == "English Porter"
    assert brewfather._extract_description(
        {"recipe": {"style": "Cider With Other Fruit"}}) == "Cider With Other Fruit"
    # Batch notes (control data) are NEVER used as the description body.
    assert brewfather._extract_description({"batchNotes": "tap:4 brew log text"}) == ""
    # Nothing available -> blank.
    assert brewfather._extract_description({"recipe": {}}) == ""


def test_extract_ebc_and_srm():
    # A measured EBC reading is taken at face value.
    assert brewfather._extract_ebc({"measuredEbc": 40}) == 40.0
    # estimatedColor / color / recipe.color are SRM -> converted to EBC (*1.97).
    assert brewfather._extract_ebc({"estimatedColor": 37.5}) == pytest.approx(73.9, abs=0.05)
    assert brewfather._extract_ebc({"recipe": {"color": 25}}) == pytest.approx(49.25, abs=0.06)
    assert brewfather._extract_ebc({"srm": 10}) == pytest.approx(19.7, abs=0.05)
    # Measured EBC wins over an estimated SRM colour.
    assert brewfather._extract_ebc({"measuredEbc": 30, "estimatedColor": 99}) == 30.0


def test_extract_image_url_handles_null():
    assert brewfather._extract_image_url({"recipe": {"img_url": None}}) is None
    assert brewfather._extract_image_url({"recipe": {"img_url": "http://x/y.webp"}}) == "http://x/y.webp"


def test_extract_saturation_from_notes():
    assert brewfather._extract_saturation({"batchNotes": "tap:3 saturation:60"}) == 0.6
    assert brewfather._extract_saturation({"batchNotes": "saturation: 0.4"}) == 0.4
    assert brewfather._extract_saturation({"batchNotes": "tap:3 only"}) is None


def test_saturation_token_stripped_from_description():
    # A stray saturation token in tasting notes is not shown on the card.
    assert brewfather._extract_description(
        {"tasteNotes": "Roasty saturation:70 finish"}) == "Roasty finish"


def test_extract_color_override_token():
    assert brewfather._extract_color_override({"batchNotes": "tap:3 colour:#780606"}) == "#780606"
    assert brewfather._extract_color_override({"batchNotes": "color: 780606"}) == "#780606"
    assert brewfather._extract_color_override({"batchNotes": "tap:3"}) is None


def test_extract_glass_token():
    assert brewfather._extract_glass({"batchNotes": "tap:3 glass:nonicpint"}) == "nonicpint"
    assert brewfather._extract_glass({"batchNotes": "glass:Teku"}) == "teku"
    assert brewfather._extract_glass({"batchNotes": "glass:notaglass"}) is None
    assert brewfather._extract_glass({"batchNotes": "tap:3"}) is None


def test_color_and_glass_tokens_stripped_from_description():
    assert brewfather._extract_description(
        {"tasteNotes": "Smooth colour:#112233 and glass:tulip pour"}) == "Smooth and pour"


def test_extract_og_fg_specific_gravity_only():
    assert brewfather._extract_og({"measuredOg": 1.052, "recipe": {"og": 1.060}}) == 1.052
    assert brewfather._extract_og({"recipe": {"og": 1.060}}) == 1.060
    assert brewfather._extract_fg({"measuredFg": 1.010}) == 1.010
    # Unset (0 / 1.0) or out-of-range (Plato-like) values are treated as missing.
    assert brewfather._extract_og({"measuredOg": 0, "og": 1.0}) is None
    assert brewfather._extract_og({"og": 12.5}) is None
    assert brewfather._extract_fg({}) is None


# ---- desired map / conflict resolution ---------------------------------

def test_conflict_newest_wins():
    batches = [
        {"_id": "a", "name": "Old", "status": "Completed", "batchNotes": "tap:3", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "Completed", "batchNotes": "tap:3", "updated": 200},
    ]
    assert brewfather._build_desired_map(batches)[3]["batch"]["name"] == "New"


def test_no_tap_token_is_ignored():
    assert brewfather._build_desired_map([{"_id": "a", "status": "Completed", "batchNotes": "x"}]) == {}


# ---- efficient list (complete=True + pagination) -----------------------

class _FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_list_batches_uses_complete_and_paginates():
    pages = [
        [{"_id": str(i), "status": "Completed"} for i in range(brewfather.PAGE_SIZE)],
        [{"_id": "last", "status": "Completed"}],
    ]
    calls = []

    class FakeClient:
        def get(self, path, params=None):
            calls.append(params)
            return _FakeResp(pages[len(calls) - 1])

    out = brewfather._list_batches(FakeClient(), ["Completed"])
    assert len(out) == brewfather.PAGE_SIZE + 1
    assert calls[0]["status"] == "Completed"
    assert calls[0]["complete"] == "True"        # one call returns full data
    assert calls[0]["limit"] == brewfather.PAGE_SIZE
    assert "start_after" not in calls[0]
    # Second page requested with the last _id of the first page.
    assert calls[1]["start_after"] == str(brewfather.PAGE_SIZE - 1)


def test_list_batches_merges_statuses_and_dedupes():
    # A batch id appearing under both requested statuses is returned once.
    pages = {
        "Completed": [{"_id": "c1", "status": "Completed"},
                      {"_id": "shared", "status": "Completed"}],
        "Conditioning": [{"_id": "shared", "status": "Conditioning"},
                         {"_id": "k1", "status": "Conditioning"}],
    }

    class FakeClient:
        def get(self, path, params=None):
            # One (short) page per status stops pagination immediately.
            return _FakeResp(pages[params["status"]])

    out = brewfather._list_batches(FakeClient(), ["Completed", "Conditioning"])
    ids = [b["_id"] for b in out]
    assert ids.count("shared") == 1
    assert set(ids) == {"c1", "shared", "k1"}


def test_list_batches_refilters_unwanted_status():
    # Defensive re-filter drops a batch the API returns in a status we didn't ask for.
    pages = {"Completed": [{"_id": "c1", "status": "Completed"},
                           {"_id": "x", "status": "Conditioning"}]}

    class FakeClient:
        def get(self, path, params=None):
            return _FakeResp(pages[params["status"]])

    out = brewfather._list_batches(FakeClient(), ["Completed"])
    assert [b["_id"] for b in out] == ["c1"]


# ---- sync orchestration (network mocked) -------------------------------

def _batch(bid, tap, name, **extra):
    b = {"_id": bid, "name": name, "status": "Completed", "batchNotes": f"tap:{tap}",
         "measuredAbv": 5.0, "measuredEbc": 12, "recipe": {"ibu": 30}, "_timestamp_ms": 1000}
    b.update(extra)
    return b


@pytest.fixture
def mock_network(monkeypatch):
    """Patch the batch fetch + image download so sync runs offline.

    The fake fetch mirrors the real `_list_batches`: it returns only batches whose
    status is among the requested statuses, deduped by _id — so run_sync tests
    genuinely exercise the include_conditioning status selection.
    """
    state = {"batches": [], "downloads": {}}

    def fake_list(client, statuses):
        wanted = {str(s).lower() for s in statuses}
        out, seen = [], set()
        for b in state["batches"]:
            if str(b.get("status", "")).lower() not in wanted:
                continue
            bid = str(b.get("_id") or b.get("id"))
            if bid in seen:
                continue
            seen.add(bid)
            out.append(b)
        return out

    monkeypatch.setattr(brewfather, "_list_batches", fake_list)

    def fake_download(client, url, stem):
        name = state["downloads"].get(stem)
        if name:
            (paths.TAPS_DIR / name).write_bytes(b"img")
        return name

    monkeypatch.setattr(brewfather, "_download_image", fake_download)
    return state


def _set_creds():
    config_store.update_config(brewfather_user_id="u", brewfather_api_key="k", num_taps=4)


def test_sync_writes_bf_tap(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Tap Two Ale")]
    result = brewfather.run_sync()
    assert result["ok"] is True
    assert result["written"] == 1
    data = md.read_tap_file(md.bf_md_path(2))
    assert data["name"] == "Tap Two Ale"
    assert data["source"] == "brewfather"


def test_sync_includes_conditioning_when_enabled(mock_network):
    _set_creds()
    config_store.update_config(include_conditioning=True)
    mock_network["batches"] = [_batch("c1", 3, "Lagering Pils", status="Conditioning")]
    result = brewfather.run_sync()
    assert result["written"] == 1
    assert md.read_tap_file(md.bf_md_path(3))["name"] == "Lagering Pils"


def test_sync_ignores_conditioning_when_disabled(mock_network):
    _set_creds()  # include_conditioning defaults False
    mock_network["batches"] = [_batch("c1", 3, "Lagering Pils", status="Conditioning")]
    result = brewfather.run_sync()
    assert result["written"] == 0
    assert not md.bf_md_path(3).exists()


def test_sync_writes_saturation_token(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Muted Ale", batchNotes="tap:2 saturation:50")]
    brewfather.run_sync()
    assert md.read_tap_file(md.bf_md_path(2))["saturation"] == 0.5


def test_sync_writes_colour_glass_and_gravity(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch(
        "b1", 2, "Loaded Ale",
        batchNotes="tap:2 colour:#445566 glass:tulip",
        measuredOg=1.055, measuredFg=1.012)]
    brewfather.run_sync()
    data = md.read_tap_file(md.bf_md_path(2))
    assert data["color_override"] == "#445566"
    assert data["glass"] == "tulip"
    assert data["og"] == 1.055
    assert data["fg"] == 1.012


def test_sync_skips_unchanged_batch(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Steady Ale")]
    first = brewfather.run_sync()
    assert first["written"] == 1
    # Second sync with the identical batch (same _timestamp_ms) writes nothing.
    second = brewfather.run_sync()
    assert second["written"] == 0
    assert second["unchanged"] == 1


def test_sync_rewrites_when_revision_changes(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Ale", _timestamp_ms=1000)]
    brewfather.run_sync()
    mock_network["batches"] = [_batch("b1", 2, "Ale Renamed", _timestamp_ms=2000)]
    result = brewfather.run_sync()
    assert result["written"] == 1
    assert md.read_tap_file(md.bf_md_path(2))["name"] == "Ale Renamed"


def test_sync_never_touches_manual_override(mock_network, write_tap):
    _set_creds()
    write_tap("custom", 2, name="My Override", abv=4.2, ebc=8)
    mock_network["batches"] = [_batch("b1", 2, "Should Not Win")]
    result = brewfather.run_sync()
    assert result["skipped_overrides"] == 1
    assert not md.bf_md_path(2).exists()
    assert md.read_tap_file(md.custom_md_path(2))["name"] == "My Override"


def test_sync_archives_undesired_bf_tap(mock_network, write_tap):
    _set_creds()
    write_tap("bf", 1, name="Retiring Ale", abv=5, ebc=10, image_ext=".jpg")
    mock_network["batches"] = [_batch("b1", 2, "New Tap Two")]
    result = brewfather.run_sync()
    assert result["archived"] == 1
    assert not md.bf_md_path(1).exists()
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.md"))
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.jpg"))


def test_failed_sync_makes_no_destructive_changes(mock_network, write_tap, monkeypatch):
    _set_creds()
    write_tap("bf", 1, name="Existing", abv=5, ebc=10)

    def boom(client, statuses):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(brewfather, "_list_batches", boom)
    result = brewfather.run_sync()
    assert result["ok"] is False
    assert md.bf_md_path(1).exists()
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []
    assert config_store.load_config()["last_sync_error"]


def test_rate_limit_429_is_reported_without_changes(mock_network, write_tap, monkeypatch):
    _set_creds()
    write_tap("bf", 1, name="Existing", abv=5, ebc=10)

    def boom(client, statuses):
        resp = httpx.Response(429, headers={"Retry-After": "120"}, request=httpx.Request("GET", "http://x"))
        raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)

    monkeypatch.setattr(brewfather, "_list_batches", boom)
    result = brewfather.run_sync()
    assert result["ok"] is False
    assert "rate limit" in result["message"].lower()
    assert md.bf_md_path(1).exists()  # nothing destroyed


def test_sync_skipped_without_credentials(mock_network):
    config_store.update_config(num_taps=4)  # no credentials
    assert brewfather.run_sync().get("skipped") is True


def test_env_credentials_take_precedence(monkeypatch):
    config_store.update_config(brewfather_user_id="cfg_user", brewfather_api_key="cfg_key")
    monkeypatch.setenv("BREWFATHER_USER_ID", "env_user")
    monkeypatch.setenv("BREWFATHER_API_KEY", "env_key")
    creds = config_store.brewfather_credentials()
    assert creds["user_id"] == "env_user" and creds["key_from_env"] is True
    monkeypatch.delenv("BREWFATHER_API_KEY")
    creds2 = config_store.brewfather_credentials()
    assert creds2["api_key"] == "cfg_key" and creds2["key_from_env"] is False


def test_sync_keeps_cached_image_when_download_fails(mock_network):
    _set_creds()
    (paths.TAPS_DIR / "bf_tap_3.webp").write_bytes(b"old-good-image")
    mock_network["batches"] = [_batch("b3", 3, "Tap Three", recipe={"img_url": "http://x/y.webp", "ibu": 20})]
    mock_network["downloads"] = {}  # download returns None
    brewfather.run_sync()
    data = md.read_tap_file(md.bf_md_path(3))
    assert data["image"] == "bf_tap_3.webp"
    assert (paths.TAPS_DIR / "bf_tap_3.webp").read_bytes() == b"old-good-image"


def test_download_image_preserves_source_extension():
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
