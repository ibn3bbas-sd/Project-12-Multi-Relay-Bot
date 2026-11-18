"""Instagram Reels publishing (Instagram API with Facebook Login) + per-video state.

Only the source channel's Shorts go to Instagram. Flow per Short:
    POST /{ig-user}/media  media_type=REELS upload_type=resumable  → container id + rupload uri
    POST rupload uri (Authorization: OAuth, offset: 0, file_size)  → bytes uploaded
    GET  /{container}?fields=status_code  until FINISHED
    POST /{ig-user}/media_publish creation_id=...                   → media id

Reels spec (Meta docs): MP4/MOV, moov atom first, no edit lists, H.264/HEVC,
23–60 fps, ≤1920 px wide, AAC ≤48 kHz, 3 s – 15 min, ≤300 MB. Files that already
fit are only remuxed (fast); others are re-encoded.

State lives in DATA_DIR/instagram_state.json:
    done      — source Short IDs already published
    skipped   — Shorts that can never go (too long/short), so they aren't re-checked
    counts    — posts per local day (TIMEZONE), for IG_DAILY_LIMIT
    last_post — UTC timestamp of the last post, for IG_MIN_GAP_MINUTES

Hidden self-test from the pod (uploads a 5 s test clip, never publishes it):
    python -m src.ig_uploader check [--upload]
"""

from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import config

logger = logging.getLogger(__name__)

MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_BYTES = 300 * 1024 * 1024
MIN_SECONDS = 3
AUTH_ERROR_CODES = {190, 102, 10, 200}
LIMIT_SUBCODES = {2207042}  # "reached the maximum number of posts"
RETRIABLE_STATUS = {500, 502, 503, 504}
POLL_SECONDS = 15
POLL_TIMEOUT = 15 * 60


class InstagramAuthError(RuntimeError):
    """Token/permission problem — every further post this run would fail too."""


class InstagramLimit(RuntimeError):
    """The account hit the API publishing limit for the rolling 24 h."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.IG_STATE_FILE.open(encoding="utf-8") as fh:
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
    tmp = config.IG_STATE_FILE.with_suffix(".json.tmp")
    out = {
        "done": sorted(set(state["done"])),
        "skipped": state["skipped"],
        "counts": dict(sorted(state["counts"].items())[-30:]),
        "last_post": state.get("last_post"),
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    tmp.replace(config.IG_STATE_FILE)


def is_handled(video_id: str) -> bool:
    """Already published, or permanently unsuitable."""
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
        "cap": config.IG_DAILY_LIMIT or None,
        "last_post": state.get("last_post"),
        "done": len(state["done"]),
    }


def pacing_block(ignore_gap: bool = False) -> str | None:
    """Why a post must wait now, or None if it may go."""
    st = pacing_status()
    if st["cap"] is not None and st["today"] >= st["cap"]:
        return f"daily limit: {st['today']}/{st['cap']} posted today"
    gap = config.IG_MIN_GAP_MINUTES
    if not ignore_gap and st["last_post"] and gap > 0:
        try:
            last = datetime.fromisoformat(st["last_post"])
        except ValueError:
            return None
        wait = last + timedelta(minutes=gap) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            return f"spacing posts: next in {int(wait.total_seconds() // 60) + 1} min"
    return None


# ── Caption ──────────────────────────────────────────────────────────────────
def build_caption(video_meta: dict) -> str:
    title = (video_meta.get("title") or "").strip()
    body = (video_meta.get("description") or "").strip()
    caption = "\n\n".join(part for part in (title, body) if part)
    if config.ADD_SOURCE_CREDIT:
        caption += config.SOURCE_CREDIT_TEMPLATE.format(
            channel_name=video_meta.get("channel_name", ""),
            video_url=video_meta.get("url", ""),
        )
    # Instagram rejects captions with more than 30 hashtags: drop the extras.
    seen = 0

    def _keep(match: re.Match) -> str:
        nonlocal seen
        seen += 1
        return match.group(0) if seen <= MAX_HASHTAGS else ""

    caption = re.sub(r"#\w+", _keep, caption)
    return caption[:MAX_CAPTION]


# ── Media preparation ────────────────────────────────────────────────────────
def _fps(raw: str | None) -> float:
    try:
        num, _, den = str(raw or "0/1").partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> dict | None:
    """Duration, size and the codec facts the Reels spec cares about."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        info = json.loads(out)
        video = next(s for s in info["streams"] if s.get("codec_type") == "video")
        audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
        return {
            "seconds": float(info["format"]["duration"]),
            "bytes": path.stat().st_size,
            "vcodec": video.get("codec_name"),
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": _fps(video.get("avg_frame_rate")),
            "acodec": audio.get("codec_name") if audio else None,
            "sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration) as exc:
        logger.warning("ffprobe failed for %s: %s", path.name, exc)
        return None


def unsuitable(info: dict) -> str | None:
    """A permanent reason this clip can't be an Instagram Reel, or None."""
    if info["seconds"] < MIN_SECONDS:
        return f"too short ({info['seconds']:.0f}s)"
    if info["seconds"] > config.IG_MAX_SECONDS:
        return f"too long ({info['seconds']:.0f}s > {config.IG_MAX_SECONDS}s)"
    return None


def _needs_reencode(info: dict) -> bool:
    return (
        info["vcodec"] not in ("h264", "hevc")
        or not 23 <= info["fps"] <= 60
        or info["width"] > 1920
        or (info["acodec"] is not None and (info["acodec"] != "aac" or info["sample_rate"] > 48000))
        or info["bytes"] > MAX_BYTES * 0.95
    )


def prepare(path: Path, info: dict) -> Path | None:
    """A Reels-ready copy (moov first, no edit lists). Caller deletes it."""
    out = path.with_name(f"{path.stem}.ig.mp4")
    if _needs_reencode(info):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path),
               "-vf", "scale='min(1920,iw)':-2,setsar=1",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-maxrate", "20M", "-bufsize", "40M"]
        if not 23 <= info["fps"] <= 60:
            cmd += ["-r", "30"]
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-c", "copy"]
    cmd += ["-movflags", "+faststart", "-use_editlist", "0", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Preparing %s for Instagram failed: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None
    if out.stat().st_size > MAX_BYTES:
        logger.warning("%s is still over 300 MB after preparing", path.name)
        out.unlink(missing_ok=True)
        return None
    return out


# ── Graph API ────────────────────────────────────────────────────────────────
def _check(resp: requests.Response) -> tuple[dict | None, str, bool]:
    """(body if OK, error text, transient?). Raises on auth/limit errors."""
    body = resp.json() if resp.content else {}
    if resp.ok and "error" not in body:
        return body, "", False
    err = body.get("error", {})
    if err.get("code") in AUTH_ERROR_CODES:
        raise InstagramAuthError(err.get("message", "auth error"))
    if err.get("error_subcode") in LIMIT_SUBCODES or err.get("code") == 9:
        raise InstagramLimit(err.get("message", "publishing limit reached"))
    msg = err.get("error_user_msg") or err.get("message") or resp.text[:200]
    error = f"HTTP {resp.status_code}: {msg}"
    return None, error, resp.status_code in RETRIABLE_STATUS or bool(err.get("is_transient"))


def _graph(method: str, path: str, params: dict | None = None, retries: int | None = None) -> dict:
    url = f"https://graph.facebook.com/{config.IG_API_VERSION}/{path}"
    params = {**(params or {}), "access_token": config.IG_ACCESS_TOKEN}
    retries = config.UPLOAD_MAX_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, data=params if method == "POST" else None,
                                    params=params if method == "GET" else None, timeout=(15, 120))
            body, error, transient = _check(resp)
        except (requests.RequestException, ValueError) as exc:
            body, error, transient = None, f"{type(exc).__name__}: {exc}", True
        if body is not None:
            return body
        if not transient or attempt == retries:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Instagram call failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)
    raise RuntimeError("unreachable")


def _rupload(uri: str, file_path: Path) -> None:
    size = file_path.stat().st_size
    for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
        try:
            with file_path.open("rb") as fh:
                resp = requests.post(
                    uri,
                    headers={
                        "Authorization": f"OAuth {config.IG_ACCESS_TOKEN}",
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
            if body.get("success") is False:
                raise RuntimeError(f"rupload refused: {body}")
            return
        if not transient or attempt == config.UPLOAD_MAX_RETRIES:
            raise RuntimeError(f"Instagram upload: {error}")
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Instagram upload failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)


def _wait_finished(container_id: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        body = _graph("GET", container_id, {"fields": "status_code,status"})
        code = body.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {code}: {body.get('status', '')}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"container still {code} after {POLL_TIMEOUT // 60} min")
        time.sleep(POLL_SECONDS)


def _upload_container(file_path: Path, caption: str) -> str:
    """Create a Reels container, upload the bytes, wait until FINISHED. Returns its ID."""
    created = _graph("POST", f"{config.IG_USER_ID}/media", {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption,
        "share_to_feed": "true",
    })
    container_id = created["id"]
    uri = created.get("uri") or (
        f"https://rupload.facebook.com/ig-api-upload/{config.IG_API_VERSION}/{container_id}"
    )
    try:
        _rupload(uri, file_path)
    except (InstagramAuthError, InstagramLimit):
        raise
    except RuntimeError as exc:
        # rupload can answer 400 ProcessingFailedError although the bytes arrived
        # and the container goes on to FINISHED (seen 2026-11-19 on 18–22 MB Shorts).
        # The container status is the real verdict: ERROR or a timeout still fails.
        logger.warning("rupload reported %s — checking the container status instead", exc)
    _wait_finished(container_id)
    return container_id


def _publish(container_id: str) -> str:
    """Publish without blind retries: a retry after a lost response would double-post."""
    try:
        return _graph("POST", f"{config.IG_USER_ID}/media_publish",
                      {"creation_id": container_id}, retries=0)["id"]
    except (InstagramAuthError, InstagramLimit):
        raise
    except Exception:
        time.sleep(POLL_SECONDS)
        status = _graph("GET", container_id, {"fields": "status_code"}).get("status_code")
        if status == "PUBLISHED":
            return container_id
        raise


def upload_short(file_path: Path, video_meta: dict) -> str | None:
    """Publish one downloaded Short as a Reel. Returns the media ID, or None on failure.

    Raises InstagramAuthError / InstagramLimit so the caller stops Instagram for this run.
    Marks unsuitable clips as skipped (returns None).
    """
    if not (config.IG_USER_ID and config.IG_ACCESS_TOKEN):
        raise InstagramAuthError("IG_USER_ID / IG_ACCESS_TOKEN not set")

    info = probe(file_path)
    if info is None:
        return None
    reason = unsuitable(info)
    if reason:
        logger.info("Short %s skipped for Instagram: %s", video_meta.get("id"), reason)
        mark_skipped(video_meta["id"], reason)
        return None

    ready = prepare(file_path, info)
    if ready is None:
        return None
    try:
        container_id = _upload_container(ready, build_caption(video_meta))
        media_id = _publish(container_id)
    except (InstagramAuthError, InstagramLimit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Instagram post failed for '%s': %s", video_meta.get("title"), exc)
        return None
    finally:
        ready.unlink(missing_ok=True)

    logger.info("Posted '%s' to Instagram as a Reel → %s", video_meta.get("title"), media_id)
    return media_id


# ── Self-test ────────────────────────────────────────────────────────────────
def _check_cli(upload: bool) -> int:
    print(f"Graph API {config.IG_API_VERSION}")
    if config.FB_PAGE_ID:
        try:
            page = _graph("GET", config.FB_PAGE_ID, {"fields": "instagram_business_account"}, retries=0)
            print("Page → instagram_business_account:", page.get("instagram_business_account", "NOT LINKED"))
        except Exception as exc:  # noqa: BLE001
            print("Page lookup failed:", exc)
    if not config.IG_USER_ID:
        print("IG_USER_ID is not set: put the id printed above into the Secret.")
        return 1
    try:
        me = _graph("GET", config.IG_USER_ID, {"fields": "username,media_count"}, retries=0)
        print(f"Account: @{me.get('username')} ({me.get('media_count')} posts)")
        limit = _graph("GET", f"{config.IG_USER_ID}/content_publishing_limit",
                       {"fields": "quota_usage,config"}, retries=0)
        print("Publishing limit:", json.dumps(limit.get("data", limit)))
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", exc)
        return 1
    if not upload:
        return 0

    # Square, 15 fps, 44.1 kHz like many source Shorts: exercises the re-encode path too.
    clip = Path("/tmp/ig-selftest.mp4")
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
        print("Prepared:", probe(ready))
        container_id = _upload_container(ready, "relay bot self-test (not published)")
        print(f"OK: test container {container_id} is FINISHED. Not published; it expires in 24 h.")
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
        print("usage: python -m src.ig_uploader check [--upload]")
        sys.exit(2)
    sys.exit(_check_cli("--upload" in sys.argv[2:]))
