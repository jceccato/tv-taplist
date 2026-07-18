"""Check GitHub for new releases once per day.

Uses the unauthenticated GitHub API (60 req/hour; one daily check is trivially
within this limit). The repo owner is baked into the image; a forker replaces it
and gets their own update feed.
"""
from __future__ import annotations

import logging
import os
import re

import httpx

from .atomic import JOB_LOCK
from .config_store import load_config, update_config
from .timezone import iso_now

log = logging.getLogger("taplist.update")

# ---------------------------------------------------------------------------
# The GitHub owner is hardcoded at build time so a forked repo's image checks
# the forker's releases, not the upstream. Keep this in sync with the CI
# workflow's image tag and the docker-compose.yml placeholder.
# ---------------------------------------------------------------------------
GITHUB_OWNER = "jceccato"
GITHUB_REPO = "tv-taplist"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# UNRELEASED means no release exists yet (e.g. freshly forked repo with no tag).
_UNRELEASED = "unreleased"

# A running version is only comparable to a GitHub release when it looks like a
# release tag (vX.Y or vX.Y.Z). The `:latest` image is built from main, which
# bakes VERSION="main"; local runs report "dev"; and the CI fallback can bake a
# bare commit SHA. None of those are "behind" a tagged release, so they must not
# be nagged with a spurious "update available". Requiring at least one dot keeps
# a hex SHA like "1a2b3c4" (no dot, may lead with a digit) from matching.
_VERSION_RE = re.compile(r"^v?\d+(\.\d+)+")


def _looks_like_release(version: str) -> bool:
    """True when `version` looks like a comparable release tag (vX.Y[.Z])."""
    return bool(_VERSION_RE.match(version or ""))


def current_version() -> str:
    """The version baked into the running container (dev when run locally)."""
    return os.environ.get("TVTAPLIST_VERSION", "dev")


def _parse_github_release(data: dict | None) -> tuple[str | None, str | None]:
    """Extract (tag_name, html_url) from a GitHub releases/latest response.
    
    Returns ("unreleased", None) when no release exists yet on the repo.
    """
    if not data or not isinstance(data, dict):
        return None, None
    tag = data.get("tag_name")
    url = data.get("html_url")
    if not tag:
        return _UNRELEASED, None
    return str(tag), str(url) if url else None


def _is_newer(latest: str, current: str) -> bool:
    """True when `latest` is a release newer than the running `current`.

    'Newer' is a simple inequality (the appliance only ever moves forward), gated
    on two guards:
      * `latest` must be a real release - never 'unreleased'/empty (a forked repo
        with no tags returns 'unreleased').
      * `current` must itself look like a release tag. An image built from main
        (VERSION="main"), a local dev run ("dev"), or a bare-SHA build isn't
        comparable to a vX.Y.Z release, so it's never considered out of date.
        This is what stops every `:latest` user seeing a permanent, false
        "update available".
    """
    if latest == _UNRELEASED or not latest:
        return False
    if not _looks_like_release(current):
        return False
    return latest != current


def is_update_available(latest: str | None, current: str) -> bool:
    """Whether a release newer than the running `current` is available.

    Public wrapper over `_is_newer` so the scheduler-driven check and the
    /api/update-status endpoint apply one identical rule.
    """
    return _is_newer(latest or "", current)


def check_for_updates() -> dict[str, str | bool | None]:
    """Query the GitHub releases API and persist any change.

    Returns a status dict suitable for the /api/update-status response.
    Designed to run from a scheduler job (takes JOB_LOCK internally to avoid
    racing with config writes).
    """
    cur = current_version()
    cfg = load_config()

    if not cfg.get("update_check_enabled", True):
        return {
            "current_version": cur,
            "latest_version": cfg.get("update_latest_version"),
            "latest_url": cfg.get("update_latest_url"),
            "update_available": False,
            "last_check": cfg.get("update_last_check"),
            "enabled": False,
        }

    latest_tag: str | None = None
    latest_url: str | None = None
    error: str | None = None

    try:
        # No auth needed for public repos.
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
        if resp.status_code == 404:
            # Repo exists but has no releases — not an error.
            latest_tag, latest_url = _UNRELEASED, None
        elif resp.status_code == 403 and "rate limit" in (resp.text or "").lower():
            error = "GitHub rate-limited; will retry tomorrow"
            log.warning("update check rate-limited by GitHub API")
        elif resp.status_code >= 400:
            error = f"GitHub API returned {resp.status_code}"
            log.warning("update check: %s", error)
        else:
            latest_tag, latest_url = _parse_github_release(resp.json())
    except httpx.RequestError as exc:
        error = f"network error: {exc}"
        log.warning("update check: %s", error)

    with JOB_LOCK:
        updates: dict[str, object] = {"update_last_check": iso_now()}
        if latest_tag is not None:
            updates["update_latest_version"] = latest_tag
        if latest_url is not None:
            updates["update_latest_url"] = latest_url
        try:
            saved = update_config(**updates)
        except Exception:
            log.exception("could not persist update check result")
            saved = cfg

    available = _is_newer(latest_tag or "", cur) if latest_tag else False
    return {
        "current_version": cur,
        "latest_version": saved.get("update_latest_version"),
        "latest_url": saved.get("update_latest_url"),
        "update_available": available,
        "last_check": saved.get("update_last_check"),
        "enabled": True,
        "error": error,
    }
