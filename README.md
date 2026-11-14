# YouTube Relay Bot

Automated pipeline that downloads new videos from a source YouTube channel and re-uploads them to a target channel. Controlled via a Telegram bot, with scheduled runs at 06:00 and 18:00 Asia/Riyadh.

## Features

- Scheduled relay twice daily, times and timezone configurable
- Telegram control: `/run`, `/status`, `/next`, `/help`, plus run notifications
- Keeps the original title and description, appends a source credit
- Tracks processed video IDs — no duplicate uploads
- Runs as a systemd service under WSL2, with crash restart and rate limiting
- Resumable uploads with backoff; aborts cleanly when the daily API quota is spent

## Architecture

```
Source YouTube Channel
        |
        v
  [yt-dlp Downloader]          discovery: flat listing -> per-video metadata
        |
        v
  Local Temp Storage           /opt/youtube-relay-bot/downloads  (ext4, not /mnt/c)
        |
        v
  [YouTube API v3 Uploader]    resumable, 8 MB chunks, quota-aware
        |
        v
Target YouTube Channel

[job_queue]     -> 06:00 / 18:00 Asia/Riyadh
[Telegram Bot]  -> /run /status /next /help + notifications

Host: WSL2 Ubuntu on <old-host>, systemd service
```

## Deployment

Bash only. Full runbook: **[docs/wsl-deployment.md](docs/wsl-deployment.md)**.

Bootstrap WSL once, from an elevated Windows Command Prompt:

```bat
wsl --install -d Ubuntu
wsl --set-default-version 2
```

Then everything else from inside Ubuntu:

```bash
cd deploy/wsl && chmod +x *.sh

sudo -E APPLY=true ./0010_install_prereqs.sh      # python, ffmpeg, tzdata, systemd
./0020_preflight_check.sh                         # egress check — read-only
sudo -E APPLY=true ./0030_setup_app.sh            # /opt/youtube-relay-bot + venv
APPLY=true ./0040_oauth_bootstrap.sh              # one-time consent, no browser needed
sudo -E APPLY=true ./0050_install_service.sh      # systemd unit
APPLY=true WIN_USER='DOMAIN\you' WIN_PASS='...' ./0055_install_boot_task.sh
./0060_service_control.sh status
```

Every script is dry-run by default. Set `APPLY=true` to make changes.

Step 0055 is not optional: WSL distros do not start at boot, so without that scheduled task the bot stays dead after every reboot.

The `Dockerfile` and `docker-compose.yml` from the earlier OCI Linux VM deployment are kept for reference and are no longer the supported path.

## Configuration

`config/config.yaml` holds the defaults; `.env` overrides any of them. Copy `.env.example` to `.env` and fill in at minimum:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_ID=...
```

Telegram token from [@BotFather](https://t.me/BotFather); chat ID from [@userinfobot](https://t.me/userinfobot).

YouTube credentials: Google Cloud Console -> enable **YouTube Data API v3** -> create an OAuth 2.0 Client ID of type **Desktop app** -> download the JSON to `config/client_secret.json`. Step 0040 turns it into `config/token.json`.

## Telegram commands

| Command | Description |
|---------|-------------|
| `/run` | Trigger the relay pipeline immediately |
| `/status` | Last run summary (or "running" if one is in flight) |
| `/next` | Next scheduled run times |
| `/help` | Show available commands |

## Operations

```bash
./deploy/wsl/0060_service_control.sh status
./deploy/wsl/0060_service_control.sh logs -n 100
./deploy/wsl/0060_service_control.sh follow
journalctl -u youtube-relay-bot -f
```

## Quota

A video insert costs ~1,600 YouTube Data API units against a 10,000/day default — roughly **six uploads per day**. `max_uploads_per_run` in `config.yaml` defaults to 5 to stay inside that. When the quota is exhausted the run aborts and reports it rather than retrying.

## Optional: Gemini

```env
GEMINI_API_KEY=your_key_here
GEMINI_ENABLED=true
```

Free key at [aistudio.google.com](https://aistudio.google.com). Intended for Arabic translation of titles and descriptions — the config keys exist, the translation step is not yet implemented.

## Project structure

```
Project-12-YouTube-Relay-Bot/
├── src/
│   ├── main.py            # entry point, logging setup
│   ├── config.py          # layered config loader
│   ├── downloader.py      # yt-dlp discovery + download
│   ├── uploader.py        # YouTube Data API v3 upload
│   ├── pipeline.py        # download -> upload orchestration
│   ├── scheduler.py       # daily job registration
│   └── telegram_bot.py    # command handlers
├── tools/
│   └── oauth_bootstrap.py # headless OAuth consent for WSL
├── deploy/wsl/            # numbered bash deployment scripts
├── config/config.yaml     # application defaults
├── docs/
│   └── wsl-deployment.md
├── .env.example
└── requirements.txt
```

## License

Internal use only.
