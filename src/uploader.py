"""YouTube Data API v3 upload with resumable transfer and quota-aware retries."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import captions, config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Transient conditions worth retrying with backoff.
RETRIABLE_STATUS = {500, 502, 503, 504}
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, ConnectionError, TimeoutError)

# Title/description hard limits enforced by the API.
MAX_TITLE = 100
MAX_DESCRIPTION = 5000


class QuotaExceeded(RuntimeError):
    """Daily YouTube API quota is spent — stop the run, do not burn retries."""


def _get_credentials() -> Credentials:
    """Load, refresh, or interactively mint OAuth credentials.

    The interactive branch opens a browser and only works in a desktop session
    (i.e. run it once over RDP on the jumpbox, not from the Windows service).
    """
    creds: Credentials | None = None

    if config.YOUTUBE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.YOUTUBE_TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired YouTube OAuth token")
        creds.refresh(Request())
    else:
        if not config.YOUTUBE_CLIENT_SECRET_FILE.exists():
            raise FileNotFoundError(
                f"OAuth client secret not found at {config.YOUTUBE_CLIENT_SECRET_FILE}. "
                "Download it from Google Cloud Console (OAuth 2.0 Client ID, Desktop app)."
            )
        logger.info("No valid token — starting interactive OAuth consent flow")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.YOUTUBE_CLIENT_SECRET_FILE), SCOPES
        )
        creds = flow.run_local_server(port=0)

    config.YOUTUBE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.YOUTUBE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Saved OAuth token to %s", config.YOUTUBE_TOKEN_FILE)
    return creds


def _build_description(video_meta: dict) -> str:
    # YouTube rejects descriptions containing < or >.
    return captions.youtube_description(video_meta).replace("<", "‹").replace(">", "›")[:MAX_DESCRIPTION]


def _is_quota_error(error: HttpError) -> bool:
    if error.resp.status != 403:
        return False
    body = (error.content or b"").decode("utf-8", errors="replace")
    return "quotaExceeded" in body or "uploadLimitExceeded" in body


def upload_video(file_path: Path, video_meta: dict) -> str | None:
    """Upload one video. Returns the new YouTube video ID, or None on failure.

    Raises QuotaExceeded so the caller can abort the whole run — retrying after
    a quota rejection just wastes time; the quota resets at midnight PT.
    """
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": (video_meta.get("title") or "Untitled")[:MAX_TITLE],
            "description": _build_description(video_meta),
            "categoryId": config.UPLOAD_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.UPLOAD_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(file_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/*"
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    attempt = 0
    last_logged_pct = -10

    while response is None:
        try:
            status, response = request.next_chunk()
            if status and int(status.progress() * 100) - last_logged_pct >= 10:
                last_logged_pct = int(status.progress() * 100)
                logger.info("Upload %s: %d%%", video_meta.get("id", "?"), last_logged_pct)

        except HttpError as exc:
            if _is_quota_error(exc):
                logger.error("YouTube API quota exceeded — aborting this run")
                raise QuotaExceeded(str(exc)) from exc
            if exc.resp.status not in RETRIABLE_STATUS:
                logger.error("Upload failed for '%s': %s", video_meta.get("title"), exc)
                return None
            attempt += 1

        except RETRIABLE_EXCEPTIONS as exc:
            logger.warning("Transient upload error (%s) — retrying", exc)
            attempt += 1

        if response is None and attempt:
            if attempt > config.UPLOAD_MAX_RETRIES:
                logger.error("Giving up on '%s' after %d retries", video_meta.get("title"), attempt - 1)
                return None
            sleep_for = min(2**attempt + random.random(), 60)
            logger.info("Retry %d/%d in %.1fs", attempt, config.UPLOAD_MAX_RETRIES, sleep_for)
            time.sleep(sleep_for)

    video_id = response.get("id")
    logger.info("Uploaded '%s' → https://youtu.be/%s", video_meta.get("title"), video_id)
    return video_id
