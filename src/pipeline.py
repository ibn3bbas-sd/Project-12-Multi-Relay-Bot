"""Orchestrates discovery → download → upload → cleanup."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, downloader, fb_story_uploader, fb_uploader, health, ig_uploader, stats_db, tiktok_uploader, uploader, x_uploader
from .fb_uploader import FacebookAuthError
from .fb_story_uploader import FacebookStoryLimit
from .fb_uploader import FacebookAuthError as FacebookStoryAuthError
from .ig_uploader import InstagramAuthError, InstagramLimit
from .tiktok_uploader import TikTokAuthError, TikTokLimit
from .uploader import QuotaExceeded
from .x_uploader import XAuthError, XLimit

logger = logging.getLogger(__name__)

SEP = "━━━━━━━━━━━━━━"  # Telegram message divider


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
        "fb_reels": 0,
        "fb_deferred": 0,  # held back by warm-up pacing; queued for a later run
        "fb_paced": None,  # last pacing reason, for the report
        "fb_error": None,  # token/permission problem: Facebook skipped for the rest of the run
        "ig_posted": 0,
        "ig_failed": 0,
        "ig_paced": None,  # why Instagram waited this run (cap, spacing, 24h API limit)
        "ig_error": None,  # token/permission problem
        "fbs_posted": 0,
        "fbs_failed": 0,
        "fbs_paced": None,
        "fbs_error": None,  # Page token/permission problem
        "x_posted": 0,
        "x_failed": 0,
        "x_paced": None,
        "x_error": None,  # keys/permission/credit problem
        "tt_posted": 0,
        "tt_failed": 0,
        "tt_paced": None,
        "tt_error": None,  # token/app/permission problem
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


def _facebook_one(video: dict, file_path, summary: dict, ignore_gap: bool = False) -> bool:
    """Post one downloaded video to the Facebook Page. True if posted (or already there).

    False also when pacing holds it back (counted as deferred, not failed); the
    caller queues it so a later run posts it.
    """
    if not config.FB_ENABLED or fb_uploader.is_done(video["id"]):
        return True
    if summary["fb_error"]:
        return False
    block = fb_uploader.pacing_block(ignore_gap=ignore_gap)
    if block:
        logger.info("Facebook post for %s deferred — %s", video["id"], block)
        summary["fb_deferred"] += 1
        summary["fb_paced"] = block
        return False
    kind = "video"
    try:
        fb_id, kind = fb_uploader.upload_video(file_path, video)
    except FacebookAuthError as exc:
        logger.error("Facebook auth problem — skipping Facebook this run: %s", exc)
        summary["fb_error"] = str(exc)
        fb_id = None
    if fb_id:
        stats_db.record("facebook", "posted", video, kind=kind, ref=str(fb_id))
        fb_uploader.mark_done(video["id"])
        summary["fb_uploaded"] += 1
        if kind == "reel":
            summary["fb_reels"] += 1
        return True
    stats_db.record("facebook", "failed", video, detail=summary["fb_error"])
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
        stats_db.record("youtube", "failed", video, detail="download failed")
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
        stats_db.record("youtube", "posted", video, kind="video", ref=str(video_id))
        summary["uploaded"] += 1
        downloader.mark_processed(video["id"])
        _record_upload()
        if not fb_ok:
            # Off the YouTube scan list now, so queue it for a Facebook-only retry.
            fb_uploader.add_pending(video)
    elif not quota_exc:
        stats_db.record("youtube", "failed", video, detail="upload failed")
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
        block = fb_uploader.pacing_block()
        if block:  # check before re-downloading, not after
            summary["fb_paced"] = block
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


def _shorts_targets() -> dict[str, dict]:
    """Shorts-only targets. Read at call time so config stays the single source."""
    return {
        "ig": {
            "platform": "instagram",
            "name": "Instagram", "short": "IG", "icon": "📸", "mod": ig_uploader,
            "enabled": config.IG_ENABLED, "flag": "IG_ENABLED",
            "auth_exc": InstagramAuthError, "limit_exc": InstagramLimit,
            "limit_text": "Instagram 24h API publishing limit",
            "max_seconds": config.IG_MAX_SECONDS, "scan_limit": config.IG_SCAN_LIMIT,
            "topup": config.IG_TOPUP,
        },
        "fbs": {
            "platform": "fb_story",
            "name": "Facebook Stories", "short": "FBS", "icon": "📖", "mod": fb_story_uploader,
            "enabled": config.FBS_ENABLED, "flag": "FBS_ENABLED",
            "auth_exc": FacebookStoryAuthError, "limit_exc": FacebookStoryLimit,
            "limit_text": "Facebook API rate limit",
            "max_seconds": config.FBS_MAX_SECONDS, "scan_limit": config.FBS_SCAN_LIMIT,
            "topup": config.FBS_TOPUP,
        },
        "x": {
            "platform": "x",
            "name": "X", "short": "X", "icon": "✖️", "mod": x_uploader,
            "enabled": config.X_ENABLED, "flag": "X_ENABLED",
            "auth_exc": XAuthError, "limit_exc": XLimit,
            "limit_text": "X API rate limit (429)",
            "max_seconds": config.X_MAX_SECONDS, "scan_limit": config.X_SCAN_LIMIT,
            "topup": config.X_TOPUP,
        },
        "tt": {
            "platform": "tiktok",
            "name": "TikTok", "short": "TT", "icon": "🎵", "mod": tiktok_uploader,
            "enabled": config.TIKTOK_ENABLED, "flag": "TIKTOK_ENABLED",
            "auth_exc": TikTokAuthError, "limit_exc": TikTokLimit,
            "limit_text": "TikTok posting/rate limit", "limit_detail": True,
            "max_seconds": config.TIKTOK_MAX_SECONDS, "scan_limit": config.TIKTOK_SCAN_LIMIT,
            "topup": config.TIKTOK_TOPUP,
        },
    }


def _post_shorts(key: str, summary: dict, failures: list[dict], limit: int = 1, manual: bool = False) -> None:
    """Post up to `limit` source Shorts to one Shorts target, newest first.

    Scheduled runs post at most one, so the target's MIN_GAP spreads the day's
    posts out. New Shorts come first; with TOPUP (or /igfill, /fbsfill, /xfill, /ttfill) older Shorts
    fill the rest of the daily cap. Unposted Shorts are simply picked up again
    by a later run, so there is no retry queue.
    """
    t = _shorts_targets()[key]
    mod = t["mod"]
    if not t["enabled"]:
        return
    block = mod.pacing_block(ignore_gap=manual)
    if block:
        summary[f"{key}_paced"] = block
        return

    deep = manual or t["topup"]
    ids = downloader.list_short_ids(
        config.SOURCE_CHANNEL_ID, config.BACKFILL_SCAN_LIMIT if deep else t["scan_limit"]
    )
    todo = [vid for vid in ids if not mod.is_handled(vid)]
    logger.info("%s: %d Shorts listed, %d not posted yet", t["name"], len(ids), len(todo))
    cutoff = datetime.now(timezone.utc).timestamp() - config.MAX_VIDEO_AGE_DAYS * 86400
    posted, attempts_left = 0, limit + 3

    for vid in todo:
        if posted >= limit or attempts_left <= 0:
            break
        block = mod.pacing_block(ignore_gap=manual or posted == 0)
        if block:
            summary[f"{key}_paced"] = block
            break
        attempts_left -= 1
        video = downloader.fetch_video(vid, failures)
        if video is None:
            summary[f"{key}_failed"] += 1
            if failures and failures[-1]["bot_check"]:
                break
            continue
        published = video.get("published_at")
        if not deep and published and published.timestamp() < cutoff:
            break  # newest first: everything after this is older still
        if (video.get("duration") or 0) > t["max_seconds"]:
            logger.info("%s: Short %s skipped — too long (%ss > %ss)", t["name"], vid,
                        video["duration"], t["max_seconds"])
            mod.mark_skipped(vid, f"too long ({video['duration']}s)")
            stats_db.record(t["platform"], "skipped", video, detail=f"too long ({video['duration']}s)")
            continue
        file_path = downloader.download_video(video, failures)
        if file_path is None:
            summary[f"{key}_failed"] += 1
            if failures and failures[-1]["bot_check"]:
                break
            continue
        mod.LAST_ERROR = None
        try:
            media_id = mod.upload_short(file_path, video)
        except t["auth_exc"] as exc:
            logger.error("%s auth problem — skipping %s this run: %s", t["name"], t["name"], exc)
            summary[f"{key}_error"] = str(exc)
            stats_db.record(t["platform"], "failed", video, detail=str(exc))
            break
        except t["limit_exc"] as exc:
            logger.warning("%s limit reached: %s", t["name"], exc)
            summary[f"{key}_paced"] = str(exc) if t.get("limit_detail") else t["limit_text"]
            break
        finally:
            downloader.cleanup(file_path)
        if media_id:
            mod.mark_done(vid)
            stats_db.record(t["platform"], "posted", video, kind="short", ref=str(media_id))
            summary[f"{key}_posted"] += 1
            posted += 1
        elif not mod.is_handled(vid):  # skipped clips aren't failures
            stats_db.record(t["platform"], "failed", video, detail=getattr(mod, "LAST_ERROR", None))
            summary[f"{key}_failed"] += 1
        else:
            stats_db.record(t["platform"], "skipped", video, detail=mod._load_state()["skipped"].get(vid))


SHORTS_FILL_DEFAULT = 1
SHORTS_FILL_MAX = 5


def run_shorts_fill(key: str, limit: int = SHORTS_FILL_DEFAULT) -> dict:
    """Blocking. Post up to `limit` Shorts to one target now (spacing ignored, daily cap kept)."""
    t = _shorts_targets()[key]
    summary = new_summary()
    failures: list[dict] = []
    error = None
    if not t["enabled"]:
        error = f"{t['name']} is disabled ({t['flag']}=false)"
    else:
        try:
            _post_shorts(key, summary, failures, limit=limit, manual=True)
        except Exception as exc:  # noqa: BLE001 — surfaced to Telegram
            logger.exception("%s fill failed", t["name"])
            error = f"{type(exc).__name__}: {exc}"
    if summary[f"{key}_error"]:
        error = f"{t['name']} token/permission: {summary[f'{key}_error']}"
    elif any(f["bot_check"] for f in failures):
        error = "YouTube bot-check: cookies likely expired (runbook 2b)"
    return {"key": key, "posted": summary[f"{key}_posted"], "failed": summary[f"{key}_failed"],
            "paced": summary[f"{key}_paced"], "error": error}


def format_shorts_fill(result: dict) -> str:
    t = _shorts_targets()[result["key"]]
    if result.get("error") or result["failed"]:
        icon = "⚠️"
    elif not result["posted"] and result.get("paced"):
        icon = "⏳"
    else:
        icon = "✅"
    lines = [
        f"{icon} {t['icon']} {t['name']} fill done",
        SEP,
        f"📤 Posted: {result['posted']}   ❌ Failed: {result['failed']}",
        format_shorts_pacing(result["key"]),
    ]
    if result.get("paced"):
        lines.append(f"⏳ {result['paced']}")
    if result.get("error"):
        lines.append(f"⚠️ {result['error']}")
    return "\n".join(lines)


def format_shorts_pacing(key: str) -> str:
    t = _shorts_targets()[key]
    st = t["mod"].pacing_status()
    cap = "no cap" if st["cap"] is None else f"{st['today']}/{st['cap']}"
    return f"📅 Today {cap} · {st['done']} Shorts in total"


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
        _post_shorts("ig", summary, failures)
        _post_shorts("fbs", summary, failures)
        _post_shorts("x", summary, failures)
        _post_shorts("tt", summary, failures)

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
        stats_db.record_run(summary, "fill" if backfill else "check")
        health.ping_healthcheck(ok=not summary["aborted"])


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
            block = fb_uploader.pacing_block(ignore_gap=True)  # manual: cap applies, spacing doesn't
            if block:
                result["error"] = f"Paused by warm-up pacing ({block})"
                break
            video = downloader.fetch_video(vid, failures)
            file_path = downloader.download_video(video, failures) if video else None
            if file_path is None:
                summary["fb_failed"] += 1
                if failures and failures[-1]["bot_check"]:
                    result["error"] = "YouTube bot-check: cookies likely expired (runbook 2b)"
                    break
                continue
            try:
                _facebook_one(video, file_path, summary, ignore_gap=True)
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
    result["reels"] = summary["fb_reels"]
    return result


def format_fb_fill(result: dict) -> str:
    ok = not result.get("error")
    lines = [
        f"{'✅' if ok else '⚠️'} 📘 Facebook fill done",
        SEP,
        f"📤 Posted: {result['posted']} ({result.get('reels', 0)} Reels)   ❌ Failed: {result['failed']}",
        f"📥 Remaining: {result['remaining']}",
        format_fb_pacing(),
    ]
    if result.get("error"):
        lines.append(f"⚠️ {result['error']}")
    return "\n".join(lines)


def format_fb_pacing() -> str:
    st = fb_uploader.pacing_status()
    cap = "no cap" if st["cap"] is None else f"{st['today']}/{st['cap']}"
    phase = f"warm-up week {st['week']}" if st["week"] else "steady"
    return f"📅 Today {cap} ({phase}) · 📥 {st['queued']} queued"


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
        or summary.get("ig_posted")
        or summary.get("ig_error")
        or summary.get("fbs_posted")
        or summary.get("fbs_error")
        or summary.get("x_posted")
        or summary.get("x_error")
        or summary.get("tt_posted")
        or summary.get("tt_error")
    )


def _has_problem(summary: dict) -> bool:
    return bool(
        summary.get("aborted") or summary.get("bot_check") or summary.get("fb_error")
        or any(summary.get(f"{k}_error") for k in _shorts_targets())
    )


def format_summary(summary: dict, header: str = "Relay complete") -> str:
    icon = "⚠️" if _has_problem(summary) else "✅"
    lines = [f"{icon} {header}", SEP]

    yt = f"▶️ YouTube: {summary['uploaded']} uploaded · {summary['failed']} failed · 🔎 {summary['found']} found"
    lines.append(yt)
    extra = []
    if summary.get("today") is not None and config.DAILY_UPLOAD_TARGET > 0:
        extra.append(f"📅 Today {summary['today']}/{config.DAILY_UPLOAD_TARGET}")
    if summary.get("backfilled"):
        extra.append(f"+{summary['backfilled']} older")
    if extra:
        lines.append(" · ".join(extra))
    if summary.get("aborted"):
        lines.append(f"🛑 {summary['aborted']}")
    if summary.get("bot_check"):
        lines.append("🍪 YouTube bot-check: cookies likely expired (runbook 2b)")

    if config.FB_ENABLED:
        lines.append("")
        lines.append(
            f"📘 Facebook: {summary.get('fb_uploaded', 0)} posted ({summary.get('fb_reels', 0)} Reels)"
            f" · {summary.get('fb_failed', 0)} failed · {summary.get('fb_deferred', 0)} held"
        )
        lines.append(format_fb_pacing())
        if summary.get("fb_error"):
            lines.append(f"⚠️ Facebook token/permission: {summary['fb_error']}")

    for key, t in _shorts_targets().items():
        if not t["enabled"]:
            continue
        lines.append("")
        lines.append(
            f"{t['icon']} {t['name']}: {summary.get(f'{key}_posted', 0)} posted"
            f" · {summary.get(f'{key}_failed', 0)} failed"
        )
        lines.append(format_shorts_pacing(key))
        if summary.get(f"{key}_paced"):
            lines.append(f"⏳ {summary[f'{key}_paced']}")
        if summary.get(f"{key}_error"):
            lines.append(f"⚠️ {t['name']} token/permission: {summary[f'{key}_error']}")

    if summary.get("started_at") and summary.get("finished_at"):
        elapsed = (summary["finished_at"] - summary["started_at"]).total_seconds()
        lines += [SEP, f"⏱ {elapsed:.0f}s"]
    return "\n".join(lines)
