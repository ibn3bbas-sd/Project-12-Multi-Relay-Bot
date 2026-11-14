"""Registers the daily relay jobs on the telegram job_queue."""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.ext import Application

from . import config
from .telegram_bot import scheduled_relay

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
    for name, value in (
        ("relay_morning", config.SCHEDULE_MORNING),
        ("relay_evening", config.SCHEDULE_EVENING),
    ):
        hour, minute = _parse_time(value)
        app.job_queue.run_daily(
            scheduled_relay,
            time=dt.time(hour, minute, tzinfo=tz),
            name=name,
        )

    logger.info(
        "Scheduled relay at %s and %s (%s)",
        config.SCHEDULE_MORNING,
        config.SCHEDULE_EVENING,
        config.TIMEZONE,
    )
