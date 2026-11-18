"""Configuration loader.

Precedence (highest wins):
    1. Environment variables / .env
    2. config/config.yaml
    3. Built-in defaults

All paths are resolved relative to the repo root so the app behaves the same
whether it is launched from an interactive shell, a Windows service (NSSM), or
a scheduled task with an arbitrary working directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

load_dotenv(BASE_DIR / ".env")


def _load_yaml() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


_YAML = _load_yaml()


def _yaml_get(path: str, default: Any = None) -> Any:
    node: Any = _YAML
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _get(env_key: str, yaml_path: str, default: Any = None) -> Any:
    """Env var wins, then config.yaml, then the default."""
    raw = os.getenv(env_key)
    if raw not in (None, ""):
        return raw
    return _yaml_get(yaml_path, default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int_list(value: Any, default: list[int]) -> list[int]:
    """"1,3" or [1, 3] -> [1, 3]. Bad input falls back to the default."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    try:
        out = [int(str(v).strip()) for v in items if str(v).strip()]
    except ValueError:
        return default
    return out if out and all(n >= 0 for n in out) else default


def _resolve(path_like: Any, default: str) -> Path:
    """Absolute path. Relative values are anchored at BASE_DIR."""
    raw = str(path_like or default)
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (BASE_DIR / p)


# ── Telegram (required) ──────────────────────────────────────────────────────
class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _require(env_key: str) -> str:
    value = os.getenv(env_key, "").strip()
    if not value:
        raise ConfigError(
            f"{env_key} is not set. Copy .env.example to .env and fill it in "
            f"(expected at {BASE_DIR / '.env'})."
        )
    return value


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_ID = int(_require("TELEGRAM_ALLOWED_CHAT_ID"))

# ── Channels ─────────────────────────────────────────────────────────────────
SOURCE_CHANNEL_ID = _get("SOURCE_CHANNEL_ID", "source_channel.id", "")
TARGET_CHANNEL_ID = _get("TARGET_CHANNEL_ID", "target_channel.id", "")

YOUTUBE_CLIENT_SECRET_FILE = _resolve(
    _get("YOUTUBE_CLIENT_SECRET_FILE", "youtube.client_secret_file"),
    "config/client_secret.json",
)
YOUTUBE_TOKEN_FILE = _resolve(
    _get("YOUTUBE_TOKEN_FILE", "youtube.token_file"),
    "config/token.json",
)

# ── Scheduler ────────────────────────────────────────────────────────────────
TIMEZONE = str(_get("TIMEZONE", "scheduler.timezone", "Asia/Riyadh"))
# How often to look for new source uploads. Quiet checks (nothing found) don't
# message Telegram, so a short interval only costs a cheap channel listing.
CHECK_INTERVAL_MINUTES = max(
    15, _as_int(_get("CHECK_INTERVAL_MINUTES", "scheduler.check_interval_minutes", 120), 120)
)
# Daily top-up: if fewer than DAILY_UPLOAD_TARGET videos went up in the current
# YouTube quota day, fill the gap with older videos from the source channel.
# The quota day resets at midnight Pacific (10:00–11:00 Riyadh), so the top-up
# runs shortly before that, after the day's new videos have had their chance.
# 0 disables the top-up.
DAILY_UPLOAD_TARGET = _as_int(_get("DAILY_UPLOAD_TARGET", "scheduler.daily_upload_target", 6), 6)
BACKFILL_TIME = str(_get("BACKFILL_TIME", "scheduler.backfill_time", "09:00"))
QUOTA_TIMEZONE = "America/Los_Angeles"  # YouTube Data API quota resets at Pacific midnight

# ── Download ─────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = _resolve(_get("DOWNLOAD_DIR", "download.dir"), "downloads")
MAX_VIDEO_AGE_DAYS = _as_int(_get("MAX_VIDEO_AGE_DAYS", "download.max_age_days", 7), 7)
VIDEO_QUALITY = str(
    _get("VIDEO_QUALITY", "download.quality", "best[height<=1080]")
)
SCAN_LIMIT = _as_int(_get("SCAN_LIMIT", "download.scan_limit", 20), 20)
MAX_UPLOADS_PER_RUN = _as_int(
    _get("MAX_UPLOADS_PER_RUN", "download.max_uploads_per_run", 5), 5
)
# How deep into the channel's history the top-up may reach.
BACKFILL_SCAN_LIMIT = _as_int(_get("BACKFILL_SCAN_LIMIT", "download.backfill_scan_limit", 300), 300)
KEEP_AFTER_UPLOAD = _as_bool(_get("KEEP_AFTER_UPLOAD", "download.keep_after_upload", False))

# Point yt-dlp at a specific ffmpeg build; blank means use PATH.
# A stale or non-existent value is discarded rather than handed to yt-dlp, which
# would otherwise fail to merge streams and produce a silently broken download.
def _ffmpeg_location() -> str:
    raw = _get("FFMPEG_LOCATION", "download.ffmpeg_location", "")
    if not raw:
        return ""
    candidate = _resolve(raw, "")
    if candidate.is_dir():
        return str(candidate)
    return ""


FFMPEG_LOCATION = _ffmpeg_location()

# Netscape cookies.txt from a throwaway Google account. Datacenter IPs (CCE) get
# "Sign in to confirm you're not a bot" on every video without it. Blank = none.
_cookies_raw = _get("YTDLP_COOKIES_FILE", "download.cookies_file", "")
YTDLP_COOKIES_FILE = _resolve(_cookies_raw, "") if _cookies_raw else None

# ── Upload ───────────────────────────────────────────────────────────────────
UPLOAD_PRIVACY = str(_get("UPLOAD_PRIVACY", "upload.privacy", "public"))
UPLOAD_CATEGORY_ID = str(_get("UPLOAD_CATEGORY_ID", "upload.category_id", "22"))
ADD_SOURCE_CREDIT = _as_bool(_get("ADD_SOURCE_CREDIT", "upload.add_source_credit", True), True)
SOURCE_CREDIT_TEMPLATE = str(
    _yaml_get(
        "upload.source_credit_template",
        "\n\n---\nOriginal video by: {channel_name}\n{video_url}",
    )
)
UPLOAD_MAX_RETRIES = _as_int(_get("UPLOAD_MAX_RETRIES", "upload.max_retries", 5), 5)

# ── Facebook Page (optional second target) ───────────────────────────────────
FB_ENABLED = _as_bool(_get("FB_ENABLED", "facebook.enabled", False))
FB_PAGE_ID = str(_get("FB_PAGE_ID", "facebook.page_id", "") or "").strip()
FB_PAGE_TOKEN = str(os.getenv("FB_PAGE_TOKEN", "") or "").strip()  # env only, never yaml
FB_API_VERSION = str(_get("FB_API_VERSION", "facebook.api_version", "v27.0"))
# Failed Facebook posts are retried on later runs; cap the re-downloads per run.
FB_RETRY_PER_RUN = _as_int(_get("FB_RETRY_PER_RUN", "facebook.retry_per_run", 3), 3)

# Format: auto = Reel when the clip fits the Reels API (3–90 s), else a normal
# video post; reels = Reel or nothing; video = always a normal video post.
FB_FORMAT = str(_get("FB_FORMAT", "facebook.format", "auto")).strip().lower()
if FB_FORMAT not in {"auto", "reels", "video"}:
    FB_FORMAT = "auto"
FB_REELS_MAX_SECONDS = _as_int(_get("FB_REELS_MAX_SECONDS", "facebook.reels_max_seconds", 90), 90)
# Landscape/square short clips get padded to 1080x1920 (ffmpeg) so they qualify as Reels.
FB_REELS_PAD = _as_bool(_get("FB_REELS_PAD", "facebook.reels_pad", True), True)

# Warm-up pacing for a Page that has been dormant. Posts per day for week 1,
# week 2, ... then FB_DAILY_LIMIT (0 = no cap). Day boundaries use TIMEZONE.
# Week 1 starts on FB_WARMUP_START (YYYY-MM-DD) or, if empty, the first day the
# bot checks pacing (stored in facebook_state.json). Over-cap videos are queued.
FB_WARMUP_SCHEDULE = _as_int_list(_get("FB_WARMUP_SCHEDULE", "facebook.warmup_schedule", "1,3"), [1, 3])
FB_WARMUP_START = str(_get("FB_WARMUP_START", "facebook.warmup_start", "") or "").strip()
# Default 8 > the ~6 YouTube uploads/day, so the queue built during warm-up drains.
FB_DAILY_LIMIT = _as_int(_get("FB_DAILY_LIMIT", "facebook.daily_limit", 8), 8)
# Automatic posts are spaced 16h / today's cap apart (1/day → 16h, 3/day → 5h20,
# 8/day → 2h), and never closer than this. /fbfill ignores spacing, not the cap.
FB_MIN_GAP_MINUTES = _as_int(_get("FB_MIN_GAP_MINUTES", "facebook.min_gap_minutes", 90), 90)

# ── Gemini (optional) ────────────────────────────────────────────────────────
GEMINI_API_KEY = str(_get("GEMINI_API_KEY", "gemini.api_key", "") or "")
GEMINI_ENABLED = _as_bool(_get("GEMINI_ENABLED", "gemini.enabled", False))

# ── Runtime dirs ─────────────────────────────────────────────────────────────
DATA_DIR = _resolve(_get("DATA_DIR", "runtime.data_dir"), "data")
LOG_DIR = _resolve(_get("LOG_DIR", "runtime.log_dir"), "logs")
PROCESSED_FILE = DATA_DIR / "processed_videos.json"
UPLOAD_COUNTS_FILE = DATA_DIR / "upload_counts.json"
FB_STATE_FILE = DATA_DIR / "facebook_state.json"
LOG_FILE = LOG_DIR / "relay.log"

LOG_LEVEL = str(_get("LOG_LEVEL", "runtime.log_level", "INFO")).upper()
LOG_MAX_BYTES = _as_int(_get("LOG_MAX_BYTES", "runtime.log_max_bytes", 10_485_760), 10_485_760)
LOG_BACKUP_COUNT = _as_int(_get("LOG_BACKUP_COUNT", "runtime.log_backup_count", 5), 5)


def ensure_dirs() -> None:
    """Create every runtime directory. Safe to call repeatedly."""
    for directory in (DATA_DIR, LOG_DIR, DOWNLOAD_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
