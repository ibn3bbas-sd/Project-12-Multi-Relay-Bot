"""Facebook Page video upload (Graph API resumable upload) + per-video state.

State lives in DATA_DIR/facebook_state.json:
    done     — source video IDs already posted to the Page
    pending  — videos already on YouTube whose Facebook post failed; retried
               on later runs (re-downloaded, Facebook only)
"""

from __future__ import annotations

import io
import json
import logging
import random
import time
from pathlib import Path

import requests

from . import config

logger = logging.getLogger(__name__)

MAX_TITLE = 255
MAX_DESCRIPTION = 5000
AUTH_ERROR_CODES = {190, 102, 10, 200}  # bad/expired token, missing permission
RETRIABLE_STATUS = {500, 502, 503, 504}
_PENDING_FIELDS = ("id", "title", "description", "url", "channel_name")


class FacebookAuthError(RuntimeError):
    """Token/permission problem — every further upload this run would fail too."""


# ── State ────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with config.FB_STATE_FILE.open(encoding="utf-8") as fh:
            state = json.load(fh)
        return {"done": list(state.get("done", [])), "pending": dict(state.get("pending", {}))}
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return {"done": [], "pending": {}}


def _save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.FB_STATE_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump({"done": sorted(set(state["done"])), "pending": state["pending"]}, fh, indent=2)
    tmp.replace(config.FB_STATE_FILE)


def is_done(video_id: str) -> bool:
    return video_id in _load_state()["done"]


def mark_done(video_id: str) -> None:
    state = _load_state()
    state["done"].append(video_id)
    state["pending"].pop(video_id, None)
    _save_state(state)


def add_pending(video: dict) -> None:
    state = _load_state()
    if video["id"] in state["done"]:
        return
    state["pending"][video["id"]] = {k: video.get(k, "") for k in _PENDING_FIELDS}
    _save_state(state)


def pending_videos() -> list[dict]:
    return list(_load_state()["pending"].values())


# ── Upload ───────────────────────────────────────────────────────────────────
def _build_description(video_meta: dict) -> str:
    """Post text. The feed shows only this, not the video title, so the title leads."""
    title = (video_meta.get("title") or "").strip()
    body = (video_meta.get("description") or "").strip()
    description = "\n\n".join(part for part in (title, body) if part)
    if config.ADD_SOURCE_CREDIT:
        description += config.SOURCE_CREDIT_TEMPLATE.format(
            channel_name=video_meta.get("channel_name", ""),
            video_url=video_meta.get("url", ""),
        )
    return description[:MAX_DESCRIPTION]


def _post(data: dict, files: dict | None = None) -> dict:
    """One Graph API call with retries for transient failures."""
    url = f"https://graph-video.facebook.com/{config.FB_API_VERSION}/{config.FB_PAGE_ID}/videos"
    data = {**data, "access_token": config.FB_PAGE_TOKEN}

    for attempt in range(config.UPLOAD_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, data=data, files=files, timeout=(15, 600))
            body = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            error, transient = f"{type(exc).__name__}: {exc}", True
        else:
            if resp.ok and "error" not in body:
                return body
            err = body.get("error", {})
            if err.get("code") in AUTH_ERROR_CODES:
                raise FacebookAuthError(err.get("message", "auth error"))
            error = f"HTTP {resp.status_code}: {err.get('message', resp.text[:200])}"
            transient = resp.status_code in RETRIABLE_STATUS or err.get("is_transient", False)

        if not transient or attempt == config.UPLOAD_MAX_RETRIES:
            raise RuntimeError(error)
        sleep_for = min(2 ** (attempt + 1) + random.random(), 60)
        logger.warning("Facebook call failed (%s) — retry in %.1fs", error, sleep_for)
        time.sleep(sleep_for)
        if files:  # rewind the chunk for the resend
            for f in files.values():
                f[1].seek(0)
    raise RuntimeError("unreachable")


def upload_video(file_path: Path, video_meta: dict) -> str | None:
    """Post one video to the Page. Returns the Facebook video ID, or None on failure.

    Raises FacebookAuthError so the caller can stop trying Facebook this run.
    """
    if not (config.FB_PAGE_ID and config.FB_PAGE_TOKEN):
        raise FacebookAuthError("FB_PAGE_ID / FB_PAGE_TOKEN not set")

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
