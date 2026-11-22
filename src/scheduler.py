"""Registers the relay jobs on the telegram job_queue: frequent checks + a daily top-up."""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.ext import Application

from . import config
from .telegram_bot import expiry_reminder, scheduled_backfill, scheduled_check, watchdog

logger = logging.getLogger(__name__)


def _parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        # A slim base image or minimal distro may ship no IANA tz database.
        raise RuntimeError(
            f"Timezone '{config.TIMEZONE}' not found. Install the tz database: "
            "apt install tzdata  (or: pip install tzdata)"
        ) from exc


def register_jobs(app: Application) -> None:
    if app.job_queue is None:
        raise RuntimeError(
            "job_queue is unavailable. Install the extra: "
            'pip install "python-telegram-bot[job-queue]"'
        )

    tz = _tz()

    # New-video check. First run a minute after start, so a restart also catches up.
    app.job_queue.run_repeating(
        scheduled_check,
        interval=dt.timedelta(minutes=config.CHECK_INTERVAL_MINUTES),
        first=dt.timedelta(minutes=1),
        name="check_new",
    )

    if config.DAILY_UPLOAD_TARGET > 0:
        hour, minute = _parse_time(config.BACKFILL_TIME)
        app.job_queue.run_daily(
            scheduled_backfill,
            time=dt.time(hour, minute, tzinfo=tz),
            name="daily_topup",
        )

    hour, minute = _parse_time(config.EXPIRY_CHECK_TIME)
    app.job_queue.run_daily(expiry_reminder, time=dt.time(hour, minute, tzinfo=tz), name="expiry_reminder")
    app.job_queue.run_repeating(
        watchdog, interval=dt.timedelta(minutes=config.WATCHDOG_MINUTES),
        first=dt.timedelta(minutes=config.WATCHDOG_MINUTES), name="watchdog",
    )

    logger.info(
        "Checking for new videos every %d min; daily top-up to %d at %s (%s)",
        config.CHECK_INTERVAL_MINUTES,
        config.DAILY_UPLOAD_TARGET,
        config.BACKFILL_TIME,
        config.TIMEZONE,
    )
