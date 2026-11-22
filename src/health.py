"""Expiry reminders and the "bot went silent" watchdog.

Expiries checked (each only when that platform is in use):
    TikTok   refresh token        DATA_DIR/tiktok_token.json  refresh_expires_at (365 days)
    Facebook Page token / data access  Graph debug_token: expires_at (0 = never), data_access_expires_at
    YouTube  yt-dlp cookies       earliest expiry of the Google login cookies in YTDLP_COOKIES_FILE
             (Google can still revoke them earlier; that shows as a bot-check in the run reports)

Watchdog (runs inside the bot, every WATCHDOG_MINUTES):
    silent  no relay run has finished for SILENT_ALERT_HOURS
    stuck   one relay has been running for more than STUCK_ALERT_MINUTES
    failing the last 3 runs were all aborted
Each alert is sent once and followed by a "recovered" message when it clears.
The watchdog can't report a bot that is completely down: for that, set HEALTHCHECK_URL to an
external dead-man's-switch (e.g. healthchecks.io), pinged after every run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import requests

from . import config, stats_db

logger = logging.getLogger(__name__)

# Google login cookies that yt-dlp's authenticated requests depend on.
AUTH_COOKIES = {"SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}


# ── Expiries ─────────────────────────────────────────────────────────────────
def _tiktok_expiry() -> datetime | None:
    try:
        tok = json.loads(config.TIKTOK_TOKEN_FILE.read_text(encoding="utf-8"))
        return datetime.fromisoformat(tok["refresh_expires_at"])
    except (OSError, ValueError, KeyError):
        return None


def _facebook_expiry() -> tuple[datetime | None, str]:
    """Earliest of the token expiry and the data-access expiry. ('', reason) when unknown."""
    token = config.FB_PAGE_TOKEN
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{config.FB_API_VERSION}/debug_token",
            params={"input_token": token, "access_token": token}, timeout=20,
        )
        data = resp.json().get("data") or {}
    except (requests.RequestException, ValueError) as exc:
        return None, f"lookup failed: {type(exc).__name__}"
    if data.get("is_valid") is False:
        return datetime.now(timezone.utc), "token is no longer valid"
    stamps = [int(data.get(k) or 0) for k in ("expires_at", "data_access_expires_at")]
    stamps = [s for s in stamps if s > 0]  # 0 = never expires
    if not stamps:
        return None, "never expires"
    return datetime.fromtimestamp(min(stamps), timezone.utc), ""


def _cookies_expiry() -> datetime | None:
    path = config.YTDLP_COOKIES_FILE
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stamps = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and parts[5] in AUTH_COOKIES and ("google" in parts[0] or "youtube" in parts[0]):
            try:
                if int(parts[4]) > 0:
                    stamps.append(int(parts[4]))
            except ValueError:
                pass
    return datetime.fromtimestamp(min(stamps), timezone.utc) if stamps else None


def expiries() -> list[dict]:
    """[{icon, name, at (datetime|None), note, fix}] for the credentials in use."""
    out = []
    if config.YTDLP_COOKIES_FILE:
        out.append({"icon": "🍪", "name": "YouTube cookies", "at": _cookies_expiry(), "note": "",
                    "fix": "export fresh cookies (runbook 2b)"})
    if config.FB_ENABLED and config.FB_PAGE_TOKEN:
        at, note = _facebook_expiry()
        out.append({"icon": "📘", "name": "Facebook token", "at": at, "note": note,
                    "fix": "re-run ~/relay-setup/ig-token.py for a new Page token"})
    if config.TIKTOK_ENABLED:
        out.append({"icon": "🎵", "name": "TikTok login", "at": _tiktok_expiry(), "note": "",
                    "fix": "re-run tiktok-auth.py + tiktok-deploy.py (runbook §8)"})
    return out


def _days_left(at: datetime) -> int:
    return (at - datetime.now(timezone.utc)).days


def expiry_lines() -> list[str]:
    lines = []
    for e in expiries():
        if e["at"] is None:
            lines.append(f"{e['icon']} {e['name']}: {e['note'] or 'unknown'}")
        else:
            days = _days_left(e["at"])
            flag = "⚠️ " if days <= config.REMIND_DAYS else ""
            lines.append(f"{flag}{e['icon']} {e['name']}: {days} days ({e['at']:%Y-%m-%d})")
    return lines


def due_reminders() -> str | None:
    """Telegram text for credentials expiring within REMIND_DAYS, or None."""
    due = [e for e in expiries() if e["at"] is not None and _days_left(e["at"]) <= config.REMIND_DAYS]
    if not due:
        return None
    lines = ["🔑 Expiry reminder", "━━━━━━━━━━━━━━"]
    for e in due:
        days = _days_left(e["at"])
        when = "EXPIRED" if days < 0 else ("today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}")
        lines.append(f"{'🛑' if days <= 3 else '⚠️'} {e['icon']} {e['name']} expires {when} ({e['at']:%Y-%m-%d})")
        lines.append(f"   ↳ {e['fix']}")
    return "\n".join(lines)


# ── Watchdog ─────────────────────────────────────────────────────────────────
def _last_runs(n: int = 3) -> list[tuple[str | None, str | None]]:
    """[(finished, aborted)] newest first."""
    try:
        with closing(stats_db._connect()) as conn:
            return conn.execute("SELECT finished, aborted FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("watchdog: cannot read runs: %s", exc)
        return []


def check(started_at: datetime, running_since: datetime | None) -> dict[str, str | None]:
    """Current problems by kind (None = fine). started_at: bot start (baseline before the first run)."""
    now = datetime.now(timezone.utc)
    runs = _last_runs()
    finished = [datetime.fromisoformat(f) for f, _ in runs if f]
    last = max(finished + [started_at])

    problems: dict[str, str | None] = {"silent": None, "stuck": None, "failing": None}
    silent_after = timedelta(hours=config.SILENT_ALERT_HOURS)
    if now - last > silent_after and running_since is None:
        hours = (now - last).total_seconds() / 3600
        problems["silent"] = f"no relay run has finished for {hours:.0f} h (checks run every {config.CHECK_INTERVAL_MINUTES} min)"
    if running_since and now - running_since > timedelta(minutes=config.STUCK_ALERT_MINUTES):
        minutes = int((now - running_since).total_seconds() // 60)
        problems["stuck"] = f"a relay has been running for {minutes} min — it may be stuck (download/upload hang)"
    # A YouTube quota stop is routine on busy days (it resets daily), not a failure.
    if len(runs) == 3 and all(a and "quota" not in a.lower() for _, a in runs):
        problems["failing"] = f"the last 3 runs were all aborted — latest: {runs[0][1]}"
    return problems


def ping_healthcheck(ok: bool) -> None:
    """External dead-man's switch (optional). Never raises."""
    url = config.HEALTHCHECK_URL
    if not url:
        return
    try:
        requests.get(url if ok else url.rstrip("/") + "/fail", timeout=10)
    except requests.RequestException as exc:
        logger.warning("healthcheck ping failed: %s", exc)
