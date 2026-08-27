"""Board resolution: custom > brewfather > vacant, hide-vacant flags, colours."""
from pathlib import Path

from app import config_store, tap_store
from app.board import build_board, resolve_beer_card, resolve_tap, resolve_visibility
from app.colors import ebc_to_hex
from app.config_store import DEFAULT_CONFIG


def _glass_url(color_hex: str | None = None, glass: str | None = None) -> str:
    """The placeholder URL the board builds: a resolved colour, or none at all."""
    params = []
    if color_hex is not None:
        params.append("hex=" + color_hex.lstrip("#"))
    if glass is not None:
        params.append("glass=" + glass)
    return "/img/beer-glass" + ("?" + "&".join(params) if params else "")


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


def test_filename_decides_source_not_front_matter(write_tap):
    # A hand-edited bf_tap_X.md claiming the Manual Source is still Brewfather:
    # the filename is authoritative and the front-matter key is never read back
    # as truth. Otherwise the display would badge a Tap as Manual while sync
    # kept rewriting it as Brewfather.
    write_tap("brewfather", 4, name="Mislabelled", source="custom")
    assert resolve_tap(4)["source"] == "brewfather"
    # And the mirror case, so this is about the filename rather than a
    # hard-coded default.
    write_tap("custom", 6, name="Also mislabelled", source="brewfather")
    assert resolve_tap(6)["source"] == "custom"


def test_photo_comes_only_from_the_winning_source(write_tap):
    # A Tap comes entirely from one Source. A Manual Tap with no photo shows the
    # placeholder glass rather than borrowing the shadowed Brewfather photo, so
    # a card's name and picture can never come from different Beers.
    write_tap("brewfather", 1, name="BF Beer", ebc=20, image_ext=".jpg")
    write_tap("custom", 1, name="Manual Beer", ebc=20)
    assert resolve_tap(1)["image_url"] == _glass_url(ebc_to_hex(20))
    # The Brewfather photo is still what a Slot with no Manual Tap shows.
    write_tap("brewfather", 2, name="BF Beer", ebc=20, image_ext=".jpg")
    assert resolve_tap(2)["image_url"] == "/img/bf_tap_2.jpg"


def test_unreadable_manual_tap_does_not_promote_brewfather(write_tap, monkeypatch):
    # An existing-but-unreadable Manual Tap file renders the Slot as an empty
    # card under the Manual Source. It must NOT fall through to Brewfather: a
    # transient read error on a bind mount would otherwise put another
    # brewery's beer on the TV, which is worse than a blank card.
    write_tap("brewfather", 3, name="BF Beer", abv=5.0, ebc=10)
    write_tap("custom", 3, name="Manual Beer", abv=4.0, ebc=8)

    real_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self.name == "custom_tap_3.md":
            raise PermissionError("simulated bind-mount hiccup")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    r = resolve_tap(3)
    assert r["source"] == "custom"
    assert r["name"] != "BF Beer"
    assert r["abv"] is None and r["ebc"] is None
    assert r["description"] == ""


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
    # Same EBC, different saturation -> a greyer swatch, and the placeholder URL
    # carries that muted colour so the pour matches the swatch. Saturation is an
    # input to resolution and never reaches the URL.
    write_tap("custom", 1, name="Vivid", ebc=20)
    write_tap("custom", 2, name="Muted", ebc=20, saturation=0.3)
    vivid, muted = resolve_tap(1), resolve_tap(2)
    assert vivid["color_hex"] != muted["color_hex"]
    assert vivid["image_url"] == _glass_url(ebc_to_hex(20))
    assert muted["image_url"] == _glass_url(ebc_to_hex(20, 0.3))
    assert "sat=" not in muted["image_url"]


def test_color_override_wins_over_ebc_everywhere(write_tap):
    # An exact colour override drives the swatch AND the placeholder glass,
    # ignoring the EBC-derived colour.
    write_tap("custom", 1, name="Forced Red", ebc=20, color_override="#780606")
    r = resolve_tap(1)
    assert r["color_hex"] == "#780606"
    assert r["image_url"] == _glass_url("#780606")


def test_color_override_with_saturation_is_not_muted(write_tap):
    # A Colour override is an exact instruction. An operator who writes both
    # tokens gets the override untouched, on the swatch and in the pour alike.
    write_tap("custom", 1, name="Forced Red", ebc=20, color_override="#780606",
              saturation=0.3)
    r = resolve_tap(1)
    assert r["color_hex"] == "#780606"
    assert r["image_url"] == _glass_url("#780606")


def test_unknown_colour_sends_no_colour_at_all(write_tap):
    # Neither an EBC nor an override: resolution answers Unknown, so the board
    # sends null rather than inventing a colour, and the placeholder URL carries
    # no colour - which is what selects the glass renderer's own amber. The two
    # surfaces' fallbacks are deliberately different (ADR-0004).
    write_tap("custom", 1, name="Colourless")
    r = resolve_tap(1)
    assert r["color_hex"] is None
    assert r["text_color"] is None
    assert r["image_url"] == _glass_url()


def test_vacant_tap_carries_no_colour_fields(write_tap):
    # A Vacant Slot has no Beer to resolve. The display styles those cards from a
    # CSS custom property and never read the colour fields, so they are not sent.
    r = resolve_tap(5)
    assert r["vacant"] is True
    assert "color_hex" not in r
    assert "text_color" not in r


def test_swatch_and_ebc_ask_different_emptiness_questions(write_tap):
    """One operator toggle, two answers - the swatch is not the EBC Attribute.

    The swatch asks whether *Colour* is known (an EBC or a Colour override); the
    EBC Attribute asks whether *EBC* is present. A Beer with only an override is
    the case that separates them, and it is why `hide_color_when_empty` cannot be
    applied once and reused. See ADR-0004 and CONTEXT.md (Attribute).
    """
    write_tap("custom", 1, name="Override only", color_override="#445566")  # no ebc
    write_tap("custom", 2, name="Ebc only", ebc=12)
    write_tap("custom", 3, name="Neither")

    override_only = resolve_tap(1)
    assert override_only["swatch_visible"] is True
    assert override_only["ebc_visible"] is False

    both_visible = resolve_tap(2)
    assert both_visible["swatch_visible"] is True
    assert both_visible["ebc_visible"] is True

    neither = resolve_tap(3)
    assert neither["swatch_visible"] is False
    assert neither["ebc_visible"] is False


def test_vacant_tap_carries_no_visibility_answers():
    # A Vacant Slot has no Beer and renders no stats block, so there is no
    # Attribute to answer for. Sending six booleans anyway would be a claim
    # rather than an answer.
    r = resolve_tap(5)
    assert r["vacant"] is True
    for key in ("abv_visible", "ibu_visible", "ebc_visible", "og_visible",
                "fg_visible", "swatch_visible"):
        assert key not in r


def test_board_payload_carries_no_visibility_inputs(write_tap):
    """The raw toggles and the per-Tap tri-states stay off the wire.

    Sending both the inputs and the answer would leave two implementations of
    the chain in play, which is exactly the state this replaced. `color_known`
    goes with them: the display has no reason to know *why* the swatch shows.
    """
    config_store.update_config(num_taps=2)
    write_tap("custom", 1, name="Beer", abv=5, ebc=10, show_og=True)
    board = build_board()

    for flag in ("show_abv", "show_ibu", "show_color", "show_og", "show_fg",
                 "hide_abv_when_empty", "hide_ibu_when_empty",
                 "hide_color_when_empty", "hide_og_when_empty",
                 "hide_fg_when_empty"):
        assert flag not in board, flag
    # The two settings that are not Visibility stay raw, on purpose.
    assert board["show_source_badge"] is False
    assert board["color_unit"] == "ebc"

    for tap in board["taps"]:
        assert "color_known" not in tap
        assert "show_og" not in tap
        assert "show_fg" not in tap


# ---- Visibility, resolved (CONTEXT.md's three-step chain) ----------------

def test_visibility_global_toggle_applies_without_an_override():
    # Step 2: with no per-Tap override the global toggle decides.
    assert resolve_visibility(5.0, True, False) is True
    assert resolve_visibility(5.0, False, False) is False


def test_visibility_per_tap_override_beats_the_global_toggle():
    # Step 1: an explicit per-Tap value wins in both directions; only None and
    # "" mean "inherit". A hand-edited front matter can produce either.
    assert resolve_visibility(1.052, False, False, per_tap=True) is True
    assert resolve_visibility(1.052, True, False, per_tap=False) is False
    assert resolve_visibility(1.052, True, False, per_tap=None) is True
    assert resolve_visibility(1.052, True, False, per_tap="") is True


def test_visibility_empty_suppression_hides_an_enabled_attribute():
    # Step 3, and only step 3: a missing value hides an *enabled* Attribute...
    assert resolve_visibility(None, True, True) is False
    # ...and does nothing when the operator asked to keep the empty stat.
    assert resolve_visibility(None, True, False) is True


def test_visibility_treats_none_as_the_only_absence():
    """A blank front-matter field is None by the time it gets here.

    The Tap file store coerces it when it builds the Beer (app/beer.py), so this
    no longer tests for the empty string as well - which it used to do once per
    Attribute per Tap on every poll from every TV.
    """
    assert resolve_visibility("", True, True) is True


def test_visibility_empty_suppression_cannot_reveal_a_disabled_attribute():
    # Order matters: suppression refines an enabled Attribute, it is not a
    # toggle of its own, so a present value can never override "switched off".
    assert resolve_visibility(42, False, True) is False
    assert resolve_visibility(42, False, False) is False


def test_visibility_treats_zero_as_a_real_reading():
    # 0 IBU is a fact about a lager, not an absent value - falsiness is the wrong
    # emptiness test here and hiding it would lose real data.
    assert resolve_visibility(0, True, True) is True
    assert resolve_visibility(0.0, True, True) is True


def test_per_tap_override_survives_into_the_resolved_answer(write_tap):
    # The tri-state stays an editable value in the Tap file; what changed is that
    # the board now applies it instead of forwarding it.
    config_store.update_config(show_og=False, show_fg=True)
    write_tap("custom", 1, name="Shown", og=1.052, fg=1.010, show_og=True, show_fg=False)
    r = resolve_tap(1)
    assert r["og_visible"] is True     # per-tap True beats the global False
    assert r["fg_visible"] is False    # per-tap False beats the global True

    # A Tap with no override inherits the globals.
    write_tap("custom", 2, name="Plain", og=1.052, fg=1.010)
    r2 = resolve_tap(2)
    assert r2["og_visible"] is False
    assert r2["fg_visible"] is True


def test_glass_override_tags_placeholder_url(write_tap):
    # A per-tap glass selection is encoded in the glass URL; the default is omitted.
    write_tap("custom", 1, name="Tulip Beer", ebc=20, glass="tulip")
    assert resolve_tap(1)["image_url"] == _glass_url(ebc_to_hex(20), "tulip")
    # A global default glass applies when the tap has none of its own.
    write_tap("custom", 2, name="Plain", ebc=20)
    assert resolve_tap(2, default_glass="teku")["image_url"] == _glass_url(ebc_to_hex(20), "teku")


def test_og_fg_values_and_their_resolved_visibility(write_tap):
    write_tap("custom", 1, name="Gravity Beer", abv=5, og=1.052, fg=1.010, show_og=True, show_fg=False)
    r = resolve_tap(1)
    assert r["og"] == 1.052
    assert r["fg"] == 1.010
    assert r["og_visible"] is True
    assert r["fg_visible"] is False
    # A tap with no value and no override: the globals are off by default, so
    # both stats resolve hidden.
    write_tap("custom", 2, name="Plain", abv=5)
    r2 = resolve_tap(2)
    assert r2["og"] is None
    assert r2["og_visible"] is False


def test_resolve_beer_card_matches_what_tap_resolution_embeds(write_tap):
    """The extracted Beer-to-card resolution cannot drift from resolve_tap (#34).

    resolve_tap calls resolve_beer_card and adds only what is specific to a
    Tap - the Slot number, vacancy, Source, description and updated timestamp.
    This pins that every other field on the resolved Tap - the Attributes, the
    six Visibility answers, the Colour and the image URL - is exactly what
    resolve_beer_card produces for the same Beer, Settings and per-Slot
    override, so a future edit cannot grow a second copy of the chain that
    only one of the two call sites sees.
    """
    write_tap("custom", 1, name="Gravity Beer", abv=5.0, ibu=30, ebc=20,
             og=1.052, fg=1.010, show_og=True, show_fg=False)
    cfg = config_store.load_config()
    tap_file = tap_store.resolve(1)
    card = resolve_beer_card(tap_file.beer, cfg, tap_file.image,
                             DEFAULT_CONFIG["glass_type"], tap_file.presentation)
    tap = resolve_tap(1, DEFAULT_CONFIG["glass_type"], cfg)

    card_fields = (
        "name", "abv", "ibu", "ebc", "og", "fg", "color_hex", "text_color",
        "image_url", "abv_visible", "ibu_visible", "ebc_visible",
        "og_visible", "fg_visible", "swatch_visible",
    )
    for field in card_fields:
        assert tap[field] == card[field], field


def test_board_carries_the_resolved_card_scales():
    # The board sends numbers, not the preset key: the display never needs to
    # know which button produced them.
    config_store.update_config(
        tap_photo_preset="small", tap_text_preset="small",
        tap_image_scale=0.6, tap_text_scale=0.75)
    b = build_board()
    assert b["tap_image_scale"] == 0.6
    assert b["tap_text_scale"] == 0.75
    assert "tap_photo_preset" not in b
    assert "tap_text_preset" not in b


def test_board_card_scales_default_to_one():
    b = build_board()
    assert b["tap_image_scale"] == 1.0
    assert b["tap_text_scale"] == 1.0


# ---- Upcoming Beers on the board payload (issue #37) -----------------------

from app import upcoming_store
from app.beer import Beer
from app.board import resolve_upcoming


def _upcoming(batch_id, *, slot=None, status="conditioning", revision=1,
             name="Teaser Beer", **beer_fields):
    upcoming_store.write(
        batch_id, Beer(name=name, **beer_fields), "coming soon",
        slot=slot, status=status, revision=revision,
    )


def test_toggle_off_payload_is_identical_to_no_upcoming_beers_at_all():
    """The headline assertion: off means today's behaviour, byte for byte.

    Two boards are built for the *same* Taps: one with Upcoming Beers cached
    on disk but the toggle off, one with nothing cached at all. If the toggle
    were only a display filter rather than a real gate, the first board would
    still differ from the second - either by carrying an "upcoming" key or by
    some other leak. It must not.
    """
    config_store.update_config(num_taps=2, show_upcoming_previews=False)
    write_tap_baseline = build_board()

    _upcoming("batch-1", slot=1, status="completed", revision=99)
    _upcoming("batch-2", slot=None, status="fermenting", revision=1)
    with_cached_entries = build_board()

    assert "upcoming" not in write_tap_baseline
    assert "upcoming" not in with_cached_entries
    assert with_cached_entries == write_tap_baseline


def test_upcoming_ordering_by_status_rank_beats_batch_id_and_recency():
    """Status rank decides first, ahead of both recency and the Batch id.

    Batch ids are deliberately chosen so their alphabetical order is the
    *opposite* of the expected result (0 < a < z, but fermenting must sort
    last regardless): a test using ids that happen to already sort correctly
    would pass even if status rank were deleted from the sort key entirely,
    because the store's own directory listing is itself alphabetical by
    Batch id. Picking adversarial ids is what makes this assertion mean
    anything.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    _upcoming("0-should-sort-last", status="fermenting", revision=999)
    _upcoming("z-should-sort-first", status="completed", revision=1)
    _upcoming("a-should-sort-second", status="conditioning", revision=1)
    b = build_board()
    assert [t["batch_id"] for t in b["upcoming"]] == [
        "z-should-sort-first", "a-should-sort-second", "0-should-sort-last"]


def test_upcoming_ordering_recency_beats_batch_id_within_one_status():
    """Within one status, the newer Batch (higher revision) sorts first.

    Again the ids are adversarial to alphabetical order (aaa < zzz) so a sort
    key with recency silently dropped - falling through to the Batch-id
    tie-break alone - would produce the wrong order rather than an
    accidentally correct one.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    _upcoming("aaa-older", status="completed", revision=1)
    _upcoming("zzz-newer", status="completed", revision=99)
    b = build_board()
    assert [t["batch_id"] for t in b["upcoming"]] == ["zzz-newer", "aaa-older"]


def test_upcoming_ordering_final_tie_break_is_batch_id_ascending(monkeypatch):
    """Identical status AND recency must still resolve to a stable order.

    Without the Batch-id tie-break, two such entries sort however the
    directory glob happens to return them - and that glob is *itself*
    alphabetical by Batch id, so entries fed to `build_board` in on-disk order
    would pass this assertion even with the tie-break deleted. `list_upcoming`
    is monkeypatched to hand `build_board` entries in a scrambled order that
    is emphatically not already sorted, so only board.py's own tie-break can
    put them back into "aaa, mmm, zzz" - see ADR-0006 and issue #37.
    """
    scrambled = [
        upcoming_store.UpcomingEntry(batch_id="zzz-batch", beer=Beer(name="Z"),
                                     status="completed", revision=7),
        upcoming_store.UpcomingEntry(batch_id="aaa-batch", beer=Beer(name="A"),
                                     status="completed", revision=7),
        upcoming_store.UpcomingEntry(batch_id="mmm-batch", beer=Beer(name="M"),
                                     status="completed", revision=7),
    ]
    monkeypatch.setattr("app.board.list_upcoming", lambda: scrambled)
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    b = build_board()
    assert [t["batch_id"] for t in b["upcoming"]] == [
        "aaa-batch", "mmm-batch", "zzz-batch"]


def test_cap_truncates_the_queue_with_no_sync():
    """Changing max_upcoming_previews takes effect on the next poll alone.

    Nothing here touches the Upcoming store between the two builds - only the
    Setting changes - which is the display-time contract ADR-0006 makes for
    both the ordering and the cap.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=1)
    # Adversarial to alphabetical order, as above: the highest-revision (and so
    # first-place) Batch id sorts *last* alphabetically.
    _upcoming("zzz-newest", status="completed", revision=3)
    _upcoming("mmm-middle", status="completed", revision=2)
    _upcoming("aaa-oldest", status="completed", revision=1)
    capped = build_board()
    assert len(capped["upcoming"]) == 1
    assert capped["upcoming"][0]["batch_id"] == "zzz-newest"

    config_store.update_config(max_upcoming_previews=2)
    raised = build_board()
    assert [t["batch_id"] for t in raised["upcoming"]] == ["zzz-newest", "mmm-middle"]


def test_pinned_is_true_only_for_a_teaser_bound_to_a_vacant_slot():
    config_store.update_config(num_taps=2, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    # Slot 1 stays Vacant; slot 2 has no Tap file either, but nothing binds to it.
    _upcoming("bound-vacant", slot=1, status="completed", revision=1)
    _upcoming("unbound", slot=None, status="completed", revision=1)
    b = build_board()

    assert b["taps"][0]["vacant"] is True
    by_id = {t["batch_id"]: t for t in b["upcoming"]}
    assert by_id["bound-vacant"]["slot"] == 1
    assert by_id["bound-vacant"]["pinned"] is True
    assert by_id["unbound"]["slot"] is None
    assert by_id["unbound"]["pinned"] is False


def test_pinned_is_false_when_bound_to_an_occupied_slot(write_tap):
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    write_tap("custom", 1, name="Pouring Now")
    _upcoming("bound-occupied", slot=1, status="completed", revision=1)
    b = build_board()
    assert b["taps"][0]["vacant"] is False
    teaser = b["upcoming"][0]
    assert teaser["slot"] == 1
    assert teaser["pinned"] is False


def test_pinned_teaser_slot_is_not_hidden_despite_hide_vacant_taps():
    """A Vacant Slot with a pinned teaser has something to show.

    hide_vacant_taps must not hide it, and the answer travels on the Tap's
    own already-resolved `hidden` flag rather than a new one (issue #37).
    """
    config_store.update_config(num_taps=2, show_upcoming_previews=True,
                                max_upcoming_previews=20, hide_vacant_taps=True)
    _upcoming("pinned-batch", slot=1, status="completed", revision=1)
    b = build_board()
    assert b["taps"][0]["vacant"] is True
    assert b["taps"][0]["hidden"] is False  # pinned overrides hide_vacant_taps
    assert b["taps"][1]["hidden"] is True   # ordinary Vacant slot 2 stays hidden
    assert b["upcoming"][0]["pinned"] is True


def test_teaser_beyond_num_taps_resolves_to_a_null_slot_then_rebinds():
    """Sync accepts tap:1..MAX_NUM_TAPS regardless of num_taps; the board must not.

    Resolved at display time, so raising num_taps re-binds it on the very
    next poll with no sync (mirrors the ordering/cap display-time contract).
    """
    config_store.update_config(num_taps=2, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    _upcoming("far-future", slot=8, status="completed", revision=1)
    too_small = build_board()
    teaser = too_small["upcoming"][0]
    assert teaser["slot"] is None
    assert teaser["pinned"] is False

    config_store.update_config(num_taps=8)
    raised = build_board()
    teaser = raised["upcoming"][0]
    assert teaser["slot"] == 8
    assert raised["taps"][7]["vacant"] is True
    assert teaser["pinned"] is True


def test_teaser_colour_matches_a_tap_with_identical_inputs(write_tap):
    """A teaser's Colour must be the same answer a Tap resolves for the same Beer.

    Mirrors the existing preview-versus-board equivalence test: both surfaces
    go through colors.resolve_color exactly once (ADR-0004), so an EBC value
    with no override must paint the same hex on a Tap card and a teaser card.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    write_tap("custom", 1, name="Tap Twin", ebc=25)
    _upcoming("teaser-twin", slot=None, status="completed", revision=1,
              name="Teaser Twin", ebc=25)
    b = build_board()
    tap_card = b["taps"][0]
    teaser_card = b["upcoming"][0]
    assert teaser_card["color_hex"] == tap_card["color_hex"]
    assert teaser_card["text_color"] == tap_card["text_color"]
    assert teaser_card["color_hex"] is not None


def test_teaser_visibility_answers_come_from_the_same_chain_a_tap_uses():
    """The six Visibility booleans on a teaser are resolve_beer_card's, not a copy.

    Uses resolve_upcoming and resolve_beer_card directly (rather than going
    through build_board) so this pins the shared resolution itself, the way
    test_resolve_beer_card_matches_what_tap_resolution_embeds pins it for a Tap.
    """
    # show_upcoming_abv defaults off (issue #39) and would otherwise force
    # abv_visible False on the teaser alone, making this comparison fail for
    # a reason unrelated to what it actually pins - so it is turned on here to
    # isolate the shared-chain assertion from the teaser-only ABV gate, which
    # has its own tests below.
    cfg = config_store.update_config(show_og=True, show_fg=True, show_upcoming_abv=True)
    entry = upcoming_store.UpcomingEntry(
        batch_id="chain-check",
        beer=Beer(name="Chain Beer", abv=5.5, ibu=None, ebc=None,
                  og=1.050, fg=None),
        slot=None, status="completed", revision=1, body="", image=None,
    )
    expected = resolve_beer_card(entry.beer, cfg, entry.image,
                                  DEFAULT_CONFIG["glass_type"])
    teaser = resolve_upcoming(entry, cfg, DEFAULT_CONFIG["glass_type"],
                             num_taps=0, taps=[])
    visibility_fields = ("abv_visible", "ibu_visible", "ebc_visible",
                         "og_visible", "fg_visible", "swatch_visible")
    for field in visibility_fields:
        assert teaser[field] == expected[field], field


def test_teaser_image_url_uses_the_upcoming_image_route_not_the_tap_route():
    """An Upcoming Beer's photo lives in its own store, not the Tap store.

    board.py must build the teaser's image_url from the Upcoming store's own
    photo via the /img/upcoming/ route, never the Tap route - the two
    directories can hold files with the same name.
    """
    upcoming_store.write(
        "batch-photo", Beer(name="Photo Beer"), "", slot=None,
        status="completed", revision=1, image_bytes=b"fake-bytes", image_ext=".jpg",
    )
    config_store.update_config(num_taps=0, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    b = build_board()
    teaser = b["upcoming"][0]
    assert teaser["image_url"].startswith("/img/upcoming/")
    assert teaser["image_url"].endswith(".jpg")


def test_teaser_with_no_photo_falls_back_to_the_tinted_glass_placeholder():
    config_store.update_config(num_taps=0, show_upcoming_previews=True,
                                max_upcoming_previews=20)
    _upcoming("batch-no-photo", status="completed", revision=1, ebc=10)
    b = build_board()
    teaser = b["upcoming"][0]
    assert teaser["image_url"].startswith("/img/beer-glass")


# ---- The teaser card's words (issue #39) -----------------------------------

def test_status_label_is_the_customer_word_and_null_when_the_setting_is_off():
    """status_label is spelled for a customer, and mutating the map breaks this.

    Pins the exact vocabulary from #4/#39 (not just "truthy"), and pins the
    null-when-off half separately so a reader cannot satisfy this by always
    returning a label regardless of the Setting.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_upcoming_status=True)
    _upcoming("status-on", status="conditioning", revision=1)
    b = build_board()
    assert b["upcoming"][0]["status_label"] == "Conditioning"

    config_store.update_config(show_upcoming_status=False)
    b = build_board()
    assert b["upcoming"][0]["status_label"] is None


def test_status_label_covers_every_customer_spelling():
    cases = {
        "completed": "Ready",
        "conditioning": "Conditioning",
        "fermenting": "Fermenting",
        "brewing": "Brewing",
        "planning": "Planned",
    }
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_upcoming_status=True)
    for raw in cases:
        _upcoming(f"status-{raw}", status=raw, revision=1)
    b = build_board()
    by_id = {t["batch_id"]: t["status_label"] for t in b["upcoming"]}
    for raw, expected in cases.items():
        assert by_id[f"status-{raw}"] == expected, raw


def test_unbound_subtitle_ignores_the_setting_in_both_directions():
    """The test that fails if boundness collapses into a plain read of the Setting.

    An unbound teaser gets a subtitle whatever show_upcoming_subtitle says -
    the setting is only consulted for a BOUND teaser. Checked with the Setting
    off AND on so a hardcoded `if unbound: True` cannot be told apart from a
    correct implementation by only one half of this test.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_upcoming_subtitle=False)
    _upcoming("unbound-off", slot=None, status="completed", revision=1)
    b = build_board()
    assert b["upcoming"][0]["subtitle"] == "no tap assigned yet"

    config_store.update_config(show_upcoming_subtitle=True)
    b = build_board()
    assert b["upcoming"][0]["subtitle"] == "no tap assigned yet"


def test_bound_subtitle_follows_the_setting():
    config_store.update_config(num_taps=2, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_upcoming_subtitle=False,
                                upcoming_label="Coming up")
    _upcoming("bound-sub", slot=1, status="completed", revision=1)
    b = build_board()
    assert b["upcoming"][0]["subtitle"] is None

    config_store.update_config(show_upcoming_subtitle=True)
    b = build_board()
    assert b["upcoming"][0]["subtitle"] == "Coming up on tap 1"


def test_bound_subtitle_uses_the_operators_own_label():
    config_store.update_config(num_taps=2, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_upcoming_subtitle=True,
                                upcoming_label="Up next")
    _upcoming("bound-custom-label", slot=2, status="completed", revision=1)
    b = build_board()
    assert b["upcoming"][0]["subtitle"] == "Up next on tap 2"


def test_show_upcoming_abv_off_forces_abv_visible_false_even_with_show_abv_on():
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_abv=True,
                                show_upcoming_abv=False)
    _upcoming("abv-gated", status="completed", revision=1, abv=6.0)
    b = build_board()
    teaser = b["upcoming"][0]
    assert teaser["abv_visible"] is False
    assert teaser["abv_estimated"] is False


def test_show_upcoming_abv_on_shows_it_and_marks_it_estimated():
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_abv=True,
                                show_upcoming_abv=True)
    _upcoming("abv-shown", status="completed", revision=1, abv=6.0)
    b = build_board()
    teaser = b["upcoming"][0]
    assert teaser["abv_visible"] is True
    assert teaser["abv_estimated"] is True


def test_abv_estimated_is_false_when_the_beer_has_no_abv_to_show():
    """abv_estimated only ever accompanies a VISIBLE abv (issue #39).

    show_upcoming_abv on is not enough by itself: hide_abv_when_empty (on by
    default) still suppresses a missing value, and there is nothing to mark
    '~' on a stat that is not drawn.
    """
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, show_abv=True,
                                show_upcoming_abv=True, hide_abv_when_empty=True)
    _upcoming("no-abv", status="completed", revision=1)  # abv left unset -> None
    b = build_board()
    teaser = b["upcoming"][0]
    assert teaser["abv_visible"] is False
    assert teaser["abv_estimated"] is False


def test_board_carries_the_operators_teaser_label_only_when_upcoming_is_on():
    config_store.update_config(num_taps=1, show_upcoming_previews=True,
                                max_upcoming_previews=20, upcoming_label="Up next")
    b = build_board()
    assert b["upcoming_label"] == "Up next"

    config_store.update_config(show_upcoming_previews=False)
    b = build_board()
    assert "upcoming_label" not in b
