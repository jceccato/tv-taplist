"""Tests for app/update_check.py — release parsing, config fields, and API."""
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


# ---- API: admin-only trigger ---------------------------------------------

def test_check_update_requires_admin():
    r = client.post("/admin/check-update", data={}, follow_redirects=False)
    assert r.status_code in (302, 303, 401)


# ---- config schema -------------------------------------------------------

def test_config_has_update_fields():
    from app.config_store import DEFAULT_CONFIG
    assert "update_check_enabled" in DEFAULT_CONFIG
    assert "update_last_check" in DEFAULT_CONFIG
    assert "update_latest_version" in DEFAULT_CONFIG
    assert "update_latest_url" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["update_check_enabled"] is True
