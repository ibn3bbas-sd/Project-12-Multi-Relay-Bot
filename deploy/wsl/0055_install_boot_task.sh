#!/usr/bin/env bash
# Step 0055 — Keep the WSL distro running across reboots.
#
#   ./0055_install_boot_task.sh
#   APPLY=true WIN_USER='DOMAIN\svc_relay' WIN_PASS='...' ./0055_install_boot_task.sh
#
# This is the one piece of Windows glue that cannot be avoided. WSL distros are
# started on demand by a user session; nothing starts them at boot. Without this
# task, systemd inside Ubuntu never comes up after a reboot and the bot is
# silently dead until someone logs in over RDP.
#
# We register it with schtasks.exe called through WSL interop, so this stays a
# bash script. The task runs `wsl.exe -d <distro> -u root -e sleep infinity`,
# which boots the distro (systemd becomes PID 1) and holds it alive.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0055 — WSL boot shim (Windows scheduled task)"
require_wsl

SCHTASKS="$(command -v schtasks.exe || true)"
if [[ -z "$SCHTASKS" ]]; then
    err "schtasks.exe not reachable — Windows interop is disabled."
    cat <<'HINT'

  Enable it in /etc/wsl.conf, then `wsl --shutdown` from Windows:

    [interop]
    enabled=true
    appendWindowsPath=true

HINT
    exit 1
fi
ok "Windows interop available: $SCHTASKS"

# The distro is registered per Windows user, so the task must run as the account
# that installed it. "Run whether user is logged on or not" needs its password.
WIN_USER="${WIN_USER:-}"
WIN_PASS="${WIN_PASS:-}"

if [[ -z "$WIN_USER" ]]; then
    detected="$(cmd.exe /c 'echo %USERDOMAIN%\%USERNAME%' 2>/dev/null | tr -d '\r\n' || true)"
    warn "WIN_USER not set. Detected current Windows account: ${detected:-unknown}"
    warn "Re-run with WIN_USER and WIN_PASS set, e.g."
    printf '      %sAPPLY=true WIN_USER=%s WIN_PASS=... ./0055_install_boot_task.sh%s\n' \
        "$C_GREY" "${detected:-DOMAIN\\\\user}" "$C_RESET"
    [[ "$APPLY" == "true" ]] && exit 1
fi

TASK_CMD="wsl.exe -d $WSL_DISTRO -u root -e sleep infinity"

printf '\n  Task name : %s\n  Action    : %s\n  Trigger   : at system startup, 30s delay\n  Run as    : %s\n\n' \
    "$BOOT_TASK_NAME" "$TASK_CMD" "${WIN_USER:-<unset>}"

if ! is_apply; then
    dry "register scheduled task '$BOOT_TASK_NAME'"
    printf '\n%sRe-run with APPLY=true, WIN_USER and WIN_PASS.%s\n' "$C_GREY" "$C_RESET"
    exit 0
fi

step "Registering scheduled task '$BOOT_TASK_NAME'"
"$SCHTASKS" /Create \
    /TN "$BOOT_TASK_NAME" \
    /TR "$TASK_CMD" \
    /SC ONSTART \
    /DELAY 0000:30 \
    /RU "$WIN_USER" \
    /RP "$WIN_PASS" \
    /RL HIGHEST \
    /F
ok "Task registered"

step "Verifying"
"$SCHTASKS" /Query /TN "$BOOT_TASK_NAME" /FO LIST | sed 's/^/      /'

printf '\n'
ok "Step 0055 finished."
cat <<'NEXT'

  Verify it for real before you trust it. Reboot the jumpbox, do NOT log in,
  wait two minutes, then reconnect and check:

      ./0060_service_control.sh status

  If the service is not running after a cold boot, the task did not fire — the
  usual cause is the stored password being rejected after a password change.

NEXT
