#!/usr/bin/env bash
# Shared helpers for the WSL2 deployment scripts. Source this, do not execute it.
#
# Execution model: every script is DRY-RUN by default. Nothing changes until you
# set APPLY=true.
#
#   ./0030_setup_app.sh              # show what would happen
#   APPLY=true ./0030_setup_app.sh   # do it

set -euo pipefail

# ─── Defaults (override by exporting before you run) ─────────────────────────
# shellcheck disable=SC2034  # these are consumed by the scripts that source this file
APP_NAME="${APP_NAME:-youtube-relay-bot}"
APP_ROOT="${APP_ROOT:-/opt/youtube-relay-bot}"
APP_USER="${APP_USER:-relaybot}"
VENV_PATH="${VENV_PATH:-$APP_ROOT/.venv}"
# shellcheck disable=SC2034
VENV_PYTHON="$VENV_PATH/bin/python"
SERVICE_NAME="${SERVICE_NAME:-youtube-relay-bot}"
# shellcheck disable=SC2034
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
WSL_DISTRO="${WSL_DISTRO:-Ubuntu}"
BOOT_TASK_NAME="${BOOT_TASK_NAME:-WSL-YouTubeRelayBot}"
OAUTH_PORT="${OAUTH_PORT:-8080}"

APPLY="${APPLY:-false}"

# ─── Output ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_GREY=$'\033[90m'; C_MAGENTA=$'\033[35m'
else
    C_RESET=''; C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_GREY=''; C_MAGENTA=''
fi

step()  { printf '%s[..]%s %s\n' "$C_CYAN"   "$C_RESET" "$*"; }
ok()    { printf '%s[OK]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn()  { printf '%s[!!]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
err()   { printf '%s[ERR]%s %s\n' "$C_RED"   "$C_RESET" "$*" >&2; }
dry()   { printf '%s[DRY] would: %s%s\n' "$C_GREY" "$*" "$C_RESET"; }

is_apply() { [[ "$APPLY" == "true" ]]; }

banner() {
    local title="$1" color="$C_GREY" mode="DRY-RUN"
    is_apply && { color="$C_MAGENTA"; mode="APPLY"; }
    printf '\n%s================================================================%s\n' "$color" "$C_RESET"
    printf '%s  %s%s\n' "$color" "$title" "$C_RESET"
    printf '%s  Mode: %s   |   App root: %s%s\n' "$color" "$mode" "$APP_ROOT" "$C_RESET"
    printf '%s================================================================%s\n\n' "$color" "$C_RESET"
    is_apply || printf '%sNothing will be changed. Re-run with APPLY=true to execute.%s\n\n' "$C_GREY" "$C_RESET"
}

# guarded "<description>" <command...>
guarded() {
    local desc="$1"; shift
    if is_apply; then
        step "$desc"
        "$@"
        ok "$desc"
    else
        dry "$desc"
    fi
}

# guarded_sh "<description>" '<shell snippet>'  — for pipelines and redirects
guarded_sh() {
    local desc="$1" snippet="$2"
    if is_apply; then
        step "$desc"
        bash -euo pipefail -c "$snippet"
        ok "$desc"
    else
        dry "$desc"
    fi
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || { err "Run this with sudo."; exit 1; }
}

require_wsl() {
    grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null \
        || { err "This is not a WSL environment."; exit 1; }
}

require_systemd() {
    [[ "$(ps -p 1 -o comm=)" == "systemd" ]] && return 0
    err "systemd is not PID 1 inside this distro."
    cat <<'HINT'

      Enable it, then restart the distro from Windows:

        sudo tee /etc/wsl.conf >/dev/null <<'WSLCONF'
        [boot]
        systemd=true
        WSLCONF

      Then, in a Windows Command Prompt:  wsl --shutdown
      Reopen the distro and re-run this script.

HINT
    exit 1
}

# Absolute path to the repo root (two levels up from deploy/wsl/)
repo_root() { cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd; }
