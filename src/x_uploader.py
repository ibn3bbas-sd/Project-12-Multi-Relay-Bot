"""X (Twitter) video posts (API v2, OAuth 1.0a user context) + per-video state.

Only the source channel's Shorts go to X. Flow per Short:
    POST /2/media/upload/initialize  {media_type, total_bytes, media_category}  → media id
    POST /2/media/upload/{id}/append  multipart media + segment_index (4 MB chunks)
    POST /2/media/upload/{id}/finalize                                          → processing_info
    GET  /2/media/upload?command=STATUS&media_id=...  until state=succeeded
    POST /2/tweets  {text, media: {media_ids: [id]}}                            → post id

Post text is the video title only: X bills ~$0.015 per post, $0.20 with a link.

Video spec (docs.x.com): MP4, H.264 + AAC, yuv420p, ≤60 fps, ≤1920x1200 / 1200x1920,
0.5 s – 20 min on a normal account. Files that already fit are only remuxed.

State lives in DATA_DIR/x_state.json (same shape as instagram_state.json):
    done, skipped, counts (posts per local day), last_post (UTC).

Hidden self-test from the pod (uploads a 5 s test clip, never posts it):
    python -m src.x_uploader check [--upload]
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests_oauthlib import OAuth1

from . import config
from .ig_uploader import probe

logger = logging.getLogger(__name__)

API = "https://api.x.com/2"
MAX_TEXT = 270  # X counts 280 weighted chars; leave room for the duplicate suffix
MIN_SECONDS = 0.5
MAX_BYTES = 512 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
RETRIABLE_STATUS = {500, 502, 503, 504}
POLL_TIMEOUT = 15 * 60


class XAuthError(RuntimeError):
    """Keys/permission/credit problem — every further post this run would fail too."""


class XLimit(RuntimeError):
    """Rate limit (429): stop posting to X for this run."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.X_STATE_FILE.open(encoding="utf-8") as fh:
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
    tmp = config.X_STATE_FILE.with_suffix(".json.tmp")
    out = {
        "done": sorted(set(state["done"])),
        "skipped": state["skipped"],
        "counts": dict(sorted(state["counts"].items())[-30:]),
        "last_post": state.get("last_post"),
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    tmp.replace(config.X_STATE_FILE)


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
        "cap": config.X_DAILY_LIMIT or None,
        "last_post": state.get("last_post"),
        "done": len(state["done"]),
    }


def pacing_block(ignore_gap: bool = False) -> str | None:
    """Why a post must wait now, or None if it may go."""
    st = pacing_status()
    if st["cap"] is not None and st["today"] >= st["cap"]:
        return f"daily limit: {st['today']}/{st['cap']} posted today"
    gap = config.X_MIN_GAP_MINUTES
    if not ignore_gap and st["last_post"] and gap > 0:
        try:
            last = datetime.fromisoformat(st["last_post"])
        except ValueError:
            return None
        wait = last + timedelta(minutes=gap) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            return f"spacing posts: next in {int(wait.total_seconds() // 60) + 1} min"
    return None


# ── Text ─────────────────────────────────────────────────────────────────────
def build_text(video_meta: dict) -> str:
    """Title only. URLs are stripped: a link makes the post cost $0.20 instead of $0.015."""
    title = " ".join(
        w for w in (video_meta.get("title") or "").split() if "://" not in w and not w.startswith("www.")
    )
    return title[:MAX_TEXT]


# ── Media preparation ────────────────────────────────────────────────────────
def unsuitable(info: dict) -> str | None:
    if info["seconds"] < MIN_SECONDS:
        return f"too short ({info['seconds']:.1f}s)"
    if info["seconds"] > config.X_MAX_SECONDS:
        return f"too long ({info['seconds']:.0f}s > {config.X_MAX_SECONDS}s)"
    return None


def _fits_frame(w: int, h: int) -> bool:
    return max(w, h) <= 1920 and min(w, h) <= 1200


def _needs_reencode(info: dict) -> bool:
    return (
        info["vcodec"] != "h264"
        or info.get("pix_fmt") not in (None, "yuv420p")
        or not 0 < info["fps"] <= 60
        or not _fits_frame(info["width"], info["height"])
        or (info["acodec"] is not None and (info["acodec"] != "aac" or info["sample_rate"] > 48000))
        or info["bytes"] > MAX_BYTES * 0.95
    )


def prepare(path: Path, info: dict) -> Path | None:
    """An X-ready MP4 (moov first). Caller deletes it."""
    out = path.with_name(f"{path.stem}.x.mp4")
    if _needs_reencode(info):
        # Fit inside 1920x1200 (landscape) or 1200x1920 (portrait), keep aspect.
        if info["width"] >= info["height"]:
            scale = "scale='min(1920,iw)':'min(1200,ih)':force_original_aspect_ratio=decrease"
        else:
            scale = "scale='min(1200,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path),
               "-vf", f"{scale},scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
               "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-maxrate", "20M", "-bufsize", "40M"]
        if not 0 < info["fps"] <= 60:
            cmd += ["-r", "30"]
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-c", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Preparing %s for X failed: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None
    if out.stat().st_size > MAX_BYTES:
        logger.warning("%s is still over 512 MB after preparing", path.name)
        out.unlink(missing_ok=True)
        return None
    return out


# ── API ──────────────────────────────────────────────────────────────────────
def _auth() -> OAuth1:
    return OAuth1(config.X_API_KEY, config.X_API_SECRET,
                  config.X_ACCESS_TOKEN, config.X_ACCESS_TOKEN_SECRET)


def _error_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    errs = body.get("errors") or []
    detail = body.get("detail") or (errs[0].get("message") if errs else "") or body.get("title")
    return str(detail or resp.text[:200])


def _call(method: str, url: str, retries: int | None = None, **kwargs) -> dict:
    """One API call with retries on 5xx/network errors. Raises XAuthError / XLimit."""
    retries = config.UPLOAD_MAX_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, auth=_auth(), timeout=(15, 300), **kwargs)
        except requests.RequestException as exc:
            error, transient = f"{type(exc).__name__}: {exc}", True
        else:
            if resp.ok:
                return resp.json() if resp.content else {}
            error = f"HTTP {resp.status_code}: {_error_text(resp)}"
            # 402 = pay-per-use credit used up; 403 = app/token lacks write access
            # (except X's per-post "duplicate content" refusal, handled by the caller).
            if resp.status_code in (401, 402) or (
                resp.status_code == 403 and "duplicate" not in error.lower()
            ):
                raise XAuthError(error)
            if resp.status_code == 429:
                raise XLimit(error)
            transient = resp.status_code in RETRIABLE_STATUS
        if not transient or attempt == retries:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("X call failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)
    raise RuntimeError("unreachable")


def _wait_processed(media_id: str, info: dict | None) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    while info and info.get("state") not in ("succeeded",):
        if info.get("state") == "failed":
            raise RuntimeError(f"X media processing failed: {info.get('error', info)}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"X media still {info.get('state')} after {POLL_TIMEOUT // 60} min")
        time.sleep(max(1, int(info.get("check_after_secs") or 5)))
        body = _call("GET", f"{API}/media/upload",
                     params={"command": "STATUS", "media_id": media_id})
        info = body.get("data", {}).get("processing_info")


def upload_media(file_path: Path) -> str:
    """Chunked upload + wait for processing. Returns the media id."""
    size = file_path.stat().st_size
    init = _call("POST", f"{API}/media/upload/initialize", json={
        "media_type": "video/mp4",
        "total_bytes": size,
        "media_category": "tweet_video",
    })
    media_id = str(init["data"]["id"])
    with file_path.open("rb") as fh:
        index = 0
        while chunk := fh.read(CHUNK):
            _call("POST", f"{API}/media/upload/{media_id}/append",
                  data={"segment_index": str(index)},
                  files={"media": ("chunk", chunk, "application/octet-stream")})
            index += 1
    fin = _call("POST", f"{API}/media/upload/{media_id}/finalize")
    _wait_processed(media_id, fin.get("data", {}).get("processing_info"))
    return media_id


def create_post(text: str, media_id: str) -> str:
    """No blind retries: a retry after a lost response would double-post."""
    body: dict = {"media": {"media_ids": [media_id]}}
    if text:
        body["text"] = text
    return str(_call("POST", f"{API}/tweets", retries=0, json=body)["data"]["id"])


def upload_short(file_path: Path, video_meta: dict) -> str | None:
    """Post one downloaded Short to X. Returns the post ID, or None on failure.

    Raises XAuthError / XLimit so the caller stops X for this run.
    Marks unsuitable clips as skipped (returns None).
    """
    if not all((config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_TOKEN_SECRET)):
        raise XAuthError("X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET not set")

    info = probe(file_path)
    if info is None:
        return None
    reason = unsuitable(info)
    if reason:
        logger.info("Short %s skipped for X: %s", video_meta.get("id"), reason)
        mark_skipped(video_meta["id"], reason)
        return None

    ready = prepare(file_path, info)
    if ready is None:
        return None
    text = build_text(video_meta)
    try:
        media_id = upload_media(ready)
        try:
            post_id = create_post(text, media_id)
        except RuntimeError as exc:
            # X refuses a post whose text matches a recent one (same title twice).
            if "duplicate" not in str(exc).lower():
                raise
            published = video_meta.get("published_at")
            suffix = published.strftime("%Y-%m-%d") if published else video_meta["id"]
            post_id = create_post(f"{text} · {suffix}".strip(" ·"), media_id)
    except (XAuthError, XLimit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("X post failed for '%s': %s", video_meta.get("title"), exc)
        return None
    finally:
        ready.unlink(missing_ok=True)

    logger.info("Posted '%s' to X → %s", video_meta.get("title"), post_id)
    return post_id


# ── Self-test ────────────────────────────────────────────────────────────────
def _check_cli(upload: bool) -> int:
    try:
        me = _call("GET", f"{API}/users/me", retries=0)
        print(f"Posting account: @{me['data']['username']}")
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", exc)
        return 1
    if not upload:
        return 0

    # Square, 15 fps, H.264 + AAC 44.1 kHz: the remux path, then a real chunked upload.
    clip = Path("/tmp/x-selftest.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=368x368:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
        check=True,
    )
    ready = None
    try:
        info = probe(clip)
        ready = prepare(clip, info) if info else None
        if ready is None:
            print("ffmpeg prepare FAILED")
            return 1
        media_id = upload_media(ready)
        print(f"OK: test media {media_id} processed. Not posted.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("Upload test FAILED:", exc)
        return 1
    finally:
        clip.unlink(missing_ok=True)
        if ready:
            ready.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2 or sys.argv[1] != "check":
        print("usage: python -m src.x_uploader check [--upload]")
        sys.exit(2)
    sys.exit(_check_cli("--upload" in sys.argv[2:]))
