"""Test setup: a throwaway DATA_DIR and every platform enabled, BEFORE src.config is imported.

src.config reads the environment once at import, so these must be set at module import time.
No test touches the network: HTTP calls are mocked.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_DATA = Path(tempfile.mkdtemp(prefix="relay-test-"))
os.environ.update({
    "DATA_DIR": str(_DATA),
    "LOG_DIR": str(_DATA / "logs"),
    "TELEGRAM_BOT_TOKEN": "123:test",
    "TELEGRAM_ALLOWED_CHAT_ID": "1",
    "SOURCE_CHANNEL_ID": "UCtest",
    "TIMEZONE": "Asia/Riyadh",
    "FB_ENABLED": "true",
    "FB_PAGE_TOKEN": "fb-test",
    "IG_ENABLED": "true",
    "X_ENABLED": "true",
    "TIKTOK_ENABLED": "true",
    "TIKTOK_CLIENT_KEY": "k",
    "TIKTOK_CLIENT_SECRET": "s",
    "TIKTOK_PRIVACY": "SELF_ONLY",
    "HEALTHCHECK_URL": "",
})


@pytest.fixture(autouse=True)
def clean_data_dir():
    """Each test starts with an empty DATA_DIR (state files, token, relay.db)."""
    for child in _DATA.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    yield


@pytest.fixture
def data_dir() -> Path:
    return _DATA


class FakeResponse:
    """Just enough of requests.Response for the uploaders."""

    def __init__(self, status: int = 200, body: dict | None = None):
        import json as _json
        self.status_code = status
        self._body = body if body is not None else {}
        self.ok = status < 400
        self.content = b"x"
        self.text = _json.dumps(self._body)

    def json(self):
        return self._body
