"""Admin authentication: signed session cookie, login rate-limiting, proxy IPs.

- The session token is signed with SESSION_SECRET (itsdangerous), so sessions
  survive container restarts and cannot be forged without the secret.
- The cookie is HttpOnly + SameSite=Strict, and Secure when the original
  request arrived over HTTPS (detected via X-Forwarded-Proto from the trusted
  proxy).
- Login attempts are rate-limited per client IP. The client IP is taken from
  X-Forwarded-For *only* for requests coming from a trusted proxy IP; otherwise
  the socket peer is used, so a directly-reachable container cannot be spoofed.
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger("taplist.auth")

COOKIE_NAME = "taplist_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

# Rate limit: lock out after this many failures within the window.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes


def _admin_password() -> str:
    return os.environ.get("ADMIN_PASSWORD", "")


def _demo_mode() -> bool:
    """True when DEMO_MODE is enabled (mirrors the parsing in app/demo.py)."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def demo_admin_open() -> bool:
    """True when the admin is intentionally open: demo mode AND no password set.

    A pure evaluation convenience so the single-command demo needs zero login. The
    instant an ADMIN_PASSWORD is configured, normal signed-cookie auth applies --
    so production (and any box with a password) is unaffected, and the non-demo
    no-password case stays fail-closed.
    """
    return _demo_mode() and not _admin_password()


def _session_secret() -> str:
    # Fall back to ADMIN_PASSWORD so the app still boots if SESSION_SECRET is
    # unset, but log loudly - sessions then rotate if the password changes.
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        log.warning("SESSION_SECRET not set; deriving from ADMIN_PASSWORD")
        secret = "taplist-fallback::" + _admin_password()
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="taplist-admin-session")


def _trusted_proxies() -> set[str]:
    raw = os.environ.get("FORWARDED_ALLOW_IPS", "")
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def client_ip(request: Request) -> str:
    """Resolve the real client IP, trusting XFF only from a known proxy."""
    peer = request.client.host if request.client else "unknown"
    trusted = _trusted_proxies()
    if peer in trusted or "*" in trusted:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Left-most entry is the original client.
            return xff.split(",")[0].strip()
    return peer


def request_is_https(request: Request) -> bool:
    """True if the original client request used HTTPS (per the trusted proxy)."""
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


# ---- rate limiting (in-memory, per IP) -----------------------------------

@dataclass
class _Attempts:
    count: int = 0
    first_ts: float = field(default_factory=time.time)


_failed: dict[str, _Attempts] = {}


def _prune_expired(now: float) -> None:
    """Drop fully-expired windows so the map can't grow unbounded over time.

    Only IPs that failed within the last window are ever retained. Cheap: the map
    only holds IPs that have failed recently, and this runs on failures.
    """
    for ip in [ip for ip, rec in _failed.items() if now - rec.first_ts > LOCKOUT_WINDOW_SECONDS]:
        _failed.pop(ip, None)


def is_locked_out(ip: str) -> bool:
    rec = _failed.get(ip)
    if rec is None:
        return False
    if time.time() - rec.first_ts > LOCKOUT_WINDOW_SECONDS:
        # Window expired; reset.
        _failed.pop(ip, None)
        return False
    return rec.count >= MAX_FAILED_ATTEMPTS


def record_failure(ip: str) -> None:
    now = time.time()
    _prune_expired(now)
    rec = _failed.get(ip)
    if rec is None or now - rec.first_ts > LOCKOUT_WINDOW_SECONDS:
        _failed[ip] = _Attempts(count=1, first_ts=now)
    else:
        rec.count += 1


def record_success(ip: str) -> None:
    _failed.pop(ip, None)


# ---- session cookie ------------------------------------------------------

def verify_password(candidate: str) -> bool:
    """Constant-ish time comparison against ADMIN_PASSWORD."""
    expected = _admin_password()
    if not expected:
        # No password configured: deny all admin access (fail closed).
        return False
    return hmac.compare_digest(candidate, expected)


def issue_session(response: Response, request: Request) -> None:
    """Set a signed session cookie on the response."""
    token = _serializer().dumps({"admin": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=request_is_https(request),
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def has_valid_session(request: Request) -> bool:
    # Demo convenience: an unconfigured demo box (DEMO_MODE + no ADMIN_PASSWORD)
    # opens the admin transparently - this makes require_admin pass, the /admin
    # dashboard render, and /admin/login redirect straight through.
    if demo_admin_open():
        return True
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return bool(data.get("admin"))
    except (BadSignature, SignatureExpired):
        return False


def require_admin(request: Request) -> None:
    """FastAPI dependency: 401 unless a valid admin session is present."""
    if not has_valid_session(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin login required",
        )
