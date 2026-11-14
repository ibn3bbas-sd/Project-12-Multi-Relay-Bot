# Deployment — YouTube Relay Bot on WSL2

Host: `<old-host>` (landing-zone / hub-vcn, me-jeddah-1, private subnet `<private-ip>`).
Access: OCI Bastion port-forward → RDP on `127.0.0.1:3392`.
Runtime: Ubuntu under WSL2 on that host. The bot runs as a systemd service inside the distro, as the `relaybot` system user, out of `/opt/youtube-relay-bot`.

Everything below is bash. There are exactly two commands in this document that are not, and both are unavoidable: bootstrapping WSL itself, and the boot shim.

---

## 0. Two things that decide whether this works

**Egress.** The jumpbox is in a private subnet. A bastion port-forward gets *you* in; it gives the host no route *out*. The bot needs sustained outbound 443 to `youtube.com`, `*.googlevideo.com`, `www.googleapis.com`, `oauth2.googleapis.com` and `api.telegram.org`. Step 0020 tests it. Worth knowing: WSL2 traffic leaves through the Windows host's NIC, so a Windows firewall block and an OCI security-list block look identical from inside Ubuntu — test from Windows too before blaming the security list.

**Boot.** WSL distros are started on demand by a user session. Nothing starts them at boot. systemd inside Ubuntu is perfectly capable of supervising the bot, but if the distro never starts, systemd never runs. Step 0055 registers a Windows scheduled task that starts the distro at boot and holds it open. Skip that step and the bot is silently dead after every patch reboot until someone logs in over RDP.

While you are in that script: `<old-host-script>.sh` carries the RDP password as a plaintext literal. Rotate it and strip the line before that file goes near `<scripts-repo>`.

---

## 1. Connect

```bash
# WSL Ubuntu on your laptop — opens the bastion tunnel, keep it open
./<old-host-script>.sh
```

RDP to `127.0.0.1:3392` once the tunnel is up.

---

## 2. Bootstrap WSL on the jumpbox (once)

The only non-bash step. Elevated **Command Prompt** on the server:

```bat
wsl --install -d Ubuntu
wsl --set-default-version 2
```

Reboot, then open Ubuntu and create your user. WSL2 needs Windows Server 2022 or later — on Server 2019 it is not supported and this whole approach is off the table.

---

## 3. Deploy

Every script is **dry-run by default**. Set `APPLY=true` to execute. Run them from inside the Ubuntu shell.

```bash
git clone <repo> ~/Project-12-YouTube-Relay-Bot   # or work from /mnt/c/...
cd ~/Project-12-YouTube-Relay-Bot/deploy/wsl
chmod +x *.sh

./0010_install_prereqs.sh                         # report what's missing
sudo -E APPLY=true ./0010_install_prereqs.sh      # python, ffmpeg, tzdata, rsync, systemd
```

If 0010 had to write `/etc/wsl.conf`, restart the distro before continuing — from a Windows Command Prompt:

```bat
wsl --shutdown
```

```bash
./0020_preflight_check.sh                         # read-only — STOP here if anything is blocked

sudo -E APPLY=true ./0030_setup_app.sh            # /opt/youtube-relay-bot, relaybot user, venv
sudo -e /opt/youtube-relay-bot/.env               # fill in the Telegram token and chat ID
```

Drop the Google OAuth client in, then authorise once:

```bash
# Google Cloud Console -> Credentials -> OAuth 2.0 Client ID -> Desktop app -> Download JSON
sudo cp /mnt/c/Users/<you>/Downloads/client_secret*.json /opt/youtube-relay-bot/config/client_secret.json
sudo chown relaybot:relaybot /opt/youtube-relay-bot/config/client_secret.json

APPLY=true ./0040_oauth_bootstrap.sh
```

WSL has no browser, so the helper prints the consent URL instead of opening one. Paste it into the browser on the Windows desktop, approve, and the redirect comes back to `localhost:8080` — WSL2's localhost forwarding carries it into the distro. The token is written once; the service refreshes it from then on and never needs a browser again.

```bash
sudo -E APPLY=true ./0050_install_service.sh      # systemd unit, enable, start
APPLY=true WIN_USER='DOMAIN\you' WIN_PASS='...' ./0055_install_boot_task.sh
./0060_service_control.sh status
```

---

## 4. Operate

```bash
./0060_service_control.sh status
./0060_service_control.sh logs -n 100
./0060_service_control.sh follow
./0060_service_control.sh errors
APPLY=true ./0060_service_control.sh restart
```

Or straight systemd, which is all the wrapper does:

```bash
systemctl status youtube-relay-bot
journalctl -u youtube-relay-bot -f
journalctl -u youtube-relay-bot --since "06:00" --until "07:00"
```

Telegram is the normal control surface: `/run`, `/status`, `/next`, `/help`.

Logs go to the journal *and* to `/opt/youtube-relay-bot/logs/relay.log` (rotating, 10 MB × 5). The journal is the one to trust for startup failures; `relay.log` only exists once logging has initialised, so a crash before that shows up only in `journalctl`.

---

## 5. The service unit, and why

| Directive | Value | Reason |
|---|---|---|
| `KillSignal` | `SIGINT` | python-telegram-bot closes its polling session on KeyboardInterrupt. `SIGTERM` leaves a stale `getUpdates` that conflicts with the next start. |
| `Restart` | `on-failure` | A crashed poll loop should come back by itself. |
| `RestartSec` | `30` | A network blip should not become a hot loop. |
| `StartLimitBurst` / `IntervalSec` | `5` / `300` | Five failures in five minutes means bad config, not a blip — systemd gives up instead of flapping. These live in `[Unit]`, not `[Service]`; systemd silently ignores them in the wrong section. |
| `ProtectSystem=full`, `ReadWritePaths` | — | The bot writes only inside its own tree. |
| `PYTHONUTF8=1` | — | Arabic titles arrive mangled otherwise. |

---

## 6. WSL-specific gotchas

**Run from ext4, not `/mnt/c`.** `/opt/youtube-relay-bot` is on the distro's own filesystem. Running from `/mnt/c` means every file operation crosses the 9p boundary — slow enough to matter on multi-hundred-MB downloads — and a OneDrive-synced path would try to upload every video the bot touches. `/mnt/c` is where you edit; `/opt` is where it runs.

**systemd is opt-in.** `/etc/wsl.conf` needs `[boot] systemd=true` and a `wsl --shutdown` to take effect. Without it `ps -p 1 -o comm=` returns `init` and there is no `systemctl`. Step 0010 writes it; step 0050 refuses to run until it has taken effect.

**Interop must stay enabled.** `0055` and the boot-task check in `0060` call `schtasks.exe` through WSL interop. If `[interop] enabled=false` is ever set, those silently stop working.

**The boot task stores a password.** The distro is registered per Windows user, so the task has to run as that account with "run whether logged on or not" — which means a stored credential. It breaks on the next password change for that account, and the symptom is the bot quietly not coming back after a reboot. A gMSA avoids it if the host is domain-joined. Verify the task for real: reboot, do *not* log in, wait two minutes, reconnect and check `status`.

**Disk.** WSL2's virtual disk grows on demand and does not shrink when files are deleted. Each relay pulls up to 1080p before uploading. Keep an eye on the `.vhdx` and compact it occasionally if downloads pile up after a mid-run crash.

**Clock drift.** WSL2 has historically drifted after the Windows host sleeps or hibernates, which would fire the 06:00 and 18:00 jobs at the wrong time. Current builds sync on resume; if you see the schedule slip, `sudo hwclock -s` is the manual fix.

---

## 7. Quota ceiling

A video insert costs about 1,600 quota units against a default 10,000/day allocation — roughly **six uploads per day in total**, not per run. `max_uploads_per_run: 5` in `config.yaml` keeps one run inside the budget, and `uploader.py` raises `QuotaExceeded` to abort the run rather than burning retries against a wall that resets at midnight Pacific. More throughput needs a quota increase from Google, which is a review, not a slider.

---

## 8. Rollback

```bash
sudo -E APPLY=true ./0090_uninstall.sh                 # remove service + boot task, keep files
sudo -E APPLY=true PURGE=true ./0090_uninstall.sh      # also delete /opt/youtube-relay-bot and the user
```
