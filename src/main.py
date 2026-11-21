"""Entry point. Run with: python -m src.main"""

from __future__ import annotations

import datetime as dt
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import requests

from . import config
from .pipeline import format_summary, is_quiet, run_relay
from .scheduler import register_jobs
from .telegram_bot import build_app, send_offline_reports


def setup_logging() -> None:
    # Directories first — a FileHandler against a missing folder kills startup,
    # which under a Windows service means a silent restart loop.
    config.ensure_dirs()

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # These two are chatty at INFO and drown out the relay's own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


TELEGRAM_RETRY_SECONDS = 60


def _telegram_ok() -> bool:
    """True when the Bot API answers getMe for our token."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe", timeout=30
        )
        return bool(resp.json().get("ok"))
    except (requests.RequestException, ValueError):
        return False


def run_without_telegram_until_back() -> list[str]:
    """Keep the relay schedule running while the Telegram Bot API is down.

    Telegram has had per-bot outages (502/timeouts on getMe for hours). Without
    this the bot would crash-loop and nothing would upload. Runs the same jobs on
    a plain scheduler, retries Telegram every minute, and returns the reports
    worth sending once Telegram is back. Returns at once if Telegram is up.
    """
    logger = logging.getLogger(__name__)
    if _telegram_ok():
        return []

    from apscheduler.schedulers.background import BackgroundScheduler

    from .scheduler import _parse_time, _tz

    logger.warning("Telegram Bot API unreachable — relaying without Telegram, retrying every minute")
    reports: list[str] = []
    lock = threading.Lock()  # one relay at a time, same as _relay_lock

    def job(backfill: bool, header: str) -> None:
        with lock:
            summary = run_relay(backfill)
        if backfill or not is_quiet(summary):
            reports.append(format_summary(summary, f"{header} (while Telegram was down)"))

    sched = BackgroundScheduler(timezone=_tz())
    sched.add_job(job, "interval", args=(False, "Scheduled check"), id="check_new",
                  minutes=config.CHECK_INTERVAL_MINUTES, max_instances=1, coalesce=True,
                  next_run_time=dt.datetime.now(_tz()) + dt.timedelta(minutes=1))
    if config.DAILY_UPLOAD_TARGET > 0:
        hour, minute = _parse_time(config.BACKFILL_TIME)
        sched.add_job(job, "cron", args=(True, "Daily top-up done"), id="daily_topup",
                      hour=hour, minute=minute, max_instances=1, coalesce=True)
    sched.start()

    while not _telegram_ok():
        time.sleep(TELEGRAM_RETRY_SECONDS)

    logger.info("Telegram is back — waiting for any running relay, then starting the bot")
    sched.shutdown(wait=True)  # a relay in progress finishes first: no overlap with the bot's jobs
    return reports


def main() -> int:
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("YouTube Relay Bot starting (base dir: %s)", config.BASE_DIR)
    logger.info("Source channel: %s → target: %s", config.SOURCE_CHANNEL_ID, config.TARGET_CHANNEL_ID)

    reports = run_without_telegram_until_back()

    app = build_app()
    register_jobs(app)
    if reports:
        app.bot_data["offline_reports"] = reports
        app.job_queue.run_once(send_offline_reports, when=5, name="offline_reports")

    app.run_polling(drop_pending_updates=True, stop_signals=None)
    logger.info("YouTube Relay Bot stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
