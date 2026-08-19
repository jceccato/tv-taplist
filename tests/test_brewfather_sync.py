"""Brewfather field extraction, the efficient fetch, and sync/archive logic."""
import httpx
import pytest

from app import brewfather, config_store, paths, status_store, tap_store as taps


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


def test_conflict_completed_beats_newer_conditioning():
    # The beer that is pouring must not be pushed off its Slot by the next brew
    # that already carries the same token - and a conditioning Batch is edited
    # far more often than a finished one, so recency alone picks the wrong beer.
    batches = [
        {"_id": "a", "name": "Pouring", "status": "Completed",
         "batchNotes": "tap:3", "_timestamp_ms": 100},
        {"_id": "b", "name": "NextBrew", "status": "Conditioning",
         "batchNotes": "tap:3", "_timestamp_ms": 900},
    ]
    assert brewfather._build_desired_map(batches)[3]["batch"]["name"] == "Pouring"
    # Order of arrival must not matter: the same pair reversed resolves the same.
    assert brewfather._build_desired_map(
        list(reversed(batches)))[3]["batch"]["name"] == "Pouring"


def test_conflict_conditioning_beats_newer_fermenting():
    batches = [
        {"_id": "a", "name": "Conditioning", "status": "Conditioning",
         "batchNotes": "tap:6", "_timestamp_ms": 100},
        {"_id": "b", "name": "Fermenting", "status": "Fermenting",
         "batchNotes": "tap:6", "_timestamp_ms": 900},
    ]
    assert brewfather._build_desired_map(batches)[6]["batch"]["name"] == "Conditioning"


def test_conflict_within_one_status_still_resolves_by_recency():
    # Status only orders DIFFERENT statuses; inside one, newest still wins.
    batches = [
        {"_id": "a", "name": "Old", "status": "Conditioning",
         "batchNotes": "tap:2", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "Conditioning",
         "batchNotes": "tap:2", "_timestamp_ms": 200},
    ]
    assert brewfather._build_desired_map(batches)[2]["batch"]["name"] == "New"
    assert brewfather._build_desired_map(
        list(reversed(batches)))[2]["batch"]["name"] == "New"


def test_conflict_unknown_status_loses_to_a_known_one():
    # An unlabelled Batch ranks below every status the API does name, however
    # recent it is - we cannot tell how far along it is, so it does not win.
    batches = [
        {"_id": "a", "name": "Fermenting", "status": "Fermenting",
         "batchNotes": "tap:4", "_timestamp_ms": 100},
        {"_id": "b", "name": "Unlabelled", "batchNotes": "tap:4",
         "_timestamp_ms": 900},
    ]
    assert brewfather._build_desired_map(batches)[4]["batch"]["name"] == "Fermenting"
    assert brewfather._build_desired_map(
        list(reversed(batches)))[4]["batch"]["name"] == "Fermenting"


def test_conflict_all_unknown_status_falls_back_to_recency():
    # If Brewfather ever stops sending `status`, everything ties on rank and
    # resolution degrades to the newest-wins behaviour that shipped before.
    batches = [
        {"_id": "a", "name": "Old", "batchNotes": "tap:5", "_timestamp_ms": 100},
        {"_id": "b", "name": "New", "status": "", "batchNotes": "tap:5",
         "_timestamp_ms": 200},
    ]
    assert brewfather._build_desired_map(batches)[5]["batch"]["name"] == "New"


def test_status_rank_orders_the_whole_lifecycle():
    ranks = [brewfather._status_rank({"status": s})
             for s in ("Completed", "Conditioning", "Fermenting", "Brewing", "Planning")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    # Case and stray whitespace from the API must not demote a Batch.
    assert brewfather._status_rank({"status": " completed "}) == \
        brewfather._status_rank({"status": "Completed"})
    # Missing, empty, non-string and unrecognised statuses all rank last.
    for batch in ({}, {"status": ""}, {"status": None}, {"status": "Archived"}):
        assert brewfather._status_rank(batch) == len(brewfather.STATUS_PRECEDENCE)


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
    status is among the requested statuses, deduped by _id - so run_sync tests
    genuinely exercise the include_conditioning status selection.

    The fake download mirrors nothing. `_download_image` now returns bytes plus an
    extension for the Tap file store to file, so the fake only has to answer
    "did this URL download, and to what?" - it picks no filename, reproduces no
    extension rules, and writes nothing. Whether the bytes reach the right file
    on disk is then a real assertion about production code rather than about the
    fixture. `state["downloads"]` maps a URL -> (bytes, ext); a URL that is
    absent from it stands for a failed download.

    The fake also records the client it was handed in `state["download_clients"]`.
    That first argument is the only place a test can observe WHICH of sync's two
    httpx clients actually reaches an image fetch, and the difference between them
    is a credential leak: image URLs are off-host and httpx applies a client's
    auth to every host. Recording it here rather than asserting inline keeps the
    fixture behaviour-free, so tests that only care about bytes are unaffected.
    """
    state = {"batches": [], "downloads": {}, "download_clients": []}

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
def test_status_list_covers_all_toggle_combinations(monkeypatch, mock_network,
                                                    conditioning, fermenting, expected):
    """Pin the exact status list `_list_batches` is asked for.

    Each status is a separate paginated sweep of the Brewfather API, so the list
    is what the rate-limit cost is proportional to - worth asserting directly
    rather than only inferring it from which Batches came back.
    """
    _set_creds()
    config_store.update_config(include_conditioning=conditioning,
                               include_fermenting=fermenting)
    seen: list[list[str]] = []

    def recording_list(client, statuses):
        seen.append(list(statuses))
        return []

    monkeypatch.setattr(brewfather, "_list_batches", recording_list)
    brewfather.run_sync()
    assert seen == [expected]


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


def test_failed_sync_makes_no_destructive_changes(mock_network, write_tap, monkeypatch):
    _set_creds()
    write_tap("bf", 1, name="Existing", abv=5, ebc=10)

    def boom(client, statuses):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(brewfather, "_list_batches", boom)
    result = brewfather.run_sync()
    assert result["ok"] is False
    assert taps.exists(1, taps.Source.BREWFATHER)
    assert list(paths.OLD_BEERS_DIR.glob("*")) == []
    assert status_store.load_status()["last_sync_error"]


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
    # internals, so it keeps holding if the Mapping half is ever split out of this
    # module (issue #10) and the client is threaded through differently.
    _set_creds()
    mock_network["downloads"] = {"http://x/pic.webp": (b"img-bytes", ".webp")}
    mock_network["batches"] = [_batch(
        "b1", 1, "Photo Ale", recipe={"img_url": "http://x/pic.webp", "ibu": 20})]

    result = brewfather.run_sync()

    assert result["ok"] is True
    # Guard against a vacuous pass: no download attempt means nothing was checked.
    assert len(mock_network["download_clients"]) == 1
    assert mock_network["download_clients"][0].auth is None
