# YouTube Relay Bot — Claude Code Context

## What this project does
Downloads new videos from a source YouTube channel and re-uploads them to the owner's channel. Telegram is the control surface; python-telegram-bot's job_queue fires the scheduled runs.

## Deploy target
**WSL2 Ubuntu on the Windows jumpbox** — `<old-host>` (landing-zone / hub-vcn, me-jeddah-1, `<private-ip>`), reached over an OCI Bastion port-forward to RDP. The bot runs as a systemd service inside the distro, as the `relaybot` user, from `/opt/youtube-relay-bot`. See `docs/wsl-deployment.md`.

Deployment is bash only. Two exceptions, both unavoidable: `wsl --install` to bootstrap the distro, and the Windows scheduled task that starts WSL at boot (registered from bash via `schtasks.exe` through interop). There are no PowerShell scripts in this repo — do not add any.

The Docker / Linux VM path is retired. `Dockerfile` and `docker-compose.yml` are kept for reference only.

## Key files
- `src/config.py` — layered config: env/.env overrides config/config.yaml overrides defaults. All paths absolute, anchored at repo root.
- `src/downloader.py` — yt-dlp. Two-stage discovery: flat listing for IDs, then per-video metadata. Flat extraction does NOT return upload_date — that was the original age-filter bug.
- `src/uploader.py` — YouTube Data API v3, resumable upload, backoff on 5xx, `QuotaExceeded` on 403 quota.
- `src/pipeline.py` — blocking orchestration. Never call it directly from an async handler.
- `src/telegram_bot.py` — `/run /status /next /help`. Pipeline runs via `asyncio.to_thread` behind a lock so polling stays responsive and two runs can't overlap.
- `src/scheduler.py` — daily jobs; raises a readable error if `tzdata` is missing.
- `tools/oauth_bootstrap.py` — headless consent for WSL: fixed port, no browser, prints the URL.
- `deploy/wsl/00*.sh` — numbered deploy scripts, dry-run by default, `APPLY=true` to execute.

## Conventions
- Deploy scripts: four-digit numeric prefix, underscores, dry-run default with `APPLY=true`, colour-coded output, preflight guards. Source `_common.sh`; use `guarded`/`guarded_sh` rather than bare commands.
- Every script must pass `shellcheck -S warning -x` and `bash -n`.
- Never widen a `try` to swallow a config error at startup — under systemd that becomes a restart loop capped by StartLimitBurst.

## Secrets — never commit
- `.env`
- `config/token.json` (YouTube OAuth token)
- `config/client_secret.json` (Google OAuth client)

## Dev setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
python -m src.main
```

## Known constraints
- YouTube Data API: ~1,600 units per upload, 10,000 units/day default -> ~6 uploads/day ceiling.
- `zoneinfo` needs the `tzdata` wheel; it is pinned.
- WSL does not start at boot — the scheduled task in `deploy/wsl/0055_install_boot_task.sh` is load-bearing.
- systemd in WSL needs `[boot] systemd=true` in `/etc/wsl.conf` plus `wsl --shutdown`.
- Run from ext4 (`/opt`), never from `/mnt/c` — 9p performance and OneDrive sync.
