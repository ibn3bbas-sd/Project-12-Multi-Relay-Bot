"""Post/run history in SQLite (DATA_DIR/relay.db) and the /stats report.

One row per post attempt (posted / failed / skipped) on every platform, one row per relay run.
The JSON state files stay the source of truth for "what is already posted"; this database is
history only, so every write here is best-effort and never breaks a relay.

History before the database existed is imported once from the state files' per-day counters
(rows with source='import', no video id). A dashboard tool (Superset, Metabase, Grafana) can
read relay.db directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

logger = logging.getLogger(__name__)

PLATFORMS = {  # key → (icon, name)
    "youtube": ("▶️", "YouTube"),
    "facebook": ("📘", "Facebook"),
    "instagram": ("📸", "Instagram"),
    "x": ("✖️", "X"),
    "tiktok": ("🎵", "TikTok"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,              -- UTC ISO time
    day       TEXT NOT NULL,              -- local day (TIMEZONE), YYYY-MM-DD
    platform  TEXT NOT NULL,              -- youtube | facebook | instagram | x | tiktok
    status    TEXT NOT NULL,              -- posted | failed | skipped
    video_id  TEXT,                       -- source YouTube video id
    title     TEXT,
    kind      TEXT,                       -- video | reel | short
    ref       TEXT,                       -- id on the target platform
    detail    TEXT,                       -- error / skip reason
    source    TEXT NOT NULL DEFAULT 'live'  -- live | import
);
CREATE INDEX IF NOT EXISTS posts_day ON posts(day, platform, status);
CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY,
    started   TEXT NOT NULL,
    finished  TEXT,
    trigger   TEXT,                       -- check | fill
    found     INTEGER, uploaded INTEGER, failed INTEGER,
    aborted   TEXT,
    seconds   REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _db_path():
    return config.DATA_DIR / "relay.db"


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.executescript(SCHEMA)
    return conn


def _local_day(ts: datetime) -> str:
    return ts.astimezone(ZoneInfo(config.TIMEZONE)).date().isoformat()


# ── Writes (best-effort) ─────────────────────────────────────────────────────
def record(platform: str, status: str, video: dict | None = None, *, kind: str | None = None,
           ref: str | None = None, detail: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    video = video or {}
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO posts (ts, day, platform, status, video_id, title, kind, ref, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now.isoformat(timespec="seconds"), _local_day(now), platform, status,
                 video.get("id"), video.get("title"), kind, ref, (detail or None) and str(detail)[:500]),
            )
    except sqlite3.Error as exc:
        logger.warning("stats db: could not record %s %s: %s", platform, status, exc)


def record_run(summary: dict, trigger: str) -> None:
    started, finished = summary.get("started_at"), summary.get("finished_at")
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO runs (started, finished, trigger, found, uploaded, failed, aborted, seconds)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (started.isoformat(timespec="seconds") if started else datetime.now(timezone.utc).isoformat(),
                 finished.isoformat(timespec="seconds") if finished else None, trigger,
                 summary.get("found"), summary.get("uploaded"), summary.get("failed"), summary.get("aborted"),
                 (finished - started).total_seconds() if started and finished else None),
            )
    except sqlite3.Error as exc:
        logger.warning("stats db: could not record run: %s", exc)


def import_history() -> None:
    """Once: turn the state files' per-day counters into 'import' rows (no video ids)."""
    sources = {
        "youtube": config.UPLOAD_COUNTS_FILE,  # flat {day: n}
        "facebook": config.FB_STATE_FILE,
        "instagram": config.IG_STATE_FILE,
        "x": config.X_STATE_FILE,
        "tiktok": config.TIKTOK_STATE_FILE,
    }
    try:
        with closing(_connect()) as conn, conn:
            if conn.execute("SELECT 1 FROM meta WHERE key='history_imported'").fetchone():
                return
            rows = 0
            for platform, path in sources.items():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                counts = data if platform == "youtube" else data.get("counts", {})
                for day, n in (counts or {}).items():
                    for _ in range(int(n)):
                        conn.execute(
                            "INSERT INTO posts (ts, day, platform, status, source) VALUES (?, ?, ?, 'posted', 'import')",
                            (f"{day}T12:00:00+00:00", day, platform),
                        )
                        rows += 1
            conn.execute("INSERT INTO meta VALUES ('history_imported', ?)",
                         (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        logger.info("stats db: imported %d historical posts", rows)
    except sqlite3.Error as exc:
        logger.warning("stats db: history import failed: %s", exc)


# ── Report ───────────────────────────────────────────────────────────────────
def _totals() -> dict[str, int]:
    """All-time posted counts from the state files (they predate the database)."""
    from . import downloader, fb_uploader, ig_uploader, tiktok_uploader, x_uploader
    out = {}
    try:
        out["youtube"] = len(downloader._load_processed())
    except Exception:  # noqa: BLE001
        pass
    for key, mod in (("facebook", fb_uploader), ("instagram", ig_uploader), ("x", x_uploader),
                     ("tiktok", tiktok_uploader)):
        try:
            out[key] = len(mod._load_state()["done"])
        except Exception:  # noqa: BLE001
            pass
    return out


def _enabled() -> list[str]:
    flags = {"youtube": True, "facebook": config.FB_ENABLED, "instagram": config.IG_ENABLED,
             "x": config.X_ENABLED, "tiktok": config.TIKTOK_ENABLED}
    return [k for k, on in flags.items() if on]


def build_report(days: int = 7) -> str:
    days = max(1, min(days, 30))
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    first = (today - timedelta(days=days - 1)).isoformat()
    with closing(_connect()) as conn:
        per = {
            (p, s, d): n
            for p, s, d, n in conn.execute(
                "SELECT platform, status, day = ?, COUNT(*) FROM posts WHERE day >= ? GROUP BY 1, 2, 3",
                (today.isoformat(), first),
            )
        }
        daily = dict(conn.execute(
            "SELECT day, COUNT(*) FROM posts WHERE status='posted' AND day >= ? GROUP BY day", (first,)))
        last_fail = conn.execute(
            "SELECT ts, platform, detail FROM posts WHERE status='failed' ORDER BY id DESC LIMIT 1").fetchone()
        runs = conn.execute(
            "SELECT COUNT(*), SUM(aborted IS NOT NULL), MAX(finished) FROM runs WHERE started >= ?",
            ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),)).fetchone()

    def count(p: str, s: str, today_only: bool = False) -> int:
        return per.get((p, s, 1), 0) + (0 if today_only else per.get((p, s, 0), 0))

    totals = _totals()
    lines = [f"📊 Stats · last {days} day{'s' if days > 1 else ''}", "━━━━━━━━━━━━━━"]
    for key in _enabled():
        icon, name = PLATFORMS[key]
        posted, failed = count(key, "posted"), count(key, "failed")
        line = f"{icon} {name}: today {count(key, 'posted', True)} · {days}d {posted}"
        if failed:
            line += f" · ❌ {failed}"
        if key in totals:
            line += f" · total {totals[key]}"
        lines.append(line)

    lines += ["", "📈 Posts per day (all platforms)"]
    peak = max(daily.values(), default=0)
    for i in range(days - 1, -1, -1):
        d: date = today - timedelta(days=i)
        n = daily.get(d.isoformat(), 0)
        bar = "▇" * (round(n / peak * 10) if peak else 0)
        lines.append(f"{d:%a %d} {bar} {n}")

    lines.append("━━━━━━━━━━━━━━")
    n_runs, n_aborted, last_run = runs if runs else (0, 0, None)
    if n_runs:
        when = datetime.fromisoformat(last_run).astimezone(ZoneInfo(config.TIMEZONE)) if last_run else None
        lines.append(f"🔁 Runs: {n_runs}" + (f" · 🛑 {n_aborted} aborted" if n_aborted else "")
                     + (f" · last {when:%a %H:%M}" if when else ""))
    if last_fail:
        ts, platform, detail = last_fail
        when = datetime.fromisoformat(ts).astimezone(ZoneInfo(config.TIMEZONE))
        lines.append(f"❌ Last failure: {PLATFORMS.get(platform, ('', platform))[1]} {when:%a %H:%M}"
                     + (f" — {detail[:120]}" if detail else ""))
    from . import health
    keys = health.expiry_lines()
    if keys:
        lines += ["", "🔑 Expiries"] + keys
    return "\n".join(lines)
