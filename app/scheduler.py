"""APScheduler setup for the recurring jobs.

- Brewfather sync: every SYNC_INTERVAL_MINUTES (default 15; env-configurable).
- Update check: once per day at 03:00 local time (polls GitHub for new releases).
- Archive cleanup: once per day at 03:30 local time.

All jobs take JOB_LOCK internally, so even though APScheduler runs them on a
thread pool they never interleave file writes. max_instances=1 prevents a slow
job from stacking up.

Brewfather's limit is 500 calls/hour/key. With the complete=True paginated
fetch, each sync costs ceil(completed_batches / 50) calls, so even a 5-minute
interval stays comfortably under the limit for a normal cellar.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from .brewfather import run_sync
from .cleanup import run_cleanup
from .timezone import local_tzinfo
from .update_check import check_for_updates

log = logging.getLogger("taplist.scheduler")


def _sync_interval_minutes() -> int:
    try:
        return max(1, int(os.environ.get("SYNC_INTERVAL_MINUTES", "15")))
    except (TypeError, ValueError):
        return 15


SYNC_INTERVAL_MINUTES = _sync_interval_minutes()

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = local_tzinfo()
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _safe_sync,
        "interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="brewfather_sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _safe_update_check,
        "cron",
        hour=3,
        minute=0,
        id="update_check",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _safe_cleanup,
        "cron",
        hour=3,
        minute=30,
        id="archive_cleanup",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("scheduler started (sync every %dm, update check 03:00, cleanup 03:30 %s)",
             SYNC_INTERVAL_MINUTES, tz)
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _safe_sync() -> None:
    try:
        run_sync()
    except Exception:  # noqa: BLE001 - never let a job crash the scheduler thread
        log.exception("unhandled error in sync job")


def _safe_cleanup() -> None:
    try:
        run_cleanup()
    except Exception:  # noqa: BLE001
        log.exception("unhandled error in cleanup job")


def _safe_update_check() -> None:
    try:
        check_for_updates()
    except Exception:  # noqa: BLE001
        log.exception("unhandled error in update check job")
