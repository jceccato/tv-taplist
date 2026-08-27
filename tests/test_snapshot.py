"""Snapshot export and import.

The tests are grouped by the question each answers: what a Snapshot carries,
what the credential opt-in does, what an import refuses, and which Brewfather
case an import falls into. The last group is the heart of it - the coupling
between "this box will sync" and "the Snapshot's Brewfather Taps are skipped"
reads as a bug on sight, so it is pinned from both directions.
"""
from __future__ import annotations

import ast
import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import board, config_store, paths, snapshot, tap_store as taps
from app.atomic import JOB_LOCK
from app.main import app

client = TestClient(app)


def _login(c: TestClient) -> TestClient:
    r = c.post("/admin/login", data={"password": "testpw"}, follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture(autouse=True)
def _clean_data_root():
    """Start from a bare data directory root.

    The shared `clean_state` fixture resets taps/, old_beers/, config.json and
    status.json, which is everything the rest of the suite touches. A Snapshot
    also covers the root images and stages an upload there, so those are cleared
    here rather than in conftest - no other test has an opinion about them.
    """
    def _wipe():
        snapshot.discard_staged()
        for name in snapshot.ROOT_IMAGE_NAMES + (".data_dir_id",):
            (paths.DATA_DIR / name).unlink(missing_ok=True)

    _wipe()
    yield
    _wipe()


@pytest.fixture
def no_credential_env(monkeypatch):
    """The default footing: neither credential is managed by the environment."""
    monkeypatch.delenv("BREWFATHER_USER_ID", raising=False)
    monkeypatch.delenv("BREWFATHER_API_KEY", raising=False)


def _export_bytes(include_credentials: bool = False) -> bytes:
    """Run a real export end to end and collect it, as a client would."""
    body = snapshot.settings_bytes(include_credentials)
    entries = snapshot.enumerate_entries()
    return b"".join(snapshot.stream_snapshot(body, entries))


def _build_snapshot(settings: dict, members: dict[str, bytes] | None = None) -> bytes:
    """A Snapshot built by hand, so a test can put anything it likes inside."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(snapshot.SETTINGS_NAME, json.dumps(settings))
        for name, data in (members or {}).items():
            # The name is assigned after construction on purpose: ZipInfo()
            # rewrites os.sep to "/", which on Windows would quietly repair the
            # backslash the traversal tests are trying to smuggle in.
            info = zipfile.ZipInfo("placeholder", date_time=(2026, 1, 1, 0, 0, 0))
            info.filename = name
            zf.writestr(info, data)
    return buf.getvalue()


def _stage(data: bytes) -> Path:
    """Put a Snapshot where the import route would have staged it."""
    snapshot.STAGED_UPLOAD_PATH.write_bytes(data)
    return snapshot.STAGED_UPLOAD_PATH


def _tree_state() -> dict[str, tuple[bytes, float]]:
    """Every file under the data directory with its content and mtime.

    Content *and* mtime, because "nothing changed" has to mean the files were
    not rewritten with identical bytes either - an import that rewrote a file
    with the same content would still have written during a refusal.
    """
    state = {}
    for path in sorted(paths.DATA_DIR.rglob("*")):
        if path.is_file():
            state[str(path.relative_to(paths.DATA_DIR))] = (
                path.read_bytes(), path.stat().st_mtime_ns)
    return state


# ---- what a Snapshot carries ---------------------------------------------

def test_snapshot_carries_the_archive_and_the_root_images(write_tap, no_credential_env):
    write_tap("custom", 1, name="Hand Pour", image_ext=".png")
    write_tap("bf", 2, name="Synced Stout", image_ext=".jpg")
    (paths.OLD_BEERS_DIR / "bf_tap_9_20260101T120000.md").write_text("---\nname: Old\n---\n")
    (paths.OLD_BEERS_DIR / "bf_tap_9_20260101T120000.jpg").write_bytes(b"old-photo")
    (paths.DATA_DIR / "venue_logo.png").write_bytes(b"logo-bytes")
    (paths.DATA_DIR / "placeholder.svg").write_bytes(b"<svg/>")

    names = set(zipfile.ZipFile(io.BytesIO(_export_bytes())).namelist())
    assert names == {
        "config.json",
        "placeholder.svg",
        "venue_logo.png",
        "taps/custom_tap_1.md", "taps/custom_tap_1.png",
        "taps/bf_tap_2.md", "taps/bf_tap_2.jpg",
        "old_beers/bf_tap_9_20260101T120000.md",
        "old_beers/bf_tap_9_20260101T120000.jpg",
    }


def test_snapshot_carries_no_status_and_no_data_dir_id(write_tap, no_credential_env):
    # Status regenerates on the next cycle, and the DDI names which directory
    # this box is using rather than which data sits in it. Neither belongs to a
    # data set that may end up on a different box.
    paths.STATUS_PATH.write_text(json.dumps({"last_sync_ok": "2026-01-01T00:00:00"}))
    (paths.DATA_DIR / ".data_dir_id").write_text("some-identifier")
    write_tap("custom", 1, name="Hand Pour")

    names = zipfile.ZipFile(io.BytesIO(_export_bytes())).namelist()
    assert "status.json" not in names
    assert ".data_dir_id" not in names


def test_snapshot_never_enumerates_the_upcoming_store_even_when_full(write_tap, no_credential_env):
    # ADR-0006: an Upcoming Beer is a projection, disposable like Status, and
    # is never carried in a Snapshot - restoring one onto a box with a working
    # key gets rewritten within minutes anyway, and onto a keyless box it
    # would advertise beers that may already have poured and gone.
    from app import upcoming_store
    from app.beer import Beer

    write_tap("custom", 1, name="Hand Pour")
    upcoming_store.write("batch-1", Beer(name="Saison"), "coming soon",
                          slot=None, status="fermenting", revision=1,
                          image_bytes=b"photo", image_ext=".jpg")

    names = zipfile.ZipFile(io.BytesIO(_export_bytes())).namelist()
    assert not any(name.startswith("upcoming/") for name in names)
    assert names == ["config.json", "taps/custom_tap_1.md"]


def test_snapshot_skips_files_that_are_not_taps(write_tap, no_credential_env):
    # A half-finished atomic write and an operator's stray note both sit in
    # taps/. Membership is decided by the Tap file store's predicate, which is
    # what keeps them out without anyone maintaining an exclusion list.
    write_tap("custom", 1, name="Hand Pour")
    (paths.TAPS_DIR / ".tmp_custom_tap_1.md").write_text("half written")
    (paths.TAPS_DIR / "notes.txt").write_text("reminder")
    (paths.TAPS_DIR / "bf_tap_03.md").write_text("---\nname: Wrong spelling\n---\n")

    names = zipfile.ZipFile(io.BytesIO(_export_bytes())).namelist()
    assert names == ["config.json", "taps/custom_tap_1.md"]


def test_export_streams_without_staging_a_second_copy(write_tap, no_credential_env):
    # The zip is produced a member at a time and never exists as a whole
    # anywhere: no temp file appears while it streams, and the bytes arrive in
    # more than one instalment rather than in one final lump.
    for slot in range(1, 6):
        write_tap("bf", slot, name=f"Beer {slot}", image_ext=".jpg")
    before = {p.name for p in paths.DATA_DIR.rglob("*")}

    chunks = []
    for chunk in snapshot.stream_snapshot(snapshot.settings_bytes(), snapshot.enumerate_entries()):
        chunks.append(chunk)
        assert {p.name for p in paths.DATA_DIR.rglob("*")} == before

    assert len(chunks) > 1
    assert zipfile.ZipFile(io.BytesIO(b"".join(chunks))).testzip() is None


def test_a_file_archived_mid_stream_is_skipped_not_torn(write_tap, no_credential_env):
    # Releasing the job lock after enumeration means a file can go between the
    # list and the read. The Snapshot must stay a well-formed zip, minus that
    # file - never a member with no bytes behind it.
    write_tap("bf", 1, name="Still Here")
    write_tap("bf", 2, name="About To Go")
    entries = snapshot.enumerate_entries()
    (paths.TAPS_DIR / "bf_tap_2.md").unlink()

    data = b"".join(snapshot.stream_snapshot(snapshot.settings_bytes(), entries))
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None
    assert zf.namelist() == ["config.json", "taps/bf_tap_1.md"]


# ---- the job lock ---------------------------------------------------------

def test_enumeration_takes_the_job_lock(write_tap, no_credential_env):
    write_tap("custom", 1, name="Hand Pour")
    done = threading.Event()

    def enumerate_in_thread():
        snapshot.enumerate_entries()
        done.set()

    with JOB_LOCK:
        worker = threading.Thread(target=enumerate_in_thread, daemon=True)
        worker.start()
        # Held by this thread, so the worker cannot get past the lock.
        assert not done.wait(timeout=0.3)
    worker.join(timeout=2)
    assert done.is_set()


def test_streaming_does_not_hold_the_job_lock(write_tap, no_credential_env):
    # The criterion in one assertion: a sync or an admin save can proceed while
    # a large export streams. Acquired from another thread, so the RLock's
    # re-entrancy cannot make this pass by accident.
    for slot in range(1, 4):
        write_tap("bf", slot, name=f"Beer {slot}", image_ext=".jpg")
    stream = snapshot.stream_snapshot(snapshot.settings_bytes(), snapshot.enumerate_entries())
    next(stream)  # mid-transfer: at least one member has been written

    acquired = []

    def take_lock():
        got = JOB_LOCK.acquire(timeout=1.0)
        acquired.append(got)
        if got:
            JOB_LOCK.release()

    worker = threading.Thread(target=take_lock, daemon=True)
    worker.start()
    worker.join(timeout=3)
    assert acquired == [True]
    list(stream)  # drain, so the generator closes cleanly


# ---- the credential opt-in ------------------------------------------------

def test_credential_option_offered_only_when_the_key_is_in_settings(no_credential_env, monkeypatch):
    assert snapshot.credential_choice_available() is False       # no key at all

    config_store.update_config(brewfather_api_key="settings-key-placeholder")
    assert snapshot.credential_choice_available() is True        # key in config.json

    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key-placeholder")
    assert snapshot.credential_choice_available() is False       # key from the environment


def test_declined_or_absent_credentials_are_blank_in_the_snapshot(no_credential_env):
    config_store.update_config(
        brewfather_user_id="settings-user-placeholder",
        brewfather_api_key="settings-key-placeholder",
    )
    for include in (False, None):
        settings = snapshot.snapshot_settings(bool(include))
        assert settings["brewfather_user_id"] == ""
        assert settings["brewfather_api_key"] == ""


def test_opting_in_carries_what_config_json_holds(no_credential_env):
    config_store.update_config(
        brewfather_user_id="settings-user-placeholder",
        brewfather_api_key="settings-key-placeholder",
    )
    settings = snapshot.snapshot_settings(True)
    assert settings["brewfather_user_id"] == "settings-user-placeholder"
    assert settings["brewfather_api_key"] == "settings-key-placeholder"


def test_export_never_carries_an_environment_credential(monkeypatch):
    # The awkward combination the one rule is meant to cover without a matrix:
    # the user ID comes from the environment while the key sits in Settings, so
    # the option IS offered - and the exported user ID must still be the empty
    # value config.json holds, never the environment's.
    monkeypatch.delenv("BREWFATHER_API_KEY", raising=False)
    monkeypatch.setenv("BREWFATHER_USER_ID", "env-user-placeholder")
    config_store.update_config(brewfather_api_key="settings-key-placeholder")

    assert snapshot.credential_choice_available() is True
    settings = snapshot.snapshot_settings(True)
    assert settings["brewfather_api_key"] == "settings-key-placeholder"
    assert settings["brewfather_user_id"] == ""
    assert "env-user-placeholder" not in json.dumps(settings)

    # And with the key in the environment too, there is nothing to opt into.
    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key-placeholder")
    settings = snapshot.snapshot_settings(True)
    assert settings["brewfather_api_key"] == ""
    assert "env-key-placeholder" not in json.dumps(settings)


# ---- refusing an archive that is not a Snapshot --------------------------

@pytest.mark.parametrize("members, expected", [
    ({"../escape.md": b"x"}, "relative path segment"),
    ({"/etc/passwd": b"x"}, "absolute path"),
    ({"taps/../../escape.md": b"x"}, "relative path segment"),
    ({"status.json": b"{}"}, "not part of a Snapshot"),
    ({".data_dir_id": b"id"}, "not part of a Snapshot"),
    ({"taps/notes.txt": b"x"}, "not part of a Snapshot"),
    ({"taps/bf_tap_03.md": b"x"}, "not part of a Snapshot"),
    ({"taps/deeper/custom_tap_1.md": b"x"}, "not part of a Snapshot"),
    ({"old_beers/custom_tap_1.md": b"x"}, "not part of a Snapshot"),
    ({"upcoming/upcoming_s_batch1.md": b"x"}, "not part of a Snapshot"),
    ({"venue_logo.png": b"a", "venue_logo.svg": b"b"}, "more than one venue logo"),
])
def test_an_archive_with_a_wrong_layout_is_refused_and_says_why(members, expected):
    path = _stage(_build_snapshot(dict(config_store.DEFAULT_CONFIG), members))
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(path)
    assert expected in str(exc.value)


@pytest.mark.parametrize("name, expected", [
    ("taps\\custom_tap_1.md", "backslash"),
    ("C:/data/config.json", "absolute path"),
    ("/etc/passwd", "absolute path"),
    ("../escape.md", "relative path segment"),
    ("taps/./custom_tap_1.md", "relative path segment"),
    ("taps//custom_tap_1.md", "relative path segment"),
    ("", "empty name"),
])
def test_unsafe_member_names_are_named_by_the_predicate(name, expected):
    """The traversal rules, asserted on the predicate rather than through a zip.

    A backslash cannot be smuggled through a zip on Windows at all: `ZipInfo`
    rewrites `os.sep` to "/" both when writing and when reading, so the test
    interpreter here would repair the very thing being tested while a Linux one
    would not. Testing the predicate directly gives the same guarantee on both.
    """
    assert expected in (snapshot._unsafe_path_reason(name) or "")


def test_legal_member_names_pass_the_predicate():
    for name in ("config.json", "venue_logo.png", "taps/custom_tap_1.md",
                 "old_beers/bf_tap_9_20260101T120000.jpg"):
        assert snapshot._unsafe_path_reason(name) is None


def test_an_archive_with_no_settings_is_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("taps/custom_tap_1.md", "---\nname: Beer\n---\n")
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(_stage(buf.getvalue()))
    assert "no config.json" in str(exc.value)


def test_a_duplicated_member_name_is_refused():
    # Two entries with one name: an extractor keeps the last, so a check that
    # looked only at the first could be walked straight past.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(snapshot.SETTINGS_NAME, json.dumps(dict(config_store.DEFAULT_CONFIG)))
        zf.writestr("taps/custom_tap_1.md", "---\nname: First\n---\n")
        zf.writestr("taps/custom_tap_1.md", "---\nname: Second\n---\n")
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(_stage(buf.getvalue()))
    assert "listed twice" in str(exc.value)


def test_something_that_is_not_a_zip_is_refused():
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(_stage(b"this is a photo, not a Snapshot"))
    assert "not a readable zip" in str(exc.value)


def test_a_rejected_import_leaves_every_existing_file_unchanged(write_tap, no_credential_env):
    # The criterion, asserted on bytes and mtimes rather than on the absence of
    # an exception: a refusal must not have written, deleted or even rewritten
    # anything on its way to the rejection.
    write_tap("custom", 1, name="Hand Pour", image_ext=".png")
    write_tap("bf", 2, name="Synced Stout")
    (paths.OLD_BEERS_DIR / "bf_tap_9_20260101T120000.md").write_text("---\nname: Old\n---\n")
    (paths.DATA_DIR / "venue_logo.png").write_bytes(b"logo-bytes")
    config_store.update_config(num_taps=4, announcement_text="Quiz night")
    before = _tree_state()

    # A Snapshot that would otherwise land plenty of files, spoiled by one
    # illegal member - so a partial write would be obvious.
    payload = _build_snapshot(
        {**config_store.DEFAULT_CONFIG, "num_taps": 8, "announcement_text": "Replaced"},
        {
            "taps/custom_tap_1.md": b"---\nname: Imported\n---\n",
            "old_beers/bf_tap_5_20250101T090000.md": b"---\nname: Imported Old\n---\n",
            "venue_logo.png": b"different-logo",
            "../escape.md": b"x",
        },
    )
    with pytest.raises(snapshot.SnapshotRejected):
        snapshot.import_snapshot(_stage(payload))

    after = _tree_state()
    del after[str(snapshot.STAGED_UPLOAD_PATH.relative_to(paths.DATA_DIR))]
    assert after == before


def test_an_archive_containing_an_upcoming_member_is_refused_and_changes_nothing(
        write_tap, no_credential_env):
    # The acceptance criterion in one test: an `upcoming/` member is not a
    # layout mistake to tolerate, it is refused whole, with the same
    # bytes-and-mtimes guarantee every other refusal gives (ADR-0006: this
    # directory is never part of a Snapshot in either direction).
    write_tap("custom", 1, name="Hand Pour", image_ext=".png")
    config_store.update_config(num_taps=4)
    before = _tree_state()

    payload = _build_snapshot(
        dict(config_store.DEFAULT_CONFIG),
        {"upcoming/upcoming_s_batch1.md": b"---\nname: Sneaked In\n---\n"},
    )
    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(_stage(payload))
    assert "not part of a Snapshot" in str(exc.value)

    after = _tree_state()
    del after[str(snapshot.STAGED_UPLOAD_PATH.relative_to(paths.DATA_DIR))]
    assert after == before


def test_a_damaged_member_is_refused_before_anything_is_written(write_tap, no_credential_env):
    # The layout can be perfect while the bytes are not. Caught in validation,
    # so a truncated download is refused whole instead of half-applied.
    write_tap("custom", 1, name="Hand Pour")
    before = _tree_state()
    good = _build_snapshot(dict(config_store.DEFAULT_CONFIG),
                           {"taps/custom_tap_1.md": b"---\nname: Imported\n---\n"})
    corrupt = bytearray(good)
    # Flip a byte inside the stored member's data, leaving the directory intact.
    corrupt[good.index(b"Imported")] = ord("X")

    with pytest.raises(snapshot.SnapshotRejected) as exc:
        snapshot.import_snapshot(_stage(bytes(corrupt)))
    assert "damaged" in str(exc.value)
    after = _tree_state()
    del after[str(snapshot.STAGED_UPLOAD_PATH.relative_to(paths.DATA_DIR))]
    assert after == before


# ---- the Brewfather question ---------------------------------------------

def _key_carrying_snapshot() -> bytes:
    return _build_snapshot(
        {**config_store.DEFAULT_CONFIG,
         "num_taps": 3,
         "brewfather_user_id": "snapshot-user-placeholder",
         "brewfather_api_key": "snapshot-key-placeholder"},
        {"taps/custom_tap_1.md": b"---\nname: Hand Pour\n---\n",
         "taps/bf_tap_2.md": b"---\nname: Snapshot Stout\n---\n",
         "old_beers/bf_tap_9_20250101T090000.md": b"---\nname: Old\n---\n"},
    )


def _keyless_snapshot() -> bytes:
    return _build_snapshot(
        {**config_store.DEFAULT_CONFIG, "num_taps": 3},
        {"taps/custom_tap_1.md": b"---\nname: Hand Pour\n---\n",
         "taps/bf_tap_2.md": b"---\nname: Snapshot Stout\n---\n"},
    )


def test_key_carrying_snapshot_onto_a_keyless_box_asks_first(no_credential_env):
    path = _stage(_key_carrying_snapshot())
    with zipfile.ZipFile(path) as zf:
        plan = snapshot.plan_import(snapshot.read_snapshot_settings(zf))
    assert plan.kind == snapshot.DECISION_CHOOSE
    assert (plan.box_has_key, plan.snapshot_has_key) == (False, True)
    # And it refuses to guess: no answer means no import, and nothing written.
    with pytest.raises(snapshot.DecisionRequired):
        snapshot.import_snapshot(path)
    assert not (paths.TAPS_DIR / "bf_tap_2.md").exists()


def test_choosing_to_keep_syncing_skips_the_brewfather_taps(no_credential_env):
    # "Keep syncing" on a keyless box means adopting the Snapshot's key, and
    # therefore skipping its Brewfather beers: the next sync would replace them
    # within minutes, so importing them would quietly undo itself.
    result = snapshot.import_snapshot(_stage(_key_carrying_snapshot()), keep_syncing=True)

    assert result["keeps_syncing"] is True
    assert result["counts"]["brewfather_skipped"] == 1
    assert (paths.TAPS_DIR / "custom_tap_1.md").exists()
    assert not (paths.TAPS_DIR / "bf_tap_2.md").exists()
    assert (paths.OLD_BEERS_DIR / "bf_tap_9_20250101T090000.md").exists()
    cfg = config_store.load_config()
    assert cfg["brewfather_api_key"] == "snapshot-key-placeholder"


def test_choosing_to_stop_syncing_imports_them_and_clears_the_key(no_credential_env):
    result = snapshot.import_snapshot(_stage(_key_carrying_snapshot()), keep_syncing=False)

    assert result["keeps_syncing"] is False
    assert result["counts"]["brewfather_skipped"] == 0
    assert (paths.TAPS_DIR / "bf_tap_2.md").exists()
    cfg = config_store.load_config()
    assert cfg["brewfather_api_key"] == ""
    assert cfg["brewfather_user_id"] == ""


def test_keyless_snapshot_onto_a_box_with_a_key_asks_and_both_branches_work(no_credential_env):
    config_store.update_config(brewfather_api_key="box-key-placeholder")
    with zipfile.ZipFile(_stage(_keyless_snapshot())) as zf:
        plan = snapshot.plan_import(snapshot.read_snapshot_settings(zf))
    assert plan.kind == snapshot.DECISION_CHOOSE
    assert (plan.box_has_key, plan.snapshot_has_key) == (True, False)

    snapshot.import_snapshot(_stage(_keyless_snapshot()), keep_syncing=True)
    assert not (paths.TAPS_DIR / "bf_tap_2.md").exists()
    assert config_store.load_config()["brewfather_api_key"] == "box-key-placeholder"

    config_store.update_config(brewfather_api_key="box-key-placeholder")
    snapshot.import_snapshot(_stage(_keyless_snapshot()), keep_syncing=False)
    assert (paths.TAPS_DIR / "bf_tap_2.md").exists()
    assert config_store.load_config()["brewfather_api_key"] == ""


def test_an_import_never_replaces_a_key_the_box_already_has(no_credential_env):
    # The single way this feature could break a box that was syncing fine, so it
    # is pinned per field: the user ID resolves independently of the key.
    config_store.update_config(brewfather_api_key="box-key-placeholder")
    snapshot.import_snapshot(_stage(_key_carrying_snapshot()), keep_syncing=True)

    cfg = config_store.load_config()
    assert cfg["brewfather_api_key"] == "box-key-placeholder"
    # The box left its user ID empty, so the Snapshot's is adopted.
    assert cfg["brewfather_user_id"] == "snapshot-user-placeholder"


def test_a_box_keyed_from_the_environment_is_never_asked(monkeypatch):
    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key-placeholder")
    monkeypatch.delenv("BREWFATHER_USER_ID", raising=False)
    path = _stage(_key_carrying_snapshot())
    with zipfile.ZipFile(path) as zf:
        plan = snapshot.plan_import(snapshot.read_snapshot_settings(zf))
    assert plan.kind == snapshot.DECISION_ENVIRONMENT

    # No answer supplied, and it imports anyway - there was no question.
    result = snapshot.import_snapshot(path)
    assert result["decision"] == snapshot.DECISION_ENVIRONMENT
    assert result["keeps_syncing"] is True
    assert (paths.TAPS_DIR / "custom_tap_1.md").exists()
    assert not (paths.TAPS_DIR / "bf_tap_2.md").exists()
    # An import cannot write a credential the environment owns.
    assert config_store.load_config()["brewfather_api_key"] == ""


def test_a_keyless_snapshot_onto_a_keyless_box_imports_everything(no_credential_env):
    path = _stage(_keyless_snapshot())
    with zipfile.ZipFile(path) as zf:
        assert snapshot.plan_import(snapshot.read_snapshot_settings(zf)).kind \
            == snapshot.DECISION_NONE

    result = snapshot.import_snapshot(path)      # no answer needed
    assert result["counts"]["brewfather_skipped"] == 0
    assert (paths.TAPS_DIR / "custom_tap_1.md").exists()
    assert (paths.TAPS_DIR / "bf_tap_2.md").exists()


def test_a_keyless_snapshot_still_restores_its_user_id(no_credential_env):
    # "Neither side has a key" is not "stop syncing": there was nothing to stop,
    # so a user ID the Snapshot carries is still worth restoring.
    payload = _build_snapshot(
        {**config_store.DEFAULT_CONFIG, "brewfather_user_id": "snapshot-user-placeholder"})
    snapshot.import_snapshot(_stage(payload))
    assert config_store.load_config()["brewfather_user_id"] == "snapshot-user-placeholder"


# ---- imported Settings ----------------------------------------------------

def test_out_of_range_settings_are_clamped_and_saved_not_rejected(no_credential_env):
    # A Snapshot's config.json is a hand-edited config file as far as the store
    # is concerned, and those clamp rather than raise (CONTEXT.md). A Snapshot
    # from a box with a wider limit must not stop this one importing.
    payload = _build_snapshot({
        **config_store.DEFAULT_CONFIG,
        "num_taps": config_store.MAX_NUM_TAPS + 500,
        "page_size": 99,
        "rotation_seconds": 1,
    })
    result = snapshot.import_snapshot(_stage(payload))

    assert result["num_taps"] == config_store.MAX_NUM_TAPS
    cfg = config_store.load_config()
    assert cfg["page_size"] == config_store.SETTINGS_BOUNDS["page_size"][1]
    assert cfg["rotation_seconds"] == config_store.SETTINGS_BOUNDS["rotation_seconds"][0]


def test_importing_replaces_a_root_image_of_a_different_extension(no_credential_env):
    # venue_logo_path() picks by a fixed extension order, so leaving the old
    # spelling behind would keep the Snapshot's logo invisible.
    (paths.DATA_DIR / "venue_logo.svg").write_bytes(b"<svg>old</svg>")
    payload = _build_snapshot(dict(config_store.DEFAULT_CONFIG),
                              {"venue_logo.png": b"new-logo-bytes"})
    snapshot.import_snapshot(_stage(payload))

    assert not (paths.DATA_DIR / "venue_logo.svg").exists()
    assert paths.venue_logo_path() == paths.DATA_DIR / "venue_logo.png"


# ---- the round trip -------------------------------------------------------

def test_a_snapshot_unpacked_into_an_empty_data_directory_rebuilds_the_board(
        write_tap, no_credential_env):
    """The strongest claim the feature makes, tested the way it is worded.

    Export, wipe the data directory to nothing, unpack the zip by hand with
    `zipfile.extractall` - deliberately not through the import route, because
    hand-unpacking is a supported restore path (ADR-0001) and this is what pins
    it - then rebuild the Board and compare it to the one that was exported.
    """
    config_store.update_config(
        num_taps=3, announcement_text="Quiz night Thursday",
        show_og=True, theme="oled", venue_logo_height_vh=12,
    )
    write_tap("custom", 1, name="Hand Pour", abv=4.2, ibu=18, ebc=14, image_ext=".png",
              body="Soft and hazy.")
    write_tap("bf", 2, name="Synced Stout", abv=6.1, ibu=40, ebc=80)
    (paths.OLD_BEERS_DIR / "bf_tap_9_20260101T120000.md").write_text("---\nname: Old\n---\n")
    (paths.DATA_DIR / "venue_logo.png").write_bytes(b"logo-bytes")
    before = board.build_board()

    data = _export_bytes()

    for path in sorted(paths.DATA_DIR.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
    assert not any(p.is_file() for p in paths.DATA_DIR.rglob("*"))

    zipfile.ZipFile(io.BytesIO(data)).extractall(paths.DATA_DIR)
    after = board.build_board()

    # The logo URL carries the file's mtime as a cache-buster, which unpacking
    # legitimately changes; everything the board says about the beers must match.
    for payload in (before, after):
        payload["venue_logo_url"] = (payload["venue_logo_url"] or "").split("?")[0] or None
    assert after == before
    assert (paths.OLD_BEERS_DIR / "bf_tap_9_20260101T120000.md").exists()


def test_the_round_trip_survives_the_import_route_too(write_tap, no_credential_env):
    # Same claim, restored through import_snapshot rather than by hand, so the
    # two restore paths cannot drift apart.
    config_store.update_config(num_taps=2, announcement_text="Same board")
    write_tap("custom", 1, name="Hand Pour", abv=4.2, ebc=14)
    write_tap("bf", 2, name="Synced Stout", abv=6.1, ebc=80)
    before = board.build_board()

    data = _export_bytes()
    for path in list(paths.TAPS_DIR.iterdir()):
        path.unlink()
    paths.CONFIG_PATH.unlink()

    snapshot.import_snapshot(_stage(data))
    assert board.build_board() == before


# ---- the HTTP surface -----------------------------------------------------

def test_snapshot_routes_require_an_admin_session():
    assert client.get("/admin/snapshot").status_code == 401
    assert client.post("/admin/snapshot/stage", content=b"x").status_code == 401
    assert client.post("/admin/snapshot/import", data={}).status_code == 401
    assert client.post("/admin/snapshot/discard", data={}).status_code == 401


def test_export_route_streams_a_zip_named_for_the_moment(write_tap, no_credential_env):
    write_tap("custom", 1, name="Hand Pour")
    r = _login(TestClient(app)).get("/admin/snapshot")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment; filename=\"taplist-snapshot-" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    assert "taps/custom_tap_1.md" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_export_route_defaults_to_leaving_the_credentials_out(no_credential_env):
    config_store.update_config(brewfather_api_key="settings-key-placeholder")
    c = _login(TestClient(app))

    plain = c.get("/admin/snapshot").content
    assert b"settings-key-placeholder" not in plain

    opted_in = c.get("/admin/snapshot", params={"credentials": "true"}).content
    settings = json.loads(zipfile.ZipFile(io.BytesIO(opted_in)).read("config.json"))
    assert settings["brewfather_api_key"] == "settings-key-placeholder"


def test_admin_page_offers_the_credential_checkbox_only_when_the_key_is_in_settings(
        no_credential_env, monkeypatch):
    c = _login(TestClient(app))
    assert 'id="snapshot-credentials"' not in c.get("/admin").text

    config_store.update_config(brewfather_api_key="settings-key-placeholder")
    html = c.get("/admin").text
    assert 'id="snapshot-credentials"' in html
    # Unchecked by default: opting in has to be a deliberate act.
    checkbox = html[html.index('id="snapshot-credentials"'):][:120]
    assert "checked" not in checkbox

    monkeypatch.setenv("BREWFATHER_API_KEY", "env-key-placeholder")
    assert 'id="snapshot-credentials"' not in c.get("/admin").text


def test_stage_then_import_over_http(write_tap, no_credential_env):
    write_tap("custom", 1, name="Before")
    data = _export_bytes()
    c = _login(TestClient(app))

    staged = c.post("/admin/snapshot/stage", content=data)
    assert staged.status_code == 200
    assert staged.json()["decision"] == snapshot.DECISION_NONE

    (paths.TAPS_DIR / "custom_tap_1.md").unlink()
    done = c.post("/admin/snapshot/import", data={"keep_syncing": ""})
    assert done.status_code == 200
    assert (paths.TAPS_DIR / "custom_tap_1.md").exists()
    # The staged copy is cleaned up on the way out, both times.
    assert not snapshot.STAGED_UPLOAD_PATH.exists()


def test_staging_something_that_is_not_a_snapshot_reports_why_and_stages_nothing():
    c = _login(TestClient(app))
    r = c.post("/admin/snapshot/stage", content=b"not a zip at all")
    assert r.status_code == 422
    assert "not a readable zip" in r.json()["detail"]
    assert not snapshot.STAGED_UPLOAD_PATH.exists()


def test_importing_without_an_answer_when_one_is_needed_is_a_conflict(no_credential_env):
    c = _login(TestClient(app))
    assert c.post("/admin/snapshot/stage", content=_key_carrying_snapshot()).json()["decision"] \
        == snapshot.DECISION_CHOOSE
    r = c.post("/admin/snapshot/import", data={"keep_syncing": ""})
    assert r.status_code == 409
    assert "Brewfather" in r.json()["detail"]


def test_importing_with_nothing_staged_is_a_conflict():
    r = _login(TestClient(app)).post("/admin/snapshot/import", data={"keep_syncing": ""})
    assert r.status_code == 409


def test_discard_removes_the_staged_snapshot(no_credential_env):
    c = _login(TestClient(app))
    c.post("/admin/snapshot/stage", content=_key_carrying_snapshot())
    assert snapshot.STAGED_UPLOAD_PATH.exists()
    assert c.post("/admin/snapshot/discard", data={}).status_code == 200
    assert not snapshot.STAGED_UPLOAD_PATH.exists()


def test_the_import_route_is_not_bound_by_the_image_upload_cap():
    # MAX_UPLOAD_BYTES exists to bound an in-memory read of a beer photo. A
    # Snapshot is streamed to disk instead, so the cap must not be anywhere near
    # this route - a Snapshot can be gigabytes.
    from app import main

    padding = b"\0" * (main.MAX_UPLOAD_BYTES + 1024)
    payload = _build_snapshot(dict(config_store.DEFAULT_CONFIG),
                              {"taps/custom_tap_1.png": padding})
    r = _login(TestClient(app)).post("/admin/snapshot/stage", content=payload)
    assert r.status_code == 200
    assert snapshot.STAGED_UPLOAD_PATH.stat().st_size > main.MAX_UPLOAD_BYTES


# ---- the filename seam ----------------------------------------------------

# Not the bare "upcoming_" prefix: `show_upcoming_previews` (the Setting,
# issue #36) is legitimate prose/code everywhere and contains "upcoming_p" as
# a substring. The Upcoming store's real filename tags are "upcoming_s_" and
# "upcoming_h_" (see _SAFE_TAG / _HASH_TAG in app/upcoming_store.py) - that is
# what a caller would have to spell to construct one of its filenames.
_FILENAME_MARKERS = ("custom_tap_", "bf_tap_", "upcoming_s_", "upcoming_h_")


def _non_docstring_strings(tree: ast.AST) -> list[str]:
    """Every string literal in a module except the docstrings.

    Prose is allowed to name a filename - the modules explain the convention at
    length. Code is not.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_no_module_outside_the_tap_file_store_spells_a_tap_filename():
    """ADR-0003 in one assertion, and the reason the Snapshot got a predicate.

    Validating a Snapshot's layout means recognising a Tap filename, which is
    exactly the knowledge this project keeps in one module. The temptation was
    a regex in the import code; this fails if anyone gives in to it.

    Extended by issue #36 to cover the Upcoming store's own filename prefix
    (ADR-0006), which is private to `app/upcoming_store.py` for the identical
    reason: `tests/test_upcoming_store.py` has its own copy of this guard for
    fast, focused failures, and this copy keeps the Snapshot's own reasoning -
    "validating a Snapshot means recognising a filename, which lives in one
    place" - true for all three stores rather than just the first one.
    """
    app_dir = Path(taps.__file__).parent
    offenders = {}
    for path in sorted(app_dir.glob("*.py")):
        if path.name in ("tap_store.py", "upcoming_store.py"):
            continue
        strings = _non_docstring_strings(ast.parse(path.read_text(encoding="utf-8")))
        found = [s for s in strings if any(marker in s for marker in _FILENAME_MARKERS)]
        if found:
            offenders[path.name] = found
    assert offenders == {}, offenders
