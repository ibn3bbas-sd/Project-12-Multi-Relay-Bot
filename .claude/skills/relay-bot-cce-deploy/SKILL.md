---
name: relay-bot-cce-deploy
description: Continue or operate the YouTube Relay Bot deployment on Huawei Cloud CCE me-east-1 (k8s/relay-bot.yaml, build-push-dockerhub workflow, Docker Hub image, HLD diagram, runbook).
---

# YouTube Relay Bot on Huawei Cloud CCE

Repo: `ibn3bbas-sd/Project-12-YouTube-Relay-Bot`. Everything is on `main`. PR #1 (`deploy/huawei-cce`) is merged.
Region: **me-east-1 (Riyadh)**. Bash only for this repo. No PowerShell.

## Architecture (keep it this simple)

Diagram: `docs/hld/hld-relay-bot-cce.svg`, theme-aware 1920×1080, in the style of the user's other HLD diagrams.
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

- **This repo is public.** Never commit IPs, hostnames, SSH users or keys, company cluster/org/repo names, account names or any secret. Cluster access details live in Claude's private project memory (`relay-bot-cluster-access`), not here. Docs say "the jumpbox" and `<jumpbox-eip>`.
- Runs #1 and #2 failed with `docker/login-action: Username and password required` because the secrets were never set. Set secrets **before** merging anything that triggers the build.
- Huawei SWR was dropped: the available AK/SK could push to an existing company org but could not create one (`403 SVCSTG.SWR.4030170`). Docker Hub is used instead. The repo secrets `SWR_REGISTRY_USER` and `SWR_REGISTRY_PASS` are unused; delete them.
- Docker Hub's PAT dropdown defaults to **Repo Public Read-only**. That fails the push with `401 ... insufficient scopes` even though login succeeds. Push token: **Repo Read & Write**. Cluster pull token: **Repo Read-only**.
- From the cluster's NAT IP, the yt-dlp channel **listing** works but every **video** hits "Sign in to confirm you're not a bot". Fixed with cookies from a throwaway Google account + deno (newest source video resolved at 1080p).
- Cookie hygiene: export from InPrivate with the **throwaway** account, never the target channel's (it was nearly exported once). Close without signing out. The browser may save to the OneDrive Desktop: delete it and empty the OneDrive recycle bin. Shred the jumpbox copy.
- Secret-volume gotchas: don't nest a mount under another Secret's mountPath, and with non-root `runAsUser` use `defaultMode: 0440` + `fsGroup`, because 0400 is root-only.
- Keep secrets out of OneDrive: the repo folder syncs to the company cloud. Build `.env` and the tokens in `~/relay-setup` (WSL ext4).
- kubectl only works on the jumpbox. Before giving the user commands, check which host their prompt shows: laptop vs jumpbox mix-ups happened twice.
- `/usr/bin/hcloud` on the laptop is the **Hetzner** CLI. For Huawei APIs use the Python SDK (`huaweicloudsdk*`) in a scratch venv.
- Ask the user to run secret-entry steps themselves (hidden `read -s` prompts, `gh secret set`), so values never pass through Claude.

## Status and next steps

See **`open-items.md`** in the repo root: it is the single checklist. Done so far:
- Image builds and pushes to private Docker Hub (`build-push-dockerhub`), with deno included.
- Outbound internet from the cluster is verified, and Secret `ytdlp-cookies` exists in ns `relay-bot`.
- The old WSL host is gone, so the setup is fresh. `processed_videos.json` was rebuilt by title-matching source vs target (0 overlap).

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
