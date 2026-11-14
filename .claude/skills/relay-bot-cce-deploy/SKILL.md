---
name: relay-bot-cce-deploy
description: Continue or operate the YouTube Relay Bot deployment on Huawei Cloud CCE me-east-1 (k8s/relay-bot.yaml, build-push-dockerhub workflow, Docker Hub image, HLD diagram, runbook).
---

# YouTube Relay Bot on Huawei Cloud CCE

Repo: `ibn3bbas-sd/Project-12-YouTube-Relay-Bot`. Everything is on `main`. PR #1 (`deploy/huawei-cce`) is merged.
Region: **me-east-1 (Riyadh)**. Bash only for this repo. No PowerShell.

## Architecture (keep it this simple)

Diagram: `docs/hld/hld-relay-bot-cce.svg`, theme-aware 1920×1080, in the same style as `maqrat-platform-infra/docs/hld`.
It is generated: edit `docs/hld/gen_hld.py`, run `python3 docs/hld/gen_hld.py`, and commit both files. Never hand-edit the SVG.

```
GitHub main --Actions--> Docker Hub docker.io/ibn3bbas/youtube-relay-bot:{latest,<sha>} (private)
                           --pull via NAT (Secret dockerhub-pull)--> CCE ns relay-bot
                                Deployment (1 pod, Recreate)
                                  Secret relay-bot-env   (env from .env)
                                  Secret relay-bot-files (token.json, client_secret.json)
                                  PVC relay-data (EVS, csi-disk) -> /data
                              egress: NAT Gateway (SNAT) + EIP -> Telegram / YouTube
```

No Service, Ingress or LB. The bot only dials out (Telegram long-polling + YouTube).

## Files

- `k8s/relay-bot.yaml`: Namespace, PVC (EVS 20Gi SAS `csi-disk`), Deployment, NetworkPolicy (deny ingress; egress 53 + 443).
  - initContainer `seed-token` copies `/secrets/token.json` to `/data/token.json` only if it is missing.
  - Non-root uid 10001, `fsGroup` 10001, `readOnlyRootFilesystem`, emptyDir for `/app/logs` and `/tmp`.
  - `imagePullSecrets: dockerhub-pull` (docker-registry Secret holding a **read-only** Docker Hub token; it also covers the busybox pull, avoiding anonymous rate limits).
  - Image `docker.io/ibn3bbas/youtube-relay-bot:latest`.
- `.github/workflows/build-push-dockerhub.yml`: push to main (src/config/requirements/Dockerfile/workflow) or manual dispatch. Builds `linux/amd64`, `provenance: false`. A first step fails loudly if the token is missing.
  - Secret `DOCKERHUB_TOKEN` (Read & Write PAT). Optional variable `DOCKERHUB_USERNAME` (default `ibn3bbas`).
  - Create the Hub repo as **private** before the first push. A repo auto-created by a push follows the account default (public).
- `docs/deploy-cce.md` (runbook), `README.md` (embeds the HLD), `Dockerfile`, `.dockerignore`.
- `deploy/wsl/` + `docs/wsl-deployment.md`: the fallback path, no longer primary. `docker-compose.yml` is for local dev only.

## Hard rules

- `replicas: 1` + `strategy: Recreate`. Never change these: two pods means double uploads (`_relay_lock` only works inside one process).
- YouTube API quota is about 6 uploads per day. Scaling changes nothing.
- The pod cannot do the OAuth browser flow. Get `token.json` locally, then seed it through the Secret.
- Never commit `.env`, `config/token.json` or `config/client_secret*.json`.
- GitHub secret values cannot be read back by any API. Never try to exfiltrate them from another repo (for example with a workflow that echoes them). The user pastes them in with `gh secret set`.

## Lessons

- Runs #1 and #2 failed on main with `docker/login-action: Username and password required`, because the secrets were never set. Set secrets and variables **before** merging anything that triggers the build.
- Huawei SWR was dropped (2026-11-14). The <infra-repo> AK/SK can push to the `<cluster>` org, but creating a `relay-bot` org failed with `403 SVCSTG.SWR.4030170 Insufficient permissions`. The user chose their own Docker Hub instead. The repo secrets `SWR_REGISTRY_USER` and `SWR_REGISTRY_PASS` are now unused.
- `/usr/bin/hcloud` on this machine is the **Hetzner** CLI, not Huawei KooCLI. Use the Python SDK (`huaweicloudsdk*`) in a scratch venv for Huawei API calls.

## Pending next steps

1. User creates the private Hub repo and two PATs (RW for GitHub, RO for the cluster), runs `gh secret set DOCKERHUB_TOKEN`, then `gh workflow run build-push-dockerhub --ref main && gh run watch`. That run is also the first real `docker build`.
2. Optional cleanup: `gh secret delete SWR_REGISTRY_USER && gh secret delete SWR_REGISTRY_PASS`.
3. Run the yt-dlp smoke test from the cluster (runbook step 2). If YouTube shows a bot-check, add a cookies file Secret or a proxy.
4. Confirm the CCE node subnet has a NAT Gateway SNAT rule + EIP.
5. Create the Secrets (`dockerhub-pull`, `relay-bot-env`, `relay-bot-files`), then `kubectl apply -f k8s/relay-bot.yaml`.
6. Still not validated: `kubeconform -strict -summary k8s/relay-bot.yaml`.

## Deploy commands

```bash
kubectl create ns relay-bot
kubectl -n relay-bot create secret docker-registry dockerhub-pull \
  --docker-server=https://index.docker.io/v1/ --docker-username=ibn3bbas \
  --docker-password=<read-only token>
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env
kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
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
