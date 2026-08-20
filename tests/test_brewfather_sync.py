"""The Brewfather Source: the efficient fetch, the image download, and sync.

The Batch-to-Beer transformation these tests exercise indirectly is tested
directly, with no client and no fake, in test_mapping.py.
"""
import httpx
import pytest

from app import brewfather, config_store, mapping, paths, status_store, tap_store as taps


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
    """Run sync offline: a fake API transport, plus a fake image download.

    The API side is faked at the **transport**, not at `_list_batches`. The
    fixture hands `_client` a real `httpx.Client` wired to an `httpx.MockTransport`
    that serves `state["batches"]` the way Brewfather does - one status per
    request, paginated by `start_after`. The production `_list_batches` then runs
    for real, so the status filtering, the dedupe and the pagination that sync
    depends on are the shipped ones rather than a fake's re-implementation of
    them. (The previous fixture reimplemented that filtering and conceded as
    much in a comment; a bug in the real listing could not fail a sync test.)
    `state["requests"]` records every request the transport saw, which is how a
    test can assert exactly which statuses were swept and at what cost.

    The download side stays patched at `_download_image`, which is a seam on
    purpose: it takes the client to use as its first argument, and WHICH of
    sync's two httpx clients arrives there is the difference between a fetch and
    a credential leak (image URLs are off-host and httpx applies a client's auth
    to every host). `state["download_clients"]` records that argument. The fake
    reproduces nothing: `_download_image` returns bytes plus an extension for the
    Tap file store to file, so the fake only answers "did this URL download, and
    to what?" - `state["downloads"]` maps a URL -> (bytes, ext), and a URL absent
    from it stands for a failed download.

    Both client factories are stood in for, and both stand-ins keep the real
    thing's auth posture - the API client authenticated, the image client not -
    so that recorded argument still means what it meant before.
    """
    state = {"batches": [], "downloads": {}, "download_clients": [],
             "requests": [], "respond": None}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if state["respond"] is not None:
            return state["respond"](request)
        params = request.url.params
        wanted = str(params.get("status", "")).lower()
        rows = [b for b in state["batches"]
                if str(b.get("status", "")).lower() == wanted]
        after = params.get("start_after")
        if after:
            ids = [str(b.get("_id") or b.get("id")) for b in rows]
            rows = rows[ids.index(after) + 1:] if after in ids else []
        limit = int(params.get("limit", brewfather.PAGE_SIZE))
        return httpx.Response(200, json=rows[:limit])

    def fake_client(user_id, api_key):
        return httpx.Client(
            base_url=brewfather.API_BASE,
            auth=(user_id, api_key),
            transport=httpx.MockTransport(handler),
        )

    def fake_image_client():
        # Unauthenticated, exactly like the real factory - that is the property
        # the download seam is watched for. (The real factory's own
        # credential-freeness is pinned separately, by
        # test_image_client_carries_no_credentials.) It carries the API base_url
        # too, which the real one has no use for: it means that transposing
        # sync's two clients still reaches the image download and trips the
        # credential assertion, instead of every sync test dying earlier on a
        # relative URL and burying the reason.
        return httpx.Client(base_url=brewfather.API_BASE,
                            transport=httpx.MockTransport(handler))

    monkeypatch.setattr(brewfather, "_client", fake_client)
    monkeypatch.setattr(brewfather, "_image_client", fake_image_client)

    def fake_download(client, url):
        state["download_clients"].append(client)
        return state["downloads"].get(url)

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
    data = taps.read(2, taps.Source.BREWFATHER).front_matter
    assert data["name"] == "Tap Two Ale"
    assert data["source"] == "brewfather"


def test_sync_includes_conditioning_when_enabled(mock_network):
    _set_creds()
    config_store.update_config(include_conditioning=True)
    mock_network["batches"] = [_batch("c1", 3, "Lagering Pils", status="Conditioning")]
    result = brewfather.run_sync()
    assert result["written"] == 1
    assert taps.read(3, taps.Source.BREWFATHER).front_matter["name"] == "Lagering Pils"


def test_sync_ignores_conditioning_when_disabled(mock_network):
    _set_creds()  # include_conditioning defaults False
    mock_network["batches"] = [_batch("c1", 3, "Lagering Pils", status="Conditioning")]
    result = brewfather.run_sync()
    assert result["written"] == 0
    assert not taps.exists(3, taps.Source.BREWFATHER)


def test_sync_includes_fermenting_when_enabled(mock_network):
    _set_creds()
    config_store.update_config(include_fermenting=True)
    mock_network["batches"] = [_batch("f1", 3, "Green IPA", status="Fermenting")]
    result = brewfather.run_sync()
    assert result["written"] == 1
    assert taps.read(3, taps.Source.BREWFATHER).front_matter["name"] == "Green IPA"


def test_sync_ignores_fermenting_when_disabled(mock_network):
    _set_creds()  # include_fermenting defaults False
    mock_network["batches"] = [_batch("f1", 3, "Green IPA", status="Fermenting")]
    result = brewfather.run_sync()
    assert result["written"] == 0
    assert not taps.exists(3, taps.Source.BREWFATHER)


def test_fermenting_batch_without_tap_token_is_ignored(mock_network):
    # The status toggle only decides what is FETCHED. Claiming a Slot still needs
    # a `tap:X` note token, exactly as for a Completed or Conditioning Batch.
    _set_creds()
    config_store.update_config(include_fermenting=True)
    mock_network["batches"] = [_batch("f1", 3, "Unassigned Ferment",
                                      status="Fermenting", batchNotes="no token here")]
    result = brewfather.run_sync()
    assert result["written"] == 0
    assert not taps.exists(3, taps.Source.BREWFATHER)


def test_fermenting_tap_file_matches_a_completed_one(mock_network):
    # A Fermenting Batch maps to a Tap identically: nothing downstream knows the
    # Batch status, which is why MAPPING_VERSION is not bumped for this toggle.
    _set_creds()
    config_store.update_config(include_fermenting=True)
    mock_network["batches"] = [
        _batch("done", 1, "Same Beer", status="Completed"),
        _batch("ferm", 2, "Same Beer", status="Fermenting"),
    ]
    brewfather.run_sync()
    completed = dict(taps.read(1, taps.Source.BREWFATHER).front_matter)
    fermenting = dict(taps.read(2, taps.Source.BREWFATHER).front_matter)
    # Only the identifying fields may differ; every mapped Beer field must match.
    for key in ("batch_id", "tap", "updated"):
        completed.pop(key, None)
        fermenting.pop(key, None)
    assert completed == fermenting


@pytest.mark.parametrize("conditioning,fermenting,expected", [
    (False, False, ["Completed"]),
    (True, False, ["Completed", "Conditioning"]),
    (False, True, ["Completed", "Fermenting"]),
    (True, True, ["Completed", "Conditioning", "Fermenting"]),
])
def test_status_list_covers_all_toggle_combinations(mock_network, conditioning,
                                                    fermenting, expected):
    """Pin the exact statuses the API is swept for.

    Each status is a separate paginated sweep of the Brewfather API, so the list
    is what the rate-limit cost is proportional to - worth asserting directly
    against the requests that actually left, rather than only inferring it from
    which Batches came back.
    """
    _set_creds()
    config_store.update_config(include_conditioning=conditioning,
                               include_fermenting=fermenting)
    brewfather.run_sync()
    assert [r.url.params["status"] for r in mock_network["requests"]] == expected


def test_sync_writes_saturation_token(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Muted Ale", batchNotes="tap:2 saturation:50")]
    brewfather.run_sync()
    assert taps.read(2, taps.Source.BREWFATHER).front_matter["saturation"] == 0.5


def test_sync_writes_colour_glass_and_gravity(mock_network):
    _set_creds()
    mock_network["batches"] = [_batch(
        "b1", 2, "Loaded Ale",
        batchNotes="tap:2 colour:#445566 glass:tulip",
        measuredOg=1.055, measuredFg=1.012)]
    brewfather.run_sync()
    data = taps.read(2, taps.Source.BREWFATHER).front_matter
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
    assert taps.read(2, taps.Source.BREWFATHER).front_matter["name"] == "Ale Renamed"


def test_a_mapping_version_bump_rewrites_every_cached_tap_once(mock_network, monkeypatch):
    """The rewrite path MAPPING_VERSION exists to trigger.

    Bumping the constant is how a change to the Batch-to-Beer mapping reaches
    Taps that were cached before it: the Batch itself is untouched, so nothing
    else in the freshness check would ask for a rewrite. Until now that path had
    no test at all, and a bump could have silently stopped refreshing anything.
    """
    _set_creds()
    mock_network["batches"] = [_batch("b1", 2, "Steady Ale")]
    assert brewfather.run_sync()["written"] == 1
    assert brewfather.run_sync()["unchanged"] == 1  # settled: nothing to do

    monkeypatch.setattr(mapping, "MAPPING_VERSION", mapping.MAPPING_VERSION + 1)

    # The same unchanged Batch is rewritten exactly once...
    bumped = brewfather.run_sync()
    assert (bumped["written"], bumped["unchanged"]) == (1, 0)
    assert taps.read(2, taps.Source.BREWFATHER).front_matter["map_rev"] == \
        mapping.MAPPING_VERSION
    # ...and then settles back to skipping, at the new version.
    settled = brewfather.run_sync()
    assert (settled["written"], settled["unchanged"]) == (0, 1)


def test_a_tap_cached_at_an_older_mapping_version_is_rewritten(mock_network, write_tap):
    # The same rule seen from the other side: a Tap file left on disk by an older
    # build (everything current except `map_rev`) is refreshed on the next sync
    # rather than kept forever because its Batch never changed.
    _set_creds()
    write_tap("bf", 2, name="Stale Mapping", batch_id="b1", source_rev=1000,
              map_rev=mapping.MAPPING_VERSION - 1)
    mock_network["batches"] = [_batch("b1", 2, "Current Mapping")]
    result = brewfather.run_sync()
    assert result["written"] == 1
    data = taps.read(2, taps.Source.BREWFATHER).front_matter
    assert data["name"] == "Current Mapping"
    assert data["map_rev"] == mapping.MAPPING_VERSION


def test_sync_writes_into_a_manual_occupied_slot_without_touching_the_manual_tap(
        mock_network, write_tap):
    # Sync no longer skips a Slot that carries a Manual override: it keeps the
    # Brewfather Tap warm underneath, so clearing the override reveals a current
    # Beer instead of a Vacant Slot. Nothing displays it meanwhile - resolve
    # picks Manual first - and the Manual file itself is still untouched.
    _set_creds()
    write_tap("custom", 2, name="My Override", abv=4.2, ebc=8)
    mock_network["batches"] = [_batch("b1", 2, "Waiting Underneath")]
    result = brewfather.run_sync()
    assert result["written"] == 1
    assert taps.read(2, taps.Source.BREWFATHER).front_matter["name"] == "Waiting Underneath"
    # The Manual Tap is unchanged, and still the one that wins.
    assert taps.read(2, taps.Source.MANUAL).front_matter["name"] == "My Override"
    assert taps.resolve(2).source is taps.Source.MANUAL


def test_sync_does_not_archive_a_claimed_slot_under_an_override(mock_network, write_tap):
    # The archive decision does not consult override state at all any more. A
    # Slot a Batch still claims is kept, override or not.
    _set_creds()
    write_tap("custom", 2, name="My Override", abv=4.2, ebc=8)
    write_tap("bf", 2, name="Was Here", abv=5, ebc=10)
    mock_network["batches"] = [_batch("b1", 2, "Still Claimed")]
    result = brewfather.run_sync()
    assert result["archived"] == 0
    assert taps.exists(2, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []


def test_sync_archives_an_unclaimed_slot_even_under_an_override(mock_network, write_tap):
    # ...and the converse: an override does not preserve a Brewfather Tap whose
    # Batch dropped its tap: token. Losing the claim is the one and only cause.
    _set_creds()
    write_tap("custom", 1, name="My Override", abv=4.2, ebc=8)
    write_tap("bf", 1, name="Retiring Ale", abv=5, ebc=10)
    mock_network["batches"] = [_batch("b1", 2, "New Tap Two")]
    result = brewfather.run_sync()
    assert result["archived"] == 1
    assert not taps.exists(1, taps.Source.BREWFATHER)
    assert taps.exists(1, taps.Source.MANUAL)  # the Manual Tap is never archived here
    assert list(paths.OLD_BEERS_DIR.glob("custom_tap_1_*")) == []


def test_tap_count_change_causes_no_write_no_archive_and_no_data_loss(mock_network):
    # The tap count is a display setting. Lowering it used to archive every
    # Brewfather Tap above the new number, and raising it back did not bring
    # them back - a presentation choice destroying Beer data with no warning.
    _set_creds()  # num_taps=4
    mock_network["batches"] = [_batch("b1", 1, "One"), _batch("b4", 4, "Four")]
    assert brewfather.run_sync()["written"] == 2

    config_store.update_config(num_taps=1)  # tap 4 is now off the board
    result = brewfather.run_sync()
    assert (result["written"], result["archived"]) == (0, 0)
    assert taps.exists(4, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []

    config_store.update_config(num_taps=4)  # and back again
    result = brewfather.run_sync()
    assert (result["written"], result["archived"]) == (0, 0)
    assert taps.read(4, taps.Source.BREWFATHER).front_matter["name"] == "Four"


def test_out_of_range_tap_token_is_rejected_and_logged(mock_network, caplog):
    # A mistyped token must not mint a file nothing can display, and must not
    # pass in silence either - silence is how a mistyped token stays mistyped.
    _set_creds()
    too_high = config_store.MAX_NUM_TAPS + 1
    mock_network["batches"] = [_batch("b1", too_high, "Fat Fingered Ale")]
    with caplog.at_level("WARNING", logger="taplist.sync"):
        result = brewfather.run_sync()
    assert result["written"] == 0
    assert not taps.exists(too_high, taps.Source.BREWFATHER)
    assert "Fat Fingered Ale" in caplog.text
    assert str(too_high) in caplog.text


def test_tap_token_above_the_tap_count_still_syncs(mock_network):
    # The bound is MAX_NUM_TAPS, not the operator's tap count: a Slot above the
    # tap count is simply not displayed, which is a display decision.
    _set_creds()  # num_taps=4
    mock_network["batches"] = [_batch("b1", 9, "Slot Nine Ale")]
    assert brewfather.run_sync()["written"] == 1
    assert taps.read(9, taps.Source.BREWFATHER).front_matter["name"] == "Slot Nine Ale"


def test_sync_archives_undesired_bf_tap(mock_network, write_tap):
    _set_creds()
    write_tap("bf", 1, name="Retiring Ale", abv=5, ebc=10, image_ext=".jpg")
    mock_network["batches"] = [_batch("b1", 2, "New Tap Two")]
    result = brewfather.run_sync()
    assert result["archived"] == 1
    assert not taps.exists(1, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.md"))
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_1_*.jpg"))


def test_failed_sync_makes_no_destructive_changes(mock_network, write_tap):
    _set_creds()
    write_tap("bf", 1, name="Existing", abv=5, ebc=10)

    def down(request):
        raise httpx.ConnectError("network down")

    mock_network["respond"] = down
    result = brewfather.run_sync()
    assert result["ok"] is False
    assert taps.exists(1, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []
    assert status_store.load_status()["last_sync_error"]


def test_rate_limit_429_is_reported_without_changes(mock_network, write_tap):
    _set_creds()
    write_tap("bf", 1, name="Existing", abv=5, ebc=10)

    def limited(request):
        return httpx.Response(429, headers={"Retry-After": "120"}, text="slow down")

    mock_network["respond"] = limited
    result = brewfather.run_sync()
    assert result["ok"] is False
    assert "rate limit" in result["message"].lower()
    assert "120" in result["message"]              # Retry-After is passed on
    assert taps.exists(1, taps.Source.BREWFATHER)  # nothing destroyed


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
    data = taps.read(3, taps.Source.BREWFATHER).front_matter
    assert data["image"] == "bf_tap_3.webp"
    assert (paths.TAPS_DIR / "bf_tap_3.webp").read_bytes() == b"old-good-image"


def test_sync_saves_downloaded_image_through_the_store(mock_network):
    # The bytes `_download_image` returns must end up in the file paired with the
    # Slot's Tap file, under the extension the download reported. This is the
    # coverage that used to live inside the download function itself, back when
    # it picked the filename and wrote the file.
    _set_creds()
    mock_network["downloads"] = {"http://x/y.webp": (b"img-bytes", ".webp")}
    mock_network["batches"] = [_batch(
        "b4", 4, "Photo Ale", recipe={"img_url": "http://x/y.webp", "ibu": 20})]
    brewfather.run_sync()
    assert (paths.TAPS_DIR / "bf_tap_4.webp").read_bytes() == b"img-bytes"
    assert taps.read(4, taps.Source.BREWFATHER).front_matter["image"] == "bf_tap_4.webp"


def test_sync_archives_bf_tap_above_the_tap_count(mock_network, write_tap):
    # The orphan scan asks the store for occupied Slots, and that enumeration is
    # deliberately unbounded by the configured tap count: a Brewfather file left
    # at a Slot above it must still be found and retired. Bounding the scan would
    # leave these files stranded forever.
    _set_creds()  # num_taps=4
    write_tap("bf", 9, name="Stranded Ale", abv=5, ebc=10)
    mock_network["batches"] = [_batch("b1", 2, "New Tap Two")]
    result = brewfather.run_sync()
    assert result["archived"] == 1
    assert not taps.exists(9, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("bf_tap_9_*.md"))


def test_download_image_preserves_source_extension():
    # `_download_image` is now a pure fetch: it returns the bytes and the
    # extension it worked out, and the Tap file store does the filing. The
    # end-to-end "the photo lands next to its Tap file" assertion lives in
    # test_sync_saves_downloaded_image_through_the_store above.
    class FakeResp:
        content = b"webp-bytes"
        headers = {"content-type": "image/webp"}
        def raise_for_status(self):
            pass

    class FakeClient:
        def get(self, url, **kw):
            return FakeResp()

    assert brewfather._download_image(FakeClient(), "http://x/pic.webp") == (b"webp-bytes", ".webp")


def test_image_client_carries_no_credentials():
    # Regression guard: batch image URLs are absolute/off-host, and httpx applies
    # a client's auth to EVERY host. The image client MUST therefore be
    # unauthenticated, or the Brewfather Basic-Auth header (User ID + API key)
    # would leak to the third-party image host on the first request.
    api = brewfather._client("user", "key")
    img = brewfather._image_client()
    try:
        assert api.auth is not None          # API client keeps its credentials
        assert img.auth is None              # image client carries none
    finally:
        api.close()
        img.close()


def test_sync_downloads_images_with_the_unauthenticated_client(mock_network):
    # The companion to the factory test above, and the one that matters: building
    # a credential-free client is worthless if the AUTHENTICATED one is the client
    # actually handed to the download. That wiring is a single `with` statement in
    # run_sync, and transposing its two clients is a live leak of the Brewfather
    # key to a third-party image host - which every other test in this suite would
    # happily pass through, because nothing else observes the download's client.
    #
    # The assertion is deliberately made against the download seam (the first
    # argument `_download_image` receives) rather than against run_sync's
    # internals, which is why it survived the Mapping split (issue #10) unchanged.
    _set_creds()
    mock_network["downloads"] = {"http://x/pic.webp": (b"img-bytes", ".webp")}
    mock_network["batches"] = [_batch(
        "b1", 1, "Photo Ale", recipe={"img_url": "http://x/pic.webp", "ibu": 20})]

    result = brewfather.run_sync()

    assert result["ok"] is True
    # Guard against a vacuous pass: no download attempt means nothing was checked.
    assert len(mock_network["download_clients"]) == 1
    assert mock_network["download_clients"][0].auth is None
