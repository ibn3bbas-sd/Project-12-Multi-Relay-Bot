# Deploy on Huawei Cloud CCE

Region **me-east-1 (Riyadh)**. Diagram: [hld/hld-relay-bot-cce.svg](hld/hld-relay-bot-cce.svg).

```
GitHub (main) ──Actions──▶ Docker Hub docker.io/ibn3bbas/youtube-relay-bot (private)
                                │ pull via NAT (Secret dockerhub-pull)
                                ▼
                     CCE ns relay-bot
                       └─ Deployment (1 pod, Recreate)
                          ├─ Secret  relay-bot-env   (env vars)
                          ├─ Secret  relay-bot-files (token.json, client_secret.json)
                          └─ PVC     relay-data (EVS) → /data
                     egress: NAT Gateway (SNAT) + EIP → Telegram / YouTube
```

No Service, no Ingress, no LB. The bot only dials out.

## 0. Prereqs

- CCE node subnet has a **NAT Gateway (SNAT rule) + EIP**, otherwise the pod has no internet.
- Docker Hub repo `ibn3bbas/youtube-relay-bot` created as **private** (a repo auto-created by a push follows the account default, usually public).
- Two Docker Hub personal access tokens (*Account settings → Personal access tokens*):
  **Read & Write** for GitHub Actions, **Read-only** for the cluster.
- GitHub repo secret `DOCKERHUB_TOKEN` (the Read & Write token). Username defaults to `ibn3bbas` (override with variable `DOCKERHUB_USERNAME`):
  ```bash
  gh secret set DOCKERHUB_TOKEN            # prompts; value stays out of shell history
  gh workflow run build-push-dockerhub --ref main && gh run watch
  ```
  Set it **before** merging to main, or the build fails at the settings check.
- `kubectl` pointed at the cluster (CCE console → cluster → kubectl → download kubeconfig).

## 1. Get the YouTube OAuth token once, on your laptop

The pod cannot open a browser. Do the OAuth dance locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # fill Telegram values
python -c "from src.uploader import _get_credentials; _get_credentials()"
# browser opens → approve → config/token.json written
```

## 2. Smoke-test YouTube from the cluster BEFORE anything else

Cloud IPs often get "Sign in to confirm you're not a bot". Check first:

```bash
kubectl create ns relay-bot
kubectl -n relay-bot run ytdlp-test --rm -it --restart=Never \
  --image=python:3.12-slim -- sh -c \
  "pip -q install yt-dlp && yt-dlp --simulate --print title 'https://www.youtube.com/watch?v=jNQXAC9IVRw'"
```

Title printed → good. `Sign in to confirm you're not a bot` → you need cookies (next section).
On the <cluster> cluster (2026-11-14) the channel **listing** worked but every **video** hit the bot-check, and cookies fixed it.

## 2b. YouTube cookies (needed on cloud IPs)

Use a **throwaway Google account**, never the target channel's account or a personal one.

1. Edge or Chrome: install **Get cookies.txt LOCALLY** (Chrome Web Store, id `cclelndahbckbenkjhflpdbgdldlbecc`, open source).
   Then *Manage extensions → Details → Allow in InPrivate*.
2. InPrivate window → youtube.com → sign in with the throwaway account → play a video for ~20 s.
3. Extension → format **Netscape** → **Export** (not *Export All Cookies*).
4. Close the window **without signing out** (signing out kills the cookies).
5. Copy to the jumpbox, make it a Secret, and destroy every copy:
   ```bash
   scp -i ~/.ssh/<ssh-key> www.youtube.com_cookies.txt ibnabbas@<jumpbox-eip>:~/cookies.txt && rm www.youtube.com_cookies.txt
   # on the jumpbox
   kubectl -n relay-bot create secret generic ytdlp-cookies --from-file=cookies.txt=$HOME/cookies.txt \
     --dry-run=client -o yaml | kubectl apply -f -
   shred -u ~/cookies.txt
   ```
   If the browser saved it under OneDrive, also empty it from the OneDrive recycle bin.

The pod reads `/cookies/cookies.txt` (`YTDLP_COOKIES_FILE`). It works on a copy in `/data/ytdlp-cookies.txt` so that
YouTube's cookie rotation persists, and it re-copies automatically when the Secret is replaced, with no restart needed.

**Refresh** when downloads start failing with the bot-check again (typically after weeks): repeat steps 2–5.

## 3. Secrets

```bash
# image pull — the read-only token
kubectl -n relay-bot create secret docker-registry dockerhub-pull \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=ibn3bbas \
  --docker-password="$(read -rsp 'Docker Hub read-only token: ' t; echo "$t")"
```

```bash
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env

# ytdlp-cookies: see section 2b

kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
```

The token Secret is a bootstrap only: an initContainer copies it onto the EVS volume
the first time, and the app keeps the refreshed copy there.

## 4. Deploy

```bash
kubectl apply -f k8s/relay-bot.yaml
kubectl -n relay-bot logs -f deploy/relay-bot
```

Send `/status` to the bot on Telegram.

## Day 2

```bash
# new image after a push to main
kubectl -n relay-bot rollout restart deploy/relay-bot

# what has been relayed
kubectl -n relay-bot exec deploy/relay-bot -- cat /data/processed_videos.json

# token got revoked → re-auth locally, then replace the copy on the volume
kubectl -n relay-bot cp config/token.json "$(kubectl -n relay-bot get pod -l app=relay-bot -o name | cut -d/ -f2)":/data/token.json
```

## Rules that matter

- `replicas: 1` and `strategy: Recreate` — never change. Two pods = the same video uploaded twice.
- YouTube API quota ≈ 6 uploads/day. More pods or bigger nodes don't change that.
- EVS is zonal: the pod follows its volume's AZ. Fine for one pod.
