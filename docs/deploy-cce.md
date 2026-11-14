# Deploy on Huawei Cloud CCE

Region **me-east-1 (Riyadh)**. Diagram: [hld/hld-relay-bot-cce.svg](hld/hld-relay-bot-cce.svg).

```
GitHub (main) ──Actions──▶ SWR swr.me-east-1.myhuaweicloud.com/<org>/youtube-relay-bot
                                │ pull (default-secret)
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
- GitHub repo **secrets** `SWR_REGISTRY_USER` (`me-east-1@<AK>`) and `SWR_REGISTRY_PASS` (long-term login key), and repo **variable** `SWR_ORG`.
  These match `<company-org>/<infra-repo>`. GitHub never reveals secret values, so paste them yourself:
  ```bash
  gh secret set SWR_REGISTRY_USER          # prompts; value stays out of shell history
  gh secret set SWR_REGISTRY_PASS
  gh variable set SWR_ORG --body <org>
  gh workflow run build-push-swr --ref main && gh run watch
  ```
  Set these **before** merging to main, or the first build fails with `Username and password required`.
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

Title printed → good. Bot-check error → you need a cookies file or a proxy before going further.

## 3. Secrets

```bash
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env

kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
```

The token Secret is a bootstrap only: an initContainer copies it onto the EVS volume
the first time, and the app keeps the refreshed copy there.

## 4. Deploy

```bash
# set your image path once
sed -i 's#myhuaweicloud.com/ORG/#myhuaweicloud.com/<org>/#' k8s/relay-bot.yaml

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
