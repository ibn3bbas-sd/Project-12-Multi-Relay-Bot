#!/usr/bin/env bash
# Step 0040 — One-time YouTube OAuth consent, producing config/token.json.
#
#   APPLY=true ./0040_oauth_bootstrap.sh
#
# WSL has no browser. The helper binds a loopback listener inside WSL and prints
# the consent URL; you paste it into the Windows browser over RDP. WSL2's
# localhost forwarding carries the redirect back to the listener.
#
# Prerequisite: config/client_secret.json from Google Cloud Console
# (APIs & Services -> Credentials -> OAuth 2.0 Client ID -> Desktop app).

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0040 — YouTube OAuth bootstrap"
require_wsl

CLIENT_SECRET="$APP_ROOT/config/client_secret.json"
TOKEN_FILE="$APP_ROOT/config/token.json"

[[ -x "$VENV_PYTHON" ]] || { err "Virtualenv missing. Run 0030_setup_app.sh first."; exit 1; }

if [[ ! -f "$CLIENT_SECRET" ]]; then
    err "Missing $CLIENT_SECRET"
    cat <<HINT

  Google Cloud Console -> APIs & Services -> Credentials
    -> Create OAuth client ID -> Desktop app -> Download JSON
    -> copy it in:  sudo cp /mnt/c/Users/<you>/Downloads/client_secret*.json $CLIENT_SECRET
    -> then:        sudo chown $APP_USER:$APP_USER $CLIENT_SECRET

HINT
    exit 1
fi
ok "Found $CLIENT_SECRET"

if [[ -f "$TOKEN_FILE" ]]; then
    warn "$TOKEN_FILE already exists. Delete it first to re-authorise."
    exit 0
fi

# The redirect URI must be registered on the OAuth client. Desktop-app clients
# accept any http://localhost:<port>/ — but the port must be fixed, not random,
# because the Windows browser has to reach this exact port through WSL.
if ! is_apply; then
    dry "run the consent flow on port $OAUTH_PORT and write $TOKEN_FILE"
    printf '\n%sRe-run with APPLY=true, with an RDP session open.%s\n' "$C_GREY" "$C_RESET"
    exit 0
fi

step "Starting consent flow on port $OAUTH_PORT"
cat <<NOTE

  1. A URL will print below. Copy it into the browser on the Windows desktop.
  2. Sign in with the account that owns the TARGET channel.
  3. Approve, and the browser will land on localhost:$OAUTH_PORT — that is WSL
     answering through localhost forwarding.

NOTE

sudo -u "$APP_USER" \
    APP_ROOT="$APP_ROOT" OAUTH_PORT="$OAUTH_PORT" \
    "$VENV_PYTHON" "$APP_ROOT/tools/oauth_bootstrap.py"

if [[ -f "$TOKEN_FILE" ]]; then
    chmod 600 "$TOKEN_FILE"
    chown "$APP_USER:$APP_USER" "$TOKEN_FILE"
    ok "Token written to $TOKEN_FILE"
    printf '\n'; ok "Step 0040 finished. Next: sudo -E APPLY=true ./0050_install_service.sh"
else
    err "Consent completed but token.json was not created."
    exit 1
fi
