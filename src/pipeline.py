"""Orchestrates discovery → download → upload → cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import config, downloader, uploader
from .uploader import QuotaExceeded

logger = logging.getLogger(__name__)


def new_summary() -> dict:
    return {
        "found": 0,
        "uploaded": 0,
        "failed": 0,
        "skipped": 0,
        "started_at": None,
        "finished_at": None,
        "aborted": None,
    }


def run_relay() -> dict:
    """Blocking. Call it from a worker thread, never directly on the event loop."""
    summary = new_summary()
    summary["started_at"] = datetime.now(timezone.utc)

    try:
        videos = downloader.fetch_new_videos(config.SOURCE_CHANNEL_ID)
        summary["found"] = len(videos)

        if not videos:
            logger.info("No new videos to relay")
            return summary

        for video in videos:
            logger.info("Processing %s — %s", video["id"], video["title"])

            file_path = downloader.download_video(video)
            if file_path is None:
                # Leave it unmarked: a transient failure should retry next run.
                summary["failed"] += 1
                continue

            try:
                video_id = uploader.upload_video(file_path, video)
            except QuotaExceeded:
                summary["aborted"] = "YouTube API quota exceeded"
                summary["skipped"] = summary["found"] - summary["uploaded"] - summary["failed"]
                downloader.cleanup(file_path)
                return summary

            if video_id:
                summary["uploaded"] += 1
                downloader.mark_processed(video["id"])
            else:
                summary["failed"] += 1

            downloader.cleanup(file_path)

        return summary

    except Exception as exc:  # noqa: BLE001 — surfaced to Telegram, never kills the service
        logger.exception("Relay run failed")
        summary["aborted"] = f"{type(exc).__name__}: {exc}"
        return summary

    finally:
        summary["finished_at"] = datetime.now(timezone.utc)


def format_summary(summary: dict, header: str = "Relay complete") -> str:
    lines = [
        header,
        f"  Found:    {summary['found']}",
        f"  Uploaded: {summary['uploaded']}",
        f"  Failed:   {summary['failed']}",
    ]
    if summary.get("skipped"):
        lines.append(f"  Skipped:  {summary['skipped']}")
    if summary.get("started_at") and summary.get("finished_at"):
        elapsed = (summary["finished_at"] - summary["started_at"]).total_seconds()
        lines.append(f"  Duration: {elapsed:.0f}s")
    if summary.get("aborted"):
        lines.append(f"  ABORTED:  {summary['aborted']}")
    return "\n".join(lines)
