#!/usr/bin/env bash
# Step 0050 — systemd unit inside WSL.
#
#   ./0050_install_service.sh                 # show the unit, change nothing
#   sudo -E APPLY=true ./0050_install_service.sh

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0050 — systemd service"
require_wsl
require_systemd
is_apply && require_root

[[ -x "$VENV_PYTHON" ]]      || { err "Virtualenv missing. Run 0030_setup_app.sh."; exit 1; }
[[ -f "$APP_ROOT/.env" ]]    || { err "$APP_ROOT/.env missing."; exit 1; }
if [[ ! -f "$APP_ROOT/config/token.json" ]]; then
    warn "config/token.json missing — the service will start but every upload will fail."
    warn "Run 0040_oauth_bootstrap.sh first."
fi

UNIT_CONTENT=$(cat <<UNIT
[Unit]
Description=YouTube Relay Bot
Documentation=file://$APP_ROOT/docs/wsl-deployment.md
After=network-online.target
Wants=network-online.target

# Restart rate limiting lives in [Unit], not [Service] (systemd >= 229).
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_ROOT
ExecStart=$VENV_PYTHON -m src.main

# Restart on crash, but back off so a bad config does not become a hot loop.
Restart=on-failure
RestartSec=30

# SIGINT rather than SIGTERM: python-telegram-bot closes its polling session
# cleanly on KeyboardInterrupt, avoiding a stale getUpdates conflict on restart.
KillSignal=SIGINT
TimeoutStopSec=30

# Arabic titles arrive mangled without an explicit UTF-8 locale.
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUTF8=1
Environment=LANG=C.UTF-8

StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# Hardening. The bot only ever writes inside its own tree.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP_ROOT

[Install]
WantedBy=multi-user.target
UNIT
)

if ! is_apply; then
    printf '%sUnit that would be written to %s:%s\n\n' "$C_GREY" "$UNIT_FILE" "$C_RESET"
    printf '%s\n\n' "$UNIT_CONTENT" | sed 's/^/    /'
fi

guarded_sh "Write $UNIT_FILE" "cat > '$UNIT_FILE' <<'EOUNIT'
$UNIT_CONTENT
EOUNIT"

guarded "Reload systemd" systemctl daemon-reload
guarded "Enable $SERVICE_NAME at boot" systemctl enable "$SERVICE_NAME"
guarded "Restart $SERVICE_NAME" systemctl restart "$SERVICE_NAME"

if is_apply; then
    sleep 5
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Service is active"
        systemctl status "$SERVICE_NAME" --no-pager --lines=5 | sed 's/^/      /'
    else
        err "Service failed to start."
        journalctl -u "$SERVICE_NAME" -n 30 --no-pager | sed 's/^/      /'
        exit 1
    fi
fi

printf '\n'
warn "systemd starts the bot when the DISTRO starts — but WSL does not start at boot."
warn "Run ./0055_install_boot_task.sh next, or the bot stays dead after every reboot."
