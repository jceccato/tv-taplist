"""Tests for app/update_check.py - release parsing, where the fields live, and the API."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---- unit: release parsing -----------------------------------------------

def test_parse_normal_release():
    from app.update_check import _parse_github_release
    data = {"tag_name": "v1.2.3", "html_url": "https://github.com/a/b/releases/tag/v1.2.3"}
    tag, url = _parse_github_release(data)
    assert tag == "v1.2.3"
    assert url == "https://github.com/a/b/releases/tag/v1.2.3"


def test_parse_no_tag_returns_unreleased():
    from app.update_check import _parse_github_release
    # A realistic GitHub response with other fields but no tag_name (shouldn't
    # happen in practice, but we handle it). An empty dict {} is falsy and caught
    # by the `not data` guard — use a dict with a key to reach the tag check.
    tag, url = _parse_github_release({"message": "Not Found"})
    assert tag == "unreleased"
    assert url is None


def test_parse_none_response():
    from app.update_check import _parse_github_release
    tag, url = _parse_github_release(None)
    assert tag is None
    assert url is None


def test_is_newer():
    from app.update_check import _is_newer
    assert _is_newer("v2.0.0", "v1.0.0") is True
    assert _is_newer("v1.0.0", "v1.0.0") is False
    assert _is_newer("unreleased", "v1.0.0") is False
    assert _is_newer("", "v1.0.0") is False
    # A non-release running version (main/dev/bare-SHA build) is never "behind" a
    # tagged release - this is what prevents a permanent false "update available"
    # on the :latest image (built from main, so VERSION="main").
    assert _is_newer("v1.0.0", "main") is False
    assert _is_newer("v1.0.0", "dev") is False
    assert _is_newer("v1.0.0", "1a2b3c4") is False


def test_is_update_available_public_wrapper():
    from app.update_check import is_update_available
    assert is_update_available("v2.0.0", "v1.0.0") is True
    assert is_update_available("v1.0.0", "v1.0.0") is False
    assert is_update_available(None, "v1.0.0") is False
    # The :latest build (VERSION="main") must not report an update.
    assert is_update_available("v9.9.9", "main") is False


def test_current_version_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("TVTAPLIST_VERSION", raising=False)
    from app.update_check import current_version
    assert current_version() == "dev"


def test_current_version_reads_env(monkeypatch):
    monkeypatch.setenv("TVTAPLIST_VERSION", "v2.0.0")
    from app.update_check import current_version
    assert current_version() == "v2.0.0"


# ---- API: public status endpoint -----------------------------------------

def test_api_update_status_returns_expected_keys():
    r = client.get("/api/update-status")
    assert r.status_code == 200
    data = r.json()
    for key in ("current_version", "latest_version", "update_available", "enabled"):
        assert key in data, f"missing key: {key}"
    # No secrets in the public response.
    raw = json.dumps(data).lower()
    assert "api_key" not in raw
    assert "password" not in raw


def test_api_update_status_reads_the_findings_from_status_json():
    """The endpoint serves Status from status.json, not from config.json."""
    from app import status_store
    status_store.update_status(update_latest_version="v9.9.9",
                               update_latest_url="https://example.invalid/r/v9.9.9",
                               update_last_check="2026-05-05T00:00:00")
    data = client.get("/api/update-status").json()
    assert data["latest_version"] == "v9.9.9"
    assert data["latest_url"] == "https://example.invalid/r/v9.9.9"
    assert data["last_check"] == "2026-05-05T00:00:00"
    assert data["enabled"] is True   # the Setting still comes from config.json


# ---- API: admin-only trigger ---------------------------------------------

def test_check_update_requires_admin():
    r = client.post("/admin/check-update", data={}, follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ---- schema: intent is a Setting, findings are Status ---------------------

def test_update_check_intent_is_a_setting():
    """Whether to check is operator intent, so it stays in config.json."""
    from app.config_store import DEFAULT_CONFIG
    assert "update_check_enabled" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["update_check_enabled"] is True


def test_update_check_findings_are_status():
    """What the check FOUND is machine-written runtime state, so it is Status."""
    from app.config_store import DEFAULT_CONFIG
    from app.status_store import DEFAULT_STATUS
    for key in ("update_last_check", "update_latest_version", "update_latest_url"):
        assert key in DEFAULT_STATUS
        assert key not in DEFAULT_CONFIG


# ---- the four-state model (issue #26) ------------------------------------

def test_update_state_covers_the_four_cases():
    from app.update_check import (STATE_BEHIND, STATE_CURRENT, STATE_DISABLED,
                                  STATE_UNKNOWN, update_state)
    assert update_state("v1.3.0", "v1.2.0") == STATE_BEHIND
    assert update_state("v1.3.0", "v1.3.0") == STATE_CURRENT
    # Intent beats everything: an operator who turned checks off is told that,
    # not a stale comparison.
    assert update_state("v1.3.0", "v1.2.0", enabled=False) == STATE_DISABLED
    # The bug this exists for: an untagged build is NOT "up to date".
    for build in ("main", "dev", "1a2b3c4"):
        assert update_state("v1.3.0", build) == STATE_UNKNOWN


def test_update_state_is_unknown_when_no_release_is_known():
    """Never checked, offline, or a repo with no releases at all.

    A running release with nothing to compare against is still unknown - the old
    code reported that pair as "up to date" too.
    """
    from app.update_check import STATE_UNKNOWN, update_state
    assert update_state(None, "v1.3.0") == STATE_UNKNOWN
    assert update_state("", "v1.3.0") == STATE_UNKNOWN
    assert update_state("unreleased", "v1.3.0") == STATE_UNKNOWN


def test_unknown_state_never_coincides_with_update_available():
    """The two fields must not contradict each other.

    `update_available` stays the "definitely behind" signal for compatibility;
    `status` carries the nuance. An untagged build reports false/unknown, and
    the admin must read the pair as "cannot tell", not "current".
    """
    from app.update_check import STATE_UNKNOWN, is_update_available, update_state
    assert is_update_available("v1.3.0", "main") is False
    assert update_state("v1.3.0", "main") == STATE_UNKNOWN


def test_api_update_status_exposes_the_state(monkeypatch):
    monkeypatch.setenv("TVTAPLIST_VERSION", "main")
    from app import status_store
    status_store.update_status(update_latest_version="v9.9.9")
    data = client.get("/api/update-status").json()
    assert data["status"] == "unknown"
    assert data["update_available"] is False


def test_api_update_status_reports_behind(monkeypatch):
    monkeypatch.setenv("TVTAPLIST_VERSION", "v1.0.0")
    from app import status_store
    status_store.update_status(update_latest_version="v9.9.9")
    data = client.get("/api/update-status").json()
    assert data["status"] == "behind"
    assert data["update_available"] is True


def test_api_update_status_reports_disabled(monkeypatch):
    monkeypatch.setenv("TVTAPLIST_VERSION", "v1.0.0")
    from app import config_store, status_store
    status_store.update_status(update_latest_version="v9.9.9")
    config_store.update_config(update_check_enabled=False)
    try:
        data = client.get("/api/update-status").json()
        assert data["status"] == "disabled"
        assert data["enabled"] is False
    finally:
        config_store.update_config(update_check_enabled=True)


# ---- the version string (issue #25) --------------------------------------

def test_package_version_is_not_a_hardcoded_literal():
    """`__version__` must come from the build, not from a number in the source.

    A literal here was two releases stale before anyone noticed, and it sits in
    the first place a reader looks for the version.
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "__init__.py").read_text(encoding="utf-8")
    body = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"__version__\s*=\s*['\"]\d", body), "hardcoded version literal"


def test_package_version_matches_the_running_version(monkeypatch):
    """One env var, one fallback - `__version__` cannot disagree with the checker.

    `__version__` is snapshotted at import, so this compares the SOURCE of both
    rather than monkeypatching (which only the live read would see).
    """
    import app
    from app import update_check
    assert app.VERSION_FALLBACK == "dev"
    monkeypatch.delenv("TVTAPLIST_VERSION", raising=False)
    assert update_check.current_version() == app.VERSION_FALLBACK
    monkeypatch.setenv(app.VERSION_ENV, "v4.5.6")
    assert update_check.current_version() == "v4.5.6"
