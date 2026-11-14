#!/usr/bin/env bash
# Step 0010 — Ubuntu packages the relay needs.
#
#   ./0010_install_prereqs.sh              # report only
#   APPLY=true sudo -E ./0010_install_prereqs.sh
#
# WSL itself must already exist. From an elevated Windows Command Prompt, once:
#   wsl --install -d Ubuntu
#   wsl --set-default-version 2

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0010 — Prerequisites"
require_wsl

PACKAGES=(python3 python3-venv python3-pip ffmpeg tzdata ca-certificates curl dnsutils rsync)

missing=()
for pkg in "${PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        ok "$pkg present"
    else
        warn "$pkg missing"
        missing+=("$pkg")
    fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
    is_apply && require_root
    guarded "apt-get update" apt-get update -qq
    guarded "Install: ${missing[*]}" apt-get install -y --no-install-recommends "${missing[@]}"
fi

# Python 3.11+ is required — src/ uses `X | None` annotations and zoneinfo.
if command -v python3 >/dev/null; then
    pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [[ "$(printf '%s\n3.11\n' "$pyver" | sort -V | head -1)" == "3.11" ]]; then
        ok "Python $pyver (>= 3.11 required)"
    else
        err "Python $pyver is too old — 3.11 or newer required."
        warn "Ubuntu 22.04 ships 3.10: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv"
        exit 1
    fi
fi

# ffmpeg is what yt-dlp shells out to when merging separate A/V streams.
if command -v ffmpeg >/dev/null; then
    ok "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
fi

# ─── systemd ─────────────────────────────────────────────────────────────────
if [[ "$(ps -p 1 -o comm=)" == "systemd" ]]; then
    ok "systemd is PID 1"
else
    warn "systemd is not PID 1 — required for the service in step 0050."
    if [[ -f /etc/wsl.conf ]] && grep -q 'systemd=true' /etc/wsl.conf; then
        ok "/etc/wsl.conf already sets systemd=true — restart the distro: wsl --shutdown"
    else
        is_apply && require_root
        guarded_sh "Write /etc/wsl.conf with systemd=true" \
            'printf "[boot]\nsystemd=true\n\n[interop]\nenabled=true\nappendWindowsPath=true\n" > /etc/wsl.conf'
        warn "Now run 'wsl --shutdown' in a Windows Command Prompt, reopen Ubuntu, and re-run this script."
    fi
fi

printf '\n'; ok "Step 0010 finished. Next: ./0020_preflight_check.sh"
