"""Facebook Page video upload (Graph API resumable upload) + per-video state.

State lives in DATA_DIR/facebook_state.json:
    done          — source video IDs already posted to the Page
    pending       — videos already on YouTube whose Facebook post failed or was
                    held back by pacing; retried on later runs (Facebook only)
    counts        — posts per local day (TIMEZONE), for the warm-up cap
    last_post     — UTC timestamp of the last post, for FB_MIN_GAP_MINUTES
    warmup_start  — first day of warm-up week 1 (unless FB_WARMUP_START is set)

Format: a clip that fits the Reels API (3–FB_REELS_MAX_SECONDS s) goes up as a
Reel (/video_reels + rupload); anything longer as a normal video (/videos).
"""

from __future__ import annotations

import io
import json
import logging
import random
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import captions, config

logger = logging.getLogger(__name__)

MAX_TITLE = 255
MAX_DESCRIPTION = 5000
AUTH_ERROR_CODES = {190, 102, 10, 200}  # bad/expired token, missing permission
RETRIABLE_STATUS = {500, 502, 503, 504}
ACTIVE_MINUTES_PER_DAY = 16 * 60
_PENDING_FIELDS = ("id", "title", "description", "url", "channel_name")


class FacebookAuthError(RuntimeError):
    """Token/permission problem — every further upload this run would fail too."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.FB_STATE_FILE.open(encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    return {
        "done": list(state.get("done", [])),
        "pending": dict(state.get("pending", {})),
        "counts": {k: int(v) for k, v in dict(state.get("counts", {})).items()},
        "last_post": state.get("last_post"),
        "warmup_start": state.get("warmup_start"),
    }


def _save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.FB_STATE_FILE.with_suffix(".json.tmp")
    out = {
        "done": sorted(set(state["done"])),
        "pending": state["pending"],
        "counts": dict(sorted(state["counts"].items())[-30:]),  # a month of history
        "last_post": state.get("last_post"),
        "warmup_start": state.get("warmup_start"),
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    tmp.replace(config.FB_STATE_FILE)


def is_done(video_id: str) -> bool:
    return video_id in _load_state()["done"]


def mark_done(video_id: str) -> None:
    """Record a successful post: done list, today's count, last-post time."""
    state = _load_state()
    state["done"].append(video_id)
    state["pending"].pop(video_id, None)
    day = _today().isoformat()
    state["counts"][day] = state["counts"].get(day, 0) + 1
    state["last_post"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.setdefault("warmup_start", None)
    if not state["warmup_start"] and not config.FB_WARMUP_START:
        state["warmup_start"] = day
    _save_state(state)


def add_pending(video: dict) -> None:
    state = _load_state()
    if video["id"] in state["done"]:
        return
    state["pending"][video["id"]] = {k: video.get(k, "") for k in _PENDING_FIELDS}
    _save_state(state)


def pending_videos() -> list[dict]:
    return list(_load_state()["pending"].values())


# ── Pacing (warm-up ramp, daily cap, minimum gap) ────────────────────────────
def _today() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def _warmup_start(state: dict) -> date:
    for raw in (config.FB_WARMUP_START, state.get("warmup_start")):
        if raw:
            try:
                return date.fromisoformat(str(raw))
            except ValueError:
                logger.warning("Ignoring bad warm-up start date %r", raw)
    return _today()


def pacing_status() -> dict:
    """Today's posts, today's cap (None = no cap), warm-up week (None once done), queue size."""
    state = _load_state()
    week = (_today() - _warmup_start(state)).days // 7
    schedule = config.FB_WARMUP_SCHEDULE
    if 0 <= week < len(schedule):
        cap, warmup_week = schedule[week], week + 1
    else:
        cap, warmup_week = (config.FB_DAILY_LIMIT or None), None
    return {
        "today": state["counts"].get(_today().isoformat(), 0),
        "cap": cap,
        "week": warmup_week,
        "queued": len(state["pending"]),
        "last_post": state.get("last_post"),
    }


def min_gap_minutes(cap: int | None) -> int:
    """Spread the day's posts over ~16 waking hours, never closer than FB_MIN_GAP_MINUTES."""
    spread = ACTIVE_MINUTES_PER_DAY // cap if cap else 0
    return max(config.FB_MIN_GAP_MINUTES, spread)


def pacing_block(ignore_gap: bool = False) -> str | None:
    """Why a post must wait right now, or None if it may go ahead."""
    state = _load_state()
    if not state.get("warmup_start") and not config.FB_WARMUP_START:
        state["warmup_start"] = _today().isoformat()  # the warm-up clock starts now
        _save_state(state)

    st = pacing_status()
    if st["cap"] is not None and st["today"] >= st["cap"]:
        label = f"warm-up week {st['week']}" if st["week"] else "daily limit"
        return f"{label}: {st['today']}/{st['cap']} posted today"
    gap = min_gap_minutes(st["cap"])
    if not ignore_gap and st["last_post"] and gap > 0:
        try:
            last = datetime.fromisoformat(st["last_post"])
        except ValueError:
            return None
        wait = last + timedelta(minutes=gap) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            return f"spacing posts: next in {int(wait.total_seconds() // 60) + 1} min"
    return None


# ── Upload ───────────────────────────────────────────────────────────────────
def _build_description(video_meta: dict) -> str:
    """Post text. The feed shows only this, not the video title, so the title leads."""
    return captions.facebook_text(video_meta)[:MAX_DESCRIPTION]


def _check(resp: requests.Response) -> tuple[dict | None, str, bool]:
    """(body if OK, error text, transient?). Raises FacebookAuthError on token problems."""
    body = resp.json() if resp.content else {}
    if resp.ok and "error" not in body:
        return body, "", False
    err = body.get("error", {})
    if err.get("code") in AUTH_ERROR_CODES:
        raise FacebookAuthError(err.get("message", "auth error"))
    error = f"HTTP {resp.status_code}: {err.get('message', resp.text[:200])}"
    return None, error, resp.status_code in RETRIABLE_STATUS or bool(err.get("is_transient"))


def _post(data: dict, files: dict | None = None, endpoint: str = "videos") -> dict:
    """One Graph API call with retries for transient failures."""
    host = "graph-video.facebook.com" if endpoint == "videos" else "graph.facebook.com"
    url = f"https://{host}/{config.FB_API_VERSION}/{config.FB_PAGE_ID}/{endpoint}"
    data = {**data, "access_token": config.FB_PAGE_TOKEN}

    for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, data=data, files=files, timeout=(15, 600))
            body, error, transient = _check(resp)
        except (requests.RequestException, ValueError) as exc:
            body, error, transient = None, f"{type(exc).__name__}: {exc}", True
        if body is not None:
            return body

        if not transient or attempt == config.UPLOAD_MAX_RETRIES:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Facebook call failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)
        if files:  # rewind the chunk for the resend
            for f in files.values():
                f[1].seek(0)
    raise RuntimeError("unreachable")


def _upload_feed_video(file_path: Path, video_meta: dict) -> str | None:
    """Normal video post via the resumable /videos upload."""
    size = file_path.stat().st_size
    try:
        start = _post({"upload_phase": "start", "file_size": size})
        session, fb_id = start["upload_session_id"], start["video_id"]
        begin, end = int(start["start_offset"]), int(start["end_offset"])

        with file_path.open("rb") as fh:
            while begin < end:
                fh.seek(begin)
                chunk = io.BytesIO(fh.read(end - begin))
                resp = _post(
                    {"upload_phase": "transfer", "upload_session_id": session, "start_offset": begin},
                    files={"video_file_chunk": (file_path.name, chunk)},
                )
                begin, end = int(resp["start_offset"]), int(resp["end_offset"])
                logger.info("Facebook upload %s: %d%%", video_meta.get("id", "?"), begin * 100 // size)

        _post({
            "upload_phase": "finish",
            "upload_session_id": session,
            "title": (video_meta.get("title") or "Untitled")[:MAX_TITLE],
            "description": _build_description(video_meta),
            "published": "true",
        })
    except FacebookAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Facebook upload failed for '%s': %s", video_meta.get("title"), exc)
        return None

    logger.info("Posted '%s' to Facebook → video %s", video_meta.get("title"), fb_id)
    return fb_id


# ── Reels ────────────────────────────────────────────────────────────────────
def _probe(path: Path) -> tuple[int, int, float] | None:
    """(width, height, seconds) of the first video stream, or None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        info = json.loads(out)
        stream = info["streams"][0]
        return int(stream["width"]), int(stream["height"]), float(info["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        logger.warning("ffprobe failed for %s: %s", path.name, exc)
        return None


def _pad_vertical(path: Path) -> Path | None:
    """Letterbox a landscape/square clip into 1080x1920 so the Reels API accepts it."""
    out = path.with_name(f"{path.stem}.reel.mp4")
    vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
          "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
        return out
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Padding to 9:16 failed for %s: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None


def _reel_source(file_path: Path) -> Path | None:
    """The file to publish as a Reel (maybe a padded copy), or None if it doesn't fit."""
    if config.FB_FORMAT == "video":
        return None
    probe = _probe(file_path)
    if probe is None:
        return None
    width, height, seconds = probe
    if not 3 <= seconds <= config.FB_REELS_MAX_SECONDS:
        return None
    if height > width:
        return file_path
    return _pad_vertical(file_path) if config.FB_REELS_PAD else None


def _upload_reel(file_path: Path, video_meta: dict) -> str:
    """Reels Publishing API: start → binary upload to rupload → finish. Returns the video ID."""
    start = _post({"upload_phase": "start"}, endpoint="video_reels")
    fb_id = start["video_id"]
    upload_url = start.get("upload_url") or (
        f"https://rupload.facebook.com/video-upload/{config.FB_API_VERSION}/{fb_id}"
    )
    size = file_path.stat().st_size

    for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
        try:
            with file_path.open("rb") as fh:
                resp = requests.post(
                    upload_url,
                    headers={
                        "Authorization": f"OAuth {config.FB_PAGE_TOKEN}",
                        "offset": "0",
                        "file_size": str(size),
                    },
                    data=fh,
                    timeout=(15, 900),
                )
            body, error, transient = _check(resp)
        except (requests.RequestException, ValueError) as exc:
            body, error, transient = None, f"{type(exc).__name__}: {exc}", True
        if body is not None:
            if not body.get("success", True):
                raise RuntimeError(f"rupload refused: {body}")
            break
        if not transient or attempt == config.UPLOAD_MAX_RETRIES:
            raise RuntimeError(f"Reel upload: {error}")
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Reel upload failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)

    done = _post(
        {
            "upload_phase": "finish",
            "video_id": fb_id,
            "video_state": "PUBLISHED",
            "title": (video_meta.get("title") or "Untitled")[:MAX_TITLE],
            "description": _build_description(video_meta),
        },
        endpoint="video_reels",
    )
    if not done.get("success", False):
        raise RuntimeError(f"Reel publish refused: {done}")
    return fb_id


# ── Entry point ──────────────────────────────────────────────────────────────
def upload_video(file_path: Path, video_meta: dict) -> tuple[str | None, str]:
    """Post one video to the Page as a Reel or a normal video.

    Returns (Facebook video ID or None on failure, "reel" | "video").
    Raises FacebookAuthError so the caller can stop trying Facebook this run.
    """
    if not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        raise FacebookAuthError("FB_PAGE_ID / FB_PAGE_TOKEN not set")

    reel_path = _reel_source(file_path)
    if reel_path is not None:
        try:
            fb_id = _upload_reel(reel_path, video_meta)
            logger.info("Posted '%s' to Facebook as a Reel → %s", video_meta.get("title"), fb_id)
            return fb_id, "reel"
        except FacebookAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reel failed for '%s': %s", video_meta.get("title"), exc)
        finally:
            if reel_path != file_path:
                reel_path.unlink(missing_ok=True)
        if config.FB_FORMAT == "reels":
            return None, "reel"
        logger.info("Falling back to a normal video post")
    elif config.FB_FORMAT == "reels":
        logger.info("'%s' doesn't fit the Reels API (3–%ds) — skipped (FB_FORMAT=reels)",
                    video_meta.get("title"), config.FB_REELS_MAX_SECONDS)
        return None, "reel"

    return _upload_feed_video(file_path, video_meta), "video"
