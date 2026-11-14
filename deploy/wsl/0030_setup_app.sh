#!/usr/bin/env bash
# Step 0030 — Lay the app down in the WSL filesystem and build the venv.
#
#   ./0030_setup_app.sh                                   # report only
#   sudo -E APPLY=true ./0030_setup_app.sh                 # do it
#   sudo -E APPLY=true SRC=/mnt/c/src/repo ./0030_setup_app.sh
#
# The app deliberately lives in ext4 (/opt), NOT under /mnt/c. Running it from
# the Windows filesystem means every file operation crosses the 9p/virtiofs
# boundary, and a OneDrive-synced path would try to upload every downloaded
# video. /mnt/c is where you edit; /opt is where it runs.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
banner "Step 0030 — Application layout and virtualenv"
require_wsl
is_apply && require_root

SRC="${SRC:-$(repo_root)}"
printf '  Source: %s\n  Target: %s\n  User:   %s\n\n' "$SRC" "$APP_ROOT" "$APP_USER"

case "$SRC" in
    /mnt/*) warn "Source is on the Windows filesystem — that is fine, it is only read once." ;;
esac

# ─── Service account ─────────────────────────────────────────────────────────
if id -u "$APP_USER" >/dev/null 2>&1; then
    ok "User '$APP_USER' exists"
else
    guarded "Create system user '$APP_USER'" \
        useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
fi

# ─── Files ───────────────────────────────────────────────────────────────────
guarded "Create $APP_ROOT" mkdir -p "$APP_ROOT"

guarded_sh "Sync application files (excluding runtime state and secrets)" \
    "rsync -a --delete \
        --exclude='.git/' --exclude='.venv/' --exclude='venv/' \
        --exclude='__pycache__/' --exclude='downloads/' --exclude='logs/' \
        --exclude='data/' --exclude='.env' --exclude='config/token.json' \
        --exclude='config/client_secret.json' \
        '$SRC/' '$APP_ROOT/'"

for d in downloads logs data config; do
    guarded "Ensure $APP_ROOT/$d" mkdir -p "$APP_ROOT/$d"
done

# ─── .env ────────────────────────────────────────────────────────────────────
if [[ -f "$APP_ROOT/.env" ]]; then
    ok ".env already present — left untouched"
else
    guarded "Seed .env from .env.example" cp "$APP_ROOT/.env.example" "$APP_ROOT/.env"
    warn "Edit $APP_ROOT/.env and fill in TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID."
fi

# ─── venv ────────────────────────────────────────────────────────────────────
if [[ -x "$VENV_PYTHON" ]]; then
    ok "Virtualenv already at $VENV_PATH"
else
    guarded "Create virtualenv at $VENV_PATH" python3 -m venv "$VENV_PATH"
fi

guarded_sh "Install / upgrade requirements" \
    "'$VENV_PATH/bin/pip' install --quiet --upgrade pip && \
     '$VENV_PATH/bin/pip' install --quiet -r '$APP_ROOT/requirements.txt'"

# ─── Ownership and permissions ───────────────────────────────────────────────
guarded "chown -R $APP_USER:$APP_USER $APP_ROOT" chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
guarded_sh "Lock down secrets (600 on .env, 700 on config/)" \
    "chmod 600 '$APP_ROOT/.env' 2>/dev/null || true; chmod 700 '$APP_ROOT/config'"

printf '\n'
if is_apply; then
    ok "Installed:"
    "$VENV_PATH/bin/pip" list 2>/dev/null | grep -iE 'yt-dlp|telegram|google-api|tzdata' | sed 's/^/      /'
fi
printf '\n'; ok "Step 0030 finished. Next: ./0040_oauth_bootstrap.sh"
