"""Source-channel discovery and video download via yt-dlp."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp

from . import config

logger = logging.getLogger(__name__)

# Partial/sidecar artefacts yt-dlp leaves behind — never treat these as the result.
_IGNORED_SUFFIXES = {".part", ".ytdl", ".temp", ".json", ".webp", ".jpg", ".png", ".vtt", ".srt"}


def _load_processed() -> set[str]:
    if not config.PROCESSED_FILE.exists():
        return set()
    try:
        with config.PROCESSED_FILE.open(encoding="utf-8") as fh:
            return set(json.load(fh))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s) — treating as empty", config.PROCESSED_FILE, exc)
        return set()


def _save_processed(video_ids: set[str]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.PROCESSED_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(sorted(video_ids), fh, indent=2)
    tmp.replace(config.PROCESSED_FILE)  # atomic — survives a service kill mid-write


def mark_processed(video_id: str) -> None:
    processed = _load_processed()
    processed.add(video_id)
    _save_processed(processed)


def _cookie_opts() -> dict:
    """yt-dlp options carrying the cookies file, if one is configured.

    yt-dlp writes rotated cookies back to the file it was given, and the
    Kubernetes Secret mount is read-only, so it works on a copy in DATA_DIR.
    The copy is refreshed whenever the source is newer (a replaced Secret),
    and otherwise kept so YouTube's rotations survive restarts.
    """
    src = config.YTDLP_COOKIES_FILE
    if src is None:
        return {}
    if not src.is_file():
        logger.warning("YTDLP_COOKIES_FILE %s not found — running without cookies", src)
        return {}

    work = config.DATA_DIR / "ytdlp-cookies.txt"
    try:
        if not work.exists() or src.stat().st_mtime > work.stat().st_mtime:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            work.write_bytes(src.read_bytes())
            work.chmod(0o600)
            logger.info("Loaded yt-dlp cookies from %s", src)
    except OSError as exc:
        logger.warning("Could not stage cookies (%s) — using %s directly", exc, src)
        return {"cookiefile": str(src)}
    return {"cookiefile": str(work)}


def _published_at(info: dict) -> datetime | None:
    """Best-effort publish time in UTC."""
    ts = info.get("timestamp") or info.get("release_timestamp")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    date_str = info.get("upload_date") or info.get("release_date")
    if date_str:
        try:
            return datetime.strptime(str(date_str), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _list_candidate_ids(channel_id: str) -> list[str]:
    """Flat listing of the channel's newest uploads — cheap, IDs only.

    `extract_flat` deliberately skips per-video metadata, which is why the
    publish date is NOT available here. Dates come from _fetch_metadata().
    """
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": config.SCAN_LIMIT,
        "ignoreerrors": True,
        **_cookie_opts(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}

    ids: list[str] = []
    for entry in info.get("entries") or []:
        if entry and entry.get("id"):
            ids.append(entry["id"])
    return ids


def is_bot_check(exc: BaseException) -> bool:
    """YouTube's "Sign in to confirm you're not a bot" — means the cookies need a refresh."""
    return "not a bot" in str(exc).lower()


def _fetch_metadata(video_id: str, failures: list[dict] | None = None) -> dict | None:
    """Full metadata for one video — this is where upload_date actually exists."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, **_cookie_opts()}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises a wide family
        logger.warning("Metadata fetch failed for %s: %s", video_id, exc)
        if failures is not None:
            failures.append({"id": video_id, "bot_check": is_bot_check(exc)})
        return None


def fetch_new_videos(channel_id: str, failures: list[dict] | None = None) -> list[dict]:
    """Unprocessed videos published within MAX_VIDEO_AGE_DAYS, oldest first.

    Videos whose metadata could not be fetched are appended to `failures` (if given)
    so the caller can report them; they stay unprocessed and are retried next run.
    """
    if not channel_id:
        logger.error("SOURCE_CHANNEL_ID is empty — nothing to scan")
        return []

    processed = _load_processed()
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_VIDEO_AGE_DAYS)

    candidates = [vid for vid in _list_candidate_ids(channel_id) if vid not in processed]
    logger.info(
        "Channel scan: %d listed, %d not yet processed", config.SCAN_LIMIT, len(candidates)
    )

    new_videos: list[dict] = []
    for video_id in candidates:
        info = _fetch_metadata(video_id, failures)
        if not info:
            continue

        published = _published_at(info)
        if published is None:
            logger.warning("No publish date for %s — skipping (will retry next run)", video_id)
            continue
        if published < cutoff:
            logger.debug("%s published %s, older than cutoff — skipping", video_id, published.date())
            continue

        new_videos.append(
            {
                "id": video_id,
                "title": info.get("title") or "",
                "description": info.get("description") or "",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "channel_name": info.get("channel") or info.get("uploader") or "",
                "published_at": published,
                "duration": info.get("duration"),
            }
        )

    new_videos.sort(key=lambda v: v["published_at"])  # relay in chronological order

    if config.MAX_UPLOADS_PER_RUN > 0:
        new_videos = new_videos[: config.MAX_UPLOADS_PER_RUN]

    logger.info("Found %d new video(s) to relay", len(new_videos))
    return new_videos


def download_video(video: dict, failures: list[dict] | None = None) -> Path | None:
    """Download to DOWNLOAD_DIR. Returns the media file path, or None on failure."""
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": config.VIDEO_QUALITY,
        "outtmpl": str(config.DOWNLOAD_DIR / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noprogress": True,  # service logs: no ANSI progress bar spam
        "retries": 5,
        "fragment_retries": 5,
        **_cookie_opts(),
    }
    if config.FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = config.FFMPEG_LOCATION

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video["url"]])
    except Exception as exc:  # noqa: BLE001
        logger.error("Download failed for %s: %s", video["id"], exc)
        if failures is not None:
            failures.append({"id": video["id"], "bot_check": is_bot_check(exc)})
        return None

    matches = [
        p
        for p in config.DOWNLOAD_DIR.glob(f"{video['id']}.*")
        if p.is_file() and p.suffix.lower() not in _IGNORED_SUFFIXES
    ]
    if not matches:
        logger.error("Download reported success but no media file found for %s", video["id"])
        return None

    result = max(matches, key=lambda p: p.stat().st_size)
    logger.info("Downloaded %s → %s (%.1f MB)", video["id"], result.name, result.stat().st_size / 1e6)
    return result


def cleanup(file_path: Path) -> None:
    """Remove the downloaded file and any leftover fragments for that video ID."""
    if config.KEEP_AFTER_UPLOAD:
        return
    stem = file_path.stem
    for leftover in config.DOWNLOAD_DIR.glob(f"{stem}.*"):
        try:
            leftover.unlink()
        except OSError as exc:
            logger.debug("Could not delete %s: %s", leftover, exc)
