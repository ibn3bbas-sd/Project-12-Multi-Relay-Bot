"""Facebook Page video Stories (Page Stories API) + per-video state.

Only the source channel's Shorts go to Stories (they vanish after 24 h). Flow per Short:
    POST /{page-id}/video_stories  upload_phase=start                → video_id + upload_url
    POST upload_url (Authorization: OAuth, offset: 0, file_size)     → bytes uploaded
    POST /{page-id}/video_stories  upload_phase=finish video_id=...  → post_id

Uses the same Page and token as the Facebook target (FB_PAGE_ID / FB_PAGE_TOKEN with
pages_manage_posts). Stories carry no caption.

Story spec (Meta docs): MP4, 9:16 (1080x1920 recommended), 3–60 s, 24–60 fps,
H.264 + AAC 48 kHz. Vertical clips that fit are only remuxed; others are scaled and
letterboxed into 1080x1920.

State lives in DATA_DIR/fb_story_state.json (same shape as instagram_state.json):
    done, skipped, counts (posts per local day), last_post (UTC).
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import config
from .fb_uploader import FacebookAuthError, _check
from .ig_uploader import probe

logger = logging.getLogger(__name__)

LAST_ERROR: str | None = None  # why the last upload_short() returned None (for the stats db)

MIN_SECONDS = 3
RATE_LIMIT_CODES = {4, 17, 32, 613}


class FacebookStoryLimit(RuntimeError):
    """Graph API rate limit: stop posting Stories for this run."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.FBS_STATE_FILE.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    return {
        "done": list(state.get("done", [])),
        "skipped": dict(state.get("skipped", {})),
        "counts": {k: int(v) for k, v in dict(state.get("counts", {})).items()},
        "last_post": state.get("last_post"),
    }


def _save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.FBS_STATE_FILE.with_suffix(".json.tmp")
    out = {
        "done": sorted(set(state["done"])),
        "skipped": state["skipped"],
        "counts": dict(sorted(state["counts"].items())[-30:]),
        "last_post": state.get("last_post"),
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    tmp.replace(config.FBS_STATE_FILE)


def is_handled(video_id: str) -> bool:
    state = _load_state()
    return video_id in state["done"] or video_id in state["skipped"]


def mark_done(video_id: str) -> None:
    state = _load_state()
    state["done"].append(video_id)
    day = _today().isoformat()
    state["counts"][day] = state["counts"].get(day, 0) + 1
    state["last_post"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state(state)


def mark_skipped(video_id: str, reason: str) -> None:
    state = _load_state()
    state["skipped"][video_id] = reason
    _save_state(state)


# ── Pacing ───────────────────────────────────────────────────────────────────
def _today() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def pacing_status() -> dict:
    state = _load_state()
    return {
        "today": state["counts"].get(_today().isoformat(), 0),
        "cap": config.FBS_DAILY_LIMIT or None,
        "last_post": state.get("last_post"),
        "done": len(state["done"]),
    }


def pacing_block(ignore_gap: bool = False) -> str | None:
    """Why a Story must wait now, or None if it may go."""
    st = pacing_status()
    if st["cap"] is not None and st["today"] >= st["cap"]:
        return f"daily limit: {st['today']}/{st['cap']} posted today"
    gap = config.FBS_MIN_GAP_MINUTES
    if not ignore_gap and st["last_post"] and gap > 0:
        try:
            last = datetime.fromisoformat(st["last_post"])
        except ValueError:
            return None
        wait = last + timedelta(minutes=gap) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            return f"spacing posts: next in {int(wait.total_seconds() // 60) + 1} min"
    return None


# ── Media preparation ────────────────────────────────────────────────────────
def unsuitable(info: dict) -> str | None:
    if info["seconds"] < MIN_SECONDS:
        return f"too short ({info['seconds']:.1f}s)"
    if info["seconds"] > config.FBS_MAX_SECONDS:
        return f"too long ({info['seconds']:.0f}s > {config.FBS_MAX_SECONDS}s)"
    return None


def _needs_reencode(info: dict) -> bool:
    return (
        info["vcodec"] != "h264"
        or info["height"] <= info["width"]  # landscape/square → letterbox to 9:16
        or info["width"] > 1080
        or not 24 <= info["fps"] <= 60
        or (info["acodec"] is not None and (info["acodec"] != "aac" or info["sample_rate"] > 48000))
    )


def prepare(path: Path, info: dict) -> Path | None:
    """A Story-ready 9:16 MP4 (moov first). Caller deletes it."""
    out = path.with_name(f"{path.stem}.story.mp4")
    if _needs_reencode(info):
        vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-maxrate", "8M", "-bufsize", "16M"]
        if not 24 <= info["fps"] <= 60:
            cmd += ["-r", "30"]
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-c", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Preparing %s for a Story failed: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None
    return out


# ── Graph API ────────────────────────────────────────────────────────────────
def _checked(resp: requests.Response) -> tuple[dict | None, str, bool]:
    """fb_uploader._check plus the rate-limit codes. Raises FacebookAuthError / FacebookStoryLimit."""
    try:
        code = (resp.json() if resp.content else {}).get("error", {}).get("code")
    except ValueError:
        code = None
    if code in RATE_LIMIT_CODES:
        raise FacebookStoryLimit("Facebook API rate limit")
    return _check(resp)


def _stories(data: dict, retries: int | None = None) -> dict:
    url = f"https://graph.facebook.com/{config.FB_API_VERSION}/{config.FB_PAGE_ID}/video_stories"
    data = {**data, "access_token": config.FB_PAGE_TOKEN}
    retries = config.UPLOAD_MAX_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, data=data, timeout=(15, 120))
            body, error, transient = _checked(resp)
        except (requests.RequestException, ValueError) as exc:
            body, error, transient = None, f"{type(exc).__name__}: {exc}", True
        if body is not None:
            return body
        if not transient or attempt == retries:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Facebook Story call failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)
    raise RuntimeError("unreachable")


def _rupload(uri: str, file_path: Path) -> None:
    size = file_path.stat().st_size
    for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
        try:
            with file_path.open("rb") as fh:
                resp = requests.post(
                    uri,
                    headers={"Authorization": f"OAuth {config.FB_PAGE_TOKEN}",
                             "offset": "0", "file_size": str(size)},
                    data=fh,
                    timeout=(15, 900),
                )
            body, error, transient = _checked(resp)
        except (requests.RequestException, ValueError) as exc:
            body, error, transient = None, f"{type(exc).__name__}: {exc}", True
        if body is not None:
            if body.get("success") is False:
                raise RuntimeError(f"Story upload refused: {body}")
            return
        if not transient or attempt == config.UPLOAD_MAX_RETRIES:
            raise RuntimeError(f"Story upload: {error}")
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Story upload failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)


def _post_story(file_path: Path) -> str:
    """start → upload → finish. Returns the Story post id (or the video id)."""
    start = _stories({"upload_phase": "start"})
    video_id = start["video_id"]
    uri = start.get("upload_url") or (
        f"https://rupload.facebook.com/video-upload/{config.FB_API_VERSION}/{video_id}"
    )
    _rupload(uri, file_path)
    # No blind retries: a retry after a lost response could post the Story twice.
    done = _stories({"upload_phase": "finish", "video_id": video_id}, retries=0)
    if done.get("success") is False:
        raise RuntimeError(f"Story publish refused: {done}")
    return str(done.get("post_id") or video_id)


def upload_short(file_path: Path, video_meta: dict) -> str | None:
    """Post one downloaded Short as a Page Story. Returns the post ID, or None on failure.

    Raises FacebookAuthError / FacebookStoryLimit so the caller stops Stories for this run.
    Marks unsuitable clips as skipped (returns None).
    """
    global LAST_ERROR
    if not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        raise FacebookAuthError("FB_PAGE_ID / FB_PAGE_TOKEN not set")

    info = probe(file_path)
    if info is None:
        LAST_ERROR = "ffprobe failed"
        return None
    reason = unsuitable(info)
    if reason:
        logger.info("Short %s skipped for Facebook Stories: %s", video_meta.get("id"), reason)
        mark_skipped(video_meta["id"], reason)
        return None

    ready = prepare(file_path, info)
    if ready is None:
        LAST_ERROR = "preparing the file failed"
        return None
    try:
        post_id = _post_story(ready)
    except (FacebookAuthError, FacebookStoryLimit):
        raise
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR = str(exc)[:300]
        logger.error("Facebook Story failed for '%s': %s", video_meta.get("title"), exc)
        return None
    finally:
        ready.unlink(missing_ok=True)

    logger.info("Posted '%s' to Facebook as a Story → %s", video_meta.get("title"), post_id)
    return post_id
