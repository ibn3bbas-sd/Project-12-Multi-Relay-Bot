from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from src import tiktok_uploader as tt
from tests.conftest import FakeResponse

MB = 1024 * 1024
INFO_15FPS = {"seconds": 60, "bytes": 1000, "vcodec": "h264", "width": 368, "height": 368, "fps": 15.0,
              "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 44100}


@pytest.mark.parametrize("size, expected", [
    (3 * MB, (3 * MB, 1)),          # under 5 MB: whole file
    (7 * MB, (7 * MB, 1)),          # 5–10 MB: chunk must not exceed the file (the real bug)
    (64 * MB, (64 * MB, 1)),        # up to 64 MB: one chunk
    (65 * MB, (10 * MB, 6)),        # bigger: 10 MB chunks, last one takes the remainder
    (100 * MB, (10 * MB, 10)),
])
def test_chunks(size, expected):
    chunk, count = tt._chunks(size)
    assert (chunk, count) == expected
    assert chunk <= size
    assert size - chunk * (count - 1) <= 2 * max(chunk, 64 * MB)  # last chunk stays within limits


def test_needs_reencode_low_fps_and_small_frame():
    assert tt._needs_reencode(INFO_15FPS)
    ok = {**INFO_15FPS, "fps": 30.0}
    assert not tt._needs_reencode(ok)
    assert tt._needs_reencode({**ok, "width": 300, "height": 300})


def test_build_title_strips_links():
    assert tt.build_title({"title": "Hello https://x.y www.a.b world"}).startswith("Hello world\n\n")


def _fake_api(calls, puts, init_privacy=None):
    def post(url, **kw):
        calls.append(url)
        if url.endswith("/oauth/token/"):
            return FakeResponse(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 86400,
                                      "refresh_expires_in": 31536000, "open_id": "o"})
        assert kw["headers"]["Authorization"] == "Bearer new"
        if "creator_info" in url:
            return FakeResponse(200, {"data": {"creator_username": "u", "privacy_level_options": ["SELF_ONLY"],
                                               "max_video_post_duration_sec": 180}, "error": {"code": "ok"}})
        if "video/init" in url:
            assert kw["json"]["post_info"]["privacy_level"] == "SELF_ONLY"
            return FakeResponse(200, {"data": {"publish_id": "p1", "upload_url": "https://up"}, "error": {"code": "ok"}})
        if "status/fetch" in url:
            return FakeResponse(200, {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})
        raise AssertionError(url)

    def put(url, data, **kw):
        puts.append(kw["headers"]["Content-Range"])
        return FakeResponse(201)

    return post, put


def test_upload_short_refreshes_token_and_posts(tmp_path):
    tt.save_token({"access_token": "old", "refresh_token": "r", "expires_at": "2000-01-01T00:00:00+00:00",
                   "refresh_expires_at": "2099-01-01T00:00:00+00:00"})
    src = tmp_path / "v.mp4"
    src.write_bytes(b"0" * 1000)

    def fake_prepare(path, info):
        out = path.with_name("ready.mp4")
        out.write_bytes(b"1" * 1000)
        return out

    calls, puts = [], []
    post, put = _fake_api(calls, puts)
    with mock.patch.object(tt.requests, "post", post), mock.patch.object(tt.requests, "put", put), \
            mock.patch.object(tt, "probe", lambda p: INFO_15FPS), mock.patch.object(tt, "prepare", fake_prepare), \
            mock.patch.object(tt.time, "sleep", lambda s: None):
        assert tt.upload_short(src, {"id": "abc", "title": "t"}) == "p1"
    assert puts == ["bytes 0-999/1000"]
    assert tt._load_token()["access_token"] == "new"
    assert not (tmp_path / "ready.mp4").exists()  # temp file cleaned up


def test_privacy_not_allowed_is_auth_error(tmp_path):
    tt.save_token({"access_token": "new", "refresh_token": "r", "expires_at": "2099-01-01T00:00:00+00:00"})
    post, _ = _fake_api([], [])
    with mock.patch.object(tt.config, "TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE"), mock.patch.object(tt.requests, "post", post):
        with pytest.raises(tt.TikTokAuthError, match="not allowed"):
            tt.upload_short(tmp_path / "v.mp4", {"id": "abc", "title": "t"})


def test_unaudited_refusal_pauses_automatic_posts_only():
    tt.save_token({"access_token": "a", "refresh_token": "r", "expires_at": "2099-01-01T00:00:00+00:00"})
    refusal = FakeResponse(403, {"error": {
        "code": "unaudited_client_can_only_post_to_private_accounts",
        "message": "Please review our integration guidelines at https://developers.tiktok.com/doc/x/"}})
    with mock.patch.object(tt.requests, "post", lambda *a, **k: refusal):
        with pytest.raises(tt.TikTokLimit, match="waiting for TikTok app review"):
            tt._api("/post/publish/video/init/", {}, retries=0)
    assert "app review" in tt.pacing_block()
    assert tt.pacing_block(ignore_gap=True) is None      # /ttfill still tries
    tt.mark_done("v1")
    assert tt.pacing_block().startswith("spacing posts")  # a successful post clears the pause


def test_error_messages_lose_doc_links():
    assert tt._no_links("Bad thing. Please review our guidelines at https://x.y/") == "Bad thing."


def test_daily_cap():
    for i in range(3):
        tt.mark_done(f"v{i}")
    assert tt.pacing_block(ignore_gap=True).startswith("daily limit: 3/3")
