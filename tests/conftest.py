"""Shared pytest fixtures.

DATA_DIR is pointed at a throwaway temp directory *before* any app module is
imported (app.paths reads it at import time). Each test gets a clean data tree
and a reset config + auth rate-limit state.
"""
from __future__ import annotations

import os
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

from app import auth, config_store, markdown_store as md, paths  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Reset the data tree, config, and auth rate-limit state before each test."""
    paths.ensure_dirs()
    for d in (paths.TAPS_DIR, paths.OLD_BEERS_DIR):
        for f in list(d.iterdir()):
            try:
                f.unlink()
            except OSError:
                pass
    if paths.CONFIG_PATH.exists():
        paths.CONFIG_PATH.unlink()
    config_store.save_config(dict(config_store.DEFAULT_CONFIG))
    auth._failed.clear()
    yield


@pytest.fixture
def write_tap():
    """Helper to write a tap markdown file (+ optional image) quickly."""
    def _write(kind: str, tap: int, *, image_ext: str | None = None, body: str = "", **fm):
        fm.setdefault("source", "custom" if kind == "custom" else "brewfather")
        path = md.custom_md_path(tap) if kind == "custom" else md.bf_md_path(tap)
        md.write_tap_file(path, fm, body)
        if image_ext:
            stem = f"{'custom' if kind == 'custom' else 'bf'}_tap_{tap}"
            (paths.TAPS_DIR / f"{stem}{image_ext}").write_bytes(b"fake-image-bytes")
        return path
    return _write
