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
- Docker Hub's PAT dropdown defaults to **Repo Public Read-only**. That fails the push with `401 ... access token has insufficient scopes` even though login succeeds. The push token needs **Repo Read & Write**, and the cluster pull token needs **Repo Read-only** (not *Public*).
- Cookie hygiene: export from an InPrivate window with a throwaway account, never the target channel's (the user nearly exported the channel account). Close the window without signing out. The browser may save to the OneDrive Desktop: delete it and empty the OneDrive recycle bin. Shred the jumpbox copy.
- Secret-volume gotchas: don't nest a mount under another Secret's mountPath, and with `runAsUser` non-root use `defaultMode: 0440` + `fsGroup`, because 0400 is root-only.
- kubectl only works **on the jumpbox** (`~/jumpboxes/<jumpbox-script>.sh`, user ibnabbas, key `~/.ssh/<ssh-key>`, SG allow-list auto-fixed by the script). The laptop's kubeconfig points to localhost:6443, which needs a tunnel. From this machine Claude can run `ssh -i ~/.ssh/<ssh-key> ibnabbas@<jumpbox-eip> 'bash -s' <<EOF ... EOF`.
- `/usr/bin/hcloud` on this machine is the **Hetzner** CLI, not Huawei KooCLI. Use the Python SDK (`huaweicloudsdk*`) in a scratch venv for Huawei API calls.

## Pending next steps

1. DONE 2026-11-14: private Hub repo `ibn3bbas/youtube-relay-bot` exists, `DOCKERHUB_TOKEN` (Repo Read & Write) is set, and run 43559441578 pushed `:latest` + `:3fd12ba…` (amd64). `docker build` is validated. The read-only PAT `cce-relay-bot-pull` exists for the cluster.
2. Optional cleanup: `gh secret delete SWR_REGISTRY_USER && gh secret delete SWR_REGISTRY_PASS`.
3. DONE 2026-11-14 on <cluster>: outbound internet works (egress IP <nat-eip>; Telegram, YouTube, googleapis and Docker Hub all reachable). The yt-dlp **listing** works, but every **video** hit "Sign in to confirm you're not a bot". With cookies + deno the newest source video resolved at 1080p. The Secret `ytdlp-cookies` exists in ns relay-bot (from the throwaway account "Relaybot Downloads", NOT the target channel).
4. Bot code now supports this: `YTDLP_COOKIES_FILE` (`src/config.py`), `_cookie_opts()` in `src/downloader.py` (works on a copy in DATA_DIR, re-copies when the Secret is newer), `yt-dlp[default]` + `deno` in requirements, `DENO_DIR=/tmp/.cache/deno`, manifest mounts the Secret at `/cookies` (mode 0440, optional).
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
