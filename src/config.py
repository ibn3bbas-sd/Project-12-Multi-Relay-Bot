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
SCHEDULE_MORNING = str(_get("SCHEDULE_MORNING", "scheduler.morning", "06:00"))
SCHEDULE_EVENING = str(_get("SCHEDULE_EVENING", "scheduler.evening", "18:00"))
TIMEZONE = str(_get("TIMEZONE", "scheduler.timezone", "Asia/Riyadh"))

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

# ── Gemini (optional) ────────────────────────────────────────────────────────
GEMINI_API_KEY = str(_get("GEMINI_API_KEY", "gemini.api_key", "") or "")
GEMINI_ENABLED = _as_bool(_get("GEMINI_ENABLED", "gemini.enabled", False))

# ── Runtime dirs ─────────────────────────────────────────────────────────────
DATA_DIR = _resolve(_get("DATA_DIR", "runtime.data_dir"), "data")
LOG_DIR = _resolve(_get("LOG_DIR", "runtime.log_dir"), "logs")
PROCESSED_FILE = DATA_DIR / "processed_videos.json"
LOG_FILE = LOG_DIR / "relay.log"

LOG_LEVEL = str(_get("LOG_LEVEL", "runtime.log_level", "INFO")).upper()
LOG_MAX_BYTES = _as_int(_get("LOG_MAX_BYTES", "runtime.log_max_bytes", 10_485_760), 10_485_760)
LOG_BACKUP_COUNT = _as_int(_get("LOG_BACKUP_COUNT", "runtime.log_backup_count", 5), 5)


def ensure_dirs() -> None:
    """Create every runtime directory. Safe to call repeatedly."""
    for directory in (DATA_DIR, LOG_DIR, DOWNLOAD_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
