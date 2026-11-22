import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

from src import config, pipeline, stats_db


def _today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def test_import_history_runs_once():
    day = (_today() - timedelta(days=1)).isoformat()
    config.UPLOAD_COUNTS_FILE.write_text(json.dumps({day: 4}))
    config.IG_STATE_FILE.write_text(json.dumps({"done": ["a"], "counts": {day: 2}}))
    stats_db.import_history()
    stats_db.import_history()
    n = sqlite3.connect(config.DATA_DIR / "relay.db").execute(
        "SELECT COUNT(*) FROM posts WHERE source='import'").fetchone()[0]
    assert n == 6


def test_report_counts_and_last_failure():
    video = {"id": "vid1", "title": "t"}
    stats_db.record("youtube", "posted", video, kind="video", ref="yt1")
    stats_db.record("tiktok", "failed", video, detail="chunk size is invalid")
    summary = pipeline.new_summary()
    summary["started_at"] = datetime.now(timezone.utc) - timedelta(seconds=5)
    summary["finished_at"] = datetime.now(timezone.utc)
    stats_db.record_run(summary, "check")
    with mock.patch("src.health.expiry_lines", lambda: []):
        report = stats_db.build_report(7)
    assert "▶️ YouTube: today 1 · 7d 1" in report
    assert "🎵 TikTok: today 0 · 7d 0 · ❌ 1" in report
    assert "🔁 Runs: 1" in report
    assert "chunk size is invalid" in report


def test_write_failure_never_raises():
    with mock.patch.object(stats_db, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")):
        stats_db.record("youtube", "posted", {"id": "x"})
        stats_db.record_run(pipeline.new_summary(), "check")
