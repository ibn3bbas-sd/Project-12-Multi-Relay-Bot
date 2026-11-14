---
name: relay-bot-cce-deploy
description: Continue or operate the YouTube Relay Bot deployment on Huawei Cloud CCE (branch deploy/huawei-cce, k8s/relay-bot.yaml, SWR workflow, runbook).
---

# YouTube Relay Bot on Huawei Cloud CCE

Repo: `ibn3bbas-sd/Project-12-YouTube-Relay-Bot`. Work branch: `deploy/huawei-cce` (pushed, PR not opened yet).
Open PR: https://github.com/ibn3bbas-sd/Project-12-YouTube-Relay-Bot/compare/main...deploy/huawei-cce

Bash only for this repo. No PowerShell.

## Architecture (keep it this simple)

```
GitHub main --Actions--> SWR image --> CCE ns relay-bot
                                        Deployment (1 pod, Recreate)
                                          Secret relay-bot-env   (env from .env)
                                          Secret relay-bot-files (token.json, client_secret.json)
                                          PVC relay-data (EVS, csi-disk) -> /data
                                      egress: NAT Gateway + EIP -> Telegram / YouTube
```

No Service, Ingress or LB. The bot only dials out (Telegram polling + YouTube).

## Files on the branch

- `k8s/relay-bot.yaml`: Namespace, PVC (EVS 20Gi, `csi-disk`, `everest.io/disk-volume-type: SAS`), Deployment, NetworkPolicy (deny ingress; egress DNS + 443).
  - initContainer `seed-token` copies `/secrets/token.json` to `/data/token.json` only if missing.
  - env: `DATA_DIR=/data`, `DOWNLOAD_DIR=/data/downloads`, `YOUTUBE_TOKEN_FILE=/data/token.json`, `YOUTUBE_CLIENT_SECRET_FILE=/secrets/client_secret.json`.
  - Non-root uid 10001, `fsGroup` 10001, `readOnlyRootFilesystem`, emptyDir for `/app/logs` and `/tmp`.
  - `imagePullSecrets: default-secret` (CCE auto-creates it for SWR).
  - Image placeholder `swr.REGION.myhuaweicloud.com/ORG/youtube-relay-bot:latest`. Replace it before applying.
- `.github/workflows/build-push-swr.yml`: push to main builds the image and pushes `:latest` + `:<sha>`. Needs repo secrets `SWR_REGISTRY`, `SWR_ORG`, `SWR_USERNAME` (`<region>@<AK>`), `SWR_PASSWORD` (long-term login key).
- `Dockerfile`: python:3.12-slim + ffmpeg, user 10001, `XDG_CACHE_HOME=/tmp/.cache`.
- `.dockerignore`, `docs/deploy-cce.md` (runbook), updated `CLAUDE.md`.
- Code: `DATA_DIR` read from env in `src/config.py`; `src/main.py` creates `logs/` before `FileHandler`; `tzdata` pinned in `requirements.txt`.

## Hard rules

- `replicas: 1` + `strategy: Recreate`. Never change these: two pods means double uploads (`_relay_lock` only works inside one process).
- YouTube API quota is about 6 uploads per day. Scaling changes nothing.
- The pod cannot do the OAuth browser flow. Get `token.json` locally, then seed it through the Secret.
- Never commit `.env`, `config/token.json` or `config/client_secret*.json`.

## Pending next steps

1. The GitHub `main` branch is still the June scaffold. Push the local WSL-era fixes to main first: two-stage discovery (flat listing, then per-video `extract_info`), `asyncio.to_thread` + `_relay_lock`, `QuotaExceeded` abort, and not marking a failed download as processed. Then rebase `deploy/huawei-cce` onto main.
2. Run the yt-dlp smoke test from the cluster (runbook step 2). If YouTube shows a bot-check, add a cookies file Secret or a proxy before migrating.
3. Confirm the CCE node subnet has a NAT Gateway SNAT rule + EIP.
4. Set the 4 SWR secrets in GitHub, open the PR, merge, and let Actions push the image.
5. Create the Secrets, run `sed` on the image path, then `kubectl apply -f k8s/relay-bot.yaml`.
6. Not validated yet: `docker build` and `kubeconform`. Run both locally:
   ```bash
   docker build -t relay-bot:test .
   kubeconform -strict -summary k8s/relay-bot.yaml
   ```

## Deploy commands

```bash
kubectl create ns relay-bot
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env
kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
sed -i 's#swr.REGION.myhuaweicloud.com/ORG#swr.<region>.myhuaweicloud.com/<org>#' k8s/relay-bot.yaml
kubectl apply -f k8s/relay-bot.yaml
kubectl -n relay-bot logs -f deploy/relay-bot
```

## Day 2

```bash
kubectl -n relay-bot rollout restart deploy/relay-bot          # pick up new :latest
kubectl -n relay-bot exec deploy/relay-bot -- cat /data/processed_videos.json
POD=$(kubectl -n relay-bot get pod -l app=relay-bot -o name | cut -d/ -f2)
kubectl -n relay-bot cp config/token.json "$POD":/data/token.json   # after local re-auth
```
