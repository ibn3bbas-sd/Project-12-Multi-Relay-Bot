# Open items

Where the Huawei Cloud CCE deployment stands. Last updated **2026-11-15**.
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

## Deployed (2026-11-15)

- [x] Account unfrozen. The PVC was recreated on `csi-disk-topology` (a separate disk, not shared with other workloads); pod running
- [x] Processed list (60 "start from now" IDs) copied into `/data/`; `/status` answers
- [x] First full relay confirmed: download through cookies → upload → Telegram report (Found 1, Uploaded 1)
- [x] Cookie expiry symptom seen: the log shows `Sign in to confirm you're not a bot` and `Metadata fetch failed`; refreshed per runbook 2b

## Still open

- [ ] Delete the old, retained EVS disk left by the first PVC (Retain policy) in the console
- [ ] Bot-check failures are logged but the Telegram report shows `Failed: 0`; count them as failed and hint "cookies expired?"
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
