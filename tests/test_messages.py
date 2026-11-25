from datetime import datetime, timedelta, timezone

from src import pipeline, telegram_bot


def test_summary_shows_every_enabled_platform_and_waiting_reason():
    s = pipeline.new_summary()
    now = datetime.now(timezone.utc)
    s.update(found=1, uploaded=1, today=3, ig_posted=1, tt_paced="waiting for TikTok app review",
             started_at=now - timedelta(seconds=24), finished_at=now)
    text = pipeline.format_summary(s, "Scheduled check")
    assert text.startswith("✅ Scheduled check")
    for line in ("▶️ YouTube: 1 uploaded", "📘 Facebook:", "📸 Instagram: 1 posted", "📖 Facebook Stories:", "✖️ X:", "🎵 TikTok:",
                 "⏳ waiting for TikTok app review", "⏱ 24s"):
        assert line in text


def test_summary_flags_problems():
    s = pipeline.new_summary()
    s["x_error"] = "HTTP 402"
    assert pipeline.format_summary(s).startswith("⚠️")


def test_only_waiting_is_quiet():
    assert pipeline.is_quiet({**pipeline.new_summary(), "tt_paced": "x"})
    assert not pipeline.is_quiet({**pipeline.new_summary(), "tt_posted": 1})
    assert not pipeline.is_quiet({**pipeline.new_summary(), "tt_error": "x"})


def test_fill_reply_icon():
    base = {"key": "tt", "posted": 0, "failed": 0, "paced": None, "error": None}
    assert pipeline.format_shorts_fill({**base, "posted": 1}).startswith("✅")
    assert pipeline.format_shorts_fill({**base, "paced": "waiting"}).startswith("⏳")
    assert pipeline.format_shorts_fill({**base, "failed": 1}).startswith("⚠️")


def test_help_lists_commands():
    for cmd in ("/run", "/fill", "/fbfill", "/igfill", "/fbsfill", "/xfill", "/ttfill", "/status", "/stats", "/next", "/help"):
        assert cmd in telegram_bot.HELP_TEXT
