"""Orchestrates discovery → download → upload → cleanup."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, downloader, fb_uploader, uploader
from .fb_uploader import FacebookAuthError
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
        "fb_uploaded": 0,
        "fb_failed": 0,
        "fb_error": None,  # token/permission problem: Facebook skipped for the rest of the run
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


def _facebook_one(video: dict, file_path, summary: dict) -> bool:
    """Post one downloaded video to the Facebook Page. True if posted (or already there)."""
    if not config.FB_ENABLED or fb_uploader.is_done(video["id"]):
        return True
    if summary["fb_error"]:
        return False
    try:
        fb_id = fb_uploader.upload_video(file_path, video)
    except FacebookAuthError as exc:
        logger.error("Facebook auth problem — skipping Facebook this run: %s", exc)
        summary["fb_error"] = str(exc)
        fb_id = None
    if fb_id:
        fb_uploader.mark_done(video["id"])
        summary["fb_uploaded"] += 1
        return True
    summary["fb_failed"] += 1
    return False


def _relay_one(video: dict, summary: dict, failures: list[dict]) -> bool:
    """Download + upload one video (YouTube, then Facebook). Returns True if on YouTube.

    Raises QuotaExceeded (after the Facebook post and cleanup) so the caller can stop the run.
    """
    logger.info("Processing %s — %s", video["id"], video["title"])

    file_path = downloader.download_video(video, failures)
    if file_path is None:
        # Leave it unmarked: a transient failure should retry next run.
        summary["failed"] += 1
        return False

    quota_exc = None
    try:
        try:
            video_id = uploader.upload_video(file_path, video)
        except QuotaExceeded as exc:
            video_id, quota_exc = None, exc
        fb_ok = _facebook_one(video, file_path, summary)
    finally:
        downloader.cleanup(file_path)

    if video_id:
        summary["uploaded"] += 1
        downloader.mark_processed(video["id"])
        _record_upload()
        if not fb_ok:
            # Off the YouTube scan list now, so queue it for a Facebook-only retry.
            fb_uploader.add_pending(video)
    elif not quota_exc:
        summary["failed"] += 1
    # If YouTube failed, the video comes back next run; Facebook skips it if already posted.

    if quota_exc:
        raise quota_exc
    return bool(video_id)


def _retry_facebook_pending(summary: dict, failures: list[dict]) -> None:
    """Re-download and post videos that reached YouTube but not Facebook."""
    if not config.FB_ENABLED:
        return
    for video in fb_uploader.pending_videos()[: config.FB_RETRY_PER_RUN]:
        if summary["fb_error"]:
            return
        logger.info("Facebook retry %s — %s", video["id"], video["title"])
        file_path = downloader.download_video(video, failures)
        if file_path is None:
            summary["fb_failed"] += 1
            continue
        try:
            _facebook_one(video, file_path, summary)
        finally:
            downloader.cleanup(file_path)


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

        _retry_facebook_pending(summary, failures)

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


FB_FILL_DEFAULT = 3
FB_FILL_MAX = 10


def run_fb_fill(limit: int = FB_FILL_DEFAULT) -> dict:
    """Blocking. Post up to `limit` videos that are on YouTube but not yet on Facebook.

    Oldest first, so the Page fills in roughly chronological order.
    """
    result = {"posted": 0, "failed": 0, "remaining": 0, "error": None}
    if not config.FB_ENABLED:
        result["error"] = "Facebook is disabled (FB_ENABLED=false)"
        return result

    summary = new_summary()
    failures: list[dict] = []
    try:
        todo = [
            vid
            for vid in downloader.relayed_ids_oldest_first(config.SOURCE_CHANNEL_ID)
            if not fb_uploader.is_done(vid)
        ]
        for vid in todo[:limit]:
            video = downloader.fetch_video(vid, failures)
            file_path = downloader.download_video(video, failures) if video else None
            if file_path is None:
                summary["fb_failed"] += 1
                if failures and failures[-1]["bot_check"]:
                    result["error"] = "YouTube bot-check: cookies likely expired (runbook 2b)"
                    break
                continue
            try:
                _facebook_one(video, file_path, summary)
            finally:
                downloader.cleanup(file_path)
            if summary["fb_error"]:
                result["error"] = f"Facebook token/permission: {summary['fb_error']}"
                break
        result["remaining"] = sum(1 for vid in todo if not fb_uploader.is_done(vid))
    except Exception as exc:  # noqa: BLE001 — surfaced to Telegram
        logger.exception("Facebook fill failed")
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["posted"], result["failed"] = summary["fb_uploaded"], summary["fb_failed"]
    return result


def format_fb_fill(result: dict) -> str:
    lines = [
        "Facebook fill done",
        f"  Posted:    {result['posted']}",
        f"  Failed:    {result['failed']}",
        f"  Remaining: {result['remaining']}",
    ]
    if result.get("error"):
        lines.append(f"  ⚠️ {result['error']}")
    return "\n".join(lines)


def is_quiet(summary: dict) -> bool:
    """Nothing uploaded and nothing seriously wrong — not worth a Telegram message.

    Plain per-video failures don't count: one unavailable video would otherwise
    ping every check. They still show in the daily top-up report and /status.
    """
    return not (
        summary["uploaded"]
        or summary["aborted"]
        or summary["bot_check"]
        or summary.get("fb_uploaded")
        or summary.get("fb_error")
    )


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
    if config.FB_ENABLED:
        lines.append(
            f"  Facebook: {summary.get('fb_uploaded', 0)} posted, {summary.get('fb_failed', 0)} failed"
        )
        if summary.get("fb_error"):
            lines.append(f"  ⚠️ Facebook token/permission: {summary['fb_error']}")
    if summary.get("started_at") and summary.get("finished_at"):
        elapsed = (summary["finished_at"] - summary["started_at"]).total_seconds()
        lines.append(f"  Duration: {elapsed:.0f}s")
    if summary.get("bot_check"):
        lines.append("  ⚠️ YouTube bot-check: cookies likely expired, refresh them (runbook 2b)")
    if summary.get("aborted"):
        lines.append(f"  ABORTED:  {summary['aborted']}")
    return "\n".join(lines)
