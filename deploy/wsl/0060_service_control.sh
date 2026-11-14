#!/usr/bin/env bash
# Step 0060 — Day-to-day control.
#
#   ./0060_service_control.sh status
#   ./0060_service_control.sh logs -n 100
#   ./0060_service_control.sh follow
#   APPLY=true ./0060_service_control.sh restart
#
# status, logs, follow and errors are read-only. start/stop/restart honour APPLY.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

ACTION="${1:-status}"
shift || true

case "$ACTION" in
    status)
        printf '\n  Service : %s\n' "$SERVICE_NAME"
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            printf '  State   : %sactive%s\n' "$C_GREEN" "$C_RESET"
        else
            printf '  State   : %s%s%s\n' "$C_RED" "$(systemctl is-active "$SERVICE_NAME" 2>&1)" "$C_RESET"
        fi
        printf '  Enabled : %s\n' "$(systemctl is-enabled "$SERVICE_NAME" 2>&1)"
        pid="$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo 0)"
        if [[ "$pid" -gt 0 ]]; then
            printf '  PID     : %s\n' "$pid"
            printf '  Memory  : %s\n' "$(systemctl show -p MemoryCurrent --value "$SERVICE_NAME" \
                | awk '{ if ($1 ~ /^[0-9]+$/) printf "%.0f MB", $1/1048576; else print "n/a" }')"
            printf '  Uptime  : since %s\n' "$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE_NAME")"
        fi

        # The boot shim is the usual reason a reboot leaves this dead.
        if command -v schtasks.exe >/dev/null 2>&1; then
            if schtasks.exe /Query /TN "$BOOT_TASK_NAME" >/dev/null 2>&1; then
                printf '  Boot task: %sregistered%s (%s)\n' "$C_GREEN" "$C_RESET" "$BOOT_TASK_NAME"
            else
                printf '  Boot task: %sMISSING%s — bot will not survive a reboot (run 0055)\n' "$C_YELLOW" "$C_RESET"
            fi
        fi

        printf '\n%s  Last 5 log lines:%s\n' "$C_GREY" "$C_RESET"
        journalctl -u "$SERVICE_NAME" -n 5 --no-pager -o cat 2>/dev/null | sed 's/^/    /'
        printf '\n'
        ;;
    logs)
        # No extra args -> last 50 lines. Anything passed goes straight through.
        if [[ $# -eq 0 ]]; then
            journalctl -u "$SERVICE_NAME" --no-pager -n 50
        else
            journalctl -u "$SERVICE_NAME" --no-pager "$@"
        fi
        ;;
    follow)
        journalctl -u "$SERVICE_NAME" -f
        ;;
    errors)
        journalctl -u "$SERVICE_NAME" -p err --no-pager -n "${1:-50}"
        ;;
    start|stop|restart)
        guarded "$ACTION $SERVICE_NAME" systemctl "$ACTION" "$SERVICE_NAME"
        ;;
    *)
        err "Unknown action '$ACTION'"
        printf 'Usage: %s {status|logs|follow|errors|start|stop|restart}\n' "$(basename "$0")"
        exit 1
        ;;
esac
