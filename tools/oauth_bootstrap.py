#!/usr/bin/env python3
"""Headless-friendly YouTube OAuth consent for WSL.

WSL has no browser, so `run_local_server` cannot open one. Instead it binds a
fixed loopback port inside WSL and prints the consent URL for you to paste into
the Windows browser. WSL2's localhost forwarding delivers the redirect back.

The port must be fixed rather than 0/random: the Windows side has to reach this
exact port, and Google's desktop-client flow accepts any http://localhost:<port>/.

Run it as the service account so the token lands with the right ownership:
    sudo -u relaybot /opt/youtube-relay-bot/.venv/bin/python tools/oauth_bootstrap.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(APP_ROOT))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
PORT = int(os.environ.get("OAUTH_PORT", "8080"))

CLIENT_SECRET = APP_ROOT / "config" / "client_secret.json"
TOKEN_FILE = APP_ROOT / "config" / "token.json"


def main() -> int:
    if not CLIENT_SECRET.exists():
        print(f"ERROR: {CLIENT_SECRET} not found", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)

    creds = flow.run_local_server(
        host="localhost",
        # 0.0.0.0 so the listener is reachable however WSL proxies the request.
        bind_addr="0.0.0.0",
        port=PORT,
        open_browser=False,
        authorization_prompt_message=(
            "\n" + "=" * 70 + "\n"
            "  Open this URL in the Windows browser:\n\n  {url}\n\n"
            + "=" * 70 + "\n"
        ),
        success_message=(
            "Authorised. You can close this tab and return to the terminal."
        ),
        # Without offline access there is no refresh token, and the service
        # would need a browser again in an hour.
        access_type="offline",
        prompt="consent",
    )

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)

    if not creds.refresh_token:
        print(
            "WARNING: no refresh_token was returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and re-run, or the service "
            "will stop uploading when this token expires.",
            file=sys.stderr,
        )

    print(f"\nToken written to {TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
