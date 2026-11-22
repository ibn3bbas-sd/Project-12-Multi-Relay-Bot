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

# ── Instagram (optional third target: Shorts only, as Reels) ─────────────────
# Source = the channel's Shorts tab (the YouTube relay only reads the Videos tab).
# Uses the Instagram API with Facebook Login: a Professional IG account linked
# to the Page, and a Page token that also has instagram_basic +
# instagram_content_publish. IG_ACCESS_TOKEN falls back to FB_PAGE_TOKEN.
IG_ENABLED = _as_bool(_get("IG_ENABLED", "instagram.enabled", False))
IG_USER_ID = str(_get("IG_USER_ID", "instagram.user_id", "") or "").strip()
IG_ACCESS_TOKEN = str(os.getenv("IG_ACCESS_TOKEN", "") or "").strip() or FB_PAGE_TOKEN
IG_API_VERSION = str(_get("IG_API_VERSION", "instagram.api_version", FB_API_VERSION))
# YouTube Shorts are ≤180 s; the Reels API takes 3 s – 15 min.
IG_MAX_SECONDS = _as_int(_get("IG_MAX_SECONDS", "instagram.max_seconds", 180), 180)
IG_SCAN_LIMIT = _as_int(_get("IG_SCAN_LIMIT", "instagram.scan_limit", 20), 20)
# A new automated account posting a burst looks like spam: keep it modest.
# Daily cap per local day (TIMEZONE); 0 = no cap (the API allows ~50/24h).
IG_DAILY_LIMIT = _as_int(_get("IG_DAILY_LIMIT", "instagram.daily_limit", 3), 3)
# Automatic posts at least this far apart; /igfill ignores it (not the cap).
IG_MIN_GAP_MINUTES = _as_int(_get("IG_MIN_GAP_MINUTES", "instagram.min_gap_minutes", 180), 180)
# The daily top-up also fills Instagram up to the cap with older Shorts.
IG_TOPUP = _as_bool(_get("IG_TOPUP", "instagram.topup", True), True)

# ── X / Twitter (optional fourth target: Shorts only) ────────────────────────
# OAuth 1.0a user context: the app's Consumer Key/Secret plus an Access Token
# for the POSTING account (made with the PIN flow, so the developer account can
# be a different X account). Pay-per-use API: ~$0.015 per post, $0.20 with a link,
# so posts carry the title only.
X_ENABLED = _as_bool(_get("X_ENABLED", "x.enabled", False))
X_API_KEY = str(os.getenv("X_API_KEY", "") or "").strip()
X_API_SECRET = str(os.getenv("X_API_SECRET", "") or "").strip()
X_ACCESS_TOKEN = str(os.getenv("X_ACCESS_TOKEN", "") or "").strip()
X_ACCESS_TOKEN_SECRET = str(os.getenv("X_ACCESS_TOKEN_SECRET", "") or "").strip()
# A normal account takes 0.5 s – 20 min; Shorts are ≤180 s.
X_MAX_SECONDS = _as_int(_get("X_MAX_SECONDS", "x.max_seconds", 180), 180)
X_SCAN_LIMIT = _as_int(_get("X_SCAN_LIMIT", "x.scan_limit", 20), 20)
X_DAILY_LIMIT = _as_int(_get("X_DAILY_LIMIT", "x.daily_limit", 3), 3)
X_MIN_GAP_MINUTES = _as_int(_get("X_MIN_GAP_MINUTES", "x.min_gap_minutes", 180), 180)
X_TOPUP = _as_bool(_get("X_TOPUP", "x.topup", True), True)

# ── TikTok (optional fifth target: Shorts only) ──────────────────────────────
# Content Posting API, Direct Post. Client key/secret from the TikTok app; the posting
# account's OAuth token lives in DATA_DIR/tiktok_token.json (refreshed by the bot).
# Unaudited apps can only post SELF_ONLY (private); after the audit use PUBLIC_TO_EVERYONE.
TIKTOK_ENABLED = _as_bool(_get("TIKTOK_ENABLED", "tiktok.enabled", False))
TIKTOK_CLIENT_KEY = str(os.getenv("TIKTOK_CLIENT_KEY", "") or "").strip()
TIKTOK_CLIENT_SECRET = str(os.getenv("TIKTOK_CLIENT_SECRET", "") or "").strip()
TIKTOK_PRIVACY = str(_get("TIKTOK_PRIVACY", "tiktok.privacy", "SELF_ONLY")).strip().upper()
# Also capped per post by the creator's own max_video_post_duration_sec.
TIKTOK_MAX_SECONDS = _as_int(_get("TIKTOK_MAX_SECONDS", "tiktok.max_seconds", 180), 180)
TIKTOK_SCAN_LIMIT = _as_int(_get("TIKTOK_SCAN_LIMIT", "tiktok.scan_limit", 20), 20)
TIKTOK_DAILY_LIMIT = _as_int(_get("TIKTOK_DAILY_LIMIT", "tiktok.daily_limit", 3), 3)
TIKTOK_MIN_GAP_MINUTES = _as_int(_get("TIKTOK_MIN_GAP_MINUTES", "tiktok.min_gap_minutes", 180), 180)
TIKTOK_TOPUP = _as_bool(_get("TIKTOK_TOPUP", "tiktok.topup", True), True)

# ── Health: expiry reminders + watchdog ─────────────────────────────────────
# Daily reminder (at EXPIRY_CHECK_TIME, TIMEZONE) for credentials expiring within REMIND_DAYS.
REMIND_DAYS = _as_int(_get("REMIND_DAYS", "health.remind_days", 14), 14)
EXPIRY_CHECK_TIME = str(_get("EXPIRY_CHECK_TIME", "health.expiry_check_time", "10:00"))
# Watchdog alerts: no finished run for this long / one run going on for this long.
WATCHDOG_MINUTES = _as_int(_get("WATCHDOG_MINUTES", "health.watchdog_minutes", 30), 30)
SILENT_ALERT_HOURS = _as_int(_get("SILENT_ALERT_HOURS", "health.silent_alert_hours", 6), 6)
STUCK_ALERT_MINUTES = _as_int(_get("STUCK_ALERT_MINUTES", "health.stuck_alert_minutes", 90), 90)
# Optional external dead-man's switch (e.g. https://hc-ping.com/<uuid>), pinged after every run.
HEALTHCHECK_URL = str(os.getenv("HEALTHCHECK_URL", "") or "").strip()

# ── Gemini (optional) ────────────────────────────────────────────────────────
GEMINI_API_KEY = str(_get("GEMINI_API_KEY", "gemini.api_key", "") or "")
GEMINI_ENABLED = _as_bool(_get("GEMINI_ENABLED", "gemini.enabled", False))

# ── Runtime dirs ─────────────────────────────────────────────────────────────
DATA_DIR = _resolve(_get("DATA_DIR", "runtime.data_dir"), "data")
LOG_DIR = _resolve(_get("LOG_DIR", "runtime.log_dir"), "logs")
PROCESSED_FILE = DATA_DIR / "processed_videos.json"
UPLOAD_COUNTS_FILE = DATA_DIR / "upload_counts.json"
FB_STATE_FILE = DATA_DIR / "facebook_state.json"
IG_STATE_FILE = DATA_DIR / "instagram_state.json"
X_STATE_FILE = DATA_DIR / "x_state.json"
TIKTOK_STATE_FILE = DATA_DIR / "tiktok_state.json"
TIKTOK_TOKEN_FILE = DATA_DIR / "tiktok_token.json"
LOG_FILE = LOG_DIR / "relay.log"

LOG_LEVEL = str(_get("LOG_LEVEL", "runtime.log_level", "INFO")).upper()
LOG_MAX_BYTES = _as_int(_get("LOG_MAX_BYTES", "runtime.log_max_bytes", 10_485_760), 10_485_760)
LOG_BACKUP_COUNT = _as_int(_get("LOG_BACKUP_COUNT", "runtime.log_backup_count", 5), 5)


def ensure_dirs() -> None:
    """Create every runtime directory. Safe to call repeatedly."""
    for directory in (DATA_DIR, LOG_DIR, DOWNLOAD_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
