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
                                  PVC relay-data (EVS, csi-disk-topology) -> /data
                              egress: NAT Gateway (SNAT) + EIP -> Telegram / YouTube
```

No Service, Ingress or LB. The bot only dials out (Telegram long-polling + YouTube).

## Files

- `k8s/relay-bot.yaml`: Namespace, PVC (EVS 20Gi `csi-disk-topology` + disk tags), Deployment, NetworkPolicy (deny ingress; egress 53 + 443).
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

- **This repo is public** (deleted and recreated from scrubbed history 2026-11-18; PRs #1/#2 are gone). Never commit IPs, hostnames, SSH users or keys, company cluster/org/repo names, account names or any secret. Cluster access details live in Claude's private project memory (`relay-bot-cluster-access`), not here. Docs say "the jumpbox" and `<jumpbox-eip>`.
- Runs #1 and #2 failed with `docker/login-action: Username and password required` because the secrets were never set. Set secrets **before** merging anything that triggers the build.
- Huawei SWR was dropped: the available AK/SK could push to an existing company org but could not create one (`403 SVCSTG.SWR.4030170`). Docker Hub is used instead. The old SWR repo secrets were deleted on 2026-11-15; only `DOCKERHUB_TOKEN` remains.
- Docker Hub's PAT dropdown defaults to **Repo Public Read-only**. That fails the push with `401 ... insufficient scopes` even though login succeeds. Push token: **Repo Read & Write**. Cluster pull token: **Repo Read-only**.
- From the cluster's NAT IP, the yt-dlp channel **listing** works but every **video** hits "Sign in to confirm you're not a bot". Fixed with cookies from a throwaway Google account + deno (newest source video resolved at 1080p).
- Cookie hygiene: export from InPrivate with the **throwaway** account, never the target channel's (it was nearly exported once). Close without signing out. The browser may save to the OneDrive Desktop: delete it and empty the OneDrive recycle bin. Shred the jumpbox copy.
- Secret-volume gotchas: don't nest a mount under another Secret's mountPath, and with non-root `runAsUser` use `defaultMode: 0440` + `fsGroup`, because 0400 is root-only.
- Keep secrets out of OneDrive: the repo folder syncs to the company cloud. Build `.env` and the tokens in `~/relay-setup` (WSL ext4).
- kubectl only works on the jumpbox. Before giving the user commands, check which host their prompt shows: laptop vs jumpbox mix-ups happened twice.
- `/usr/bin/hcloud` on the laptop is the **Hetzner** CLI. For Huawei use KooCLI `hwcloud ... --cli-profile=<profile>` (profile name in private memory); it prints a "multi-version" banner, so `grep -v multi-version` before `jq`.
- Ask the user to run secret-entry steps themselves (hidden `read -s` prompts, `gh secret set`), so values never pass through Claude.

## Status and next steps

**LIVE since 2026-11-15.** The account freeze (2026-11-14) was lifted. First full relay confirmed
(download through cookies → upload → Telegram report). Checks for new videos every 2h (silent when nothing uploads); daily top-up to 6 uploads with older source videos at 09:00 Asia/Riyadh (before the Pacific-midnight quota reset). Per-quota-day counts in `/data/upload_counts.json`. Rolled out 2026-11-17; `/fill` tested (6/6).

Single checklist: **`open-items.md`**. Remaining: see `open-items.md`.

- All 4 Secrets exist in ns `relay-bot`: `dockerhub-pull`, `relay-bot-env`, `relay-bot-files`, `ytdlp-cookies`.
- PVC `relay-data` is on `csi-disk-topology` (Delete policy, own tagged disk, separate from company workloads).
- Google Cloud OAuth app is **In production** (home + privacy pages on `gh-pages`). Upload token scope is
  `youtube.upload` only, so `channels.list` returns 403 by design. The token auto-refreshes into `/data/token.json`.

Private details (hosts, IPs, local paths, account names, the cookie account) are in Claude's project memory, never here.

## Lessons from going live (2026-11-15)

- **Storage class:** the user wants the bot's disk separate from company business. Use `csi-disk-topology` (repo manifest),
  never `clickhouse-block` (a company class with **Retain** policy). A PVC's class is immutable: to switch, scale to 0,
  delete PVC + PV, then delete the retained EVS disk in the console (it keeps billing), then re-apply the manifest.
- Get the manifest onto the jumpbox with `curl` from raw.githubusercontent.com (repo is public) or `scp`.
- Copy `processed_videos.json` in right after the first start and check the count
  (`python -c "import json;print(len(json.load(open(...))))"`) before the next scheduled run.
- **Cookie expiry** looks like `Sign in to confirm you're not a bot` + `Metadata fetch failed` in the logs. The Telegram report
  now counts these as Failed and adds a "cookies likely expired" hint (commit `2fcfc92`). Refresh per runbook 2b; the pod
  picks up the replaced Secret in about a minute, no restart needed. Test with `/run`.
- **Don't suggest creating a new throwaway Google account:** the user's only phone number and the phone itself (QR
  verification) are both "used too many times". Reuse the existing cookie account (named in private memory); recover it
  via accounts.google.com/signin/usernamerecovery if needed.
- Claude can push the cookie file itself without reading it: `scp` to the jumpbox, `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`,
  `shred -u` there, `rm` the local file. The user still empties the OneDrive recycle bin.
- After a push to main: wait for `build-push-dockerhub`, then `kubectl -n relay-bot rollout restart deploy/relay-bot`.
- The bot ignores `/start`; only `/run`, `/fill`, `/status`, `/next`, `/help` reply.

## More lessons from the setup

- Multi-line pastes into `read -s` can swallow the next line. Have the user paste the `read` line alone, then the command.
- Screenshots leak secrets (a bot token was exposed once and revoked). Tell the user not to screenshot token or secret dialogs.
- Get the Telegram chat ID from the bot's own `getUpdates` after the user sends `/start`, rather than a third-party bot.
  Updates sent before a token revoke are gone.
- Google's OAuth "Publish app" stays greyed until Branding has a home page + privacy policy + authorized domain.
- A Docker Hub read-only PAT gets 401 if you ask the auth endpoint for `pull,push`. Test with `pull` only.

## Lessons from closing open items (2026-11-17/18)

- The upload counter starts at 0 on rollout, so uploads made earlier that quota day are not counted (day one saw 8 uploads, no quota error).
- Extract downloaded tool tarballs (e.g. kubeconform) in a scratch dir: they contain `LICENSE`/`README.md` and silently overwrite the repo's.
- `~/projects/Project-12-YouTube-Relay-Bot` is a **symlink** to the OneDrive clone, not a second clone.
- Global git email is the company address; this repo has a **repo-local** `user.email` = GitHub noreply. Check `git log -1 --format=%ae` before pushing.
- History rewrite: `git filter-repo --replace-text F --replace-message F --mailmap M` on a `--mirror` clone, verify with `git log -p --all | grep`, `push --force --mirror` (`refs/pull/*` rejections are expected). Reset the working clone and `reflog expire` + `gc --prune=now` instead of deleting it (keeps ignored `data/`, `logs/`).
- PR refs keep old commits and only GitHub Support can drop them, but Support **declines on the free plan**. What worked: back up with `git bundle`, delete the repo in the web UI (gh lacks `delete_repo` scope), recreate with the same name, push `main` + `gh-pages`, re-enter `DOCKERHUB_TOKEN`. Never pick **Deletes** on the support form (it purges the whole repo).
- Making the repo private turns GitHub Pages off (free plan), taking the OAuth privacy page down. The token still refreshes.
- The jumpbox port-22 security-group allow-list grows with every new laptop IP (rules described `allow-my-ip: <date> for <host>`). Prune old ones with `hwcloud VPC DeleteSecurityGroupRule`; never touch rules with other descriptions (shared company SG).
- Jumpbox SSH is key-only (`/etc/ssh/sshd_config.d/00-hardening.conf`, the `00-` beats cloud-init's `50-`) with a fail2ban `sshd` jail (5 tries/10m, 1h ban). After `restart fail2ban`, wait a few seconds before `fail2ban-client status`.
- Before deleting a retained EVS disk, prove nothing references it: `kubectl get pv,pvc -A`, `volumeattachment`, and the PV `volumeHandle`, grepping both disk name and ID.
- Auto-mode may refuse edits to cluster Secrets; hand the user the exact `kubectl patch` command.

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
