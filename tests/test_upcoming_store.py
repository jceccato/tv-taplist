"""The Upcoming store: /data/upcoming/, one markdown-plus-image pair per Batch.

Every test here asserts what the store holds on disk after a call, not how it
got there - the same "assert the seam" discipline the rest of the suite uses
(see tests/test_snapshot.py's own note on this). `clean_state` in conftest.py
wipes UPCOMING_DIR before each test, so these do not need their own fixture.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import paths, upcoming_store as store
from app.beer import Beer


def _beer(name: str = "Test Saison") -> Beer:
    return Beer(name=name, abv=6.5, ibu=22, ebc=8)


# ---- the round trip --------------------------------------------------------

def test_write_then_read_round_trips_every_field():
    store.write(
        "batch-abc123", _beer(), "Peppery and dry.",
        slot=3, status="conditioning", revision=42,
        image_bytes=b"photo-bytes", image_ext=".jpg",
    )
    entry = store.read("batch-abc123")
    assert entry is not None
    assert entry.batch_id == "batch-abc123"
    assert entry.beer.name == "Test Saison"
    assert entry.beer.abv == 6.5
    assert entry.beer.ibu == 22
    assert entry.beer.ebc == 8
    assert entry.slot == 3
    assert entry.status == "conditioning"
    assert entry.revision == 42
    assert entry.body == "Peppery and dry."
    assert entry.image is not None
    assert entry.image.read_bytes() == b"photo-bytes"


def test_write_with_no_image_leaves_image_none():
    store.write("batch-noimg", _beer(), "", slot=None, status="fermenting", revision=1)
    entry = store.read("batch-noimg")
    assert entry is not None
    assert entry.slot is None
    assert entry.image is None


def test_a_second_write_keeps_a_previous_image_when_none_is_given():
    # Mirrors tap_store/brewfather.py's "a failed download keeps the cached
    # photo" rule: passing no image bytes must not erase a good one.
    store.write("batch-keep", _beer(), "v1", slot=1, status="completed", revision=1,
                image_bytes=b"original", image_ext=".png")
    store.write("batch-keep", _beer(), "v2", slot=1, status="completed", revision=2)
    entry = store.read("batch-keep")
    assert entry.body == "v2"
    assert entry.image is not None
    assert entry.image.read_bytes() == b"original"


def test_a_new_image_extension_replaces_the_old_file():
    store.write("batch-ext", _beer(), "", slot=None, status="fermenting", revision=1,
                image_bytes=b"jpg-bytes", image_ext=".jpg")
    store.write("batch-ext", _beer(), "", slot=None, status="fermenting", revision=2,
                image_bytes=b"png-bytes", image_ext=".png")
    entry = store.read("batch-ext")
    assert entry.image.suffix == ".png"
    assert entry.image.read_bytes() == b"png-bytes"
    # The old extension's file must be gone, not just superseded.
    leftovers = [p for p in paths.UPCOMING_DIR.iterdir() if p.suffix == ".jpg"]
    assert leftovers == []


def test_read_of_an_unknown_batch_id_is_none():
    assert store.read("never-written") is None


def test_list_all_returns_every_entry():
    store.write("batch-1", _beer("One"), "", slot=1, status="completed", revision=1)
    store.write("batch-2", _beer("Two"), "", slot=None, status="fermenting", revision=2)
    names = {e.beer.name for e in store.list_all()}
    assert names == {"One", "Two"}


def test_list_all_on_an_empty_store_is_an_empty_list():
    assert store.list_all() == []


# ---- the disposable read policy --------------------------------------------

def test_an_unreadable_entry_yields_nothing_and_the_next_write_replaces_it():
    """The test that fails if someone unifies the three stores' read policies.

    Unlike config_store's never-overwrite guard, nothing here is operator-
    authored, so a bad file must not raise and must not survive as a
    permanent gap - the next write for the same Batch id simply lands.
    """
    store.write("batch-bad", _beer(), "", slot=None, status="fermenting", revision=1)
    # Corrupt the file directly, bypassing the store, the way a truncated
    # write or a disk hiccup would.
    path = paths.UPCOMING_DIR / next(
        p.name for p in paths.UPCOMING_DIR.glob("*.md")
    )
    path.write_bytes(b"\xff\xfe not valid utf-8 \x00\x01")

    assert store.read("batch-bad") is None
    assert store.list_all() == []  # does not raise, and hides nothing

    # The next write for the same id lands cleanly - "replaced", not stuck.
    store.write("batch-bad", _beer("Recovered"), "", slot=None, status="fermenting", revision=2)
    entry = store.read("batch-bad")
    assert entry is not None
    assert entry.beer.name == "Recovered"


def test_a_file_with_no_batch_id_is_treated_as_absent():
    # Hand-write a syntactically fine markdown file that simply never went
    # through this store - the same "nothing to key it by" case a completely
    # foreign file in the directory would hit.
    (paths.UPCOMING_DIR / "upcoming_s_orphan.md").write_text(
        "---\nname: Orphan\n---\nNo batch_id here.\n", encoding="utf-8"
    )
    assert store.list_all() == []


# ---- the rebuild ------------------------------------------------------------

def test_rebuild_removes_entries_no_longer_in_the_keep_set():
    store.write("batch-1", _beer("Keep"), "", slot=1, status="completed", revision=1)
    store.write("batch-2", _beer("Drop"), "", slot=2, status="completed", revision=1,
                image_bytes=b"x", image_ext=".jpg")
    removed = store.rebuild(["batch-1"])
    assert removed == 1
    assert store.read("batch-1") is not None
    assert store.read("batch-2") is None
    # The image went with it too - nothing left over for a Batch id that is gone.
    remaining = list(paths.UPCOMING_DIR.glob("*"))
    assert len(remaining) == 1
    assert remaining[0].suffix == ".md"


def test_rebuild_with_an_empty_keep_set_clears_everything():
    store.write("batch-1", _beer(), "", slot=1, status="completed", revision=1)
    store.write("batch-2", _beer(), "", slot=None, status="fermenting", revision=1)
    assert store.rebuild([]) == 2
    assert store.list_all() == []


def test_rebuild_sweeps_an_entry_the_reader_cannot_key():
    """An unreadable or unkeyable file must not outlive the rebuild (ADR-0006).

    `_load` returns None for a file with no `batch_id` (or unparseable front
    matter), so a rebuild that walks readable entries can never name it for
    removal - it would sit in /data/upcoming/ forever, invisible to the board
    but visibly stale to an operator reading the directory by hand (ADR-0001
    makes that a supported surface). The sweep judges by filename instead:
    everything the store owns that the current cycle did not keep goes.
    """
    store.write("batch-keep", _beer("Keep"), "", slot=1, status="completed", revision=1)
    # A hand-mangled entry: parseable path prefix, no batch_id to key it by.
    (paths.UPCOMING_DIR / "upcoming_s_mangled.md").write_text(
        "---\nname: Orphan\n---\nno batch_id here\n", encoding="utf-8")
    # An orphan image whose markdown half is already gone.
    (paths.UPCOMING_DIR / "upcoming_s_ghost.jpg").write_bytes(b"stale-photo")
    assert store.rebuild(["batch-keep"]) == 2
    remaining = sorted(p.name for p in paths.UPCOMING_DIR.iterdir())
    assert remaining == ["upcoming_s_batch-keep.md"]


def test_rebuild_never_removes_a_kept_batch_even_when_its_file_is_sick():
    """The sweep must key on the KEEP set, not on readability.

    A transiently unreadable file for a Batch the cycle still wants is
    replaced by that same cycle's write, never swept as an orphan - sweeping
    by "could I read it" instead of "was it kept" would turn a read hiccup
    into a deleted entry.
    """
    store.write("batch-sick", _beer("Sick"), "", slot=1, status="completed", revision=1)
    # Corrupt it so _load cannot key it; the id is still in the keep set.
    md = paths.UPCOMING_DIR / "upcoming_s_batch-sick.md"
    md.write_text("---\nname: no id any more\n---\n", encoding="utf-8")
    assert store.rebuild(["batch-sick"]) == 0
    assert md.exists()


def test_rebuild_does_not_touch_old_beers():
    # "A Batch that stops qualifying leaves no file behind, and nothing
    # reaches old_beers/" - rebuild must never write there.
    store.write("batch-1", _beer(), "", slot=1, status="completed", revision=1)
    before = list(paths.OLD_BEERS_DIR.iterdir())
    store.rebuild([])
    assert list(paths.OLD_BEERS_DIR.iterdir()) == before


# ---- clear ------------------------------------------------------------------

def test_clear_removes_every_entry_and_leaves_the_rest_of_data_untouched():
    store.write("batch-1", _beer(), "", slot=1, status="completed", revision=1,
                image_bytes=b"x", image_ext=".jpg")
    store.write("batch-2", _beer(), "", slot=None, status="fermenting", revision=1)
    (paths.TAPS_DIR / "custom_tap_1.md").write_text("---\nname: Untouched\n---\n")
    (paths.DATA_DIR / "config.json").write_text("{}")

    removed = store.clear()

    assert removed == 3  # 2 md files + 1 image
    assert store.list_all() == []
    assert (paths.TAPS_DIR / "custom_tap_1.md").exists()
    assert (paths.DATA_DIR / "config.json").exists()


def test_clear_on_an_empty_or_missing_directory_is_a_safe_noop():
    assert store.clear() == 0
    import shutil
    shutil.rmtree(paths.UPCOMING_DIR)
    assert store.clear() == 0
    assert store.list_all() == []


# ---- batch-id sanitisation --------------------------------------------------

@pytest.mark.parametrize("batch_id", [
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "/etc/passwd",
    "id/with/slashes",
    "id with spaces",
    "id:with:colons",
    "",
])
def test_a_hostile_or_unsafe_batch_id_cannot_escape_the_directory(batch_id):
    store.write(batch_id, _beer(), "", slot=None, status="fermenting", revision=1)
    for path in paths.UPCOMING_DIR.iterdir():
        # Every file this store wrote sits directly inside UPCOMING_DIR (no
        # path separator survived into a name) and is a legal filename on
        # this filesystem - both checks fail loudly if a raw id ever reached
        # a path unsanitised.
        assert path.parent == paths.UPCOMING_DIR
        assert "/" not in path.name and "\\" not in path.name
    entry = store.read(batch_id)
    assert entry is not None
    assert entry.batch_id == batch_id


def test_two_different_ids_never_collide_on_one_filename():
    # The case a naive "safe chars pass through, else digest" scheme gets
    # wrong: a legal, alphanumeric id that happens to equal what another id's
    # digest would produce. Constructed by using the real digest of one id as
    # the literal (and therefore "safe") value of a second.
    import hashlib
    hostile_id = "../not/safe/at/all"
    digest = hashlib.sha256(str(hostile_id).encode("utf-8")).hexdigest()[:32]
    lookalike_id = digest  # a plain hex string: alnum, so it passes straight through

    store.write(hostile_id, _beer("Hostile"), "", slot=None, status="fermenting", revision=1)
    store.write(lookalike_id, _beer("Lookalike"), "", slot=None, status="fermenting", revision=1)

    names = {p.name for p in paths.UPCOMING_DIR.glob("*.md")}
    assert len(names) == 2  # two distinct files, not one clobbering the other
    assert store.read(hostile_id).beer.name == "Hostile"
    assert store.read(lookalike_id).beer.name == "Lookalike"


def test_a_safe_batch_id_is_stored_readably_on_disk():
    # Not a hard requirement of the interface, but worth pinning: an ordinary
    # Firestore-style id should not be needlessly digested, so an operator
    # inspecting /data/upcoming/ by hand can still recognise which file is
    # which Batch.
    store.write("Xk9pQ2vN7mZaB3cD", _beer(), "", slot=None, status="fermenting", revision=1)
    names = [p.name for p in paths.UPCOMING_DIR.glob("*.md")]
    assert any("Xk9pQ2vN7mZaB3cD" in name for name in names)


# ---- the filename seam (AST guard) -----------------------------------------

# Not the bare "upcoming_" prefix: `show_upcoming_previews` (the Setting) is
# legitimate prose/code everywhere, and "upcoming_p" is a substring of it.
# The store's actual filename tags are "upcoming_s_" and "upcoming_h_" (see
# _SAFE_TAG / _HASH_TAG in app/upcoming_store.py), which is what a caller
# would have to spell to construct one of this store's real filenames.
_FILENAME_MARKERS = ("custom_tap_", "bf_tap_", "upcoming_s_", "upcoming_h_")


def _non_docstring_strings(tree: ast.AST) -> list[str]:
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


def test_no_module_outside_the_upcoming_store_spells_its_filename_prefix():
    """ADR-0003, extended to the third store (ADR-0006).

    Restates tests/test_snapshot.py's tap-file guard for this store's own
    prefix: nothing outside app/upcoming_store.py may construct or parse one
    of its filenames.
    """
    app_dir = Path(store.__file__).parent
    offenders = {}
    for path in sorted(app_dir.glob("*.py")):
        if path.name in ("tap_store.py", "upcoming_store.py"):
            continue
        strings = _non_docstring_strings(ast.parse(path.read_text(encoding="utf-8")))
        found = [s for s in strings if any(marker in s for marker in _FILENAME_MARKERS)]
        if found:
            offenders[path.name] = found
    assert offenders == {}, offenders


def test_the_ast_guard_actually_fails_on_a_spelling_outside_the_store(tmp_path):
    """Prove the guard bites: a module that spells the prefix must be caught."""
    offender = tmp_path / "fake_module.py"
    offender.write_text('STEM = "upcoming_s_should_not_be_spelled_here"\n', encoding="utf-8")
    strings = _non_docstring_strings(ast.parse(offender.read_text(encoding="utf-8")))
    found = [s for s in strings if any(marker in s for marker in _FILENAME_MARKERS)]
    assert found == ["upcoming_s_should_not_be_spelled_here"]
