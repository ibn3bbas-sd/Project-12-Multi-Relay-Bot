import asyncio
from datetime import datetime, timedelta, timezone
from unittest import mock

from src import config, health, pipeline, stats_db, telegram_bot
from src import tiktok_uploader as tt
from tests.conftest import FakeResponse


def _run(aborted=None):
    s = pipeline.new_summary()
    s["started_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    s["finished_at"] = datetime.now(timezone.utc)
    s["aborted"] = aborted
    stats_db.record_run(s, "check")


def test_cookie_expiry_uses_google_login_cookies_only(data_dir):
    now = datetime.now(timezone.utc)
    soon, far = int((now + timedelta(days=10)).timestamp()), int((now + timedelta(days=300)).timestamp())
    ck = data_dir / "cookies.txt"
    ck.write_text(
        "# Netscape HTTP Cookie File\n"
        f".youtube.com\tTRUE\t/\tTRUE\t{far}\tSID\tx\n"
        f"#HttpOnly_.google.com\tTRUE\t/\tTRUE\t{soon}\t__Secure-3PSID\tx\n"
        f".youtube.com\tTRUE\t/\tTRUE\t{soon - 86400 * 5}\tVISITOR_INFO1_LIVE\tx\n"
    )
    with mock.patch.object(config, "YTDLP_COOKIES_FILE", ck):
        assert health._cookies_expiry() == datetime.fromtimestamp(soon, timezone.utc)


def test_due_reminders():
    tt.save_token({"access_token": "a", "refresh_token": "r",
                   "refresh_expires_at": (datetime.now(timezone.utc) + timedelta(days=2, hours=1)).isoformat()})
    fb = FakeResponse(200, {"data": {"is_valid": True, "expires_at": 0, "data_access_expires_at":
                                     int((datetime.now(timezone.utc) + timedelta(days=85)).timestamp())}})
    with mock.patch.object(health.requests, "get", lambda *a, **k: fb), mock.patch.object(config, "YTDLP_COOKIES_FILE", None):
        text = health.due_reminders()
        lines = health.expiry_lines()
    assert "🛑 🎵 TikTok login expires in 2 days" in text
    assert "Facebook" not in text                       # 85 days: no reminder yet
    assert any(line.startswith("📘 Facebook token: 84 days") for line in lines)


def test_facebook_invalid_token_is_due_now():
    bad = FakeResponse(200, {"data": {"is_valid": False}})
    with mock.patch.object(health.requests, "get", lambda *a, **k: bad):
        at, note = health._facebook_expiry()
    assert note == "token is no longer valid" and at <= datetime.now(timezone.utc)


def test_watchdog_conditions():
    start = datetime.now(timezone.utc) - timedelta(hours=7)
    assert health.check(start, None)["silent"]
    _run()
    assert health.check(start, None) == {"silent": None, "stuck": None, "failing": None}
    assert health.check(start, datetime.now(timezone.utc) - timedelta(minutes=100))["stuck"]
    for _ in range(3):
        _run("YouTube API quota exceeded")
    assert health.check(start, None)["failing"] is None    # quota stops are routine
    for _ in range(3):
        _run("RuntimeError: boom")
    assert "boom" in health.check(start, None)["failing"]


def test_watchdog_alerts_once_then_recovers():
    sent = []

    class Bot:
        async def send_message(self, chat_id, text):
            sent.append(text.splitlines()[0])

    ctx = type("Ctx", (), {"bot": Bot(), "application": type("App", (), {"bot_data": {}})()})()
    for _ in range(3):
        _run("RuntimeError: boom")

    async def go():
        await telegram_bot.watchdog(ctx)
        await telegram_bot.watchdog(ctx)   # same problem: no second alert
        _run()                              # a healthy run clears it
        await telegram_bot.watchdog(ctx)

    asyncio.run(go())
    assert sent == ["🛑 Runs failing", "✅ Recovered: runs failing cleared"]


def test_healthcheck_ping_off_when_unset():
    with mock.patch.object(health.requests, "get") as get:
        health.ping_healthcheck(ok=True)
    get.assert_not_called()
    with mock.patch.object(config, "HEALTHCHECK_URL", "https://hc-ping.com/abc"), \
            mock.patch.object(health.requests, "get") as get:
        health.ping_healthcheck(ok=False)
    get.assert_called_once_with("https://hc-ping.com/abc/fail", timeout=10)
