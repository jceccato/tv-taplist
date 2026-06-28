"""APScheduler setup for the two recurring jobs.

- Brewfather sync: every 10 minutes.
- Archive cleanup: once per day at 03:30 local time.

Both jobs already take JOB_LOCK internally, so even though APScheduler runs them
on a thread pool they never interleave file writes. max_instances=1 prevents a
slow sync from stacking up.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .brewfather import run_sync
from .cleanup import run_cleanup
from .timezone import local_tzinfo

log = logging.getLogger("taplist.scheduler")

SYNC_INTERVAL_MINUTES = 10

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
        _safe_cleanup,
        "cron",
        hour=3,
        minute=30,
        id="archive_cleanup",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("scheduler started (sync every %dm, cleanup daily 03:30 %s)",
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
