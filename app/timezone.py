"""Timezone-aware 'now', honouring the TZ env var.

Archive timestamps and the daily cleanup boundary must use the container's
configured timezone, not UTC. The Docker image sets the system clock from TZ
(via tzdata + /etc/localtime), so datetime.now().astimezone() yields local time
with the correct offset. We resolve the zone explicitly from TZ where possible
so behaviour is correct even if /etc/localtime was not configured.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python <3.9, not expected on 3.12
    ZoneInfo = None  # type: ignore[assignment]


def local_tzinfo() -> tzinfo:
    """Resolve the configured local timezone from TZ, falling back sensibly."""
    tz_name = os.environ.get("TZ")
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 - bad TZ string; fall through
            pass
    # Fall back to the system local zone, then UTC.
    local = datetime.now().astimezone().tzinfo
    return local or timezone.utc


def now_local() -> datetime:
    """Current time as a timezone-aware datetime in the configured zone."""
    return datetime.now(local_tzinfo())


def iso_now() -> str:
    """ISO8601 timestamp in local time, used for status fields."""
    return now_local().isoformat(timespec="seconds")
