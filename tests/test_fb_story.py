"""Facebook Stories: start → rupload → finish, 9:16 preparation, skips and pacing."""

import pytest

from src import config, fb_story_uploader as fbs, pipeline
from tests.conftest import FakeResponse

VERTICAL = {"seconds": 40.0, "bytes": 9 * 1024 * 1024, "vcodec": "h264", "width": 1080,
            "height": 1920, "fps": 30.0, "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 44100}


def test_vertical_h264_is_only_remuxed_landscape_is_letterboxed():
    assert not fbs._needs_reencode(VERTICAL)
    assert fbs._needs_reencode({**VERTICAL, "width": 1920, "height": 1080})


def test_long_short_is_unsuitable():
    assert fbs.unsuitable({**VERTICAL, "seconds": 75.0}).startswith("too long")
    assert fbs.unsuitable(VERTICAL) is None


@pytest.fixture
def page(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FB_PAGE_ID", "page1")
    monkeypatch.setattr(config, "FB_PAGE_TOKEN", "tok")
    monkeypatch.setattr(fbs, "probe", lambda p: dict(VERTICAL))
    ready = tmp_path / "s.story.mp4"
    monkeypatch.setattr(fbs, "prepare", lambda p, i: (ready.write_bytes(b"x" * 10), ready)[1])
    return tmp_path / "s.mp4"


def test_story_flow(monkeypatch, page):
    calls = []

    def post(url, data=None, headers=None, timeout=None):
        if hasattr(data, "read"):
            calls.append(("rupload", url, headers["file_size"]))
            return FakeResponse(200, {"success": True})
        calls.append((data["upload_phase"], url))
        if data["upload_phase"] == "start":
            return FakeResponse(200, {"video_id": "v9", "upload_url": "https://rupload.example/v9"})
        assert data["video_id"] == "v9"
        return FakeResponse(200, {"success": True, "post_id": "page1_77"})

    monkeypatch.setattr(fbs.requests, "post", post)
    assert fbs.upload_short(page, {"id": "a1", "title": "t"}) == "page1_77"
    assert [c[0] for c in calls] == ["start", "rupload", "finish"]
    assert calls[0][1].endswith("/page1/video_stories")
    assert calls[1][2] == "10"


def test_auth_error_stops_the_target(monkeypatch, page):
    monkeypatch.setattr(fbs.requests, "post",
                        lambda *a, **k: FakeResponse(400, {"error": {"code": 190, "message": "expired"}}))
    with pytest.raises(fbs.FacebookAuthError):
        fbs.upload_short(page, {"id": "a2", "title": "t"})


def test_rate_limit_raises_limit(monkeypatch, page):
    monkeypatch.setattr(fbs.requests, "post",
                        lambda *a, **k: FakeResponse(400, {"error": {"code": 32, "message": "slow down"}}))
    with pytest.raises(fbs.FacebookStoryLimit):
        fbs.upload_short(page, {"id": "a3", "title": "t"})


def test_too_long_is_skipped_not_failed(monkeypatch, page):
    monkeypatch.setattr(fbs, "probe", lambda p: {**VERTICAL, "seconds": 90.0})
    assert fbs.upload_short(page, {"id": "a4", "title": "t"}) is None
    assert fbs.is_handled("a4")


def test_daily_cap(monkeypatch):
    monkeypatch.setattr(config, "FBS_DAILY_LIMIT", 1)
    assert fbs.pacing_block(ignore_gap=True) is None
    fbs.mark_done("a5")
    assert fbs.pacing_block(ignore_gap=True).startswith("daily limit")


def test_summary_shows_stories(monkeypatch):
    monkeypatch.setattr(config, "FBS_ENABLED", True)
    s = {**pipeline.new_summary(), "fbs_posted": 1}
    assert "📖 Facebook Stories: 1 posted" in pipeline.format_summary(s)
    assert not pipeline.is_quiet(s)
