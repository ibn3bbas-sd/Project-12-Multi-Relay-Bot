"""Instagram: smaller Reel copies, a short wait after ProcessingFailedError, skip after N failed runs."""

from pathlib import Path

import pytest

from src import config, ig_uploader

INFO_1080 = {"seconds": 50.0, "bytes": 25 * 1024 * 1024, "vcodec": "h264", "width": 1080,
             "height": 1920, "fps": 30.0, "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 44100}


def test_1080p_short_is_reencoded_smaller():
    assert ig_uploader._needs_reencode(INFO_1080)
    kbps = ig_uploader._video_kbps(INFO_1080)
    assert 1000 <= kbps <= 3000
    # 50 s at the cap stays near IG_TARGET_MB
    assert (kbps + 128) * 1000 / 8 * 50 <= config.IG_TARGET_MB * 1024 * 1024 * 1.05


def test_small_480p_short_is_only_remuxed():
    small = {**INFO_1080, "width": 480, "height": 854, "bytes": 5 * 1024 * 1024}
    assert not ig_uploader._needs_reencode(small)


def test_skipped_after_max_failed_runs():
    for n in range(1, config.IG_MAX_FAILS):
        assert ig_uploader.record_failure("vid1", "boom") == n
        assert not ig_uploader.is_handled("vid1")
    ig_uploader.record_failure("vid1", "boom")
    assert ig_uploader.is_handled("vid1")
    assert "failed" in ig_uploader._load_state()["skipped"]["vid1"]


def test_success_clears_failures():
    ig_uploader.record_failure("vid2", "boom")
    ig_uploader.mark_done("vid2")
    assert "vid2" not in ig_uploader._load_state()["fails"]


@pytest.fixture
def fake_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "IG_USER_ID", "ig1")
    monkeypatch.setattr(config, "IG_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(ig_uploader, "probe", lambda p: dict(INFO_1080))
    ready = tmp_path / "x.ig.mp4"
    monkeypatch.setattr(ig_uploader, "prepare", lambda p, i: (ready.write_bytes(b"x"), ready)[1])
    return tmp_path / "x.mp4"


def test_processing_failed_waits_briefly_then_retries_with_new_container(monkeypatch, fake_upload):
    created, waits = [], []

    def graph(method, path, params=None, retries=None):
        if path.endswith("/media"):
            created.append(path)
            return {"id": f"c{len(created)}"}
        if path.endswith("/media_publish"):
            return {"id": "m1"}
        raise AssertionError(path)

    def rupload(uri, fp):
        if len(created) == 1:
            raise RuntimeError("Instagram upload: HTTP 400: ProcessingFailedError")

    def wait(cid, timeout=ig_uploader.POLL_TIMEOUT):
        waits.append((cid, timeout))
        if cid == "c1":
            raise RuntimeError("container still IN_PROGRESS after 2 min")

    monkeypatch.setattr(ig_uploader, "_graph", graph)
    monkeypatch.setattr(ig_uploader, "_rupload", rupload)
    monkeypatch.setattr(ig_uploader, "_wait_finished", wait)

    assert ig_uploader.upload_short(fake_upload, {"id": "vid3", "title": "t"}) == "m1"
    assert waits == [("c1", ig_uploader.FAILED_UPLOAD_WAIT), ("c2", ig_uploader.POLL_TIMEOUT)]


def test_failed_run_is_counted(monkeypatch, fake_upload):
    monkeypatch.setattr(ig_uploader, "_upload_container",
                        lambda fp, cap: (_ for _ in ()).throw(RuntimeError("container ERROR")))
    assert ig_uploader.upload_short(fake_upload, {"id": "vid4", "title": "t"}) is None
    assert ig_uploader._load_state()["fails"]["vid4"] == 1
    assert "container ERROR" in ig_uploader.LAST_ERROR
