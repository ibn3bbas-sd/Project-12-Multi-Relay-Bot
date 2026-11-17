"""Orchestrates discovery → download → upload → cleanup."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, downloader, uploader
from .uploader import QuotaExceeded

logger = logging.getLogger(__name__)


def new_summary() -> dict:
    return {
        "found": 0,
        "uploaded": 0,
        "failed": 0,
        "started_at": None,
        "finished_at": None,
        "aborted": None,
        "bot_check": False,
        "backfilled": 0,
        "today": None,  # uploads in the current quota day, set at the end of a run
    }


# ── Per-quota-day upload counter ─────────────────────────────────────────────
def _quota_day() -> str:
    return datetime.now(ZoneInfo(config.QUOTA_TIMEZONE)).date().isoformat()


def _load_counts() -> dict[str, int]:
    try:
        with config.UPLOAD_COUNTS_FILE.open(encoding="utf-8") as fh:
            return {k: int(v) for k, v in json.load(fh).items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, AttributeError):
        return {}


def uploads_today() -> int:
    return _load_counts().get(_quota_day(), 0)


def _record_upload() -> None:
    counts = _load_counts()
    day = _quota_day()
    counts[day] = counts.get(day, 0) + 1
    counts = dict(sorted(counts.items())[-30:])  # keep a month of history
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.UPLOAD_COUNTS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(counts, fh, indent=2)
    tmp.replace(config.UPLOAD_COUNTS_FILE)


def _relay_one(video: dict, summary: dict, failures: list[dict]) -> bool:
    """Download + upload one video. Returns True if uploaded.

    Raises QuotaExceeded (after cleanup) so the caller can stop the run.
    """
    logger.info("Processing %s — %s", video["id"], video["title"])

    file_path = downloader.download_video(video, failures)
    if file_path is None:
        # Leave it unmarked: a transient failure should retry next run.
        summary["failed"] += 1
        return False

    try:
        video_id = uploader.upload_video(file_path, video)
    finally:
        downloader.cleanup(file_path)

    if not video_id:
        summary["failed"] += 1
        return False
    summary["uploaded"] += 1
    downloader.mark_processed(video["id"])
    _record_upload()
    return True


def _backfill(summary: dict, failures: list[dict], tried: set[str]) -> None:
    """Top up today's uploads to DAILY_UPLOAD_TARGET with older source videos."""
    missing = config.DAILY_UPLOAD_TARGET - uploads_today()
    if missing <= 0:
        logger.info("Daily target of %d already met — no backfill", config.DAILY_UPLOAD_TARGET)
        return

    logger.info("Backfilling %d older video(s)", missing)
    attempts_left = missing + 3  # don't grind through the archive if things keep failing
    for video in downloader.iter_backfill_videos(config.SOURCE_CHANNEL_ID, tried, failures):
        if missing <= 0 or attempts_left <= 0:
            break
        attempts_left -= 1
        summary["found"] += 1
        if video is None:  # metadata fetch failed
            summary["failed"] += 1
            if failures and failures[-1]["bot_check"]:
                break  # cookies are dead; every further fetch would fail the same way
            continue
        if _relay_one(video, summary, failures):
            summary["backfilled"] += 1
            missing -= 1


def run_relay(backfill: bool = False) -> dict:
    """Blocking. Call it from a worker thread, never directly on the event loop.

    backfill=True also tops the day up to DAILY_UPLOAD_TARGET with older videos.
    """
    summary = new_summary()
    summary["started_at"] = datetime.now(timezone.utc)

    failures: list[dict] = []  # fetch/download errors; unmarked, so retried next run
    try:
        videos = downloader.fetch_new_videos(config.SOURCE_CHANNEL_ID, failures)
        fetch_failures = len(failures)
        summary["found"] = len(videos) + fetch_failures
        summary["failed"] = fetch_failures

        if not videos:
            logger.info("No new videos to relay")

        for video in videos:
            _relay_one(video, summary, failures)

        if backfill and config.DAILY_UPLOAD_TARGET > 0:
            tried = {v["id"] for v in videos} | {f["id"] for f in failures}
            _backfill(summary, failures, tried)

        return summary

    except QuotaExceeded:
        summary["aborted"] = "YouTube API quota exceeded"
        return summary

    except Exception as exc:  # noqa: BLE001 — surfaced to Telegram, never kills the service
        logger.exception("Relay run failed")
        summary["aborted"] = f"{type(exc).__name__}: {exc}"
        return summary

    finally:
        summary["bot_check"] = any(f["bot_check"] for f in failures)
        summary["today"] = uploads_today()
        summary["finished_at"] = datetime.now(timezone.utc)


def is_quiet(summary: dict) -> bool:
    """Nothing uploaded and nothing seriously wrong — not worth a Telegram message.

    Plain per-video failures don't count: one unavailable video would otherwise
    ping every check. They still show in the daily top-up report and /status.
    """
    return not (summary["uploaded"] or summary["aborted"] or summary["bot_check"])


def format_summary(summary: dict, header: str = "Relay complete") -> str:
    lines = [
        header,
        f"  Found:    {summary['found']}",
        f"  Uploaded: {summary['uploaded']}",
        f"  Failed:   {summary['failed']}",
    ]
    if summary.get("backfilled"):
        lines.append(f"  (older videos: {summary['backfilled']})")
    if summary.get("today") is not None and config.DAILY_UPLOAD_TARGET > 0:
        lines.append(f"  Today:    {summary['today']}/{config.DAILY_UPLOAD_TARGET}")
    if summary.get("started_at") and summary.get("finished_at"):
        elapsed = (summary["finished_at"] - summary["started_at"]).total_seconds()
        lines.append(f"  Duration: {elapsed:.0f}s")
    if summary.get("bot_check"):
        lines.append("  ⚠️ YouTube bot-check: cookies likely expired, refresh them (runbook 2b)")
    if summary.get("aborted"):
        lines.append(f"  ABORTED:  {summary['aborted']}")
    return "\n".join(lines)
