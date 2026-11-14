#!/usr/bin/env bash
# Step 0090 — Remove the service and the boot task. Files and secrets are kept
# unless you pass PURGE=true.
#
#   ./0090_uninstall.sh
#   sudo -E APPLY=true ./0090_uninstall.sh
#   sudo -E APPLY=true PURGE=true ./0090_uninstall.sh

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0090 — Uninstall"
is_apply && require_root

PURGE="${PURGE:-false}"

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    guarded "Stop $SERVICE_NAME"    systemctl stop "$SERVICE_NAME"
    guarded "Disable $SERVICE_NAME" systemctl disable "$SERVICE_NAME"
    guarded "Remove $UNIT_FILE"     rm -f "$UNIT_FILE"
    guarded "Reload systemd"        systemctl daemon-reload
else
    warn "Service '$SERVICE_NAME' is not installed."
fi

if command -v schtasks.exe >/dev/null 2>&1 && schtasks.exe /Query /TN "$BOOT_TASK_NAME" >/dev/null 2>&1; then
    guarded "Remove Windows boot task '$BOOT_TASK_NAME'" \
        schtasks.exe /Delete /TN "$BOOT_TASK_NAME" /F
else
    warn "Boot task '$BOOT_TASK_NAME' not present."
fi

if [[ "$PURGE" == "true" ]]; then
    warn "PURGE will delete $APP_ROOT including .env and config/token.json."
    guarded "Delete $APP_ROOT" rm -rf "$APP_ROOT"
    guarded "Delete user $APP_USER" userdel -r "$APP_USER"
else
    ok "Application files kept at $APP_ROOT"
fi

printf '\n'; ok "Step 0090 finished."
