# Open items

Where the Huawei Cloud CCE deployment stands. Last updated **2026-11-14**.
Runbook: [docs/deploy-cce.md](docs/deploy-cce.md) · Diagram: [docs/hld/hld-relay-bot-cce.svg](docs/hld/hld-relay-bot-cce.svg)

> This repo is **public**. Never write tokens, IPs, hostnames, SSH users/keys or account names here.
> Use placeholders such as `<jumpbox-eip>`.

## Done

- [x] Image builds and pushes to private Docker Hub `ibn3bbas/youtube-relay-bot` (workflow `build-push-dockerhub`, linux/amd64, includes deno)
- [x] GitHub secret `DOCKERHUB_TOKEN` set (Read & Write). A read-only token for the cluster exists.
- [x] Cluster egress verified: Telegram, YouTube, googleapis and Docker Hub are all reachable through the NAT
- [x] YouTube bot-check on the cloud IP solved with a throwaway account's cookies + deno; Secret `ytdlp-cookies` is in ns `relay-bot`
- [x] Bot code supports `YTDLP_COOKIES_FILE`; the manifest mounts the cookies Secret
- [x] `processed_videos.json` prepared: "start from now", 60 current source IDs marked (none had been relayed before)

## Blocked (2026-11-14): hosting account frozen

The cloud account behind the CCE cluster was **frozen by its partner** ("cannot purchase or use cloud services").
New EVS disks are refused with `403 Policy doesn't allow to be performed` for every storage class, so the
PVC stays Pending and the pod cannot start. Deployment `relay-bot` is scaled to 0. All four Secrets
(`dockerhub-pull`, `relay-bot-env`, `relay-bot-files`, `ytdlp-cookies`) are in place.

- [ ] **Decide:** wait for the account to be unfrozen, or move to another host. The image is on Docker Hub and the
      manifest is portable; any Kubernetes cluster or small VM works.
- [ ] When unfrozen: `kubectl -n relay-bot scale deploy/relay-bot --replicas=1`. The PVC binds by itself.
      Then `kubectl cp` the processed list (60 "start from now" IDs; local copy in `~/relay-setup/data/`) into `/data/`.
- [ ] Fallback if disks stay unavailable: drop the PVC and keep the processed list in a ConfigMap (code change).

## First deploy checklist (status as of 2026-11-14)

Build everything in `~/relay-setup` inside WSL, **not** in this OneDrive-synced folder.
Secret values are entered by the user directly (hidden prompts), never pasted into chat.

- [x] **`.env`**: copy `.env.example`, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_ID`, with no quotes around values
- [x] **`client_secret.json`**: Google Cloud Console → Credentials → Desktop OAuth client → download JSON
- [x] **OAuth consent screen is "In production"** (home/privacy pages on GitHub Pages, `gh-pages` branch): in *Testing*, the refresh token expires every 7 days
- [x] **`token.json`**: `APP_ROOT=~/relay-setup python tools/oauth_bootstrap.py`, signed in as the **target channel** account
- [x] **Decide catch-up** (chose: start from now, 60 IDs marked): with an empty processed list, the first run relays the source's last 7 days (≤5 per run). To start from "now" instead, mark the current source IDs as processed.
- [x] Copy `.env`, `config/`, `data/processed_videos.json` to the jumpbox (tar → scp → shred local copy)
- [x] On the jumpbox, create Secrets `dockerhub-pull` (read-only token), `relay-bot-env`, `relay-bot-files`
- [ ] `kubectl apply -f k8s/relay-bot.yaml` (applied; blocked on the PVC, see above); `kubectl -n relay-bot rollout status deploy/relay-bot`
- [ ] `kubectl cp` `processed_videos.json` into `/data/` **before the next 06:00 / 18:00 run**
- [ ] Shred every copy on the jumpbox. Send `/status` to the bot. Watch the first scheduled run's logs.

## After the first deploy

- [ ] Confirm one full relay: download through cookies → upload → Telegram report
- [ ] Note the cookie expiry symptom (bot-check returns) and the refresh steps (runbook 2b) once seen in practice
- [ ] `kubeconform -strict -summary k8s/relay-bot.yaml` (not yet validated)

## Cleanup

- [ ] Delete the unused GitHub secrets: `gh secret delete SWR_REGISTRY_USER && gh secret delete SWR_REGISTRY_PASS`
- [ ] Delete the old `github-actions-push` Docker Hub token that had *Public Read-only* scope (if not already)
- [ ] Review the auto-generated Docker Hub "desktop" tokens (Read/Write/Delete, never expire) and revoke unused ones
- [ ] Empty the exported cookies file from the OneDrive recycle bin
- [ ] Decide whether to purge infrastructure details from **git history** (see below)

## Known: infrastructure details in git history

Earlier commits of this public repo contain internal infrastructure identifiers (cluster/org names, a jumpbox
public IP and SSH key name, the NAT egress IP, an old host's name and private IP). They were removed from the
current files on 2026-11-14 but **remain in history**. No passwords, tokens or keys were ever committed.
Options: rewrite history (e.g. `git filter-repo --replace-text`) and force-push `main`, or make the repo private.
