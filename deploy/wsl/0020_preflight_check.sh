#!/usr/bin/env bash
# Step 0020 — Can this box actually reach what the bot needs?
#
# Read-only. No APPLY needed.
#
# The jumpbox sits in a private subnet in hub-vcn. A bastion port-forward gets
# YOU in; it gives the host no route OUT. Without a NAT gateway route the bot
# starts cleanly and then fails every single run. Check this first.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0020 — Preflight connectivity check"

TARGETS=(
    "www.youtube.com|443|YouTube"
    "www.googleapis.com|443|Google APIs (upload)"
    "oauth2.googleapis.com|443|Google OAuth"
    "rr1---sn-4g5e6nsz.googlevideo.com|443|YouTube media CDN"
    "api.telegram.org|443|Telegram Bot API"
    "pypi.org|443|PyPI"
    "files.pythonhosted.org|443|PyPI files"
)

failures=0
for entry in "${TARGETS[@]}"; do
    IFS='|' read -r host port label <<< "$entry"

    if ! getent hosts "$host" >/dev/null 2>&1; then
        err "$(printf '%-24s DNS resolution FAILED for %s' "$label" "$host")"
        failures=$((failures + 1))
        continue
    fi

    if timeout 8 bash -c "</dev/tcp/$host/$port" 2>/dev/null; then
        ok "$(printf '%-24s %s:%s reachable' "$label" "$host" "$port")"
    else
        err "$(printf '%-24s %s:%s BLOCKED' "$label" "$host" "$port")"
        failures=$((failures + 1))
    fi
done

printf '\n'

# WSL2 inherits DNS from Windows via /etc/resolv.conf unless generateResolvConf
# is off. A broken resolver here is a WSL config problem, not a network one.
if [[ -f /etc/resolv.conf ]]; then
    ok "Resolver: $(grep -m1 '^nameserver' /etc/resolv.conf || echo 'none configured')"
fi

if [[ -n "${HTTPS_PROXY:-}${https_proxy:-}" ]]; then
    warn "Proxy set in the environment: ${HTTPS_PROXY:-$https_proxy}"
    warn "Add it to the systemd unit in step 0050 or the service will not inherit it."
else
    ok "No proxy in the environment — direct egress."
fi

printf '\n'
if [[ $failures -gt 0 ]]; then
    err "$failures target(s) unreachable."
    cat <<'HINT'

  The jumpbox is in a private subnet (landing-zone / hub-vcn). Check, in order:
    1. Subnet route table has 0.0.0.0/0 -> NAT Gateway
    2. Security list / NSG egress allows TCP 443 to 0.0.0.0/0
    3. Windows Firewall outbound rules on the host
    4. Any transparent proxy or TLS-inspection agent

  Note: WSL2 traffic leaves through the Windows host's NIC, so a Windows-side
  block shows up here identically to an OCI-side one. Test from Windows too
  before you go blaming the security list.

HINT
    exit 1
fi

ok "All egress checks passed. Next: sudo -E APPLY=true ./0030_setup_app.sh"
