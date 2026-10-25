"""Shared pytest fixtures.

DATA_DIR is pointed at a throwaway temp directory *before* any app module is
imported (app.paths reads it at import time). Each test gets a clean data tree
and a reset config + auth rate-limit state.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# --- must run before importing the app package ---
_TMP = Path(tempfile.mkdtemp(prefix="taplist_test_"))
os.environ["DATA_DIR"] = str(_TMP)
os.environ.setdefault("ADMIN_PASSWORD", "testpw")
os.environ.setdefault("SESSION_SECRET", "testsecret")
os.environ.setdefault("TZ", "UTC")
os.environ.setdefault("FORWARDED_ALLOW_IPS", "127.0.0.1")
os.environ.setdefault("DEMO_MODE", "false")

import pytest  # noqa: E402
import yaml  # noqa: E402

from app import auth, config_store, paths, photo_library, seen_screens  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Reset the data tree, config, and auth rate-limit state before each test."""
    paths.ensure_dirs()
    for d in (paths.TAPS_DIR, paths.OLD_BEERS_DIR, paths.UPCOMING_DIR):
        for f in list(d.iterdir()):
            try:
                f.unlink()
            except OSError:
                pass
    # The photo album: originals and the disposable Display copy cache, plus the
    # library's in-memory index of them.
    shutil.rmtree(paths.PHOTOS_DIR, ignore_errors=True)
    shutil.rmtree(paths.PHOTO_CACHE_DIR, ignore_errors=True)
    shutil.rmtree(paths.PHOTO_UPLOAD_STAGING_DIR, ignore_errors=True)
    paths.PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    photo_library.reset_state()
    for logo in paths.DATA_DIR.glob("venue_logo.*"):     # the browser tests' venue fixture writes one
        logo.unlink()
    if paths.CONFIG_PATH.exists():
        paths.CONFIG_PATH.unlink()
    # Status is a separate file and is deliberately NOT seeded: a fresh box has
    # no status.json at all until a job writes one, so tests start there too.
    if paths.STATUS_PATH.exists():
        paths.STATUS_PATH.unlink()
    config_store.save_config(dict(config_store.DEFAULT_CONFIG))
    auth._failed.clear()
    config_store.clear_unknown_profile_names()
    seen_screens.clear_seen_screens()
    yield


@pytest.fixture
def write_tap():
    """Write a Tap file (+ optional image) straight to disk, bypassing the store.

    The filenames and the front-matter-plus-body layout are spelled out by hand
    here **on purpose**, and this fixture deliberately imports neither the Tap
    file store nor anything else that knows the naming convention.

    ADR-0001 makes the mapped data directory something operators read and edit
    by hand, so these names are a user-facing contract rather than an internal
    detail. This fixture is the suite's independent restatement of that
    contract: if the store ever renamed a file or changed the file layout, the
    tests fail loudly instead of the store quietly grading its own homework by
    agreeing with itself. Routing this through app.tap_store would destroy
    exactly that property, so do not.
    """
    def _write(kind: str, tap: int, *, image_ext: str | None = None, body: str = "", **fm):
        fm.setdefault("source", "custom" if kind == "custom" else "brewfather")
        stem = f"{'custom_tap_' if kind == 'custom' else 'bf_tap_'}{tap}"
        path = paths.TAPS_DIR / f"{stem}.md"
        front = yaml.safe_dump(
            fm, sort_keys=False, default_flow_style=False, allow_unicode=True
        ).strip()
        path.write_text(f"---\n{front}\n---\n{(body or '').strip()}\n", encoding="utf-8")
        if image_ext:
            (paths.TAPS_DIR / f"{stem}{image_ext}").write_bytes(b"fake-image-bytes")
        return path
    return _write
