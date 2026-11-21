"""TikTok video posts (Content Posting API, Direct Post, FILE_UPLOAD) + per-video state.

Only the source channel's Shorts go to TikTok. Flow per Short:
    POST /v2/post/publish/creator_info/query/   → privacy options, max duration (per creator)
    POST /v2/post/publish/video/init/           {post_info, source_info: FILE_UPLOAD} → publish_id, upload_url
    PUT  upload_url                              Content-Range chunks (5–64 MB; one chunk if < 5 MB)
    POST /v2/post/publish/status/fetch/          until PUBLISH_COMPLETE / FAILED

Auth: OAuth 2.0 user token for the POSTING account (Login Kit, scopes user.info.basic +
video.publish). The access token lives 24 h and the refresh token 365 days; both are kept in
DATA_DIR/tiktok_token.json and refreshed here, so the token file must sit on the persistent volume.
Seed it once from the laptop token (see `seed` below).

Unaudited apps may only post SELF_ONLY (private). After TikTok's audit set
TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE.

Video spec (developers.tiktok.com): MP4/MOV/WebM, H.264 recommended, 23–60 fps, 360–4096 px per
side, ≤ 4 GB, duration ≤ the creator's max_video_post_duration_sec (3 min for most, 5–10 for some).

State lives in DATA_DIR/tiktok_state.json (same shape as x_state.json).

From the pod:
    python -m src.tiktok_uploader seed < tiktok_token.json   # write the token file (stdin JSON)
    python -m src.tiktok_uploader check                       # refresh if needed, print creator info
"""

from __future__ import annotations

import json
import logging
import os
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
from .ig_uploader import probe

logger = logging.getLogger(__name__)

API = "https://open.tiktokapis.com/v2"
MAX_TITLE = 2200
MIN_SECONDS = 3
MAX_BYTES = 4 * 1024 ** 3
MIN_CHUNK = 5 * 1024 * 1024
CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
RETRIABLE_STATUS = {500, 502, 503, 504}
POLL_TIMEOUT = 10 * 60
REFRESH_MARGIN = timedelta(minutes=10)

AUTH_CODES = {
    "access_token_invalid", "scope_not_authorized", "scope_permission_missed",
    "unaudited_client_can_only_post_to_private_accounts", "privacy_level_option_mismatch",
    "spam_risk_user_banned_from_posting", "url_ownership_unverified",
}
LIMIT_CODES = {"rate_limit_exceeded", "spam_risk_too_many_posts", "spam_risk_too_many_pending_share",
               "reached_active_user_cap", "spam_risk"}


# Plain-language text for the codes that reach Telegram (TikTok's own messages carry doc links).
FRIENDLY = {
    "unaudited_client_can_only_post_to_private_accounts":
        "waiting for TikTok app review: until it passes, posts need a private account",
    "access_token_invalid": "login expired: redo the TikTok login (runbook §8)",
    "scope_not_authorized": "login lacks video.publish: redo the TikTok login (runbook §8)",
    "privacy_level_option_mismatch": "TIKTOK_PRIVACY is not allowed for this account",
    "spam_risk_too_many_posts": "TikTok daily posting limit reached",
    "spam_risk_user_banned_from_posting": "this account is blocked from posting via apps",
    "reached_active_user_cap": "app's daily user cap reached (unaudited apps: 5 users/day)",
    "rate_limit_exceeded": "TikTok rate limit, retry later",
}
PAUSE_CODES = {"unaudited_client_can_only_post_to_private_accounts"}
PAUSE_HOURS = 12


class TikTokAuthError(RuntimeError):
    """Token/permission/app problem — every further post this run would fail too."""


class TikTokLimit(RuntimeError):
    """Rate or daily posting limit: stop posting to TikTok for this run."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.TIKTOK_STATE_FILE.open(encoding="utf-8") as fh:
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
        "paused_until": state.get("paused_until"),
        "pause_reason": state.get("pause_reason"),
    }


def _save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.TIKTOK_STATE_FILE.with_suffix(".json.tmp")
    out = {
        "done": sorted(set(state["done"])),
        "skipped": state["skipped"],
        "counts": dict(sorted(state["counts"].items())[-30:]),
        "last_post": state.get("last_post"),
    }
    if state.get("paused_until"):
        out["paused_until"] = state["paused_until"]
        out["pause_reason"] = state.get("pause_reason")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    tmp.replace(config.TIKTOK_STATE_FILE)


def is_handled(video_id: str) -> bool:
    state = _load_state()
    return video_id in state["done"] or video_id in state["skipped"]


def mark_done(video_id: str) -> None:
    state = _load_state()
    state["done"].append(video_id)
    day = _today().isoformat()
    state["counts"][day] = state["counts"].get(day, 0) + 1
    state["last_post"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["paused_until"] = None
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
        "cap": config.TIKTOK_DAILY_LIMIT or None,
        "last_post": state.get("last_post"),
        "done": len(state["done"]),
    }


def pacing_block(ignore_gap: bool = False) -> str | None:
    """Why a post must wait now, or None if it may go. /ttfill (ignore_gap) skips the pause."""
    st = pacing_status()
    if st["cap"] is not None and st["today"] >= st["cap"]:
        return f"daily limit: {st['today']}/{st['cap']} posted today"
    state = _load_state()
    if not ignore_gap and state.get("paused_until"):
        try:
            until = datetime.fromisoformat(state["paused_until"])
        except ValueError:
            until = None
        if until and until > datetime.now(timezone.utc):
            return state.get("pause_reason") or "paused"
    gap = config.TIKTOK_MIN_GAP_MINUTES
    if not ignore_gap and st["last_post"] and gap > 0:
        try:
            last = datetime.fromisoformat(st["last_post"])
        except ValueError:
            return None
        wait = last + timedelta(minutes=gap) - datetime.now(timezone.utc)
        if wait.total_seconds() > 0:
            return f"spacing posts: next in {int(wait.total_seconds() // 60) + 1} min"
    return None


# ── Token ────────────────────────────────────────────────────────────────────
def _load_token() -> dict:
    try:
        with config.TIKTOK_TOKEN_FILE.open(encoding="utf-8") as fh:
            tok = json.load(fh)
    except FileNotFoundError:
        raise TikTokAuthError(f"no token file {config.TIKTOK_TOKEN_FILE} (seed it, runbook §8)") from None
    except (json.JSONDecodeError, OSError) as exc:
        raise TikTokAuthError(f"unreadable token file: {exc}") from None
    if not tok.get("access_token") or not tok.get("refresh_token"):
        raise TikTokAuthError("token file has no access_token/refresh_token")
    return tok


def save_token(tok: dict) -> None:
    """Write the token file atomically, owner-only."""
    config.TIKTOK_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.TIKTOK_TOKEN_FILE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tok, fh, indent=2)
    tmp.replace(config.TIKTOK_TOKEN_FILE)


def token_from_response(body: dict) -> dict:
    """Normalize an /oauth/token/ response into the stored shape (absolute expiry times)."""
    now = datetime.now(timezone.utc)
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "open_id": body.get("open_id"),
        "scope": body.get("scope"),
        "expires_at": (now + timedelta(seconds=int(body.get("expires_in") or 86400))).isoformat(timespec="seconds"),
        "refresh_expires_at": (
            now + timedelta(seconds=int(body.get("refresh_expires_in") or 365 * 86400))
        ).isoformat(timespec="seconds"),
    }


def _refresh(tok: dict) -> dict:
    if not (config.TIKTOK_CLIENT_KEY and config.TIKTOK_CLIENT_SECRET):
        raise TikTokAuthError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET not set")
    try:
        resp = requests.post(f"{API}/oauth/token/", timeout=30, data={
            "client_key": config.TIKTOK_CLIENT_KEY,
            "client_secret": config.TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        })
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"TikTok token refresh failed: {exc}") from None
    if not resp.ok or "access_token" not in body:
        raise TikTokAuthError(
            f"token refresh refused: {body.get('error')} {body.get('error_description', '')}".strip()
            + " — redo the login (runbook §8)"
        )
    new = token_from_response(body)
    save_token(new)
    logger.info("TikTok access token refreshed (valid until %s)", new["expires_at"])
    return new


def access_token() -> str:
    tok = _load_token()
    try:
        expires = datetime.fromisoformat(tok.get("expires_at", ""))
    except ValueError:
        expires = datetime.min.replace(tzinfo=timezone.utc)
    if expires - REFRESH_MARGIN <= datetime.now(timezone.utc):
        tok = _refresh(tok)
    return tok["access_token"]


def refresh_days_left() -> int | None:
    try:
        exp = datetime.fromisoformat(_load_token().get("refresh_expires_at", ""))
    except (TikTokAuthError, ValueError):
        return None
    return (exp - datetime.now(timezone.utc)).days


# ── API ──────────────────────────────────────────────────────────────────────
def _no_links(text: str) -> str:
    """TikTok error messages end with 'Please review ... https://…' — drop that for Telegram."""
    return re.sub(r"\s*(Please review\b.*|https?://\S+)", "", text or "").strip()


def _pause(reason: str) -> None:
    """Stop automatic TikTok attempts for PAUSE_HOURS (each one downloads a Short first)."""
    state = _load_state()
    state["paused_until"] = (datetime.now(timezone.utc) + timedelta(hours=PAUSE_HOURS)).isoformat(timespec="seconds")
    state["pause_reason"] = reason
    _save_state(state)
    logger.warning("TikTok paused for %d h: %s", PAUSE_HOURS, reason)


def _api(path: str, body: dict, retries: int | None = None) -> dict:
    """POST to the Content Posting API. Returns `data`. Raises TikTokAuthError / TikTokLimit."""
    retries = config.UPLOAD_MAX_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{API}{path}", json=body, timeout=(15, 120),
                headers={"Authorization": f"Bearer {access_token()}",
                         "Content-Type": "application/json; charset=UTF-8"},
            )
            payload = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            error, code, transient = f"{type(exc).__name__}: {exc}", "", True
        else:
            err = payload.get("error") or {}
            code = str(err.get("code") or "")
            if resp.ok and code in ("", "ok"):
                return payload.get("data") or {}
            error = FRIENDLY.get(code) or f"HTTP {resp.status_code} {code}: {_no_links(err.get('message', ''))}".strip()
            if code in PAUSE_CODES:
                _pause(error)
                raise TikTokLimit(error)
            if code in AUTH_CODES or resp.status_code == 401:
                raise TikTokAuthError(error)
            if code in LIMIT_CODES or resp.status_code == 429:
                raise TikTokLimit(error)
            transient = resp.status_code in RETRIABLE_STATUS or code == "internal_error"
        if not transient or attempt == retries:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("TikTok call %s failed (%s) — retry in %.1fs", path, error, sleep_for)
        time.sleep(sleep_for)
    raise RuntimeError("unreachable")


def creator_info() -> dict:
    return _api("/post/publish/creator_info/query/", {})


def _chunks(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules: chunk_size 5–64 MB, except a file under
    5 MB, which goes whole. chunk_size may never exceed the file, so anything up to 64 MB is sent as
    one chunk; bigger files use 10 MB chunks and the last chunk absorbs the remainder."""
    if size <= MAX_CHUNK:
        return size, 1
    return CHUNK, size // CHUNK


def _put_chunks(upload_url: str, path: Path, size: int, chunk_size: int, count: int) -> None:
    with path.open("rb") as fh:
        for i in range(count):
            start = i * chunk_size
            end = size - 1 if i == count - 1 else start + chunk_size - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
                try:
                    resp = requests.put(upload_url, data=data, timeout=(15, 600), headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    })
                    if resp.status_code in (200, 201, 206):
                        break
                    error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    transient = resp.status_code in RETRIABLE_STATUS
                except requests.RequestException as exc:
                    error, transient = f"{type(exc).__name__}: {exc}", True
                if not transient or attempt == config.UPLOAD_MAX_RETRIES:
                    raise RuntimeError(f"chunk {i + 1}/{count} upload failed: {error}")
                time.sleep(min(2 ** (attempt + 1) + random.random(), 60))


def _wait_published(publish_id: str) -> str:
    """Final status: PUBLISH_COMPLETE (returns the post id or publish_id) or raises."""
    deadline = time.monotonic() + POLL_TIMEOUT
    status = ""
    while time.monotonic() < deadline:
        time.sleep(5)
        data = _api("/post/publish/status/fetch/", {"publish_id": publish_id})
        status = data.get("status", "")
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            return str(ids[0]) if ids else publish_id
        if status == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {data.get('fail_reason') or data}")
    # Upload finished but TikTok is still processing: it will most likely publish. Treat it as
    # posted so a later run doesn't post the same Short twice.
    logger.warning("TikTok %s still %s after %d min — counting it as posted",
                   publish_id, status or "unknown", POLL_TIMEOUT // 60)
    return publish_id


# ── Text and media ───────────────────────────────────────────────────────────
def build_title(video_meta: dict) -> str:
    """The video title, links stripped."""
    title = " ".join(
        w for w in (video_meta.get("title") or "").split() if "://" not in w and not w.startswith("www.")
    )
    return title[:MAX_TITLE]


def unsuitable(info: dict, max_seconds: int) -> str | None:
    if info["seconds"] < MIN_SECONDS:
        return f"too short ({info['seconds']:.1f}s)"
    if info["seconds"] > max_seconds:
        return f"too long ({info['seconds']:.0f}s > {max_seconds}s)"
    return None


def _needs_reencode(info: dict) -> bool:
    return (
        info["vcodec"] not in ("h264", "hevc")
        or info.get("pix_fmt") not in (None, "yuv420p")
        or not 23 <= info["fps"] <= 60
        or min(info["width"], info["height"]) < 360
        or max(info["width"], info["height"]) > 4096
        or (info["acodec"] is not None and info["acodec"] != "aac")
        or info["bytes"] > MAX_BYTES * 0.95
    )


def prepare(path: Path, info: dict) -> Path | None:
    """A TikTok-ready MP4 (moov first). Caller deletes it."""
    out = path.with_name(f"{path.stem}.tt.mp4")
    if _needs_reencode(info):
        # Short side ≥ 360 and long side ≤ 4096, keep aspect.
        short = min(info["width"], info["height"])
        up = 360 / short if short < 360 else 1.0
        scale = f"scale=trunc(iw*{up:.4f}/2)*2:trunc(ih*{up:.4f}/2)*2" if up > 1 else \
            "scale='min(4096,iw)':'min(4096,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path),
               "-vf", f"{scale},setsar=1",
               "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p"]
        if not 23 <= info["fps"] <= 60:
            cmd += ["-r", "30"]
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-c", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Preparing %s for TikTok failed: %s", path.name, exc)
        out.unlink(missing_ok=True)
        return None
    return out


def upload_short(file_path: Path, video_meta: dict) -> str | None:
    """Post one downloaded Short to TikTok. Returns the post/publish ID, or None on failure.

    Raises TikTokAuthError / TikTokLimit so the caller stops TikTok for this run.
    Marks unsuitable clips as skipped (returns None).
    """
    creator = creator_info()  # also refreshes the token and checks the account can post now
    options = creator.get("privacy_level_options") or []
    privacy = config.TIKTOK_PRIVACY
    if privacy not in options:
        raise TikTokAuthError(f"TIKTOK_PRIVACY={privacy} not allowed for this account/app (allowed: {options})")
    max_seconds = min(config.TIKTOK_MAX_SECONDS, int(creator.get("max_video_post_duration_sec") or 600))

    info = probe(file_path)
    if info is None:
        return None
    reason = unsuitable(info, max_seconds)
    if reason:
        logger.info("Short %s skipped for TikTok: %s", video_meta.get("id"), reason)
        mark_skipped(video_meta["id"], reason)
        return None

    ready = prepare(file_path, info)
    if ready is None:
        return None
    try:
        size = ready.stat().st_size
        chunk_size, count = _chunks(size)
        # No blind retry on init: a retry after a lost response could post twice.
        init = _api("/post/publish/video/init/", retries=0, body={
            "post_info": {
                "title": build_title(video_meta),
                "privacy_level": privacy,
                "disable_comment": bool(creator.get("comment_disabled")),
                "disable_duet": bool(creator.get("duet_disabled")),
                "disable_stitch": bool(creator.get("stitch_disabled")),
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": chunk_size, "total_chunk_count": count},
        })
        publish_id = init["publish_id"]
        _put_chunks(init["upload_url"], ready, size, chunk_size, count)
        post_id = _wait_published(publish_id)
    except (TikTokAuthError, TikTokLimit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("TikTok post failed for '%s': %s", video_meta.get("title"), exc)
        return None
    finally:
        ready.unlink(missing_ok=True)

    logger.info("Posted '%s' to TikTok (%s) → %s", video_meta.get("title"), privacy, post_id)
    return post_id


# ── CLI ──────────────────────────────────────────────────────────────────────
def _seed_cli() -> int:
    """Write the token file from JSON on stdin (the laptop's tiktok_token.json)."""
    try:
        tok = json.load(sys.stdin)
    except ValueError as exc:
        print("FAILED: stdin is not JSON:", exc)
        return 1
    if not tok.get("access_token") or not tok.get("refresh_token"):
        print("FAILED: JSON has no access_token/refresh_token")
        return 1
    save_token(tok)
    print(f"Token saved to {config.TIKTOK_TOKEN_FILE} (refresh token valid until {tok.get('refresh_expires_at')})")
    return 0


def _check_cli() -> int:
    try:
        c = creator_info()
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", exc)
        return 1
    print(f"Posting account: @{c.get('creator_username')} ({c.get('creator_nickname')})")
    print(f"Privacy options: {c.get('privacy_level_options')} — using {config.TIKTOK_PRIVACY}")
    print(f"Max video length: {c.get('max_video_post_duration_sec')} s")
    print(f"Refresh token: {refresh_days_left()} days left")
    if config.TIKTOK_PRIVACY not in (c.get("privacy_level_options") or []):
        print("WARNING: TIKTOK_PRIVACY is not in the allowed options — posts would fail")
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        sys.exit(_seed_cli())
    if cmd == "check":
        sys.exit(_check_cli())
    print("usage: python -m src.tiktok_uploader seed|check")
    sys.exit(2)
